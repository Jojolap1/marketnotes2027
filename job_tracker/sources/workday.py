"""Workday candidate-experience (CXS) public JSON API.

This is the API the bank's own public career site calls from the browser:
  POST https://{host}/wday/cxs/{tenant}/{site}/jobs      (search, paginated)
  GET  https://{host}/wday/cxs/{tenant}/{site}{path}     (vacancy detail)

Search is restricted server-side to the target countries/cities using the
site's own location facets, then paginated fully so no vacancy is missed.
Only titles that pass the cheap screen get a detail request, and details
are cached between runs.
"""
import logging
import re

from filters import normalize_text
from jobs import html_to_text, make_job
from status import CLOSED, OPEN, UNKNOWN, from_end_date, parse_date

LOG = logging.getLogger(__name__)
PAGE = 20
DEFAULT_COUNTRIES = ['United Kingdom', 'Hong Kong', 'Singapore', 'France', 'United Arab Emirates']


def _walk_facets(facets):
    for facet in facets or []:
        values = facet.get('values') or []
        if values and isinstance(values[0], dict) and 'facetParameter' in values[0]:
            yield from _walk_facets(values)
            continue
        yield facet.get('facetParameter'), values


def _latest_posting(start, posted_on):
    """Most recent of the advert's start date and the listing's 'Posted N Days Ago':
    employers repost adverts (OCBC internships), which renews 'postedOn' but keeps
    the original start date, and a reposted advert is a current one."""
    dates = [d for d in (parse_date(start), parse_date(posted_on)) if d]
    return max(dates).date().isoformat() if dates else (start or posted_on or '')


def location_facets(facets, ctx, countries):
    """Pick one facet parameter and the value ids covering target locations.

    Prefers a country-level facet (exact country names); otherwise uses the
    facet whose values name the most target cities.
    """
    wanted = {normalize_text(c) for c in countries}
    best_country, best_city = None, None
    for param, values in _walk_facets(facets):
        if not param or param in ('jobFamilyGroup', 'timeType', 'workerSubType'):
            continue
        country_ids = [v['id'] for v in values if normalize_text(v.get('descriptor')) in wanted]
        city_ids = [v['id'] for v in values if ctx.city_of(v.get('descriptor'))]
        if country_ids and (best_country is None or len(country_ids) > len(best_country[1])):
            best_country = (param, country_ids)
        if city_ids and (best_city is None or len(city_ids) > len(best_city[1])):
            best_city = (param, city_ids)
    choice = best_country or best_city
    return {choice[0]: choice[1]} if choice else {}


def _listing(source, ctx, base, applied, search_text):
    max_results = int(source.get('max_results', 1500))
    posts, offset, total = {}, 0, None
    while offset < max_results:
        data = ctx.http.post_json(base + '/jobs', {'appliedFacets': applied, 'limit': PAGE,
                                                   'offset': offset, 'searchText': search_text})
        page = data.get('jobPostings') or []
        if total is None:
            total = data.get('total') or 0
        for post in page:
            if post.get('externalPath'):
                posts[post['externalPath']] = post
        offset += PAGE
        if not page or offset >= total:
            break
    return posts, total or 0


def _detail(source, ctx, base, path):
    key = f'workday:{base}{path}'
    cached = ctx.cache.get(key, max_age_hours=int(source.get('detail_cache_hours', 96)))
    if cached is not None:
        ctx.stat(source['company'], 'detail_cache_hits')
        return cached
    response = ctx.http.get(base + path, headers={'Accept': 'application/json'})
    if response.status_code in (404, 410):
        value = {'gone': True}
    else:
        response.raise_for_status()
        info = response.json().get('jobPostingInfo') or {}
        extra = [x if isinstance(x, str) else (x or {}).get('descriptor', '') for x in info.get('additionalLocations') or []]
        value = {
            'title': info.get('title') or '', 'location': '; '.join(filter(None, [info.get('location')] + extra)),
            'description': html_to_text(info.get('jobDescription')), 'start': info.get('startDate') or '',
            'end': info.get('endDate') or '', 'time_type': info.get('timeType') or '',
            'can_apply': info.get('canApply'), 'req_id': info.get('jobReqId') or '',
            'url': info.get('externalUrl') or '', 'country': (info.get('country') or {}).get('descriptor', ''),
        }
    ctx.cache.put(key, value)
    ctx.stat(source['company'], 'detail_requests')
    return value


def _status(detail, ctx):
    by_date = from_end_date(detail.get('end'), ctx.now)
    if by_date and by_date[0] == CLOSED:
        return by_date
    if detail.get('can_apply') is False:
        return UNKNOWN, 'listed, but Workday reports applications cannot be submitted online'
    evidence = 'listed in the official Workday careers feed with online application enabled'
    if by_date:
        evidence += f'; {by_date[1]}'
    return OPEN, evidence


def fetch(source, ctx):
    host, tenant, site = source['host'], source['tenant'], source['site']
    base = f'https://{host}/wday/cxs/{tenant}/{site}'
    company = source['company']
    head = ctx.http.post_json(base + '/jobs', {'appliedFacets': {}, 'limit': 1, 'offset': 0, 'searchText': ''})
    applied = location_facets(head.get('facets'), ctx, source.get('countries', DEFAULT_COUNTRIES))
    if not applied:
        LOG.warning('%s: no location facet found; scanning unfiltered listing', company)
    posts = {}
    for text in source.get('search_texts', ['']):
        found, total = _listing(source, ctx, base, applied, text)
        posts.update(found)
        ctx.stat(company, 'listed_total', total)
    ctx.stat(company, 'listed', len(posts))
    site_url = f'https://{host}/en-US/{site}'
    jobs = []
    for path, post in posts.items():
        title = post.get('title') or ''
        loc_text = post.get('locationsText') or ''
        if not ctx.screen(company, title, loc_text):
            continue
        ctx.stat(company, 'candidates')
        detail = _detail(source, ctx, base, path)
        if detail.get('gone'):
            continue
        location = detail.get('location') or loc_text
        status, evidence = _status(detail, ctx)
        req_id = detail.get('req_id') or ''.join((post.get('bulletFields') or [])[:1])
        jobs.append(make_job(
            id=f'workday:{tenant}:{req_id or path}', company=company, title=detail.get('title') or title,
            location=location, url=detail.get('url') or site_url + path, published=_latest_posting(detail.get('start'), post.get('postedOn')),
            description=detail.get('description') or '', employment_type=detail.get('time_type') or '',
            source=f'Workday careers API ({host}/{site})', requisition_id=req_id,
            status=status, status_evidence=evidence))
    return jobs
