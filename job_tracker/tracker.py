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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from filters import Classifier
from http_client import AccessDenied, HttpClient
from jobs import borrow_descriptions, company_key, configure_companies, fingerprints, normalized_title
from notifier import Telegram, format_message
from report import Report
from sources import fetch_source
from sources.context import Context, DetailCache
from state import State
from status import CLOSED, age_days, parse_date
import watches

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
        stats = ctx.stats.get(name, {})
        listed = stats.get('listed', len(found))
        listed = listed if isinstance(listed, int) else len(found)
        if stats.get('not_due'):   # throttled source (search quota): nothing listed on purpose
            listed = None
        elif state.went_silent(name, listed):
            report.silent.append(name)
            LOG.warning('%s: lists no advert although it listed %s before', name, state.data['sources'][name]['last_listed'])
        state.source_result(name, True, listed=listed)
        report.source_ok(source, found, time.monotonic() - started, stats)
        for job in found:
            job['source_name'] = name
        jobs.extend(found)
    return jobs


SECONDARY = {'email_alerts', 'web_search', 'web_leads'}


def drop_stale_web_leads(jobs, config, report):
    """A web search lead for an employer whose own careers site was read
    successfully this run, but which that site no longer lists, is closed or
    stale (job boards keep adverts after the employer closes them): drop it."""
    platform = {s['company']: s['platform'] for s in config.get('sources', [])}
    read_ok = {company_key(r['name']) for r in report.sources
               if r.get('ok') and platform.get(r['name']) not in SECONDARY}
    official = {(company_key(j['company']), normalized_title(j['title'])) for j in jobs
                if platform.get(j.get('source_name')) not in SECONDARY}
    tracked = {company_key(s['company']) for s in config.get('sources', [])
               if s.get('enabled', True) and s['platform'] not in SECONDARY}
    kept = []
    for job in jobs:
        if platform.get(job.get('source_name')) == 'web_leads':
            employer = company_key(job['company'])
            if employer in read_ok and (employer, normalized_title(job['title'])) not in official:
                LOG.info('web lead dropped, not on %s\'s own site any more: %s', job['company'], job['title'][:80])
                continue
            if employer in tracked and employer not in read_ok:
                # The employer's site failed this run: check again next run
                LOG.info('web lead held, %s\'s own site could not be read this run: %s', job['company'], job['title'][:80])
                continue
        kept.append(job)
    return kept


def select(jobs, classifier, state, config, report):
    """Classify, deduplicate within the run and split into new / known."""
    notify_statuses = set(config.get('notifications', {}).get('statuses', ['open', 'unknown']))
    # Jobs older than this are not alerted; jobs without a publication date are (just in case).
    max_age = config.get('notifications', {}).get('max_age_days')
    # Newly added employers: their open adverts are alerted once whatever their
    # publication date, until `backfill_until` (the user has never seen them).
    today = datetime.now(timezone.utc).date().isoformat()
    backfill = {s['company'] for s in config.get('sources', []) if str(s.get('backfill_until', '')) >= today}
    # Newly added target cities (Dubai, 2026-10-24): same one-time backfill
    city_backfill = {city for city, until in config.get('notifications', {}).get('city_backfill', {}).items() if str(until) >= today}
    borrowed = borrow_descriptions(jobs)
    if borrowed:
        LOG.info('%d title-only offers checked against the advert text of the official site', borrowed)
    jobs = drop_stale_web_leads(jobs, config, report)
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
        age = age_days(job.get('published'))
        if max_age is not None and age is not None and age > max_age and job['company'] not in backfill and c.city not in city_backfill:
            reasons[f'published more than {max_age} days ago'] += 1
            job['too_old'] = True
            continue
        new.append((job, c))
    new.sort(key=_sort_key)
    report.classification(matched, new, reasons, near, samples)
    return matched, new


def in_send_window(window, now=None):
    """True when alerts may be sent now. window = {timezone, start_hour, end_hour}
    (end hour inclusive: 11-22 allows 11:00 to 22:59 local time). No window = always."""
    if not window:
        return True
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(window.get('timezone', 'UTC')))
    return int(window.get('start_hour', 0)) <= local.hour <= int(window.get('end_hour', 23))


def too_soon(state, notif, now=None):
    minutes = notif.get('min_minutes_between_sends')
    last = state.data.get('last_send_slot')
    if not minutes or not last:
        return False
    last = datetime.fromisoformat(last)
    return (now or datetime.now(timezone.utc)) - last < timedelta(minutes=int(minutes))


def status_date(notif, now=None):
    tz = ZoneInfo((notif.get('send_window') or {}).get('timezone', 'UTC'))
    return (now or datetime.now(timezone.utc)).astimezone(tz).date().isoformat()


