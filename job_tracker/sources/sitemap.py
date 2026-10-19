"""Official XML sitemaps + vacancy detail pages.

1. Read the sitemap (index) and collect vacancy URLs with their lastmod.
2. Screen URL slugs with the same title rules as the classifier (and the
   city, when the site puts it in the URL) before any page request.
3. Fetch detail pages incrementally: new/changed URLs first, then cached
   pages older than `refresh_hours` (to re-check status), up to
   `max_fetch_per_run` requests. Later runs continue where this one stopped,
   so no candidate is permanently skipped by a per-run cap.
4. Parse schema.org JobPosting JSON-LD (title, description, location,
   datePosted, validThrough, employmentType); fall back to page markup.
"""
import json
import logging
import re
from collections import deque
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from jobs import html_to_text, make_job
from sources.context import slug_words
from status import CLOSED, assess_page

LOG = logging.getLogger(__name__)
# Bump when parse_detail output changes so cached pages are re-parsed.
PAGE_KEY = 'page:v3:'
LOCATION_SELECTORS = ('[itemprop="jobLocation"]', '[data-testid*="location"]', '.job-location', '.jobLocation',
                      '[class*="job-location"]', '[class*="jobGeoLocation"]', '[itemprop="addressLocality"]')


def read_sitemaps(source, ctx):
    root = source['sitemap_url']
    host = urlparse(root).hostname
    pending, seen, urls = deque([root]), set(), {}
    max_sitemaps = int(source.get('max_sitemaps', 30))
    pattern = re.compile(source['job_url_pattern'], re.I)
    while pending and len(seen) < max_sitemaps:
        url = pending.popleft()
        if url in seen:
            continue
        seen.add(url)
        response = ctx.http.get(url)
        response.raise_for_status()
        xml = ET.fromstring(response.content)
        for node in xml:
            loc = lastmod = ''
            for child in node:
                tag = child.tag.rsplit('}', 1)[-1]
                if tag == 'loc':
                    loc = (child.text or '').strip()
                elif tag == 'lastmod':
                    lastmod = (child.text or '').strip()
            if not loc or urlparse(loc).hostname != host:
                continue
            if node.tag.endswith('sitemap') or loc.lower().split('?')[0].endswith('.xml'):
                pending.append(loc)
            elif pattern.search(urlparse(loc).path):
                urls[loc] = lastmod
    return urls


def slug_candidate(url, source, ctx):
    words = slug_words(unquote(urlparse(url).path))
    if not ctx.title_worth_fetching(words):
        return False
    if source.get('slug_has_city'):
        return bool(ctx.city_of(words))
    return True


def _jsonld_postings(soup):
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
            if 'JobPosting' in (types if isinstance(types, list) else [types]):
                yield obj


def _places(posting):
    places = posting.get('jobLocation') or []
    if isinstance(places, dict):
        places = [places]
    out = []
    for place in places:
        if not isinstance(place, dict):
            continue
        address = place.get('address') or {}
        if isinstance(address, dict):
            out.append(', '.join(str(address.get(k) or '') for k in ('addressLocality', 'addressRegion', 'addressCountry') if address.get(k)))
        if place.get('name'):
            out.append(str(place['name']))
    return '; '.join(filter(None, out))


def _labelled_date(text, label):
    m = re.search(r'(?:' + label + r')\s*:?\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', text, re.I)
    if m:
        return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'  # day/month/year
    m = re.search(r'(?:' + label + r')\s*:?\s*(\d{4}-\d{2}-\d{2}|\d{1,2} [A-Z][a-z]{2,8} \d{4})', text, re.I)
    return m.group(1) if m else ''


