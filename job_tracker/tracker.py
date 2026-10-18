"""Track public Greenhouse and Lever postings and notify via Telegram."""
import json
import html
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
import requests

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
STATE_FILE = ROOT / 'seen_jobs.json'
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'PersonalJobAlertTracker/2.0 (public job feeds)'})
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')


def fetch(source):
    company, board = source['company'], source.get('board', '')
    kind = source['platform'].lower()
    if kind == 'greenhouse':
        url = f'https://boards-api.greenhouse.io/v1/boards/{quote(board, safe="")}/jobs?content=true'
        response = SESSION.get(url, timeout=25)
        response.raise_for_status()
        payload = response.json()
        return [{'id': f'greenhouse:{board}:{j["id"]}', 'company': company,
                 'title': j.get('title') or '',
                 'location': (j.get('location') or {}).get('name') or '',
                 'url': j.get('absolute_url') or '',
                 'published': j.get('updated_at') or '', 'description': html.unescape(re.sub(r'<[^>]*>', ' ', j.get('content') or ''))}
                for j in payload.get('jobs', [])]
    if kind == 'lever':
        url = f'https://api.lever.co/v0/postings/{quote(board, safe="")}?mode=json'
        response = SESSION.get(url, timeout=25)
        response.raise_for_status()
        return [{'id': f'lever:{board}:{j["id"]}', 'company': company,
                 'title': j.get('text') or '',
                 'location': (j.get('categories') or {}).get('location') or '',
                 'url': j.get('hostedUrl') or '', 'published': '', 'description': html.unescape(re.sub(r'<[^>]*>', ' ', (j.get('descriptionPlain') or j.get('description') or '') + ' ' + ' '.join(x.get('text','') for x in j.get('lists',[]) if isinstance(x,dict))))}
                for j in response.json()]
    if kind == 'ashby':
        url = f'https://api.ashbyhq.com/posting-api/job-board/{quote(board, safe="")}'
        response = SESSION.get(url, timeout=25)
        response.raise_for_status()
        payload = response.json()
        return [{'id': f'ashby:{board}:{j.get("id") or j.get("jobUrl")}',
                 'company': company, 'title': j.get('title') or '',
                 'location': '; '.join(filter(None, [j.get('location')] + [
                     x.get('location') for x in j.get('secondaryLocations', []) if isinstance(x, dict)])),
                 'url': j.get('jobUrl') or j.get('applyUrl') or '',
                 'published': j.get('publishedAt') or '',
                 'description': html.unescape(re.sub(r'<[^>]*>', ' ', j.get('descriptionHtml') or j.get('descriptionPlain') or ''))}
                for j in payload.get('jobs', []) if j.get('isListed', True)]
    if kind == 'sitemap':
        from official_sites import fetch_sitemap_jobs
        return fetch_sitemap_jobs(source)
    if kind == 'official':
        from official_sites import fetch_official
        return fetch_official(source)
    raise ValueError(f'Unknown platform: {kind}')


def matches(job):
    title = job['title']
    location = job['location']
    description = job.get('description') or ''
    if not any(re.search(p, location, re.I) for p in CONFIG['location_patterns']):
        return False
    if any(re.search(p, title, re.I) for p in CONFIG['exclude_title_patterns']):
        return False
    if any(re.search(p, title, re.I) for p in CONFIG['non_finance_title_patterns']):
        return False
    if not any(re.search(p, title, re.I) for p in CONFIG['include_title_patterns']):
        return False
    entry_signal = any(re.search(p, title, re.I) for p in CONFIG['entry_title_patterns'])
    # Keep junior / internships / VIE; do not assume an unqualified "Trader" is junior.
    if CONFIG.get('require_entry_signal', True) and not entry_signal:
        return False
    # Reject explicit requirements above the user's maximum, including ranges.
    # Avoid treating years in company histories as job requirements.
    max_years = int(CONFIG.get('max_years_experience', 1))
    section = re.split(r'(?i)\\b(?:about us|about the company|benefits|equal opportunity)\\b', description)[0]
    requirements = re.split(r'(?i)\\b(?:requirements|qualifications|your profile|about you|what you bring|experience required)\\b', section)
    relevant = ' '.join(requirements[1:]) if len(requirements) > 1 else section
    patterns = [
        r'(?i)\\b(?:at least|minimum(?: of)?|more than|over|requires?|required|must have)\\s*(\\d{1,2})\\s*\\+?\\s*years?',
        r'(?i)\\b(\\d{1,2})\\s*\\+\\s*years?\\s+(?:of\\s+)?(?:relevant\\s+|professional\\s+|industry\\s+)?experience',
        r'(?i)\\b(\\d{1,2})\\s*(?:-|to|–)\\s*\\d{1,2}\\s+years?\\s+(?:of\\s+)?experience',
        r'(?i)\\b(\\d{1,2})\\s+years?\\s+(?:of\\s+)?(?:relevant\\s+|professional\\s+|industry\\s+)?experience\\s+(?:required|minimum|in)',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, relevant):
            if int(match.group(1)) > max_years:
                return False
    # A bare 'Analyst' title is not evidence of entry-level eligibility.
    if re.search(r'(?i)\\banalyst\\b', title) and not re.search(
        r'(?i)\\b(?:intern|internship|junior|graduate|trainee|summer|off[ -]?cycle|vie|v\\.i\\.e|entry[ -]?level|new grad)\\b', title
    ):
        if not re.search(r'(?i)\\b(?:0\\s*[-–to]+\\s*1|0\\s*(?:or|to)\\s*1|no (?:prior|previous) experience|entry[ -]?level|recent graduate)\\b', relevant):
            return False
    return True


def notify_one(job):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logging.warning('Missing Telegram secrets; not marking offers as sent')
        return False
    message = (f"🆕 {job['title']}\n🏦 {job['company']}\n"
               f"📍 {job['location']}\n🔗 {job['url']}")
    try:
        response = SESSION.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': message, 'disable_web_page_preview': True},
            timeout=25)
        response.raise_for_status()
        if not response.json().get('ok'):
            raise ValueError('Telegram API returned ok=false')
        return True
    except (requests.RequestException, ValueError) as exc:
        logging.error('Telegram notification failed for %s: %s', job['id'], exc)
        return False


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
                if job['url'] and matches(job):
                    found[job['id']] = job
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            logging.warning('Source unavailable (%s): %s', source['company'], exc)
    if not successful:
        raise RuntimeError('All job feeds failed; preserving existing state')
    new = [job for id_, job in found.items() if id_ not in seen]
    logging.info('%d matching jobs, %d new', len(found), len(new))
    if os.environ.get('DRY_RUN') == '1':
        for job in new[:30]:
            logging.info('DRY RUN MATCH: %s | %s | %s', job['company'], job['title'], job['url'])
        logging.info('DRY RUN: no Telegram messages, no state changes')
        return
    max_per_run = int(CONFIG.get('max_notifications_per_run', 20))
    sent = 0
    now = datetime.now(timezone.utc).isoformat()
    for job in new[:max_per_run]:
        if notify_one(job):
            seen[job['id']] = {'company': job['company'], 'title': job['title'], 'first_seen': now}
            sent += 1
            time.sleep(0.2)
    STATE_FILE.write_text(json.dumps(seen, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    logging.info('%d notifications sent; %d pending/unsent', sent, len(new)-sent)


if __name__ == '__main__':
    main()
