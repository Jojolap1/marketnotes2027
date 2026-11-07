"""Normalized job records, URL canonicalization and duplicate fingerprints."""
import html
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from filters import normalize_text

# Query parameters that never identify a vacancy.
TRACKING_PARAMS = re.compile(r'^(?:utm_.*|source|src|ref|refid|gh_src|lever-source.*|codes?|jobboard|mode|locale|lang|iis|iisn|_ga|fbclid|gclid|feedid)$', re.I)
TITLE_NOISE = re.compile(r'\((?:m|f|w|d|h|x)(?:\s*/\s*(?:m|f|w|d|h|x))+\)|\b(?:h|f)\s*/\s*(?:h|f)\b|\bm\s*/\s*f(?:\s*/\s*d)?\b', re.I)

FIELDS = ('id', 'company', 'title', 'location', 'url', 'apply_url', 'published', 'description',
          'employment_type', 'source', 'status', 'status_evidence', 'requisition_id')


def html_to_text(markup):
    if not markup:
        return ''
    text = BeautifulSoup(html.unescape(str(markup)), 'html.parser').get_text(' ', strip=True)
    return re.sub(r'\s+', ' ', text).strip()


def make_job(**kwargs):
    job = {key: '' for key in FIELDS}
    job.update({k: v for k, v in kwargs.items() if v is not None})
    job['title'] = re.sub(r'\s+', ' ', str(job['title'])).strip()
    job['location'] = re.sub(r'\s+', ' ', str(job['location'])).strip()
    if not job['status']:
        job['status'] = 'unknown'
    return job


def canonical_url(url):
    """Stable form of a vacancy URL: https, lowercase host, no tracking params,
    no fragment, no trailing slash, and no language segment for known sites."""
    if not url:
        return ''
    parts = urlparse(url.strip())
    host = (parts.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    path = re.sub(r'/+$', '', parts.path) or '/'
    # Workday: /en-US/site/job/... and /site/job/... are the same vacancy.
    path = re.sub(r'^/[a-z]{2}-[A-Z]{2}(?=/)', '', path)
    # Société Générale: ...-26000M1T-en and ...-26000M1T-fr are the same vacancy.
    path = re.sub(r'(-[0-9A-Z]{8})-(?:en|fr)$', r'\1', path)
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)))
    return urlunparse(('https', host, path.lower(), '', query, ''))


def normalized_title(title):
    title = TITLE_NOISE.sub(' ', title or '')
    title = normalize_text(title)
    title = re.sub(r'[^a-z0-9]+', ' ', title)
    return re.sub(r'\s+', ' ', title).strip()


_COMPANY_BASES = []


def configure_companies(names):
    """Known employer names: an aggregator (Business France) naming 'SOCIETE
    GENERALE SA' maps to the same company key as the bank's own source."""
    bases = {normalized_title(re.sub(r'\(.*?\)', ' ', n or '')) for n in names}
    _COMPANY_BASES[:] = sorted((b for b in bases if len(b) >= 3), key=len)  # shortest first: "natixis" covers "natixis cib"


def company_key(name):
    plain = normalized_title(re.sub(r'\(.*?\)', ' ', name or ''))
    for base in _COMPANY_BASES:
        if re.search(r'\b' + re.escape(base) + r'\b', plain):
            return base
    # 'J.P. Morgan' / 'JPMorgan', 'Societe Generale' / 'SocieteGenerale'
    compact = plain.replace(' ', '')
    for base in _COMPANY_BASES:
        squeezed = base.replace(' ', '')
        if compact == squeezed or (len(squeezed) >= 5 and compact.startswith(squeezed)):
            return base
    return plain


VIE_MARK = re.compile(r'^(?:v i e|vie)\b\s*|\s*\b(?:v i e|vie)$')


def borrow_descriptions(jobs):
    """Title-only offers (LinkedIn / eFinancialCareers alerts, web search
    leads) borrow the advert text and posting date of the same offer on the
    employer's own site, read in the same run, so its experience requirement
    and age are checked. LinkedIn pages are
    never fetched (LinkedIn forbids robots). Returns the number matched."""
    def key(job):
        return company_key(job.get('company')), VIE_MARK.sub('', normalized_title(job.get('title'))).strip()
    official, dated = {}, {}
    for job in jobs:
        if not job.get('title_only'):
            if len(job.get('description') or '') > 200:
                official.setdefault(key(job), job)
            if job.get('published'):
                dated.setdefault(key(job), job['published'])
    matched = 0
    for job in jobs:
        if job.get('title_only') and key(job) in dated:
            # The employer's own posting date decides the 14-day rule, even
            # when its advert text is too short to borrow
            job['published'] = dated[key(job)]
        twin = job.get('title_only') and official.get(key(job))
        if twin:
            job['description'] = twin['description']
            job['title_only'] = False
            job['description_from'] = twin.get('url', '')
            # The employer's own posting date decides the 14-day rule (a web
            # search lead carries no date of its own)
            if twin.get('published'):
                job['published'] = twin['published']
            matched += 1
    return matched


def fingerprints(job, city=''):
    """All identity keys for a job. Any match against history means 'seen'."""
    keys = []
    if job.get('id'):
        keys.append('id:' + job['id'])
    company = company_key(job.get('company'))
    if job.get('requisition_id'):
        keys.append(f'req:{company}:{normalize_text(job["requisition_id"])}')
    if job.get('url'):
        keys.append('url:' + canonical_url(job['url']))
    title = VIE_MARK.sub('', normalized_title(job.get('title'))).strip()
    if title:
        keys.append(f'title:{company}:{title}:{normalize_text(city)}')
    return keys