def parse_detail(html, url, source):
    """Parse one vacancy page into a dict, or {'reject': reason}."""
    soup = BeautifulSoup(html, 'html.parser')
    posting = next(_jsonld_postings(soup), None)
    h1 = soup.select_one('h1')
    h1_text = re.sub(r'\s+', ' ', h1.get_text(' ', strip=True)) if h1 else ''
    for tag in soup(['script', 'style', 'noscript', 'nav', 'header', 'footer']):
        tag.decompose()
    visible = re.sub(r'\s+', ' ', soup.get_text(' ', strip=True))
    if posting:
        title = html_to_text(posting.get('title'))
        # Some sites (Société Générale) append ' - <business line> - <city>' to
        # the structured title; the visible heading is the real job title.
        if h1_text and len(h1_text) < len(title) and title.lower().startswith(h1_text.lower()):
            title = h1_text
        description = html_to_text(posting.get('description'))
        location = _places(posting)
        valid_through = posting.get('validThrough') or ''
        published = posting.get('datePosted') or ''
        employment = posting.get('employmentType') or ''
        if isinstance(employment, list):
            employment = ', '.join(employment)
        identifier = posting.get('identifier')
        req_id = identifier.get('value', '') if isinstance(identifier, dict) else str(identifier or '')
    else:
        heading = soup.select_one('h1') or soup.select_one('[itemprop="title"]')
        title = heading.get_text(' ', strip=True) if heading else ''
        main = soup.select_one('[itemprop="description"], .jobdescription, main, article')
        description = main.get_text(' ', strip=True) if main else ''
        location = ''
        for selector in LOCATION_SELECTORS:
            el = soup.select_one(selector)
            if el:
                location = el.get_text(' ', strip=True)
                break
        valid_through = published = employment = req_id = ''
    if not title or len(title) > 200:
        return {'reject': 'no_title'}
    if not posting and not description:
        return {'reject': 'no_job_detail'}
    if source.get('url_location_segment') is not None:
        segments = urlparse(url).path.strip('/').split('/')
        idx = int(source['url_location_segment'])
        if len(segments) > idx:
            location = (location + '; ' + segments[idx].replace('-', ' ')).strip('; ')
    # SuccessFactors career pages (Standard Chartered) print the dates as text:
    # 'Posting Start Date: 27/08/2026 Posting End Date: 31/12/2026'.
    if not published:
        published = (_labelled_date(visible, r'Posting Start Date|Date Posted|Posted (?:on|date)')
                     or _labelled_date(visible, r'(?<![A-Za-z] )\bDate'))  # SuccessFactors RMK 'Date: 16 Oct 2026'
    if not valid_through:
        valid_through = _labelled_date(visible, r'Posting End Date|Closing Date|Application Deadline|Apply by')
    status, evidence = assess_page(visible, valid_through)
    return {'title': title, 'description': description, 'location': location, 'published': published,
            'valid_through': valid_through, 'employment_type': employment, 'req_id': req_id,
            'status': status, 'status_evidence': evidence}


def fetch(source, ctx):
    company = source['company']
    urls = read_sitemaps(source, ctx)
    ctx.stat(company, 'listed', len(urls))
    candidates = [u for u in urls if slug_candidate(u, source, ctx)]
    ctx.stat(company, 'candidates', len(candidates))
    refresh = float(source.get('refresh_hours', 72))
    budget = int(source.get('max_fetch_per_run', 40))
    fresh, stale = [], []
    for url in candidates:
        if ctx.cache.get(PAGE_KEY + url, max_age_hours=refresh, lastmod=urls[url]) is not None:
            continue
        (stale if ctx.cache.get(PAGE_KEY + url) is not None else fresh).append(url)
    # Never-seen URLs first (newest lastmod first), then status re-checks.
    fresh.sort(key=lambda u: urls[u], reverse=True)
    to_fetch = (fresh + stale)[:budget]
    ctx.stat(company, 'pending_after_run', max(0, len(fresh) + len(stale) - len(to_fetch)))
    for url in to_fetch:
        response = ctx.http.get(url)
        if response.status_code in (404, 410):
            value = {'reject': 'gone', 'status': CLOSED}
        else:
            response.raise_for_status()
            value = parse_detail(response.text, url, source)
        ctx.cache.put(PAGE_KEY + url, value, lastmod=urls[url])
        ctx.stat(company, 'detail_requests')
    jobs = []
    for url in candidates:
        value = ctx.cache.get(PAGE_KEY + url)
        if not value:
            continue
        if value.get('reject'):
            ctx.stat(company, 'rejected_' + value['reject'])
            continue
        location = value['location']
        if source.get('location_from_slug'):
            # The page gives no location: take the target city named in the URL.
            slug = slug_words(unquote(urlparse(url).path))
            location = location.replace(slug, '').strip('; ')
            city = ctx.city_of(slug)
            if city and not ctx.city_of(location):
                location = (location + '; ' + f'{city} (from job URL)').strip('; ')
        jobs.append(make_job(
            id=f'sitemap:{company}:{url}', company=company, title=value['title'], location=location,
            url=url, published=value['published'], description=value['description'],
            employment_type=value['employment_type'], requisition_id=value['req_id'],
            source='official sitemap + vacancy page', status=value['status'],
            status_evidence=value['status_evidence'] + ' (page checked)'))
    return jobs
