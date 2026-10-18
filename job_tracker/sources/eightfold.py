"""Eightfold careers portal public API (HSBC).

  GET https://{host}/api/apply/v2/jobs?domain=..&location=..&start=..&num=..
  GET https://{host}/api/apply/v2/jobs/{id}?domain=..       (detail)

The portal returns 10 positions per page; every page for each target city
is read. Only live postings are listed.
"""
from urllib.parse import quote

from jobs import html_to_text, make_job
from status import OPEN

PAGE = 10


def _detail(source, ctx, position_id):
    key = f'eightfold:{source["host"]}:{position_id}'
    cached = ctx.cache.get(key, max_age_hours=int(source.get('detail_cache_hours', 96)))
    if cached is not None:
        ctx.stat(source['company'], 'detail_cache_hits')
        return cached
    data = ctx.http.get_json(f'https://{source["host"]}/api/apply/v2/jobs/{position_id}?domain={quote(source["domain"])}')
    value = {'description': html_to_text(data.get('job_description')), 'apply': data.get('apply_redirect_url') or ''}
    ctx.cache.put(key, value)
    ctx.stat(source['company'], 'detail_requests')
    return value


def fetch(source, ctx):
    host, domain, company = source['host'], source['domain'], source['company']
    max_pages = int(source.get('max_pages_per_location', 60))
    positions = {}
    for location in source.get('locations', ['Hong Kong', 'Singapore', 'London', 'Paris']):
        start = 0
        for _ in range(max_pages):
            data = ctx.http.get_json(f'https://{host}/api/apply/v2/jobs?domain={quote(domain)}&start={start}'
                                     f'&num={PAGE}&location={quote(location)}&sort_by=timestamp')
            batch = data.get('positions') or []
            for p in batch:
                positions[p['id']] = p
            start += len(batch)
            if not batch or start >= (data.get('count') or 0):
                break
    ctx.stat(company, 'listed', len(positions))
    jobs = []
    for pid, p in positions.items():
        title = p.get('name') or ''
        location = '; '.join(p.get('locations') or [p.get('location') or ''])
        if not ctx.title_worth_fetching(title) or not ctx.city_of(location):
            continue
        ctx.stat(company, 'candidates')
        detail = _detail(source, ctx, pid)
        jobs.append(make_job(
            id=f'eightfold:{host}:{p.get("display_job_id") or pid}', company=company, title=title, location=location,
            url=p.get('canonicalPositionUrl') or f'https://{host}/careers/job/{pid}', apply_url=detail.get('apply', ''),
            published=p.get('t_create') or '', description=detail['description'],
            source='Eightfold careers portal API', requisition_id=str(p.get('display_job_id') or ''),
            status=OPEN, status_evidence='listed in the official careers portal feed (live postings only)'))
    return jobs
