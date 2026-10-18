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
    # Prioritize job-title slugs before making expensive page requests.
    # Do not mistake sitemap entries for confirmed, open vacancies.
    role = re.compile(r'(sales|trading|trader|structur|market[-_]?risk|equity|credit|fixed[-_]?income|fx[-_]|rates|derivative|investment|portfolio|asset[-_]?manage|quantitative|market[-_]?analyst)', re.I)
    junior = re.compile(r'(intern|trainee|graduate|junior|summer|off[-_]?cycle|analyst|vie|v-i-e|assistant)', re.I)
    location = re.compile(r'(hong[-_]?kong|singapore|paris|london|kowloon)', re.I)
    senior = re.compile(r'(?:^|[-_/])(?:vp|avp|svp|vice[-_]president|director|senior|head[-_]of|principal|managing[-_]director)(?:[-_/]|$)', re.I)
    candidates = []
    for link in dict.fromkeys(job_urls):
        slug = urlparse(link).path.lower()
        if role.search(slug) and junior.search(slug) and not senior.search(slug):
            score = 2 + (2 if location.search(slug) else 0)
            candidates.append((score, link))
    candidates.sort(key=lambda item: -item[0])
    selected = [link for _, link in candidates[:max_jobs]]
    LOG.info('%s sitemap: %d URLs discovered, %d junior finance slug candidates, %d selected',
             source['company'], len(job_urls), len(candidates), len(selected))
    jobs = {}
    errors = 0
    samples = []
    rejected = {'no_title': 0, 'closed': 0, 'no_job_detail': 0, 'no_location': 0}
    for link in selected:
        try:
            res = requests.get(link, headers=HEADERS, timeout=20)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, 'html.parser')
            heading = soup.select_one('h1')
            if not heading or len(heading.get_text(' ', strip=True)) > 160:
                rejected['no_title'] += 1
                continue
            title = heading.get_text(' ', strip=True)
            body = soup.get_text(' ', strip=True)
            if re.search(r'(?i)(?:no longer accepting applications|this job is no longer available|position has been filled|this position is closed)', body[:5000]):
                rejected['closed'] += 1
                continue
            # A title and relevant slug alone are insufficient to validate a posting.
            if not (re.search(r'(?i)(?:apply now|apply for this|job description|job reference|requisition id|job id|apply to this job|apply for job|postuler|candidater)', body) or soup.select_one('a[href*="apply"], button[class*="apply"], [data-automation-id*="apply"]')):
                rejected['no_job_detail'] += 1
                if len(samples) < 3:
                    samples.append({'reason':'no_job_detail','url':link,'h1':title[:100], 'page_title':(soup.title.get_text(' ',strip=True) if soup.title else '')[:120], 'text_excerpt':body[:160]})
                continue
            # Do not infer job location from the global navigation/footer.
            location_text = ''
            for selector in ('[itemprop="jobLocation"]', '[data-testid*="location"]',
                             '.job-location', '.jobLocation', '[class*="job-location"]'):
                el = soup.select_one(selector)
                if el:
                    location_text = el.get_text(' ', strip=True)
                    break
            for script in soup.select('script[type="application/ld+json"]'):
                try:
                    payload = json.loads(script.string or script.get_text())
                except (ValueError, TypeError):
                    continue
                for obj in (payload if isinstance(payload, list) else [payload]):
                    if not isinstance(obj, dict) or 'JobPosting' not in str(obj.get('@type', '')):
                        continue
                    place = obj.get('jobLocation') or []
                    if isinstance(place, dict):
                        place = [place]
                    for entry in place:
                        if isinstance(entry, dict):
                            addr = entry.get('address') or {}
                            if isinstance(addr, dict):
                                location_text += ' ' + ' '.join(str(addr.get(k) or '') for k in ('addressLocality','addressRegion','addressCountry'))
            # Vacancy title and Citi URL location segments are authoritative fallbacks;
            # never infer location from global page navigation.
            if not location_text:
                location_text = (soup.title.get_text(' ', strip=True) if soup.title else '')
            if source['company'].lower() == 'citi':
                segments = urlparse(link).path.strip('/').split('/')
                if len(segments) > 2 and segments[0] == 'job':
                    location_text += ' ' + segments[1].replace('-', ' ')
            locs = sorted(set(re.findall(r'(?i)\b(?:Hong Kong|Singapore|Paris|London|Kowloon)\b', location_text)))
            if not locs:
                rejected['no_location'] += 1
                if len(samples) < 6:
                    samples.append({'reason':'no_location','url':link,'h1':title[:100], 'page_title':(soup.title.get_text(' ',strip=True) if soup.title else '')[:120], 'text_excerpt':body[:160]})
                continue
            key = 'sitemap:' + source['company'] + ':' + link
            jobs[key] = {'id': key, 'company': source['company'], 'title': title,
                         'location': ', '.join(locs), 'url': link,
                         'description': body[:10000], 'published': ''}
        except requests.RequestException as exc:
            errors += 1
            if errors <= 3:
                LOG.warning('%s vacancy fetch failed %s: %s', source['company'], link, exc)
    LOG.info('%s sitemap result: %d verified candidates; %d HTTP errors; rejected=%s',
             source['company'], len(jobs), errors, rejected)
    for sample in samples:
        LOG.info('%s rejection sample: %s', source['company'], sample)
    return list(jobs.values())
