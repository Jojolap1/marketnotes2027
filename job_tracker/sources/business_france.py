"""Business France V.I.E offers (mon-vie-via.businessfrance.fr).

The site's search page calls the Civiweb offers API with the public key it
serves to every visitor in its runtime config; the key is read from that page
on each run (never stored), exactly as the browser does.

  POST https://civiweb-api-prd.azurewebsites.net/api/Offers/search  {limit, skip}

Offers name the real employer (bank, energy trader...), so they share the
company/title/city fingerprint with the employer's own site (no duplicates).
"""
import re

from jobs import html_to_text, make_job
from status import CLOSED, OPEN, from_end_date

SITE = 'https://mon-vie-via.businessfrance.fr'
API = 'https://civiweb-api-prd.azurewebsites.net/api/Offers/search'
PAGE = 100


def _public_key(ctx):
    page = ctx.http.get(f'{SITE}/en/offres/recherche')
    page.raise_for_status()
    for raw in re.findall(r'API_KEY:"([^"]+)"', page.text):
        key = raw.encode().decode('unicode_escape')
        if not key.startswith('AIza'):  # skip the Google Maps key
            return key
    raise ValueError('public API key not found in the search page (site changed?)')


def fetch(source, ctx):
    company_label = source['company']
    headers = {'X-API-KEY': _public_key(ctx)}
    countries = {c.upper() for c in source.get('countries', [])}
    offers, skip = [], 0
    while skip < int(source.get('max_results', 8000)):
        data = ctx.http.post_json(API, {'limit': PAGE, 'skip': skip}, headers=headers)
        batch = data.get('result') or []
        offers += batch
        skip += PAGE
        if len(batch) < PAGE:
            break
    ctx.stat(company_label, 'listed', len(offers))
    jobs = []
    for o in offers:
        country = (o.get('countryNameEn') or o.get('countryName') or '').strip()
        if countries and country.upper() not in countries:
            continue
        title = (o.get('missionTitle') or '').strip()
        if not re.search(r'\bv\.?\s?i\.?\s?e\b', title, re.I):
            title += ' (VIE)'
        location = ', '.join(filter(None, [re.sub(r'\s+', ' ', o.get('cityNameEn') or o.get('cityName') or '').strip().title(),
                                           country.title()]))
        if not ctx.screen(company_label, title, location):
            continue
        ctx.stat(company_label, 'candidates')
        employer = re.sub(r'\s+', ' ', o.get('organizationName') or '').strip().title()
        end = (o.get('endBroadcastDate') or '')[:10]
        by_date = from_end_date(end, ctx.now) if end else None
        status, evidence = by_date if by_date and by_date[0] == CLOSED else (
            OPEN, 'listed in the Business France V.I.E search' + (f'; {by_date[1]}' if by_date else ''))
        duration = o.get('missionDuration')
        jobs.append(make_job(
            id=f'businessfrance:{o.get("id")}', company=employer or 'Unknown employer', title=title, location=location,
            url=f'{SITE}/offres/{o.get("id")}', published=(o.get('startBroadcastDate') or o.get('creationDate') or '')[:10],
            description=' '.join([html_to_text(o.get('missionDescription')), html_to_text(o.get('missionProfile')),
                                  'Contract: V.I.E (volontariat international en entreprise)'
                                  + (f', {duration} months.' if duration else '.')]),
            employment_type='V.I.E' + (f' ({duration} months)' if duration else ''),
            source='Business France V.I.E (mon-vie-via.businessfrance.fr)', requisition_id=str(o.get('reference') or ''),
            status=status, status_evidence=evidence))
    return jobs
