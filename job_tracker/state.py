"""Persistent tracking history (seen_jobs.json).

Version 2 layout::

    {"version": 2,
     "jobs": {<key>: {company, title, location, url, first_seen, last_seen,
                      notified_at, status, fingerprints: [...]}},
     "sources": {<company>: {last_ok, last_error, consecutive_failures}}}

Version 1 files (a flat {id: {company, title, first_seen}} mapping written
by the original tracker) are migrated in place: every previously notified
job keeps its id, so it is never re-sent.
"""
import json
from datetime import datetime, timedelta, timezone

from jobs import fingerprints, normalized_title


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today():
    return datetime.now(timezone.utc).date().isoformat()


class State:
    def __init__(self, data=None, title_dedup_days=180):
        data = data or {}
        if data.get('version') != 2:
            data = self._migrate_v1(data)
        self.data = data
        self.title_dedup_days = title_dedup_days
        self._index = {}
        for key, rec in self.data['jobs'].items():
            for fp in rec.get('fingerprints', []):
                self._index[fp] = key

    @staticmethod
    def _migrate_v1(old):
        jobs = {}
        for job_id, rec in (old or {}).items():
            if not isinstance(rec, dict):
                continue
            company = rec.get('company', '')
            title = rec.get('title', '')
            fps = ['id:' + job_id]
            if title:
                fps.append(f'title:{normalized_title(company)}:{normalized_title(title)}:')
            jobs[job_id] = {'company': company, 'title': title.strip(), 'location': '', 'url': '',
                            'first_seen': rec.get('first_seen', ''), 'last_seen': '',
                            'notified_at': rec.get('first_seen', ''), 'status': 'unknown',
                            'fingerprints': fps, 'migrated_from_v1': True}
        return {'version': 2, 'jobs': jobs, 'sources': {}}

    @classmethod
    def load(cls, path, **kwargs):
        try:
            data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        except ValueError:
            raise RuntimeError(f'{path} is corrupt; refusing to overwrite tracking history')
        return cls(data, **kwargs)

    def save(self, path):
        path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')

    # -- dedup -------------------------------------------------------------
    def find(self, job, city=''):
        """Key of the history record matching this job, if any."""
        for fp in fingerprints(job, city):
            key = self._index.get(fp)
            if key is None:
                continue
            if fp.startswith('title:') and self._title_match_expired(key):
                continue
            return key
        return None

    def _title_match_expired(self, key):
        """A same-title repost months later is treated as a new vacancy."""
        last = self.data['jobs'][key].get('last_seen') or self.data['jobs'][key].get('notified_at') or ''
        try:
            seen = datetime.fromisoformat(last[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        return datetime.now(timezone.utc) - seen > timedelta(days=self.title_dedup_days)

    def is_notified(self, job, city=''):
        key = self.find(job, city)
        return bool(key and self.data['jobs'][key].get('notified_at'))

    def record(self, job, city='', notified=False):
        """Create or refresh the history record for a matching job."""
        key = self.find(job, city) or job['id']
        rec = self.data['jobs'].setdefault(key, {
            'company': job['company'], 'title': job['title'], 'location': job.get('location', ''),
            'url': job.get('url', ''), 'first_seen': now_iso(), 'notified_at': '', 'fingerprints': []})
        rec['last_seen'] = today()
        rec['status'] = job.get('status', 'unknown')
        if job.get('url') and not rec.get('url'):
            rec['url'] = job['url']
        if notified:
            rec['notified_at'] = now_iso()
        for fp in fingerprints(job, city):
            if fp not in rec['fingerprints']:
                rec['fingerprints'].append(fp)
            self._index[fp] = key
        return key

    def went_silent(self, company, listed, minimum=5):
        """Listed nothing although it listed at least `minimum` adverts before."""
        return listed == 0 and self.data['sources'].get(company, {}).get('last_listed', 0) >= minimum

    def mark_closed(self, key, reason):
        rec = self.data['jobs'].get(key)
        if rec and rec.get('status') != 'closed':
            rec['status'] = 'closed'
            rec['closed_reason'] = reason
            rec['closed_on'] = today()

    # -- source health -----------------------------------------------------
    def source_result(self, company, ok, error='', listed=None):
        rec = self.data['sources'].setdefault(company, {'consecutive_failures': 0})
        if ok:
            rec['last_ok'] = today()
            rec['consecutive_failures'] = 0
            rec.pop('last_error', None)
            if listed:
                rec['last_listed'] = listed
        else:
            rec['consecutive_failures'] = rec.get('consecutive_failures', 0) + 1
            rec['last_error'] = str(error)[:200]
        return rec
