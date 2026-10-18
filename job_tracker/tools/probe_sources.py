"""Diagnostic probe for candidate public career feeds.

Run from GitHub Actions (open internet) to discover which official, publicly
accessible endpoints return real vacancies. Prints one block per probe.
It never logs in, never sends credentials and makes one request per probe.
"""
import json
import re
import sys
import time
from urllib.parse import urlparse

import requests

UA = 'Mozilla/5.0 (compatible; PersonalJobTracker/4.0; +https://github.com/Jojolap1/sales-trading-job-tracker)'
S = requests.Session()
S.headers.update({'User-Agent': UA, 'Accept-Language': 'en-GB,en;q=0.8'})
ATS = re.compile(r'https?://[a-z0-9.-]*(?:myworkdayjobs\.com|myworkdaysite\.com|oraclecloud\.com|eightfold\.ai|successfactors\.(?:com|eu)|taleo\.net|avature\.net|brassring\.com|smartrecruiters\.com|greenhouse\.io|lever\.co|ashbyhq\.com|tal\.net|icims\.com|phenompeople\.com|beesite\.de|jobvite\.com|workable\.com|teamtailor\.com|recruitee\.com|csod\.com|hirebridge|jobs\.[a-z0-9.-]+)[^\s"\'<>)]*', re.I)


def show(name, fn):
    t = time.time()
    try:
        out = fn()
    except Exception as exc:  # diagnostic tool: report everything
        out = f'EXC {type(exc).__name__}: {str(exc)[:200]}'
    print(f'### {name} ({time.time() - t:.1f}s)\n{out}\n', flush=True)


def get(url, **kw):
    return S.get(url, timeout=25, **kw)


def summarize_json(r, path_fn):
    if r.status_code != 200:
        return f'HTTP {r.status_code} {r.url} body={r.text[:200]!r}'
    data = r.json()
    total, titles = path_fn(data)
    return f'HTTP 200 total={total} sample={titles[:6]}'


def discover(url):
    r = get(url)
    hosts = {}
    for m in ATS.findall(r.text):
        u = urlparse(m)
        key = u.scheme + '://' + u.netloc + '/' + u.path.strip('/').split('/')[0]
        hosts[key] = hosts.get(key, 0) + 1
    return f'HTTP {r.status_code} final={r.url} len={len(r.text)} ats_links={sorted(hosts.items(), key=lambda x: -x[1])[:15]}'


def sitemap(url):
    r = get(url)
    if r.status_code != 200:
        return f'HTTP {r.status_code} {r.url} {r.text[:150]!r}'
    locs = re.findall(r'<loc>\s*([^<]+?)\s*</loc>', r.text)
    return f'HTTP 200 locs={len(locs)} first={locs[:5]} last={locs[-3:]}'


def workday(host, tenant, site, text=''):
    url = f'https://{host}/wday/cxs/{tenant}/{site}/jobs'
    r = S.post(url, json={'appliedFacets': {}, 'limit': 20, 'offset': 0, 'searchText': text}, timeout=25,
               headers={'Accept': 'application/json', 'Content-Type': 'application/json'})
    if r.status_code != 200:
        return f'HTTP {r.status_code} {url} {r.text[:120]!r}'
    d = r.json()
    facets = [f.get('facetParameter') for f in d.get('facets', [])]
    return f'HTTP 200 total={d.get("total")} facets={facets} sample={[(j.get("title"), j.get("locationsText"), j.get("postedOn"), j.get("externalPath")) for j in d.get("jobPostings", [])[:5]]}'


def oracle(host, site, kw='analyst'):
    url = (f'https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true'
           f'&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},limit=10,offset=0,keyword=%22{kw}%22,sortBy=POSTING_DATES_DESC')
    r = get(url, headers={'Accept': 'application/json'})
    if r.status_code != 200:
        return f'HTTP {r.status_code} {r.text[:150]!r}'
    items = r.json().get('items', [])
    if not items:
        return 'HTTP 200 no items'
    it = items[0]
    reqs = it.get('requisitionList', [])
    return f'HTTP 200 total={it.get("TotalJobsCount")} sample={[(q.get("Title"), q.get("PrimaryLocation"), q.get("PostedDate"), q.get("Id")) for q in reqs[:5]]} keys={list(reqs[0].keys()) if reqs else []}'


