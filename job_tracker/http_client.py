"""Polite HTTP client shared by all source adapters.

* identifies itself honestly (no browser impersonation, no anti-bot bypass)
* enforces a minimum delay between requests to the same host
* retries transient failures (429/5xx, timeouts) with backoff, honouring
  Retry-After, but never retries 401/403 (access restrictions are respected)
* counts requests per host for the diagnostic report
"""
import logging
import re
import time
from collections import Counter
from urllib.parse import urlparse

import requests

LOG = logging.getLogger(__name__)
USER_AGENT = ('Mozilla/5.0 (compatible; PersonalJobTracker/4.0; '
              '+https://github.com/Jojolap1/marketnotes2027)')
RETRY_STATUSES = {429, 500, 502, 503, 504}


BOT_CHECK = re.compile(r'<title>\s*(?:Quick Check Needed|Just a moment\.\.\.|Attention Required!|Access Denied|'
                       r'Are you a robot\?|Security Check|Pardon Our Interruption)', re.I)


class AccessDenied(requests.HTTPError):
    """The source refused access (401/403/451 or bot challenge). Not retried."""


def refuse_bot_check(response, url):
    """A challenge page served with HTTP 200 (e.g. Oleeo 'Quick Check Needed') is an
    access refusal, not an empty list; it is never solved (not bypassed)."""
    if response.status_code == 200 and BOT_CHECK.search(response.text[:4000]):
        raise AccessDenied(f'bot-check page instead of content for {url}', response=response)


class HttpClient:
    def __init__(self, min_interval=1.0, max_retries=2, timeout=25, sleep=time.sleep):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT,
                                     'Accept-Language': 'en-GB,en;q=0.9,fr;q=0.8'})
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.sleep = sleep
        self.last_request = {}
        self.requests_per_host = Counter()
        self.host_intervals = {}

    def set_host_interval(self, host, seconds):
        self.host_intervals[host] = float(seconds)

    def _wait(self, host):
        interval = self.host_intervals.get(host, self.min_interval)
        elapsed = time.monotonic() - self.last_request.get(host, -1e9)
        if elapsed < interval:
            self.sleep(interval - elapsed)
        self.last_request[host] = time.monotonic()

    def request(self, method, url, **kwargs):
        host = urlparse(url).hostname or ''
        kwargs.setdefault('timeout', self.timeout)
        attempt = 0
        while True:
            self._wait(host)
            self.requests_per_host[host] += 1
            try:
                response = self.session.request(method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= self.max_retries:
                    raise
                attempt += 1
                LOG.info('Retrying %s after %s', url, type(exc).__name__)
                self.sleep(2 ** attempt)
                continue
            if response.status_code in (401, 403, 451):
                raise AccessDenied(f'{response.status_code} access denied for {url}', response=response)
            refuse_bot_check(response, url)
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                attempt += 1
                retry_after = response.headers.get('Retry-After', '')
                delay = int(retry_after) if retry_after.isdigit() else 2 ** attempt * 2
                self.sleep(min(delay, 60))
                continue
            return response

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)

    def get_json(self, url, **kwargs):
        response = self.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    def post_json(self, url, payload, **kwargs):
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        headers.update(kwargs.pop('headers', {}))
        response = self.post(url, json=payload, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()
