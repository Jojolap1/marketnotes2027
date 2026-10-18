"""Sales & Trading job tracker.

Pipeline: fetch official vacancies -> normalize/deduplicate -> classify ->
check application status -> select genuinely new jobs -> Telegram alerts ->
save history -> diagnostic report. A failing source is recorded and skipped;
it never stops the other sources.

Usage:
  python tracker.py                 # production (sends alerts, saves state)
  DRY_RUN=1 python tracker.py       # no alerts, no state changes
  python tracker.py --check-telegram  # validate secrets without sending
"""
import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path

from filters import Classifier
from http_client import AccessDenied, HttpClient
from jobs import fingerprints
from notifier import Telegram, format_message
from report import Report
from sources import fetch_source
from sources.context import Context, DetailCache
from state import State
from status import CLOSED, parse_date

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / 'config.json'
STATE_FILE = ROOT / 'seen_jobs.json'
CACHE_FILE = ROOT / '.cache' / 'details.json'
REPORT_FILE = ROOT / 'reports' / 'last_run.md'
LOG = logging.getLogger('tracker')


def load_config(path=CONFIG_FILE):
    return json.loads(Path(path).read_text(encoding='utf-8'))


CONFIG = load_config()
_CLASSIFIER = Classifier(CONFIG)


def matches(job):
    """Backward-compatible boolean filter used by older tests and tools."""
    return _CLASSIFIER.classify(job).eligible


def _sort_key(item):
    job, _ = item
    published = parse_date(job.get('published'))
    return (job.get('status') != 'open', -(published.timestamp() if published else 0))


def collect(config, ctx, state, report):
    jobs = []
    for source in config['sources']:
        name = source['company']
        if not source.get('enabled', True):
            report.source_skipped(source, source.get('disabled_reason', 'disabled in config'))
            continue
        started = time.monotonic()
        try:
            found = fetch_source(source, ctx)
        except AccessDenied as exc:
            rec = state.source_result(name, False, exc)
            report.source_failed(source, f'access denied by the site ({exc})', rec['consecutive_failures'], time.monotonic() - started, ctx.stats.get(name))
            continue
        except Exception as exc:  # one broken source must not stop the run
            rec = state.source_result(name, False, f'{type(exc).__name__}: {exc}')
            report.source_failed(source, f'{type(exc).__name__}: {exc}', rec['consecutive_failures'], time.monotonic() - started, ctx.stats.get(name))
            continue
        state.source_result(name, True)
        report.source_ok(source, found, time.monotonic() - started, ctx.stats.get(name, {}))
        for job in found:
            job['source_name'] = name
        jobs.extend(found)
    return jobs


def select(jobs, classifier, state, config, report):
    """Classify, deduplicate within the run and split into new / known."""
    notify_statuses = set(config.get('notifications', {}).get('statuses', ['open', 'unknown']))
    run_keys, new, matched, near = set(), [], [], []
    reasons = Counter()
    samples = {}
    for job in jobs:
        c = classifier.classify(job)
        if not c.eligible:
            reasons[c.reject_reason.split(' (')[0]] += 1
            bucket = samples.setdefault(job['company'], [])
            if len(bucket) < 6 and not c.reject_reason.startswith('location not targeted'):
                bucket.append((job, c))
            if c.city and c.role_family:
                near.append((job, c))
            continue
        keys = fingerprints(job, c.city)
        if run_keys.intersection(keys):
            reasons['duplicate within run'] += 1
            continue
        run_keys.update(keys)
        matched.append((job, c))
        if job.get('status') == CLOSED:
            key = state.find(job, c.city)
            if key:
                state.mark_closed(key, job.get('status_evidence', 'closed'))
            reasons['matched but closed'] += 1
            continue
        if state.is_notified(job, c.city):
            state.record(job, c.city)
            continue
        if job.get('status') not in notify_statuses:
            reasons[f'status {job.get("status")} not notified'] += 1
            continue
        new.append((job, c))
    new.sort(key=_sort_key)
    report.classification(matched, new, reasons, near, samples)
    return matched, new


def run(config, state_path=STATE_FILE, cache_path=CACHE_FILE, report_path=REPORT_FILE,
        dry_run=False, http=None, telegram=None):
    notif = config.get('notifications', {})
    classifier = Classifier(config)
    state = State.load(Path(state_path), title_dedup_days=int(notif.get('title_dedup_days', 180)))
    http = http or HttpClient(min_interval=float(config.get('http', {}).get('min_interval_seconds', 1.0)))
    for host, seconds in config.get('http', {}).get('host_intervals', {}).items():
        http.set_host_interval(host, seconds)
    ctx = Context(http, classifier, DetailCache(Path(cache_path) if cache_path else None))
    report = Report(dry_run=dry_run)

    jobs = collect(config, ctx, state, report)
    if not report.any_source_ok:
        report.write(report_path, http)
        raise RuntimeError('All job sources failed; preserving existing state')
    matched, new = select(jobs, classifier, state, config, report)

    max_per_run = int(notif.get('max_per_run', 15))
    exit_code = 0
    if dry_run:
        for job, c in new[:60]:
            LOG.info('DRY RUN NEW: %s | %s | %s | %s | %s', job['company'], job['title'], job['location'][:60], job['status'], job['url'])
        for job, c in new[:2]:
            LOG.info('DRY RUN sample alert:\n%s', format_message(job, c))
        report.notifications(0, len(new), 'dry run: nothing sent, state not saved')
    else:
        telegram = telegram or Telegram()
        if not telegram.configured:
            report.notifications(0, len(new), 'TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing: nothing sent')
            exit_code = 2 if new else 0
        else:
            sent = failures = 0
            for job, c in new[:max_per_run]:
                if telegram.send(format_message(job, c)):
                    state.record(job, c.city, notified=True)
                    sent += 1
                else:
                    failures += 1
                    if failures >= 3:
                        break
            note = f'{max(0, len(new) - sent)} pending for the next run' if len(new) > sent else 'all sent'
            report.notifications(sent, len(new), note + (f'; {failures} send failures' if failures else ''))
            if failures:
                exit_code = 3
        state.save(Path(state_path))
    ctx.cache.save()
    report.write(report_path, http)
    return exit_code, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check-telegram', action='store_true', help='validate Telegram secrets without sending')
    parser.add_argument('--dry-run', action='store_true', help='do not send alerts or save state')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    if args.check_telegram:
        ok, message = Telegram().check()
        LOG.info('Telegram check: %s', message)
        return 0 if ok else 1
    dry_run = args.dry_run or os.environ.get('DRY_RUN') == '1'
    exit_code, _ = run(CONFIG, dry_run=dry_run)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
