"""Bank-specific public job-search endpoints that are not a standard ATS API.

* Bank of America: the JSON service behind careers.bankofamerica.com search.
* UBS: the Kenexa BrassRing search used by jobs.ubs.com (anonymous search;
  the page's own anti-forgery token is sent back as the site itself does).
* Beisen "zhiye" portals (CICC, China Galaxy): the portal's job-ad list API.
"""
import re

from jobs import html_to_text, make_job
from status import CLOSED, OPEN, from_end_date

BOFA = 'https://careers.bankofamerica.com'


def _mdy(value):
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})$', str(value or '').strip())
    return f'{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}' if m else ''


def bank_of_america(source, ctx):
    company, window = source['company'], 1000
    jobs_raw, start, total = [], 0, None
    # The service returns items [start, rows): 'rows' is an end index.
    while total is None or start < min(total, 6000):
        data = ctx.http.get_json(f'{BOFA}/services/jobssearchservlet?start={start}&rows={start + window}&search=getAllJobs')
        batch = data.get('jobsList') or []
        total = int(data.get('totalMatches') or 0)
        jobs_raw += batch
        start += window
        if not batch:
            break
    ctx.stat(company, 'listed', len(jobs_raw))
    jobs = []
    for j in jobs_raw:
        title = j.get('postingTitle') or ''
        location = '; '.join(filter(None, {j.get('location'), j.get('primaryMultiLocation'), j.get('additionalLocations')} - {'None'}))
        if not ctx.screen(company, title, location):
            continue
        ctx.stat(company, 'candidates')
        min_years = str(j.get('minYearsOfExperience') or '0')
        description = ' '.join(filter(None, [
            html_to_text(j.get('jobDescriptionExternal')) if j.get('jobDescriptionExternal') not in (None, 'None') else '',
            f'Requires at least {min_years} years of experience.' if min_years.isdigit() and int(min_years) > 0 else '',
            f"Area: {j.get('area') or ''}. Line of business: {j.get('lob') or ''}."]))
        apply_by = _mdy(j.get('applyByDate'))
        by_date = from_end_date(apply_by, ctx.now) if apply_by else None
        status, evidence = by_date if by_date and by_date[0] == CLOSED else (
            OPEN, 'listed in the official Bank of America careers search' + (f'; {by_date[1]}' if by_date else ''))
        jobs.append(make_job(
            id=f'bofa:{j.get("jobRequisitionId")}', company=company, title=title, location=location,
            url=BOFA + (j.get('jcrURL') or ''), published=_mdy(j.get('postedDate')), description=description,
            employment_type=j.get('timeType') if j.get('timeType') not in (None, 'None') else '',
            source='Bank of America careers search service', requisition_id=str(j.get('jobRequisitionId') or ''),
            status=status, status_evidence=evidence))
    return jobs


def brassring(source, ctx):
    """Kenexa BrassRing anonymous job search (UBS)."""
    company, host, partner = source['company'], source['host'], source['partner_id']
    country_city = source.get('country_city', {})
    raw = {}
    for site in source['site_ids']:
        page = ctx.http.get(f'https://{host}/TGnewUI/Search/Home/Home?partnerid={partner}&siteid={site}')
        page.raise_for_status()
        token = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page.text)
        headers = {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}
        if token:
            headers['RFT'] = token.group(1)
        body = {'partnerId': int(partner), 'siteId': int(site), 'keyword': '', 'location': '', 'Latitude': 0, 'Longitude': 0,
                'pageNumber': 1, 'FacetFilterFields': {'Facet': []}, 'PowerSearchOptions': {'PowerSearchOption': []},
                'SortType': 'LastUpdated', 'encryptedSessionValue': '',
                'KeywordCustomSolrFields': 'AutoReq,JobTitle,Description', 'LocationCustomSolrFields': 'FORMTEXT2,FORMTEXT1'}
        endpoint = 'PowerSearchJobs'
        for page_no in range(1, int(source.get('max_pages', 40)) + 1):
            body['pageNumber'] = page_no
            data = ctx.http.post_json(f'https://{host}/TgNewUI/Search/Ajax/{endpoint}', body, headers=headers)
            batch = (data.get('Jobs') or {}).get('Job') or []
            for job in batch:
                fields = {q['QuestionName']: q.get('Value') or '' for q in job.get('Questions', [])}
                fields['_link'] = job.get('Link') or ''
                raw[fields.get('reqid') or fields['_link']] = fields
            endpoint = 'ProcessSortAndShowMoreJobs'
            if len(batch) < 50:
                break
    ctx.stat(company, 'listed', len(raw))
    jobs = []
    for req, f in raw.items():
        title = html_to_text(f.get('jobtitle'))
        location = f.get('formtext23') or ''
        if not ctx.city_of(location):
            # Some UBS adverts only give the country; name the country's main
            # office explicitly so the alert shows the assumption.
            city = country_city.get(location.split(' - ')[0].strip())
            location = f'{location} ({city} office assumed: city not given)' if city else location
        if not ctx.screen(company, title, location):
            continue
        ctx.stat(company, 'candidates')
        jobs.append(make_job(
            id=f'brassring:{partner}:{req}', company=company, title=title, location=location, url=f['_link'],
            published=f.get('lastupdated') or '', description=html_to_text(f.get('jobdescription')),
            employment_type=f.get('formtext22') or '', source='UBS careers search (BrassRing)', requisition_id=req,
            status=OPEN, status_evidence='listed in the official UBS job search (live postings only)'))
    return jobs