def eightfold(host, domain, q='analyst'):
    url = f'https://{host}/api/apply/v2/jobs?domain={domain}&start=0&num=10&query={q}'
    r = get(url, headers={'Accept': 'application/json'})
    if r.status_code != 200:
        return f'HTTP {r.status_code} {r.text[:150]!r}'
    d = r.json()
    return f'HTTP 200 count={d.get("count")} sample={[(p.get("name"), p.get("location"), p.get("t_create"), p.get("canonicalPositionUrl")) for p in d.get("positions", [])[:5]]} keys={list(d.get("positions",[{}])[0].keys())[:40] if d.get("positions") else []}'


def greenhouse(board):
    return summarize_json(get(f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs'),
                          lambda d: (len(d.get('jobs', [])), [(j['title'], (j.get('location') or {}).get('name')) for j in d.get('jobs', [])]))


def lever(board):
    return summarize_json(get(f'https://api.lever.co/v0/postings/{board}?mode=json'),
                          lambda d: (len(d), [(j['text'], (j.get('categories') or {}).get('location')) for j in d]))


def smartrecruiters(company):
    return summarize_json(get(f'https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=20'),
                          lambda d: (d.get('totalFound'), [(j['name'], (j.get('location') or {}).get('city')) for j in d.get('content', [])]))


def jsonld_page(url):
    r = get(url)
    blocks = re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', r.text, re.S | re.I)
    info = []
    for b in blocks:
        try:
            d = json.loads(b)
        except ValueError:
            info.append('unparseable')
            continue
        for o in (d if isinstance(d, list) else [d]):
            if isinstance(o, dict):
                info.append({k: (str(v)[:80]) for k, v in o.items() if k in ('@type', 'title', 'datePosted', 'validThrough', 'employmentType', 'jobLocation', 'identifier', 'hiringOrganization')})
    applies = sorted(set(m for m in re.findall(r'href="([^"]*(?:apply|postul|candidat)[^"]*)"', r.text, re.I)))[:6]
    h1 = re.findall(r'<h1[^>]*>(.*?)</h1>', r.text, re.S)
    return f'HTTP {r.status_code} final={r.url} h1={[re.sub("<[^>]+>|\\s+", " ", h)[:80] for h in h1[:2]]} jsonld={info[:3]} apply_links={applies} ats={sorted(set(urlparse(m).netloc for m in ATS.findall(r.text)))[:10]}'


PROBES = []
def P(name, fn, *a):
    PROBES.append((name, lambda: fn(*a)))

# --- Discovery of ATS hosts from official career pages ---
for name, url in [
    ('JPM landing', 'https://careers.jpmorgan.com/global/en/students/programs'),
    ('JPM jobs', 'https://www.jpmorganchase.com/careers/explore-opportunities'),
    ('GS higher', 'https://higher.gs.com/'),
    ('GS students', 'https://www.goldmansachs.com/careers/students/programs-and-internships'),
    ('MS students', 'https://www.morganstanley.com/people-opportunities/students-graduates'),
    ('MS careers', 'https://www.morganstanley.com/careers/career-opportunities-search'),
    ('BofA campus', 'https://campus.bankofamerica.com/'),
    ('BofA careers', 'https://careers.bankofamerica.com/en-us'),
    ('HSBC careers', 'https://www.hsbc.com/careers'),
    ('HSBC students', 'https://www.hsbc.com/careers/students-and-graduates'),
    ('UBS careers', 'https://www.ubs.com/global/en/careers.html'),
    ('DB careers', 'https://careers.db.com/'),
    ('Barclays', 'https://search.jobs.barclays/'),
    ('Barclays early', 'https://home.barclays/careers/early-careers/'),
    ('SC careers', 'https://www.sc.com/en/careers/'),
    ('SC jobs', 'https://jobs.standardchartered.com/'),
    ('CA-CIB', 'https://www.ca-cib.com/careers'),
    ('CA jobs', 'https://jobs.ca-cib.com/'),
    ('CA group jobs', 'https://groupecreditagricole.jobs/en/'),
    ('Natixis', 'https://www.natixis.com/natixis/en/careers'),
    ('Natixis CIB', 'https://gsh.natixis.com/en/careers'),
    ('BPCE recrutement', 'https://recrutement.groupebpce.com/'),
    ('MUFG EMEA', 'https://www.mufgemea.com/careers/'),
    ('MUFG Americas', 'https://careers.mufgamericas.com/'),
    ('Nomura careers', 'https://www.nomura.com/careers/'),
    ('Nomura EMEA', 'https://www.nomura.com/europe/careers/'),
    ('SMBC EMEA', 'https://www.smbcgroup.com/emea/careers'),
    ('SMBC Nikko', 'https://www.smbcnikko-gl.com/careers/'),
    ('RBC jobs', 'https://jobs.rbc.com/ca/en'),
    ('CIBC careers', 'https://www.cibc.com/en/about-cibc/careers.html'),
    ('BNP group', 'https://group.bnpparibas/en/careers/all-job-offers'),
    ('BNP CIB', 'https://cib.bnpparibas/careers/'),
    ('BlackRock careers', 'https://careers.blackrock.com/'),
    ('Pictet careers', 'https://www.group.pictet/careers'),
    ('Optiver', 'https://optiver.com/working-at-optiver/career-opportunities/'),
    ('CICC', 'https://www.cicc.com/'),
    ('CLSA', 'https://www.clsa.com/careers/'),
    ('CITIC Sec Intl', 'https://www.citics.com.hk/'),
    ('BOCI', 'https://www.bocigroup.com/en/careers/'),
    ('ICBC Asia', 'https://www.icbcasia.com/en/about-us/careers'),
    ('CCB Intl', 'https://www.ccbintl.com.hk/'),
    ('Haitong Intl', 'https://www.htisec.com/en-us/careers'),
    ('Huatai Intl', 'https://www.htsc.com.hk/'),
    ('Citi jobs', 'https://jobs.citi.com/'),
    ('SG careers', 'https://careers.societegenerale.com/en/search'),
]:
    P('DISCOVER ' + name, discover, url)

# --- Candidate APIs ---
P('Oracle JPMC CX_1001', oracle, 'jpmc.fa.oraclecloud.com', 'CX_1001')
P('Oracle JPMC CX_1002', oracle, 'jpmc.fa.oraclecloud.com', 'CX_1002')
P('Oracle GS hdpc CX_1', oracle, 'hdpc.fa.us2.oraclecloud.com', 'CX_1')
P('Eightfold MS', eightfold, 'morganstanley.eightfold.ai', 'morganstanley.com')
P('Eightfold HSBC portal', eightfold, 'portal.careers.hsbc.com', 'hsbc.com')
P('Eightfold HSBC', eightfold, 'hsbc.eightfold.ai', 'hsbc.com')
P('Eightfold BofA', eightfold, 'bankofamerica.eightfold.ai', 'bankofamerica.com')
P('Eightfold SC', eightfold, 'standardchartered.eightfold.ai', 'sc.com')
for host, tenant, site in [
    ('citi.wd5.myworkdayjobs.com', 'citi', '2'),
    ('blackrock.wd1.myworkdayjobs.com', 'blackrock', 'BlackRock_Professional'),
    ('blackrock.wd1.myworkdayjobs.com', 'blackrock', 'BlackRock_Campus'),
    ('nomura.wd3.myworkdayjobs.com', 'nomura', 'Nomura_Campus'),
    ('nomura.wd3.myworkdayjobs.com', 'nomura', 'Nomura_Careers'),
    ('nomura.wd3.myworkdayjobs.com', 'nomura', 'Nomura'),
    ('mufgub.wd3.myworkdayjobs.com', 'mufgub', 'MUFG-Careers'),
    ('mufgub.wd3.myworkdayjobs.com', 'mufgub', 'MUFG-EMEA'),
    ('rbc.wd3.myworkdayjobs.com', 'rbc', 'RBCGLOBAL1'),
    ('cibc.wd3.myworkdayjobs.com', 'cibc', 'search'),
    ('cibc.wd3.myworkdayjobs.com', 'cibc', 'campus'),
    ('ms.wd5.myworkdayjobs.com', 'ms', 'External'),
    ('db.wd3.myworkdayjobs.com', 'db', 'DBWebsite'),
    ('hsbc.wd3.myworkdayjobs.com', 'hsbc', 'external'),
    ('smbcgroup.wd3.myworkdayjobs.com', 'smbcgroup', 'SMBC'),
    ('statestreet.wd1.myworkdayjobs.com', 'statestreet', 'Global'),
    ('bnymellon.wd1.myworkdayjobs.com', 'bnymellon', 'BNY_Careers'),
    ('pictet.wd3.myworkdayjobs.com', 'pictet', 'Pictet'),
    ('man.wd3.myworkdayjobs.com', 'man', 'Man_Group'),
    ('jefferies.wd5.myworkdayjobs.com', 'jefferies', 'JefferiesCareers'),
    ('mizuho.wd1.myworkdayjobs.com', 'mizuho', 'mizuhoamericas'),
    ('nb.wd1.myworkdayjobs.com', 'nb', 'NBCareers'),
]:
    P(f'Workday {tenant}/{site}', workday, host, tenant, site)

for b in ['optiver', 'imc', 'flowtraders', 'jumptrading', 'wehrtyou', 'drweng', 'towerresearchcapital', 'point72', 'squarepointcapital', 'janestreet', 'virtu', 'mavensecurities', 'xtxmarketstechnologies', 'citadelsecurities', 'susquehanna', 'qube', 'gsa', 'schroders', 'pictet', 'clsa']:
    P(f'Greenhouse {b}', greenhouse, b)
for b in ['optiver', 'mavensecurities', 'qrt', 'cfm', 'pictet']:
    P(f'Lever {b}', lever, b)
for c in ['Pictet', 'Natixis', 'NatixisInvestmentManagers', 'BNPParibas', 'AXAInvestmentManagers', 'Amundi', 'CreditAgricoleCIB', 'Nomura', 'SMBC', 'UBS', 'Schroders', 'Mirabaud', 'LombardOdier']:
    P(f'SmartRecruiters {c}', smartrecruiters, c)

for name, url in [
    ('Citi sitemap', 'https://jobs.citi.com/sitemap.xml'),
    ('Barclays sitemap', 'https://search.jobs.barclays/sitemap.xml'),
    ('SC sitemap', 'https://jobs.standardchartered.com/sitemap.xml'),
    ('BofA sitemap', 'https://careers.bankofamerica.com/sitemap.xml'),
    ('GS sitemap', 'https://higher.gs.com/sitemap.xml'),
    ('MS sitemap', 'https://www.morganstanley.com/sitemap.xml'),
    ('RBC sitemap', 'https://jobs.rbc.com/sitemap.xml'),
    ('UBS sitemap', 'https://jobs.ubs.com/sitemap.xml'),
    ('DB sitemap', 'https://careers.db.com/sitemap.xml'),
    ('BNP sitemap', 'https://group.bnpparibas/sitemap.xml'),
    ('CA sitemap', 'https://jobs.ca-cib.com/sitemap.xml'),
    ('CA group sitemap', 'https://groupecreditagricole.jobs/sitemap.xml'),
    ('HSBC mycareer sitemap', 'https://mycareer.hsbc.com/sitemap.xml'),
    ('BlackRock sitemap', 'https://careers.blackrock.com/sitemap.xml'),
    ('Natixis sitemap', 'https://www.natixis.com/sitemap.xml'),
    ('Optiver sitemap', 'https://optiver.com/sitemap.xml'),
    ('SG sitemap', 'https://careers.societegenerale.com/sitemap.xml'),
]:
    P(name, sitemap, url)

P('Citi job page', jsonld_page, 'https://jobs.citi.com/job/london/markets-sales-and-trading-summer-analyst-london-united-kingdom-2027/287/100035343072')
P('SG job page', jsonld_page, 'https://careers.societegenerale.com/en/job-offers/trainee-sales-assistant-26000M1T-en')
P('DB beesite', lambda: summarize_json(get('https://api-deutschebank.beesite.de/search/?data=' + requests.utils.quote(json.dumps({'LanguageCode': 'en', 'SearchParameters': {'FirstItem': 1, 'CountItem': 10, 'MatchedObjectDescriptor': ['PositionTitle', 'PositionLocation.CityName', 'PublicationStartDate', 'PositionURI']}, 'SearchCriteria': []}))),
    lambda d: (d.get('SearchResult', {}).get('SearchResultCountAll'), [i.get('MatchedObjectDescriptor', {}).get('PositionTitle') for i in d.get('SearchResult', {}).get('SearchResultItems', [])])))
P('Jane Street json', lambda: summarize_json(get('https://www.janestreet.com/jobs/main.json'), lambda d: (len(d), [(j.get('position'), j.get('city')) for j in d])))
P('BofA servlet', lambda: get('https://careers.bankofamerica.com/services/jobssearchservlet?start=0&rows=10&search=getAllJobs').text[:400])

if __name__ == '__main__':
    only = sys.argv[1] if len(sys.argv) > 1 else ''
    for name, fn in PROBES:
        if only.lower() in name.lower():
            show(name, fn)
            time.sleep(0.3)
