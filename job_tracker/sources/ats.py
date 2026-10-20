"""Public job-board APIs: Greenhouse, Lever, Ashby, SmartRecruiters.

These APIs only list published postings, so a listed vacancy is evidence
that applications are open.
"""
from urllib.parse import quote

from jobs import html_to_text, make_job
from status import OPEN


def greenhouse(source, ctx):
    board = source['board']
    data = ctx.http.get_json(f'https://boards-api.greenhouse.io/v1/boards/{quote(board, safe="")}/jobs?content=true')
    jobs = []
    for j in data.get('jobs', []):
        jobs.append(make_job(
            id=f'greenhouse:{board}:{j["id"]}', company=source['company'], title=j.get('title') or '',
            location=(j.get('location') or {}).get('name') or '',
            url=j.get('absolute_url') or '', published=j.get('first_published') or j.get('updated_at') or '',
            description=html_to_text(j.get('content')), source='Greenhouse job board API',
            requisition_id=str(j.get('requisition_id') or ''),
            status=OPEN, status_evidence='listed in the official Greenhouse job board API (live postings only)'))
    return jobs


def lever(source, ctx):
    board = source['board']
    data = ctx.http.get_json(f'https://api.lever.co/v0/postings/{quote(board, safe="")}?mode=json')
    jobs = []
    for j in data:
        cats = j.get('categories') or {}
        lists = ' '.join(x.get('text', '') + ' ' + html_to_text(x.get('content')) for x in j.get('lists', []) if isinstance(x, dict))
        jobs.append(make_job(
            id=f'lever:{board}:{j["id"]}', company=source['company'], title=j.get('text') or '',
            location='; '.join(filter(None, [cats.get('location')] + list(cats.get('allLocations') or []))),
            url=j.get('hostedUrl') or '', apply_url=j.get('applyUrl') or '',
            published=j.get('createdAt') or '', employment_type=cats.get('commitment') or '',
            description=(j.get('descriptionPlain') or html_to_text(j.get('description'))) + ' ' + lists,
            source='Lever postings API', status=OPEN,
            status_evidence='listed in the official Lever postings API (live postings only)'))
    return jobs


def ashby(source, ctx):
    board = source['board']
    data = ctx.http.get_json(f'https://api.ashbyhq.com/posting-api/job-board/{quote(board, safe="")}')
    jobs = []
    for j in data.get('jobs', []):
        if not j.get('isListed', True):
            continue
        locations = [j.get('location')] + [x.get('location') for x in j.get('secondaryLocations', []) if isinstance(x, dict)]
        jobs.append(make_job(
            id=f'ashby:{board}:{j.get("id") or j.get("jobUrl")}', company=source['company'], title=j.get('title') or '',
            location='; '.join(filter(None, locations)), url=j.get('jobUrl') or j.get('applyUrl') or '',
            apply_url=j.get('applyUrl') or '', published=j.get('publishedAt') or '',
            employment_type=j.get('employmentType') or '',
            description=j.get('descriptionPlain') or html_to_text(j.get('descriptionHtml')),
            source='Ashby job board API', status=OPEN,
            status_evidence='listed in the official Ashby job board API (live postings only)'))
    return jobs


def smartrecruiters(source, ctx):
    company_id = source['board']
    jobs, offset = [], 0
    while True:
        data = ctx.http.get_json(f'https://api.smartrecruiters.com/v1/companies/{quote(company_id, safe="")}/postings?limit=100&offset={offset}')
        content = data.get('content', [])
        for j in content:
            loc = j.get('location') or {}
            jobs.append(make_job(
                id=f'smartrecruiters:{company_id}:{j["id"]}', company=source['company'], title=j.get('name') or '',
                location=', '.join(filter(None, [loc.get('city'), loc.get('region'), loc.get('country')])),
                url=f'https://jobs.smartrecruiters.com/{company_id}/{j["id"]}', published=j.get('releasedDate') or '',
                employment_type=(j.get('typeOfEmployment') or {}).get('label') or '',
                source='SmartRecruiters postings API', status=OPEN,
                status_evidence='listed in the official SmartRecruiters postings API (live postings only)'))
        offset += len(content)
        if not content or offset >= data.get('totalFound', 0) or offset >= 1000:
            return jobs


def bamboohr(source, ctx):
    """BambooHR careers page JSON: /careers/list, and /careers/{id}/detail for the advert text."""
    host = f'https://{source["board"]}.bamboohr.com'
    jobs = []
    for j in ctx.http.get_json(f'{host}/careers/list').get('result', []):
        loc = j.get('atsLocation') or j.get('location') or {}
        location = ', '.join(filter(None, [loc.get('city'), loc.get('state'), loc.get('country')]))
        title, description, published = j.get('jobOpeningName') or '', '', ''
        if ctx.screen(source['company'], title, location):
            opening = (ctx.http.get_json(f'{host}/careers/{j["id"]}/detail').get('result') or {}).get('jobOpening') or {}
            description, published = html_to_text(opening.get('description')), opening.get('datePosted') or ''
        jobs.append(make_job(
            id=f'bamboohr:{source["board"]}:{j["id"]}', company=source['company'], title=title, location=location,
            url=f'{host}/careers/{j["id"]}', published=published, employment_type=j.get('employmentStatusLabel') or '',
            description=description, source='BambooHR careers page', status=OPEN,
            status_evidence='listed on the official BambooHR careers page (open positions only)'))
    return jobs
