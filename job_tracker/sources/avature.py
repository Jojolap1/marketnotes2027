"""Avature career sites: the public job search pages.

  GET {base}/{listing}/?jobOffset=N    result pages (TotalEnergies: 20 per page,
                                       newest first, with date and country;
                                       Macquarie, Two Sigma: no date)
  GET {base}/JobDetail/...             advert page: text, and labelled
                                       country/city/contract/experience when present

Dated listings (newest first) are read back to `read_back_days` only (older
adverts can no longer be alerted). Undated listings are read completely, at
most every `listing_refresh_hours`, so no advert is skipped while the site
(which answers 406 when crawled too fast or too deep) is not hammered.
Only adverts in a target city (or country) with a promising title are opened.
"""
import re
from datetime import timedelta

from jobs import html_to_text, make_job
from status import OPEN, parse_date

PAGE = 20
ARTICLE = re.compile(r'<(?:div|article) class="article article--result.*?(?=<(?:div|article) class="article article--result|</section>)', re.S)
FIELD = re.compile(r'<li class="list-item list-item-(\w+)">(.*?)</li>', re.S)
LINK = re.compile(r'<a\b[^>]*\bhref="([^"]*JobDetail[/?][^"]+)"[^>]*>(.*?)</a>', re.S)
ROW = re.compile(r'<tr\b.*?</tr>', re.S)  # table layout (National Bank of Canada)
LABELS = ('Country', 'City', 'Area', 'Workplace location', 'Employer company', 'Domain', 'Type of contract',
          'Experience', 'Context & Environment', 'Activities', 'Candidate Profile', 'Job Description')


def _label(text, label):
    others = '|'.join(re.escape(x) for x in LABELS if x != label)
    m = re.search(r'\b' + re.escape(label) + r'\s+(.+?)\s+(?:' + others + r')\b', text)
    return m.group(1).strip() if m else ''


def _dmy(value):
    m = re.match(r'(\d{1,2})-(\d{1,2})-(\d{4})$', value.strip())
    return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}' if m else value


def _detail(source, ctx, url):
    key = 'avature:' + url
    cached = ctx.cache.get(key, max_age_hours=int(source.get('detail_cache_hours', 96)))
    if cached is not None:
        ctx.stat(source['company'], 'detail_cache_hits')
        return cached
    response = ctx.http.get(url)
    response.raise_for_status()
    text = html_to_text(re.sub(r'(?s)<(script|style).*?</\1>', ' ', response.text))
    start = text.find('Job Description')
    value = {'city': _label(text, 'City'), 'country': _label(text, 'Country'),
             'contract': _label(text, 'Type of contract'), 'experience': _label(text, 'Experience'),
             'description': text[start:start + 6000] if start >= 0 else text[:6000]}
    ctx.cache.put(key, value)
    ctx.stat(source['company'], 'detail_requests')
    return value


def _read_listing(source, ctx, base):
    """{url: (title, fields, text)} for the listing pages to read this run."""
    path = source.get('listing_path', 'SearchJobs')
    horizon = ctx.now - timedelta(days=int(source.get('read_back_days', 16)))
    adverts, complete, offset = {}, False, 0
    for _ in range(int(source.get('max_pages', 80))):
        response = ctx.http.get(f'{base}/{path}/?jobRecordsPerPage={PAGE}&jobOffset={offset}')
        response.raise_for_status()
        blocks = ARTICLE.findall(response.text) or [r for r in ROW.findall(response.text) if 'JobDetail' in r]
        new = 0
        for block in blocks:
            link = LINK.search(block)
            if link and link.group(1) not in adverts:
                title = html_to_text(link.group(2))
                fields = {k: html_to_text(v) for k, v in FIELD.findall(block)}
                text = re.sub(r'^\s*ID\s+\d+\s*', '', html_to_text(block).replace(title, ' ', 1).strip())
                # Macquarie: 'London Office 15 Oct 2026 Commodities, Sales...': keep the office only
                text = re.split(r'\s+\d{1,2} [A-Z][a-z]{2} \d{4}\b', text, maxsplit=1)[0]
                adverts[link.group(1)] = (title, fields, text)
                new += 1
        dates = [parse_date(_dmy(f)) for f in re.findall(r'list-item-jobCreationDate">([^<]+)<', response.text)]
        if not blocks or not new or (dates and all(d and d < horizon for d in dates)):
            complete = True
            break
        offset += len(blocks)
    if not complete:
        ctx.stat(source['company'], 'pages_cap_reached')  # shown in the report: raise max_pages
    return adverts


def fetch(source, ctx):
    company, base = source['company'], source['base_url'].rstrip('/')
    countries = set(source.get('countries', []))
    refresh = source.get('listing_refresh_hours')
    key = 'avature-listing:' + base
    cached = ctx.cache.get(key, max_age_hours=float(refresh)) if refresh else None
    if cached is not None:
        adverts = {u: tuple(v) for u, v in cached.items()}
        ctx.stat(company, 'listing_from_cache', 1)
    else:
        adverts = _read_listing(source, ctx, base)
        if refresh:
            ctx.cache.put(key, adverts)
    ctx.stat(company, 'listed', len(adverts))
    jobs = []
    for url, (title, fields, text) in adverts.items():
        country = fields.get('jobCountry', '')
        if countries and country and country not in countries:
            continue
        if not ctx.screen(company, title, country or text) and not (country and ctx.title_worth_fetching(title)):
            continue
        detail = _detail(source, ctx, url)
        location = ', '.join(filter(None, [detail['city'].title(), detail['country'] or country])) or text[:120]
        if not ctx.screen(company, title, location):
            continue
        ctx.stat(company, 'candidates')
        ref = (re.search(r'(\d+)/?$', url) or re.search(r'jobId=(\d+)', url))
        ref = ref.group(1) if ref else url
        jobs.append(make_job(
            id=f'avature:{ref}:{company}', company=company, title=title, location=location, url=url,
            published=_dmy(fields.get('jobCreationDate', '')),
            description=detail['description'] + (f" Experience: {detail['experience']}." if detail['experience'] else ''),
            employment_type=detail['contract'], source='official careers site search (Avature)', requisition_id=ref,
            status=OPEN, status_evidence='listed in the official careers site search (live offers only)'))
    return jobs
