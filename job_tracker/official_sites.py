"""Read public job listings from official bank career pages (no login/bypass)."""
import json
import logging
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests
from xml.etree import ElementTree as ET
from collections import deque

LOG = logging.getLogger(__name__)
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; PersonalJobAlertTracker/3.0)'}
JOB_LINK = re.compile(r'(?i)(job|career|position|vacanc|opportunit|requisition)')
SKIP = re.compile(r'(?i)(sign.in|login|register|privacy|cookie|accessibility|talent.network|job.alert)')


def fetch_official(source):
    """Extract links and JSON-LD JobPosting objects from a bank's public search page.

    An empty result does not imply there are no vacancies: many sites render
    results using JavaScript. Such sites need a dedicated adapter.
    """
    url = source['url']
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    final_host = urlparse(response.url).hostname or ''
    allowed = source.get('allowed_hosts', [])
    if allowed and not any(final_host == h or final_host.endswith('.' + h) for h in allowed):
        raise ValueError('Unexpected redirect host: ' + final_host)
    soup = BeautifulSoup(response.text, 'html.parser')
    jobs = {}
    def add(title, location, link, description=''):
        title = re.sub(r'\\s+', ' ', str(title or '')).strip()
        link = urljoin(response.url, str(link or ''))
        if not title or not link.startswith('https://') or len(title) > 180:
            return
        if SKIP.search(link):
            return
        key = 'official:' + source['company'] + ':' + link
        jobs[key] = {'id': key, 'company': source['company'], 'title': title,
                     'location': location or '', 'url': link,
                     'description': description or '', 'published': ''}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        queue = payload if isinstance(payload, list) else [payload]
        while queue:
            obj = queue.pop()
            if not isinstance(obj, dict):
                continue
            queue.extend(x for x in obj.get('@graph', []) if isinstance(x, dict))
            types = obj.get('@type', [])
            if isinstance(types, str):
                types = [types]
            if 'JobPosting' not in types:
                continue
            loc = obj.get('jobLocation') or []
            if isinstance(loc, dict):
                loc = [loc]
            places = []
            for item in loc:
                if isinstance(item, dict):
                    address = item.get('address') or {}
                    if isinstance(address, dict):
                        places.append(', '.join(str(address.get(k) or '') for k in ('addressLocality','addressRegion','addressCountry')).strip(', '))
            add(obj.get('title'), '; '.join(places), obj.get('url') or response.url,
                BeautifulSoup(obj.get('description') or '', 'html.parser').get_text(' ', strip=True))
    # Navigation links on career landing pages are not individual vacancies.
    # Only structured JobPosting records count until a site-specific adapter is validated.
    # Site-specific search result cards: only links inside known vacancy
    # result containers, never arbitrary career/navigation links.
    if source.get('adapter') == 'citi':
        for item in soup.select('li.jobs-list-item, li.job-result, .job-listing, .job-result, [data-job-id]'):
            a = item.select_one('a[href*="/job/"], a[href*="/job-detail/"], a[href*="/job/"]')
            if not a:
                continue
            title = (item.select_one('h2, h3, .job-title') or a).get_text(' ', strip=True)
            location = (item.select_one('.job-location, .location, [class*="location"]') or item).get_text(' ', strip=True)
            add(title, location, a.get('href'))
    elif source.get('adapter') == 'sg':
        for item in soup.select('article, .job-card, .job-item, [data-job-id]'):
            a = item.select_one('a[href*="/job/"], a[href*="/offre/"], a[href*="/jobs/"]')
            if not a:
                continue
            title = (item.select_one('h2, h3, .job-title') or a).get_text(' ', strip=True)
            location = (item.select_one('.job-location, .location, [class*="location"]') or item).get_text(' ', strip=True)
            add(title, location, a.get('href'))
    LOG.info('%s official page: %d extracted job records; adapter=%s', source['company'], len(jobs), source.get('adapter', 'jsonld'))
    return list(jobs.values())

def fetch_sitemap_jobs(source):
    """Read official XML sitemaps and verify individual vacancy pages."""
    root_url = source['sitemap_url']
    host = urlparse(root_url).hostname
    pending = deque([root_url])
    visited = set()
    job_urls = []
    max_sitemaps = int(source.get('max_sitemaps', 25))
    max_jobs = int(source.get('max_jobs', 60))
    while pending and len(visited) < max_sitemaps and len(job_urls) < max_jobs * 3:
        url = pending.popleft()
        if url in visited or urlparse(url).hostname != host:
            continue
        visited.add(url)
        try:
            res = requests.get(url, headers=HEADERS, timeout=25)
            res.raise_for_status()
            xml = ET.fromstring(res.content)
        except (requests.RequestException, ET.ParseError) as exc:
            LOG.warning('%s sitemap unavailable %s: %s', source['company'], url, exc)
            continue
        for elem in xml.iter():
            if not elem.tag.endswith('loc') or not elem.text:
                continue
            link = elem.text.strip()
            if urlparse(link).hostname != host:
                continue
            if link.lower().split('?')[0].endswith('.xml'):
                pending.append(link)
            elif re.search(source['job_url_pattern'], urlparse(link).path, re.I):
                job_urls.append(link)
    jobs = {}
    for link in job_urls[:max_jobs]:
        try:
            res = requests.get(link, headers=HEADERS, timeout=20)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, 'html.parser')
            heading = soup.select_one('h1')
            if not heading:
                continue
            title = heading.get_text(' ', strip=True)
            text = soup.get_text(' ', strip=True)
            if not title or len(title) > 160:
                continue
            # Job pages must contain an explicit location signal.
            locations = re.findall(r'(?i)\\b(?:Hong Kong|Singapore|Paris|London)\\b', text[:2500])
            if not locations:
                continue
            key = 'sitemap:' + source['company'] + ':' + link
            jobs[key] = {'id': key, 'company': source['company'],
                         'title': title, 'location': ', '.join(sorted(set(locations))),
                         'url': link, 'description': text[:10000], 'published': ''}
        except requests.RequestException as exc:
            LOG.debug('Vacancy unavailable %s: %s', link, exc)
    LOG.info('%s sitemap: %d job pages checked, %d candidates',
             source['company'], min(len(job_urls), max_jobs), len(jobs))
    return list(jobs.values())
