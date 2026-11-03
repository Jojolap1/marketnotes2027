"""Public LinkedIn posts and job pages found through a web-search API.

LinkedIn is never fetched (it refuses scripts and its terms forbid it): the
tracker asks SerpApi (Google results, official API, free plan of about 250
searches a month) for public pages under linkedin.com/posts and
linkedin.com/jobs/view, and reads only what the search returns: page title,
snippet, date and URL.

- Posts go through the same rules as the LinkedIn posts read from emails
  (says it is hiring + a targeted role named next to those words), with the
  same job id, so a post seen both ways is alerted once.
- Public job pages ('BNP Paribas hiring Structured Products Sales Intern in
  Paris, Île-de-France, France | LinkedIn') become a job with that title,
  employer and city.

Budget: queries are split by city and role and rotated: each runs every
`every_days` (Hong Kong and Singapore daily), at most `max_per_day` searches a
day, and searches exactly the days since it last ran (no day searched twice,
no gap). Results are kept in the detail cache until the next day; the
remaining quota is read first (free call) and the searches are skipped when
it is too low. The log gives each query's yield so weak ones can be reworded.
The API key lives in GitHub secrets (SERPAPI_API_KEY). SerpApi wants it in the
query string, so request errors are re-raised without the URL: the key never
reaches the logs, which are public.
"""
import logging
import os
import re
from datetime import date, timedelta

import requests

from jobs import html_to_text, make_job
from sources.email_alerts import post_jobs
from status import parse_date

LOG = logging.getLogger(__name__)
API = 'https://serpapi.com/search.json'
ACCOUNT = 'https://serpapi.com/account.json'
POST_ID = re.compile(r'linkedin\.com/(?:posts/\S*?-activity-|feed/update/urn:li:(?:activity|share|ugcPost):)(\d{15,})', re.I)
JOB_ID = re.compile(r'linkedin\.com/jobs/view/(?:[^/?#]*?-)?(\d{8,})', re.I)
JOB_TITLE = re.compile(r'^(.{2,80}?) (?:is )?hiring (.{4,160}?) in (.{2,120}?)\s*(?:\||$)', re.I)
# Other Google form: 'Structured Products Sales Intern - BNP Paribas - LinkedIn'
JOB_TITLE_DASH = re.compile(r'^(.{4,160}?) [-–|] (.{2,80}?) [-–|] LinkedIn\b', re.I)
AGO = re.compile(r'(\d+)\s+(hour|day|week)s?\s+ago', re.I)


class SearchError(RuntimeError):
    """Request failure described without the URL (it carries the API key)."""


def _call(ctx, url, params):
    try:
        response = ctx.http.get(url, params=params)
    except requests.RequestException as exc:
        raise SearchError(f'{type(exc).__name__} calling {url}') from None
    if response.status_code != 200:
        raise SearchError(f'HTTP {response.status_code} from {url}')
    data = response.json()
    if data.get('error'):
        raise SearchError(f'{url}: {str(data["error"])[:120]}')
    return data


def fetch(source, ctx):
    name = source['company']
    key = os.environ.get(source.get('key_env', 'SERPAPI_API_KEY'), '').strip()
    if not key:
        ctx.stat(name, 'api_key_not_configured')
        ctx.stat(name, 'not_due')
        return []
    forced = os.environ.get('WEB_SEARCH_FORCE') == '1'
    cache_key = 'web-search:' + name
    cached = None if forced else ctx.cache.get(cache_key, max_age_hours=float(source.get('refresh_hours', 22)))
    hours = source.get('hours_utc')
    if cached is not None or (hours and ctx.now.hour not in hours and not forced):
        # Between two searches: the last results stay listed, no query spent
        ctx.stat(name, 'results_from_cache' if cached is not None else 'not_due')
        return [dict(job) for job in (cached or [])]
    due = due_queries(source, ctx, forced)
    if not due:
        ctx.stat(name, 'not_due')
        return []
    left = _call(ctx, ACCOUNT, {'api_key': key}).get('total_searches_left')
    LOG.info('%s: %s searches left this month, %d due today', name, left, len(due))
    if isinstance(left, int) and left < len(due):
        ctx.stat(name, 'quota_too_low')
        ctx.stat(name, 'not_due')
        return []
    jobs = {}
    today = ctx.now.date()
    for query, since in due:
        # Exactly the days since this query last ran: nothing searched twice, no gap
        tbs = f'cdr:1,cd_min:{since:%m/%d/%Y},cd_max:{today:%m/%d/%Y}'
        data = _call(ctx, API, {'engine': 'google', 'q': query['q'], 'api_key': key,
                                'tbs': tbs, 'num': int(source.get('count', 10))})
        ctx.stat(name, 'queries')
        ctx.cache.put('web-search-query:' + query['q'], today.isoformat())
        results = data.get('organic_results') or []
        ctx.stat(name, 'results', len(results))
        linkedin = kept = 0
        for result in results:
            linkedin += bool(POST_ID.search(result.get('link') or '') or JOB_ID.search(result.get('link') or ''))
            for job in result_jobs(result, source, ctx):
                kept += 1
                jobs.setdefault(job['id'], job)
        # Per-query yield, to rework the queries that never bring anything
        LOG.info('  %-28s since %s: %d results, %d LinkedIn posts/job pages, %d kept',
                 query.get('label', query['q'][:28]), since, len(results), linkedin, kept)
    ctx.stat(name, 'listed', len(jobs))
    LOG.info('%s: %d queries, %d jobs', name, len(due), len(jobs))
    ctx.cache.put(cache_key, list(jobs.values()))
    return list(jobs.values())


