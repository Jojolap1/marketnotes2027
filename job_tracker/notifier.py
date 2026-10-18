"""Telegram alerts.

The bot token is read from the environment only and is never logged: error
messages are scrubbed because request exceptions embed the full API URL.
"""
import html
import logging
import os
import re
import time

import requests

from status import age_days, label as status_label, parse_date

LOG = logging.getLogger(__name__)
API = 'https://api.telegram.org/bot{token}/{method}'


def credentials():
    return os.environ.get('TELEGRAM_BOT_TOKEN', '').strip(), os.environ.get('TELEGRAM_CHAT_ID', '').strip()


def _scrub(text, token):
    text = str(text)
    if token:
        text = text.replace(token, '***')
    return re.sub(r'bot\d+:[A-Za-z0-9_-]+', 'bot***', text)


def format_date(value):
    d = parse_date(value)
    return d.date().isoformat() if d else ''


def format_message(job, classification):
    e = html.escape
    c = classification
    status_icon = {'open': '✅', 'unknown': '❔'}.get(job.get('status'), '⚠️')
    employment = job.get('employment_type') or ''
    lines = [
        f'🆕 <b>{e(job["title"])}</b>',
        f'🏦 {e(job["company"])}',
        f'📍 {e(job["location"][:150])}',
        f'🎓 {e(c.level_label)} · experience: {e(c.experience_label)}',
        f'💼 Employment type: {e(employment or c.level_label)}',
    ]
    published = format_date(job.get('published'))
    age = age_days(job.get('published'))
    if published:
        lines.append(f'📅 Published: {published} ({"today" if age == 0 else f"{age} day(s) ago"})')
    else:
        lines.append('📅 Published: date not provided by the source (sent in case it is recent)')
    lines.append(f'{status_icon} Application status: {e(status_label(job.get("status")))}'
                 + (f' — {e(job.get("status_evidence", "")[:160])}' if job.get('status_evidence') else ''))
    lines.append(f'💡 Why it matches: {e("; ".join(c.reasons))}')
    url = job.get('apply_url') or job.get('url')
    lines.append(f'🔗 <a href="{e(url, quote=True)}">Official posting / apply</a>')
    lines.append(e(url))
    return '\n'.join(lines)


class Telegram:
    def __init__(self, session=None, sleep=time.sleep):
        self.token, self.chat_id = credentials()
        self.session = session or requests.Session()
        self.sleep = sleep

    @property
    def configured(self):
        return bool(self.token and self.chat_id)

    def _call(self, method, payload, attempts=3):
        for attempt in range(attempts):
            try:
                response = self.session.post(API.format(token=self.token, method=method), json=payload, timeout=25)
                data = response.json() if response.content else {}
            except (requests.RequestException, ValueError) as exc:
                LOG.error('Telegram %s failed: %s', method, _scrub(exc, self.token))
                self.sleep(2 ** attempt)
                continue
            if response.status_code == 429:
                wait = int((data.get('parameters') or {}).get('retry_after', 5))
                LOG.warning('Telegram rate limit; waiting %ss', wait)
                self.sleep(min(wait, 60))
                continue
            if not data.get('ok'):
                LOG.error('Telegram %s rejected: HTTP %s %s', method, response.status_code,
                          _scrub(data.get('description', ''), self.token))
                return None
            return data.get('result')
        return None

    def check(self):
        """Validate token and chat without sending a message (getMe/getChat)."""
        if not self.configured:
            return False, 'TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID are not set'
        me = self._call('getMe', {})
        if not me:
            return False, 'getMe failed: bot token rejected'
        chat = self._call('getChat', {'chat_id': self.chat_id})
        if not chat:
            return False, f'bot @{me.get("username")} cannot access the configured chat'
        return True, f'bot @{me.get("username")} can post to chat type "{chat.get("type")}"'

    def send(self, text):
        result = self._call('sendMessage', {'chat_id': self.chat_id, 'text': text, 'parse_mode': 'HTML',
                                            'disable_web_page_preview': True})
        self.sleep(1.1)  # stay well under Telegram's per-chat limit
        return result is not None
