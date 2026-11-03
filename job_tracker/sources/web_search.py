"""Public LinkedIn posts and job pages found through a web-search API.

LinkedIn is never fetched (it refuses scripts and its terms forbid it): the
tracker asks a search engine's official API (Brave Search) for public pages
under linkedin.com/posts and linkedin.com/jobs/view, and reads only what the
search engine returns: page title, snippet, date and URL.

- Posts go through the same rules as the LinkedIn posts read from emails
  (says it is hiring + a targeted role named next to those words), with the
  same job id, so a post seen both ways is alerted once.
- Public job pages ('BNP Paribas hiring Structured Products Sales Intern in
  Paris, Île-de-France, France | LinkedIn') become a job with that title,
  employer and city.

The API key lives in GitHub secrets (BRAVE_SEARCH_API_KEY), sent as a header,
never in the URL or the logs. Searches run only at the configured UTC hours
so the monthly query budget is respected; other runs skip the source.
"""
import logging
import os
import re

from jobs import html_to_text, make_job
from sources.email_alerts import post_jobs

LOG = logging.getLogger(__name__)
API = 'https://api.search.brave.com/res/v1/web/search'
POST_ID = re.compile(r'linkedin\.com/(?:posts/\S*?-activity-|feed/update/urn:li:(?:activity|share|ugcPost):)(\d{15,})', re.I)
JOB_ID = re.compile(r'linkedin\.com/jobs/view/(?:[^/?#]*?-)?(\d{8,})', re.I)
JOB_TITLE = re.compile(r'^(.{2,80}?) (?:is )?hiring (.{4,160}?) in (.{2,120}?)\s*(?:\||$)', re.I)


def fetch(source, ctx):
    name = source['company']
    key = os.environ.get(source.get('key_env', 'BRAVE_SEARCH_API_KEY'), '').strip()
    if not key:
        ctx.stat(name, 'api_key_not_configured')
        ctx.stat(name, 'not_due')
        return []
    hours = source.get('hours_utc')
    if hours and ctx.now.hour not in hours:
        ctx.stat(name, 'not_due')
        return []
    ctx.http.set_host_interval('api.search.brave.com', float(source.get('interval_seconds', 1.2)))
    headers = {'Accept': 'application/json', 'X-Subscription-Token': key}
    jobs = {}
    for query in source['queries']:
        data = ctx.http.get_json(API, headers=headers, params={
            'q': query, 'count': int(source.get('count', 20)),
            'freshness': source.get('freshness', 'pw'), 'extra_snippets': 'true'})
        ctx.stat(name, 'queries')
        results = (data.get('web') or {}).get('results') or []
        ctx.stat(name, 'results', len(results))
        for result in results:
            for job in result_jobs(result, source, ctx):
                jobs.setdefault(job['id'], job)
    ctx.stat(name, 'listed', len(jobs))
    LOG.info('%s: %d queries, %d jobs', name, len(source['queries']), len(jobs))
    return list(jobs.values())


def result_jobs(result, source, ctx):
    url = result.get('url') or ''
    title = html_to_text(result.get('title') or '')
    snippet = ' ¶ '.join(html_to_text(s) for s in [result.get('description') or ''] + (result.get('extra_snippets') or []) if s)
    published = (result.get('page_age') or '')[:10]
    evidence = f'public LinkedIn page found by web search{f" (dated {published})" if published else ""}'
    post = POST_ID.search(url)
    if post:
        text = f'{title} ¶ {snippet}'
        return post_jobs([(f'linkedin:{post.group(1)}', url.split('?')[0], text)], source, ctx, published, evidence)
    job = JOB_ID.search(url)
    head = JOB_TITLE.search(title)
    if job and head:
        company, role, place = (g.strip() for g in head.groups())
        return [make_job(
            id=f'linkedin-job:{job.group(1)}', company=company, title=role, location=ctx.city_of(place) or place,
            url=f'https://www.linkedin.com/jobs/view/{job.group(1)}/', published=published,
            description=snippet.replace(' ¶ ', ' ')[:3000], source=f'LinkedIn job page ({source["company"]})',
            status='unknown', status_evidence=evidence)]
    return []