def due_queries(source, ctx, forced=False):
    """Today's queries: those not run for `every_days` days (Hong Kong and
    Singapore ones run daily), most overdue first, at most `max_per_day`.
    Each comes with the date it last ran (its search window starts there,
    at most `max_window_days` back)."""
    today = ctx.now.date()
    longest = int(source.get('max_window_days', 7))
    due = []
    for n, query in enumerate(source['queries']):
        query = query if isinstance(query, dict) else {'q': query}
        every = int(query.get('every_days', 1))
        last = ctx.cache.get('web-search-query:' + query['q'])
        last = date.fromisoformat(last) if last else None
        overdue = (today - last).days - every if last else 0
        if forced or overdue >= 0:
            since = max(last or today - timedelta(days=every), today - timedelta(days=longest))
            due.append((-overdue, every, n, query, since))
    due.sort(key=lambda d: d[:3])
    return [(query, since) for *_, query, since in due[:int(source.get('max_per_day', 7))]]


def _published(value, now):
    ago = AGO.search(value or '')
    if ago:
        n, unit = int(ago.group(1)), ago.group(2).lower()
        delta = timedelta(hours=n) if unit == 'hour' else timedelta(days=n * (7 if unit == 'week' else 1))
        return (now - delta).date().isoformat()
    d = parse_date(value)
    return d.date().isoformat() if d else ''


def result_jobs(result, source, ctx):
    url = result.get('link') or ''
    title = html_to_text(result.get('title') or '')
    snippet = html_to_text(result.get('snippet') or '')
    published = _published(result.get('date'), ctx.now)
    evidence = f'public LinkedIn page found by web search{f" (dated {published})" if published else ""}'
    post = POST_ID.search(url)
    if post:
        # Google titles read 'Jane Smith on LinkedIn: ...' or 'Jane Smith's Post - LinkedIn';
        # otherwise the author is the /posts/<name>_... part of the URL
        who = (re.match(r"^(.{2,80}?)(?:['’]s Post)? (?:on|sur) LinkedIn\b", title)
               or re.match(r"^(.{2,80}?)['’]s Post\b", title))
        slug = re.search(r'/posts/([^_/?]+)_', url)
        author = who.group(1).strip() if who else slug.group(1).replace('-', ' ').title() if slug else ''
        text = (f'{author} posted ¶ ' if author else '') + f'{title} ¶ {snippet}'
        return post_jobs([(f'linkedin:{post.group(1)}', url.split('?')[0], text)], source, ctx, published, evidence)
    job = JOB_ID.search(url)
    head, dash = JOB_TITLE.search(title), JOB_TITLE_DASH.search(title)
    if job and (head or dash):
        if head:
            company, role, place = (g.strip() for g in head.groups())
        else:
            (role, company), place = (g.strip() for g in dash.groups()), snippet
        return [make_job(
            id=f'linkedin-job:{job.group(1)}', company=company, title=role, location=ctx.city_of(place) or place,
            url=f'https://www.linkedin.com/jobs/view/{job.group(1)}/', published=published,
            description=snippet[:3000], source=f'LinkedIn job page ({source["company"]})',
            status='unknown', status_evidence=evidence)]
    return []
