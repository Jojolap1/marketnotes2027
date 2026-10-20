"""Avature career sites (TotalEnergies): the public job search pages.

  GET {base}/SearchJobs/?jobOffset=N   20 results per page, newest first: title, date, country
  GET {base}/JobDetail/{slug}/{id}     country, city, contract, experience, text

Only adverts in a target country with a promising title are opened. The listing
is newest first, so pages are read only back to `read_back_days` (older adverts
can no longer be alerted): the site answers 406 when crawled too deep or fast.
"""
import re
from datetime import timedelta

from jobs import html_to_text, make_job
from status import OPEN, parse_date

PAGE = 20
ARTICLE = re.compile(r'<div class="article article--result".*?(?=<div class="article article--result"|</section>)', re.S)
FIELD = re.compile(r'<li class="list-item list-item-(\w+)">(.*?)</li>', re.S)
LINK = re.compile(r'<a class="link" href="([^"]*JobDetail/[^"]+)"[^>]*>(.*?)</a>', re.S)
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


def fetch(source, ctx):
    company, base = source['company'], source['base_url'].rstrip('/')
    countries = set(source.get('countries', []))
    # The listing is newest first: read until a whole page is older than
    # `read_back_days` (beyond the alert age limit), so no recent advert is skipped.
    horizon = ctx.now - timedelta(days=int(source.get('read_back_days', 16)))
    adverts, reached_horizon = {}, False
    for page in range(int(source.get('max_pages', 40))):
        response = ctx.http.get(f'{base}/SearchJobs/?jobRecordsPerPage={PAGE}&jobOffset={page * PAGE}')
        response.raise_for_status()
        blocks = ARTICLE.findall(response.text)
        for block in blocks:
            link = LINK.search(block)
            if link:
                fields = {k: html_to_text(v) for k, v in FIELD.findall(block)}
                adverts[link.group(1)] = (html_to_text(link.group(2)), fields)
        dates = [parse_date(_dmy(f)) for f in re.findall(r'list-item-jobCreationDate">([^<]+)<', response.text)]
        if len(blocks) < PAGE or (dates and all(d and d < horizon for d in dates)):
            reached_horizon = True
            break
    if not reached_horizon:
        ctx.stat(company, 'pages_cap_reached')  # shown in the report: raise max_pages
    ctx.stat(company, 'listed', len(adverts))
    jobs = []
    for url, (title, fields) in adverts.items():
        country = fields.get('jobCountry', '')
        if (countries and country not in countries) or not ctx.title_worth_fetching(title):
            continue
        detail = _detail(source, ctx, url)
        location = ', '.join(filter(None, [detail['city'].title(), detail['country'] or country]))
        if not ctx.screen(company, title, location):
            continue
        ctx.stat(company, 'candidates')
        jobs.append(make_job(
            id=f'avature:{url.rsplit("/", 1)[-1]}:{company}', company=company, title=title, location=location, url=url,
            published=_dmy(fields.get('jobCreationDate', '')),
            description=detail['description'] + (f" Experience: {detail['experience']}." if detail['experience'] else ''),
            employment_type=detail['contract'], source='official careers site search (Avature)',
            requisition_id=url.rsplit('/', 1)[-1],
            status=OPEN, status_evidence='listed in the official careers site search (live offers only)'))
    return jobs
