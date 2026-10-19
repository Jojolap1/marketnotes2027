"""Smaller official feeds: Goldman Sachs role search, SuccessFactors XML,
RSS vacancy feeds (Talentsoft / SuccessFactors) and Oleeo (tal.net) boards."""
import re
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from jobs import canonical_url, html_to_text, make_job
from status import CLOSED, OPEN, assess_page, from_end_date

GS_QUERY = """query GetCampusRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items { roleId corporateTitle jobTitle jobFunction locations { primary state country city } status division
            jobType { code description } externalSource { sourceId } educationLevel startDate }
  }
}"""


def goldman(source, ctx):
    """higher.gs.com public GraphQL role search (the site's own API)."""
    company = source['company']
    api = source.get('api', 'https://api-higher.gs.com/gateway/api/v1/graphql')
    headers = {'Origin': 'https://higher.gs.com', 'Referer': 'https://higher.gs.com/'}
    page_size = int(source.get('page_size', 50))
    roles = {}
    for experience in source.get('experiences', [['CAMPUS'], ['EARLY_CAREER']]):
        page, total = 0, None
        while total is None or page * page_size < min(total, int(source.get('max_results', 1500))):
            payload = {'query': GS_QUERY, 'variables': {'searchQueryInput': {
                'page': {'pageSize': page_size, 'pageNumber': page}, 'filters': [],
                'experiences': experience, 'searchTerm': ''}}}
            data = ctx.http.post_json(api, payload, headers=headers)
            if data.get('errors'):
                raise ValueError(f'GraphQL error: {str(data["errors"])[:200]}')
            result = (data.get('data') or {}).get('roleSearch') or {}
            total = result.get('totalCount') or 0
            items = result.get('items') or []
            for item in items:
                item['_experience'] = '/'.join(experience)
                roles[item['roleId']] = item
            page += 1
            if not items:
                break
    ctx.stat(company, 'listed', len(roles))
    jobs = []
    for role_id, r in roles.items():
        locations = '; '.join(', '.join(filter(None, [l.get('city'), l.get('state'), l.get('country')]))
                              for l in r.get('locations') or [])
        title = re.sub(r'\s*[|I]\s+', ' | ', r.get('jobTitle') or '').strip()
        if not ctx.title_worth_fetching(title) or not ctx.city_of(locations):
            continue
        ctx.stat(company, 'candidates')
        source_id = (r.get('externalSource') or {}).get('sourceId') or role_id
        corporate = r.get('corporateTitle') or ''
        experience = r['_experience']
        description = (f'Corporate title: {corporate}. Division: {r.get("division") or ""}. '
                       f'Job function: {r.get("jobFunction") or ""}. Education level: {r.get("educationLevel") or ""}.')
        if experience == 'CAMPUS':
            description += ' Campus programme for students and recent graduates.'
        jobs.append(make_job(
            id=f'goldman:{source_id}', company=company, title=title, location=locations,
            url=f'https://higher.gs.com/roles/{source_id}', published=r.get('startDate') or '',
            description=description, employment_type=f'{(r.get("jobType") or {}).get("description") or ""} ({experience.title()})'.strip(),
            source='Goldman Sachs careers role search API', requisition_id=str(source_id),
            status=OPEN if (r.get('status') or 'POSTED') == 'POSTED' else 'unknown',
            status_evidence=f'listed in the official careers role search with status {r.get("status")}'))
    return jobs


def successfactors_xml(source, ctx):
    """SAP SuccessFactors public job listing XML (career_ns=job_listing_summary)."""
    company, sf_company, host = source['company'], source['sf_company'], source['host']
    response = ctx.http.get(f'https://{host}/career?company={sf_company}&career_ns=job_listing_summary&resultType=XML')
    response.raise_for_status()
    # Some tenants (RWE) emit control characters or bare '&' that are not valid XML.
    content = re.sub(rb'[\x00-\x08\x0b\x0c\x0e-\x1f]', b' ', response.content)
    content = re.sub(rb'&(?!#?\w+;)', b'&amp;', content)
    try:
        nodes = list(ET.fromstring(content).iter('Job'))
    except ET.ParseError:
        # Still malformed: parse each <Job> on its own and drop only the broken ones.
        nodes = []
        for block in re.findall(rb'<Job>.*?</Job>', content, re.S):
            try:
                nodes.append(ET.fromstring(block))
            except ET.ParseError:
                ctx.stat(company, 'unparsable_jobs')
    jobs = []
    listed = 0
    for node in nodes:
        listed += 1
        fields = {}
        for child in node:
            label = child.findtext('label')
            if label:
                fields[label.strip()] = (child.findtext('value') or '').strip()
        title = (node.findtext('JobTitle') or '').strip()
        req_id = (node.findtext('ReqId') or '').strip()
        location = ', '.join(filter(None, [fields.get('City'), fields.get('Country')]))
        if not location:  # tenants label their location fields differently
            location = ', '.join(v for k, v in fields.items() if re.search(r'(?i)city|country|location|state', k))
        location = location or (node.findtext('Location') or '').strip()
        description_text = html_to_text(node.findtext('Job-Description'))
        if not location and source.get('location_from_description'):
            # SMBC leaves <Location> empty: use the target cities named in the advert.
            found = [city for city in ('London', 'Paris', 'Hong Kong', 'Singapore')
                     if re.search(r'\b' + city + r'\b', description_text[:3000], re.I)]
            location = ', '.join(found) + (' (from job description)' if found else '')
        posted = (node.findtext('Posted-Date') or '').strip()
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})$', posted)
        if m:
            posted = f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
        if not ctx.title_worth_fetching(title) or not ctx.city_of(location):
            continue
        experience = fields.get('Experience level', '')
        jobs.append(make_job(
            id=f'successfactors:{sf_company}:{req_id}', company=company, title=title, location=location,
            url=f'https://{host}/career?company={sf_company}&career_job_req_id={req_id}&career_ns=job_listing',
            description=description_text + (f' Experience level: {experience}.' if experience else ''), published=posted,
            employment_type=experience, source='SuccessFactors job listing XML', requisition_id=req_id,
            status=OPEN, status_evidence='listed in the official SuccessFactors job feed (live postings only)'))
    ctx.stat(company, 'listed', listed)
    return jobs


