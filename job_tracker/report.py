"""Diagnostic run report (Markdown), also appended to the GitHub job summary."""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger('report')


class Report:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.started = datetime.now(timezone.utc)
        self.sources = []
        self.matched = []
        self.new = []
        self.reasons = {}
        self.near_misses = []
        self.sent = 0
        self.pending = 0
        self.note = ''

    @property
    def any_source_ok(self):
        return any(s['ok'] for s in self.sources)

    def source_ok(self, source, jobs, seconds, stats):
        row = {'name': source['company'], 'platform': source['platform'], 'ok': True, 'jobs': len(jobs),
               'listed': stats.get('listed', ''), 'candidates': stats.get('candidates', ''),
               'details': stats.get('detail_requests', 0), 'cache_hits': stats.get('detail_cache_hits', 0),
               'pending': stats.get('pending_after_run', 0), 'seconds': seconds, 'error': ''}
        self.sources.append(row)
        LOG.info('%s: ok, %s listed, %s candidates, %d jobs after screening, %d detail requests (%.1fs)',
                 row['name'], row['listed'], row['candidates'], row['jobs'], row['details'], seconds)

    def source_failed(self, source, error, consecutive, seconds, stats=None):
        self.sources.append({'name': source['company'], 'platform': source['platform'], 'ok': False, 'jobs': 0,
                             'listed': (stats or {}).get('listed', ''), 'candidates': '', 'details': 0, 'cache_hits': 0,
                             'pending': 0, 'seconds': seconds, 'error': f'{error} (failures in a row: {consecutive})'})
        LOG.warning('%s: FAILED (%s in a row): %s', source['company'], consecutive, error)

    def source_skipped(self, source, reason):
        self.sources.append({'name': source['company'], 'platform': source['platform'], 'ok': None, 'jobs': 0,
                             'listed': '', 'candidates': '', 'details': 0, 'cache_hits': 0, 'pending': 0,
                             'seconds': 0, 'error': 'skipped: ' + reason})

    def classification(self, matched, new, reasons, near_misses=(), samples=None):
        self.matched, self.new, self.reasons = matched, new, dict(reasons)
        self.near_misses = list(near_misses)
        self.samples = samples or {}
        LOG.info('%d relevant jobs matched, %d new (not yet notified)', len(matched), len(new))

    def notifications(self, sent, total_new, note):
        self.sent, self.pending, self.note = sent, max(0, total_new - sent), note
        LOG.info('Notifications: %d sent; %s', sent, note)

    def render(self, http=None):
        mode = 'DRY RUN' if self.dry_run else 'PRODUCTION'
        ok = sum(1 for s in self.sources if s['ok'])
        failed = sum(1 for s in self.sources if s['ok'] is False)
        lines = [f'# Job tracker run — {self.started:%Y-%m-%d %H:%M} UTC ({mode})', '',
                 f'Sources: {ok} ok, {failed} failed, {len(self.sources) - ok - failed} skipped. '
                 f'Relevant jobs: {len(self.matched)}. New: {len(self.new)}. Alerts sent: {self.sent}. {self.note}', '',
                 '## Sources', '', '| Source | Platform | Result | Listed | Screened | Kept | Detail req. | Cache hits | Backlog | Time |',
                 '|---|---|---|---|---|---|---|---|---|---|']
        for s in self.sources:
            result = 'ok' if s['ok'] else ('FAILED: ' + s['error'] if s['ok'] is False else s['error'])
            lines.append(f"| {s['name']} | {s['platform']} | {result.replace('|', '/')[:160]} | {s['listed']} | {s['candidates']} | "
                         f"{s['jobs']} | {s['details']} | {s['cache_hits']} | {s['pending']} | {s['seconds']:.0f}s |")
        lines += ['', '## Relevant jobs', '']
        new_ids = {j['id'] for j, _ in self.new}
        for job, c in sorted(self.matched, key=lambda x: (x[0]['company'], x[0]['title'])):
            flag = ('NEW' if job['id'] in new_ids else 'closed' if job.get('status') == 'closed'
                    else 'older than limit' if job.get('too_old') else 'already notified')
            lines.append(f"- [{flag}] **{job['company']}** — {job['title']} — {c.city} — {c.level_label} — "
                         f"exp: {c.experience_label} — status: {job.get('status')}"
                         + (f" ({job.get('status_evidence', '')[:90]})" if job.get('status') != 'open' else '') + f" — {job['url']}")
        if self.near_misses:
            lines += ['', '## Near misses (target city and role, rejected on level/experience)', '']
            for job, c in self.near_misses[:150]:
                lines.append(f"- {job['company']} — {job['title']} — {c.city} — {c.reject_reason}")
        if getattr(self, 'samples', None):
            lines += ['', '## Rejected samples per source (target-city screening passed)', '']
            for company, items in sorted(self.samples.items()):
                for job, c in items:
                    lines.append(f"- {company} — {job['title'][:110]} — [{job['location'][:50]}] — {c.reject_reason}")
        if getattr(self, 'screened_out', None):
            lines += ['', '## Titles screened out before detail fetch (target cities, samples)', '']
            for company, titles in sorted(self.screened_out.items()):
                lines.append(f"- {company}: " + ' | '.join(t[:70] for t in titles))
        if self.reasons:
            lines += ['', '## Exclusion reasons (all screened jobs)', '']
            for reason, n in sorted(self.reasons.items(), key=lambda x: -x[1]):
                lines.append(f'- {reason}: {n}')
        if getattr(self, 'watch_notes', None):
            lines += ['', '## Page watches (employers without a job list)', ''] + [f'- {n}' for n in self.watch_notes]
        if http is not None:
            lines += ['', '## Requests per host', '']
            for host, n in http.requests_per_host.most_common():
                lines.append(f'- {host}: {n}')
        return '\n'.join(lines) + '\n'

    def write(self, path, http=None):
        text = self.render(http)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text, encoding='utf-8')
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a', encoding='utf-8') as fh:
                fh.write(text)
        return text
