"""Free job tracker: public Greenhouse/Lever APIs, Telegram notifications."""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
STATE_FILE = ROOT / 'seen_jobs.json'
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'PersonalJobAlertTracker/1.0 (public job feeds)'})
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')


def fetch(source):
    company, board = source['company'], source['board']
    kind = source['platform'].lower()
    if kind == 'greenhouse':
        url = f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs"
        data = SESSION.get(url, timeout=25)
        data.raise_for_status()
        return [{'id': f'greenhouse:{board}:{j["id"]}', 'company': company,
                 'title': j.get('title', ''), 'location': j.get('location', {}).get('name', ''),
                 'url': j.get('absolute_url', ''), 'published': j.get('updated_at', '')}
                for j in data.json().get('jobs', [])]
    if kind == 'lever':
        url = f"https://api.lever.co/v0/postings/{quote(board)}?mode=json"
        data = SESSION.get(url, timeout=25)
        data.raise_for_status()
        return [{'id': f'lever:{board}:{j["id"]}', 'company': company,
                 'title': j.get('text', ''), 'location': j.get('categories', {}).get('location', ''),
                 'url': j.get('hostedUrl', ''), 'published': ''}
                for j in data.json()]
    raise ValueError(f'Unknown platform: {kind}')


def matches(job):
    title = job['title'].lower()
    loc = job['location'].lower()
    if not any(re.search(p, loc, re.I) for p in CONFIG['location_patterns']):
        return False
    if any(re.search(p, title, re.I) for p in CONFIG['exclude_title_patterns']):
        return False
    return any(re.search(p, title, re.I) for p in CONFIG['include_title_patterns'])


def send_telegram(jobs):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logging.warning('Telegram secrets missing; jobs printed but not marked as notified.')
        for job in jobs:
            logging.info('MATCH: %s | %s | %s', job['company'], job['title'], job['url'])
        return False
    for job in jobs:
        message = (f"🆕 New job: {job['title']}\n"
                   f"🏦 {job['company']}\n📍 {job['location']}\n"
                   f"🔗 {job['url']}")
        response = SESSION.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                json={'chat_id': chat_id, 'text': message,
                                      'disable_web_page_preview': True}, timeout=25)
        response.raise_for_status()
        time.sleep(0.15)
    return True


def main():
    seen = json.loads(STATE_FILE.read_text(encoding='utf-8')) if STATE_FILE.exists() else {}
    found = {}
    successful = 0
    for source in CONFIG['sources']:
        try:
            jobs = fetch(source)
            successful += 1
            logging.info('%s: %d jobs', source['company'], len(jobs))
            for job in jobs:
                if matches(job) and job['url']:
                    found[job['id']] = job
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            logging.warning('Source unavailable (%s): %s', source['company'], exc)
    if not successful:
        raise RuntimeError('No job feeds available; preserving state and failing run.')
    new = [job for id_, job in found.items() if id_ not in seen]
    logging.info('%d matching jobs, %d new', len(found), len(new))
    if new and not send_telegram(new):
        return
    now = datetime.now(timezone.utc).isoformat()
    for job in new:
        seen[job['id']] = {'company': job['company'], 'title': job['title'], 'first_seen': now}
    STATE_FILE.write_text(json.dumps(seen, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