def rss(source, ctx):
    """Official RSS vacancy feeds. Location comes from item categories or a
    '(City, CC)' suffix in the title."""
    company = source['company']
    items = {}
    for feed in source['feeds']:
        response = ctx.http.get(feed)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for item in root.iter('item'):
            link = (item.findtext('link') or '').strip()
            if link:
                items[link] = item
    ctx.stat(company, 'listed', len(items))
    jobs = []
    for link, item in items.items():
        title = re.sub(r'^\d{4}-\d+\s*-\s*', '', (item.findtext('title') or '').strip())
        categories = [c.text or '' for c in item.findall('category')]
        suffix = re.search(r'\(([^()]+)\)\s*$', title)
        location = '; '.join(filter(None, categories + [suffix.group(1) if suffix else '']))
        if not ctx.city_of(location) and source.get('location_in_title'):
            location = '; '.join(filter(None, [location, title]))  # e.g. '... - Hong Kong'
        if not ctx.title_worth_fetching(title) or not ctx.city_of(location):
            continue
        description = html_to_text(item.findtext('description'))
        contract = ', '.join(c for c in categories if re.search(r'intern|trainee|vie|contract|graduate|apprenti', c, re.I))
        jobs.append(make_job(
            id=f'rss:{company}:{canonical_url(link)}', company=company, title=title,
            location=location, url=link, published=item.findtext('pubDate') or '', description=description,
            employment_type=contract, source='official RSS vacancy feed', status=OPEN,
            status_evidence='listed in the official vacancy RSS feed (published offers only)'))
    return jobs


def _oleeo_detail(source, ctx, url):
    key = 'oleeo:' + url
    cached = ctx.cache.get(key, max_age_hours=int(source.get('detail_cache_hours', 48)))
    if cached is not None:
        ctx.stat(source['company'], 'detail_cache_hits')
        return cached
    response = ctx.http.get(url)
    if response.status_code in (404, 410):
        value = {'gone': True}
    else:
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        meta = soup.find('meta', attrs={'name': 'description'})
        meta_text = meta.get('content', '') if meta else ''
        location = re.search(r'(?:Location|City):\s*([^.]+)', meta_text)
        deadline = re.search(r'(?:Application )?Deadline:\s*(\d{1,2})/(\d{1,2})/(\d{4})', meta_text)
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'form']):
            tag.decompose()
        main = soup.select_one('main, #content, .content, article') or soup
        value = {'location': location.group(1).strip() if location else '',
                 'deadline': f'{deadline.group(3)}-{int(deadline.group(2)):02d}-{int(deadline.group(1)):02d}' if deadline else '',
                 'description': re.sub(r'\s+', ' ', main.get_text(' ', strip=True))[:12000]}
    ctx.cache.put(key, value)
    ctx.stat(source['company'], 'detail_requests')
    return value


def oleeo(source, ctx):
    """Oleeo (tal.net) campus vacancy boards (Nomura, Bank of America)."""
    company = source['company']
    opportunities = {}
    for board in source['boards']:
        response = ctx.http.get(board)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        for a in soup.select('a[href*="/opp/"]'):
            href = urljoin(board, a['href'])
            title = re.sub(r'\s+', ' ', a.get_text(' ', strip=True))
            if title and len(title) < 200:
                opportunities.setdefault(href, title)
    ctx.stat(company, 'listed', len(opportunities))
    jobs = []
    for url, title in opportunities.items():
        if not ctx.title_worth_fetching(title):
            continue
        ctx.stat(company, 'candidates')
        detail = _oleeo_detail(source, ctx, url)
        if detail.get('gone'):
            continue
        location = detail.get('location') or title
        if not ctx.city_of(location) and not ctx.city_of(title):
            continue
        status, evidence = assess_page(detail.get('description', ''), detail.get('deadline'))
        if status != CLOSED:
            status = OPEN
            evidence = 'listed on the official campus vacancy board' + (
                f'; {from_end_date(detail["deadline"], ctx.now)[1]}' if detail.get('deadline') else '')
        opp_id = re.search(r'/opp/(\d+)', url).group(1)
        jobs.append(make_job(
            id=f'oleeo:{company}:{opp_id}', company=company, title=title, location=location if ctx.city_of(location) else title,
            url=url, description=detail.get('description', ''), requisition_id=opp_id,
            source='official Oleeo campus vacancy board', status=status, status_evidence=evidence))
    return jobs