def daily_status(report, state, notif, now=None, sent_now=0):
    """Evening health message (once a day, from `daily_status_hour` local time):
    sources read, failing or gone silent, and alerts sent today. Real run data
    only, so the user knows the tracker runs even on days without offers."""
    hour = notif.get('daily_status_hour')
    if hour is None:
        return None
    tz = ZoneInfo((notif.get('send_window') or {}).get('timezone', 'UTC'))
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    if local.hour < int(hour) or state.data.get('last_status_date') == local.date().isoformat():
        return None
    today = local.date().isoformat()
    sent_today = sum(1 for r in state.data['jobs'].values()
                     if r.get('notified_at') and datetime.fromisoformat(r['notified_at']).astimezone(tz).date().isoformat() == today)
    ok = [s for s in report.sources if s['ok']]
    failed = [s['name'] for s in report.sources if s['ok'] is False]
    lines = [f'📊 <b>Point du jour</b> ({local:%d/%m %H:%M})',
             f'Offres envoyées aujourd\'hui : {sent_today}',
             f'Sites lus sans erreur : {len(ok)} / {len(ok) + len(failed)}']
    if failed:
        lines.append('En erreur : ' + ', '.join(failed[:12]) + (' …' if len(failed) > 12 else ''))
    if report.silent:
        lines.append('Muets (0 offre, inhabituel) : ' + ', '.join(report.silent))
    if not failed and not report.silent:
        lines.append('Aucun site en erreur.')
    return '\n'.join(lines)


def describe_window(window):
    return f"{window.get('start_hour')}h-{window.get('end_hour')}h {window.get('timezone')}"


def run(config, state_path=STATE_FILE, cache_path=CACHE_FILE, report_path=REPORT_FILE,
        dry_run=False, http=None, telegram=None, now=None, scheduled=False):
    notif = config.get('notifications', {})
    configure_companies([s['company'] for s in config['sources']] + config.get('company_aliases', []))
    classifier = Classifier(config)
    state = State.load(Path(state_path), title_dedup_days=int(notif.get('title_dedup_days', 180)))
    http = http or HttpClient(min_interval=float(config.get('http', {}).get('min_interval_seconds', 1.0)))
    for host, seconds in config.get('http', {}).get('host_intervals', {}).items():
        http.set_host_interval(host, seconds)
    ctx = Context(http, classifier, DetailCache(Path(cache_path) if cache_path else None))
    report = Report(dry_run=dry_run)

    if not dry_run and not in_send_window(notif.get('send_window'), now):
        # External triggers (cron-job.org every 2 h) also fire at night: skip
        # every source request until the send window opens.
        report.notifications(0, 0, f'skipped: outside the send window ({describe_window(notif["send_window"])})')
        report.write(report_path, http)
        return 0, report

    if scheduled and not dry_run and too_soon(state, notif, now):
        # Extra scheduled triggers only cover runs GitHub drops: skip all
        # source requests until the next send slot is due.
        report.notifications(0, 0, f'skipped: last send slot less than {notif["min_minutes_between_sends"]} min ago')
        report.write(report_path, http)
        return 0, report

    jobs = collect(config, ctx, state, report)
    if not report.any_source_ok:
        report.write(report_path, http)
        raise RuntimeError('All job sources failed; preserving existing state')
    matched, new = select(jobs, classifier, state, config, report)
    changes, report.watch_notes = watches.check(config.get('page_watches'), http, state)

    max_per_run = int(notif.get('max_per_run', 15))
    exit_code = 0
    if dry_run:
        for job, c in new[:60]:
            LOG.info('DRY RUN NEW: %s | %s | %s | %s | %s', job['company'], job['title'], job['location'][:60], job['status'], job['url'])
        for job, c in new[:2]:
            LOG.info('DRY RUN sample alert:\n%s', format_message(job, c))
        for watch, _, text in changes:
            LOG.info('DRY RUN page watch alert:\n%s', watches.message(watch, text))
        report.notifications(0, len(new), 'dry run: nothing sent, state not saved')
    elif not in_send_window(notif.get('send_window'), now):
        report.notifications(0, len(new), f'outside the send window ({describe_window(notif["send_window"])}): '
                                          f'{len(new)} pending for the next slot')
        state.save(Path(state_path))
    else:
        state.data['last_send_slot'] = (now or datetime.now(timezone.utc)).isoformat()
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
            for watch, digest, text in changes:
                if telegram.send(watches.message(watch, text)):
                    watches.mark_sent(state, watch, digest)
                else:
                    failures += 1
            note = f'{max(0, len(new) - sent)} pending for the next run' if len(new) > sent else 'all sent'
            status = daily_status(report, state, notif, now, sent)
            if status and telegram.send(status):
                state.data['last_status_date'] = status_date(notif, now)
            report.notifications(sent, len(new), note + (f'; {failures} send failures' if failures else ''))
            if failures:
                exit_code = 3
        state.save(Path(state_path))
    ctx.cache.save()
    report.screened_out = ctx.screened_out
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
    exit_code, _ = run(CONFIG, dry_run=dry_run, scheduled=os.environ.get('GITHUB_EVENT_NAME') == 'schedule')
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
