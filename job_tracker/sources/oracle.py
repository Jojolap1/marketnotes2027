"""Oracle Recruiting Cloud candidate-experience public REST API (JPMorgan).

  GET /hcmRestApi/resources/latest/recruitingCEJobRequisitions   (search)
  GET /hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails (detail)

These are the anonymous endpoints the official career site uses. Searches
are restricted to the target cities through the site's own location facet.
"""
import logging

from jobs import html_to_text, make_job
from status import CLOSED, OPEN, from_end_date

LOG = logging.getLogger(__name__)
API = 'https://{host}/hcmRestApi/resources/latest/'


def _get(ctx, host, resource, finder, expand=''):
    url = API.format(host=host) + resource + '?onlyData=true' + (f'&expand={expand}' if expand else '') + '&finder=' + finder
    return ctx.http.get_json(url, headers={'Accept': 'application/json'})


def location_ids(source, ctx):
    host, site = source['host'], source['site']
    data = _get(ctx, host, 'recruitingCEJobRequisitions', f'findReqs;siteNumber={site},facetsList=LOCATIONS,limit=1')
    facets = (data.get('items') or [{}])[0].get('locationsFacet') or []
    chosen = {}
    for facet in facets:
        city = ctx.city_of(facet.get('Name'))
        # Keep the broadest facet per city (e.g. 'LONDON, United Kingdom'
        # rather than the narrower 'LONDON, LONDON, United Kingdom').
        if city and facet.get('TotalCount', 0) > chosen.get(city, (None, -1))[1]:
            chosen[city] = (facet['Id'], facet.get('TotalCount', 0))
    return {city: fid for city, (fid, _) in chosen.items()}


def _detail(source, ctx, req_id):
    key = f'oracle:{source["host"]}:{source["site"]}:{req_id}'
    cached = ctx.cache.get(key, max_age_hours=int(source.get('detail_cache_hours', 96)))
    if cached is not None:
        ctx.stat(source['company'], 'detail_cache_hits')
        return cached
    data = _get(ctx, source['host'], 'recruitingCEJobRequisitionDetails',
                f'ById;Id=%22{req_id}%22,siteNumber={source["site"]}', expand='all')
    item = (data.get('items') or [{}])[0]
    value = {
        'description': ' '.join(html_to_text(item.get(k)) for k in (
            'ExternalDescriptionStr', 'ExternalResponsibilitiesStr', 'ExternalQualificationsStr')),
        'end': item.get('ExternalPostedEndDate') or '', 'schedule': item.get('JobSchedule') or '',
        'contract': item.get('ContractType') or '', 'job_level': item.get('JobLevel') or '',
    }
    ctx.cache.put(key, value)
    ctx.stat(source['company'], 'detail_requests')
    return value


def fetch(source, ctx):
    host, site, company = source['host'], source['site'], source['company']
    page = int(source.get('page_size', 25))
    max_results = int(source.get('max_results', 1500))
    ids = location_ids(source, ctx)
    if not ids:
        raise ValueError('no target-city location facets found')
    reqs = {}
    for city, fid in ids.items():
        offset = 0
        while offset < max_results:
            data = _get(ctx, host, 'recruitingCEJobRequisitions',
                        f'findReqs;siteNumber={site},selectedLocationsFacet={fid},limit={page},offset={offset},sortBy=POSTING_DATES_DESC',
                        expand='requisitionList.secondaryLocations')
            item = (data.get('items') or [{}])[0]
            batch = item.get('requisitionList') or []
            for req in batch:
                reqs[req['Id']] = req
            offset += len(batch)
            if not batch or offset >= (item.get('TotalJobsCount') or 0):
                break
        ctx.stat(company, f'listed_{city}', offset)
    ctx.stat(company, 'listed', len(reqs))
    jobs = []
    for req_id, req in reqs.items():
        title = req.get('Title') or ''
        locations = '; '.join(filter(None, [req.get('PrimaryLocation')] + [
            loc.get('Name', '') for loc in req.get('secondaryLocations') or [] if isinstance(loc, dict)]))
        if not ctx.screen(company, title, locations):
            continue
        ctx.stat(company, 'candidates')
        detail = _detail(source, ctx, req_id)
        end = req.get('PostingEndDate') or detail.get('end')
        by_date = from_end_date(end, ctx.now)
        if by_date and by_date[0] == CLOSED:
            status, evidence = by_date
        else:
            status = OPEN
            evidence = 'listed in the official Oracle Recruiting Cloud feed (live requisitions only)'
            if by_date:
                evidence += f'; {by_date[1]}'
        jobs.append(make_job(
            id=f'oracle:{host.split(".")[0]}:{req_id}', company=company, title=title, location=locations,
            url=f'https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req_id}',
            published=req.get('PostedDate') or '', description=req.get('ShortDescriptionStr', '') + ' ' + detail['description'],
            employment_type=' '.join(filter(None, [detail.get('schedule'), detail.get('contract')])),
            source=f'Oracle Recruiting Cloud API ({site})', requisition_id=str(req_id),
            status=status, status_evidence=evidence))
    return jobs
