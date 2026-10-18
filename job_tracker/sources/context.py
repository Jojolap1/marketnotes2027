"""Shared state handed to adapters: HTTP client, detail cache, prefilter."""
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

LOG = logging.getLogger(__name__)


class DetailCache:
    """JSON cache of parsed vacancy details, keyed by URL.

    Lets sitemap/ATS adapters skip re-downloading detail pages that have
    not changed (incremental fetching across hourly runs).
    """

    def __init__(self, path=None, max_entries=20000):
        self.path = path
        self.max_entries = max_entries
        self.data = {}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding='utf-8'))
            except ValueError:
                LOG.warning('Ignoring corrupt cache %s', path)

    def get(self, key, max_age_hours=None, lastmod=None):
        entry = self.data.get(key)
        if not entry:
            return None
        if lastmod and entry.get('lastmod') and entry['lastmod'] != lastmod:
            return None
        if max_age_hours is not None:
            age = time.time() - entry.get('fetched', 0)
            if age > max_age_hours * 3600:
                return None
        return entry.get('value')

    def put(self, key, value, lastmod=None):
        self.data[key] = {'value': value, 'fetched': time.time(), 'lastmod': lastmod or ''}

    def save(self):
        if not self.path:
            return
        if len(self.data) > self.max_entries:
            newest = sorted(self.data.items(), key=lambda kv: -kv[1].get('fetched', 0))[:self.max_entries]
            self.data = dict(newest)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')


class Context:
    def __init__(self, http, classifier, cache=None, now=None):
        self.http = http
        self.classifier = classifier
        self.cache = cache or DetailCache()
        self.now = now or datetime.now(timezone.utc)
        self.stats = {}

    def stat(self, company, key, inc=1):
        bucket = self.stats.setdefault(company, {})
        bucket[key] = bucket.get(key, 0) + inc

    def title_worth_fetching(self, title):
        """Cheap title-only screen applied before any per-vacancy request.

        Rejects titles that can never match (senior, non front-office, not a
        targeted role) so detail requests are spent on plausible vacancies.
        """
        c = self.classifier
        if any(p.search(title) for p in c.senior + c.non_front_office):
            return False
        entry = c.entry_type(title)
        if (not entry or entry == 'entry_level') and any(p.search(title) for p in c.soft_senior):
            return False
        return bool(c.role_family(title))

    def city_of(self, text):
        return self.classifier.city(text or '')

    def recent(self, published, days):
        from status import parse_date
        d = parse_date(published)
        return d is None or d >= self.now - timedelta(days=days)


def slug_words(url):
    return re.sub(r'[-_/+%2C]+', ' ', url.lower())