def zhiye(source, ctx):
    """Beisen 'zhiye' recruitment portals (CICC, China Galaxy): job-ad list API.

    Ads are mostly in Chinese and often carry no structured location, so the
    location is taken from the cities the ad itself names (title/duties)."""
    company, host = source['company'], source['host']
    ads, page = [], 0
    while page < int(source.get('max_pages', 30)):
        data = ctx.http.post_json(f'https://{host}/api/Jobad/GetJobAdPageList',
                                  {'PageIndex': page, 'PageSize': 50, 'KeyWords': '', 'SpecialType': 0})
        batch = data.get('Data') or []
        ads += batch
        page += 1
        if not batch or len(ads) >= int(data.get('Count') or 0):
            break
    ctx.stat(company, 'listed', len(ads))
    jobs = []
    for ad in ads:
        title = (ad.get('JobAdName') or '').strip()
        text = ' '.join(str(ad.get(k) or '') for k in ('JobAdName', 'Duty', 'Require'))
        locations = [str(x) for x in (ad.get('LocNames') or []) if x]
        location = '; '.join(locations) or '; '.join(
            c for c in ('香港', 'Hong Kong', '新加坡', 'Singapore', '伦敦', '倫敦', 'London', '巴黎', 'Paris', '迪拜', 'Dubai') if c in text)
        if not ctx.screen(company, title, location):
            continue
        ctx.stat(company, 'candidates')
        end = ad.get('EndTime') if not str(ad.get('EndTime', '')).startswith('0001') else ''
        by_date = from_end_date(end, ctx.now) if end else None
        status, evidence = by_date if by_date and by_date[0] == CLOSED else (OPEN, 'listed on the official recruitment portal')
        published = ad.get('PostDate') if not str(ad.get('PostDate', '')).startswith('0001') else ''
        jobs.append(make_job(
            id=f'zhiye:{host}:{ad.get("JobAdId") or ad.get("Id")}', company=company, title=title, location=location,
            url=f'https://{host}/zpdetail/{ad.get("JobAdId")}', published=published or '',
            description=html_to_text(str(ad.get('Duty') or '') + ' ' + str(ad.get('Require') or '')),
            source='official recruitment portal (Beisen)', requisition_id=str(ad.get('JobAdId') or ''),
            status=status, status_evidence=evidence))
    return jobs


def bpce(source, ctx):
    """BPCE group recruitment sites (Natixis): the site's own job-search API.

    POST {base}/app/wp-json/bpce/v1/search/jobs  {lang, external, from, size}
    Each item carries its city/country ('localisations'), date and contract."""
    company, base = source['company'], source['base_url'].rstrip('/')
    items, start, total = [], 0, None
    while total is None or start < min(total, int(source.get('max_results', 2000))):
        data = ctx.http.post_json(f'{base}/app/wp-json/bpce/v1/search/jobs',
                                  {'lang': source.get('lang', 'en'), 'external': bool(source.get('external', False)),
                                   'from': start, 'size': 50}).get('data') or {}
        batch = data.get('items') or []
        total = int(data.get('total') or 0)
        items += batch
        start += 50
        if not batch:
            break
    ctx.stat(company, 'listed', len(items))
    jobs = []
    for it in items:
        title = html_to_text(it.get('title'))
        places = it.get('localisations') or []
        location = '; '.join(', '.join(filter(None, [p.get('city'), p.get('country')])) for p in places) or it.get('localisation') or ''
        if not ctx.screen(company, title, location):
            continue
        ctx.stat(company, 'candidates')
        link = (it.get('link') or {}).get('url') or ''
        apply = (it.get('postulate_link') or {}).get('url') or ''
        jobs.append(make_job(
            id=f'bpce:{it.get("job_number") or it.get("post_id")}', company=company, title=title, location=location,
            url=link if link.startswith('http') else base + link, apply_url=apply, published=it.get('date') or '',
            description=' '.join([html_to_text(it.get('description')), 'Entity: ' + ', '.join(it.get('brand') or []),
                                  'Experience: ' + ', '.join(it.get('experience') or [])]),
            employment_type=', '.join(it.get('contract') or []), source='official recruitment site search API',
            requisition_id=str(it.get('job_number') or ''),
            status=OPEN, status_evidence='listed in the official recruitment site search (live offers only)'))
    return jobs
