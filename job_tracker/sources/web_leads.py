"""Offers found by the daily web search session (job_tracker/web_leads.json).

Once a day a scheduled Claude session searches the web the way a person
would ('stage sales trading Hong Kong linkedin', 'graduate structuring
London 2027'...), keeps only offers actually shown in the search results
(title, employer, city, link, date and snippet as the results give them) and
appends them to web_leads.json on the `web-leads` branch. The workflow
copies that file in before each run; this adapter turns its recent entries
into jobs, and the usual filters, deduplication and 14-day rule apply.

The leads carry a search snippet, not the advert: they are title-only for
the junior check (a bare 'Analyst' needs an explicit junior signal). A lead
needs a posting date (14-day rule), a deadline still to come, or
`open_confirmed` (the employer's page says applications are open / rolling);
a lead with none of these is skipped, as is one whose deadline has passed.
"""
import json
import logging
import re
from datetime import timedelta
from pathlib import Path

from jobs import canonical_url, make_job

LOG = logging.getLogger(__name__)
LEADS_FILE = Path(__file__).resolve().parent.parent / 'web_leads.json'


def fetch(source, ctx):
    path = Path(source.get('file') or LEADS_FILE)
    if not path.exists():
        ctx.stat(source['company'], 'no_leads_file')
        return []
    data = json.loads(path.read_text(encoding='utf-8'))
    keep_from = (ctx.now - timedelta(days=int(source.get('days', 14)))).date().isoformat()
    jobs = {}
    for lead in data.get('leads', []):
        url, title = (lead.get('url') or '').strip(), (lead.get('title') or '').strip()
        if not url.startswith('http') or not title or (lead.get('found') or '') < keep_from:
            continue
        # Same ids / URL as the LinkedIn alerts and post search, so an offer
        # seen several ways is alerted once
        job_id, post_id = re.search(r'linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*?-)?(\d{8,})', url), \
            re.search(r'linkedin\.com/(?:posts/\S*?-(?:activity|ugcPost|share)-|feed/update/urn:li:(?:activity|share|ugcPost):)(\d{15,})', url)
        if job_id:
            key, url = f'linkedin-job:{job_id.group(1)}', f'https://www.linkedin.com/jobs/view/{job_id.group(1)}/'
        elif post_id:
            key = f'post:linkedin:{post_id.group(1)}'
        else:
            key = 'web:' + canonical_url(url)
        snippet = (lead.get('snippet') or '').strip()
        found = lead.get('found', '')
        published, deadline = (lead.get('published') or '').strip(), (lead.get('deadline') or '').strip()
        today = ctx.now.date().isoformat()
        if deadline and deadline < today:
            ctx.stat(source['company'], 'deadline_passed')
            continue
        if not published and not deadline and lead.get('open_confirmed') is not True:
            # No posting date and no deadline: often a programme opened months
            # ago; the 14-day rule cannot be checked, so it is not alerted
            ctx.stat(source['company'], 'undated_skipped')
            continue
        job = make_job(
            id=key, company=(lead.get('company') or '').strip() or 'Unknown employer',
            title=title, location=(lead.get('location') or '').strip(), url=url,
            published=published, description=snippet[:3000] + (f' Application deadline: {deadline}.' if deadline else ''),
            source=f'web search ({lead.get("site") or "web"})',
            status='open' if lead.get('open_confirmed') is True else 'unknown',
            status_evidence=f'found by the daily web search on {found}'
            + (f'; deadline {deadline}' if deadline else '')
            + ('; employer page says applications are open' if lead.get('open_confirmed') is True else ''),
            title_only=True)
        jobs.setdefault(job['id'], job)
    ctx.stat(source['company'], 'listed', len(jobs))
    LOG.info('%s: %d leads found since %s', source['company'], len(jobs), keep_from)
    return list(jobs.values())
