"""Test doubles: an in-memory HTTP client and Telegram stub."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from http_client import AccessDenied, refuse_bot_check  # noqa: E402


class FakeResponse:
    def __init__(self, status=200, body='', url=''):
        self.status_code = status
        self.url = url
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        self.text = body
        self.content = body.encode('utf-8')
        self.headers = {}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} for {self.url}', response=self)


class FakeHttp:
    """Routes requests to canned responses by URL substring (first match wins).

    A route value may be a body, (status, body), or a callable(method, url, json)."""

    def __init__(self, routes):
        self.routes = list(routes.items()) if isinstance(routes, dict) else list(routes)
        self.calls = []
        self.requests_per_host = Counter()

    def set_host_interval(self, host, seconds):
        pass

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get('json')))
        self.requests_per_host[url.split('/')[2]] += 1
        for pattern, value in self.routes:
            if pattern in url:
                if callable(value):
                    value = value(method, url, kwargs.get('json'))
                status, body = value if isinstance(value, tuple) else (200, value)
                if status in (401, 403):
                    raise AccessDenied(f'{status} access denied for {url}')
                response = FakeResponse(status, body, url)
                refuse_bot_check(response, url)
                return response
        raise requests.ConnectionError(f'no route for {url}')

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)

    def get_json(self, url, **kwargs):
        r = self.get(url, **kwargs)
        r.raise_for_status()
        return r.json()

    def post_json(self, url, payload, **kwargs):
        r = self.request('POST', url, json=payload)
        r.raise_for_status()
        return r.json()


class FakeTelegram:
    def __init__(self, configured=True, fail_titles=()):
        self.configured = configured
        self.sent = []
        self.fail_titles = fail_titles

    def send(self, text):
        if any(t in text for t in self.fail_titles):
            return False
        self.sent.append(text)
        return True


def config_with(sources):
    from tracker import load_config
    config = load_config()
    config = dict(config)
    config['sources'] = sources
    return config


def context(routes, now=None):
    from filters import Classifier
    from sources.context import Context
    from tracker import load_config
    return Context(FakeHttp(routes), Classifier(load_config()), now=now)
