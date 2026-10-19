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
    h1 = [re.sub(r'<[^>]+>|\s+', ' ', h)[:80] for h in re.findall(r'<h1[^>]*>(.*?)</h1>', r.text, re.S)[:2]]
    return f'HTTP {r.status_code} final={r.url} h1={h1} jsonld={info[:3]} apply_links={applies} ats={sorted(set(urlparse(m).netloc for m in ATS.findall(r.text)))[:10]}'


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


# ---------------- Round 2 ----------------
def links(url, pattern, n=12):
    r = get(url)
    found = sorted(set(re.findall(pattern, r.text, re.I)))
    return f'HTTP {r.status_code} final={r.url} len={len(r.text)} matches={found[:n]}'


def oracle_facets(host, site):
    url = (f'https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true'
           f'&expand=requisitionList.secondaryLocations,flexFieldsFacet.values&finder=findReqs;siteNumber={site},facetsList=LOCATIONS%3BTITLES%3BCATEGORIES,limit=1')
    d = get(url, headers={'Accept': 'application/json'}).json()
    it = d['items'][0]
    locs = [(l.get('Id'), l.get('Name'), l.get('TotalCount')) for l in it.get('locationsFacet', [])]
    hits = [l for l in locs if re.search('London|Hong Kong|Singapore|Paris|United Kingdom|France', str(l[1]))]
    cats = [(c.get('Id'), c.get('Name'), c.get('TotalCount')) for c in it.get('categoriesFacet', [])]
    return f'locations={len(locs)} hits={hits} categories={cats[:40]} keys={list(it.keys())}'


def oracle_loc(host, site, loc_id):
    url = (f'https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true'
           f'&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},selectedLocationsFacet={loc_id},limit=25,offset=0,sortBy=POSTING_DATES_DESC')
    d = get(url, headers={'Accept': 'application/json'}).json()
    it = d['items'][0]
    reqs = it.get('requisitionList', [])
    return f'total={it.get("TotalJobsCount")} n={len(reqs)} sample={[(q["Title"], q["PrimaryLocation"], q["PostedDate"], q.get("PostingEndDate"), q.get("JobFamily"), q.get("ShortDescriptionStr", "")[:60]) for q in reqs[:8]]}'


def oracle_detail(host, site, req_id):
    url = (f'https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true'
           f'&finder=ById;Id=%22{req_id}%22,siteNumber={site}')
    d = get(url, headers={'Accept': 'application/json'}).json()
    it = (d.get('items') or [{}])[0]
    return f'keys={list(it.keys())} title={it.get("Title")} desc_len={len(it.get("ExternalDescriptionStr") or "")} qual={str(it.get("ExternalQualificationsStr"))[:200]} end={it.get("ExternalPostedEndDate")} start={it.get("ExternalPostedStartDate")}'


def workday_facets(host, tenant, site):
    url = f'https://{host}/wday/cxs/{tenant}/{site}/jobs'
    d = S.post(url, json={'appliedFacets': {}, 'limit': 1, 'offset': 0, 'searchText': ''}, timeout=25).json()
    out = []
    def walk(facets, depth=0):
        for f in facets:
            vals = f.get('values', [])
            if vals and 'facetParameter' in vals[0]:
                walk(vals, depth + 1)
                continue
            hits = [(v.get('id'), v.get('descriptor'), v.get('count')) for v in vals if re.search('London|Hong Kong|Singapore|Paris|United Kingdom|France|Intern|Graduate|Analyst|Student|Campus|Early', str(v.get('descriptor')), re.I)]
            out.append((f.get('facetParameter'), len(vals), hits[:12]))
    walk(d.get('facets', []))
    return f'total={d.get("total")} facets={out}'


def workday_detail(host, tenant, site, text):
    url = f'https://{host}/wday/cxs/{tenant}/{site}/jobs'
    d = S.post(url, json={'appliedFacets': {}, 'limit': 5, 'offset': 0, 'searchText': text}, timeout=25).json()
    jp = d.get('jobPostings', [])
    if not jp:
        return f'total={d.get("total")} none'
    path = jp[0]['externalPath']
    det = get(f'https://{host}/wday/cxs/{tenant}/{site}{path}', headers={'Accept': 'application/json'}).json()
    info = det.get('jobPostingInfo', {})
    return f'total={d.get("total")} list0={jp[0]} detail_keys={list(info.keys())} startDate={info.get("startDate")} endDate={info.get("endDate")} timeType={info.get("timeType")} canApply={info.get("canApply")} location={info.get("location")} addl={info.get("additionalLocations")} jobReqId={info.get("jobReqId")} url={info.get("externalUrl")} desc={len(info.get("jobDescription",""))}'


def eightfold_loc(host, domain, location, q=''):
    url = f'https://{host}/api/apply/v2/jobs?domain={domain}&start=0&num=10&location={requests.utils.quote(location)}&query={q}&sort_by=timestamp'
    d = get(url).json()
    return f'count={d.get("count")} sample={[(p.get("name"), p.get("location"), p.get("t_create"), p.get("display_job_id"), p.get("department")) for p in d.get("positions", [])[:8]]}'


R2 = []
def Q(name, fn, *a):
    R2.append(('R2 ' + name, lambda: fn(*a)))

Q('JPM oracle facets', oracle_facets, 'jpmc.fa.oraclecloud.com', 'CX_1001')
Q('JPM oracle detail', oracle_detail, 'jpmc.fa.oraclecloud.com', 'CX_1001', '210801318')
Q('MS workday facets', workday_facets, 'ms.wd5.myworkdayjobs.com', 'ms', 'External')
Q('Citi workday facets', workday_facets, 'citi.wd5.myworkdayjobs.com', 'citi', '2')
Q('Citi workday detail', workday_detail, 'citi.wd5.myworkdayjobs.com', 'citi', '2', 'Sales and Trading Summer Analyst London')
Q('DB workday facets', workday_facets, 'db.wd3.myworkdayjobs.com', 'db', 'DBWebsite')
Q('Barclays workday', workday, 'barclays.wd3.myworkdayjobs.com', 'barclays', 'External_Career_Site_Barclays')
Q('Barclays workday facets', workday_facets, 'barclays.wd3.myworkdayjobs.com', 'barclays', 'External_Career_Site_Barclays')
Q('CLSA workday', workday_facets, 'citicclsa.wd3.myworkdayjobs.com', 'citicclsa', 'External')
Q('HSBC eightfold London', eightfold_loc, 'portal.careers.hsbc.com', 'hsbc.com', 'London, United Kingdom', 'graduate')
Q('HSBC eightfold HK', eightfold_loc, 'portal.careers.hsbc.com', 'hsbc.com', 'Hong Kong', '')
Q('Citi eightfold', eightfold, 'citi.eightfold.ai', 'citi.com')
Q('Haitong workday link', links, 'https://www.htisec.com/en-us/careers', r'https://htisec\.wd3\.myworkdayjobs\.com[^"\'\s<>]*')
Q('BofA ghr links', links, 'https://careers.bankofamerica.com/en-us', r'https://ghr\.wd1\.myworkdayjobs\.com[^"\'\s<>]*|https://bankcampuscareers\.tal\.net[^"\'\s<>]*')
Q('BofA servlet search', lambda: get('https://careers.bankofamerica.com/services/jobssearchservlet?start=0&rows=5&search=getAllJobs&location=London').text[:1500])
Q('BofA sitemap jobs', lambda: str([l for l in re.findall(r'<loc>\s*([^<]+?)\s*</loc>', get('https://careers.bankofamerica.com/sitemap.xml').text) if 'job' in l.lower()][:15]))
Q('Nomura tal links', links, 'https://www.nomura.com/europe/careers/', r'https://nomuracampus\.tal\.net[^"\'\s<>]*')
Q('Nomura tal board', links, 'https://nomuracampus.tal.net/vx/lang-en-GB/mobile-0/appcentre-1/brand-4/candidate/jobboard/vacancy/1/adv/', r'opp/\d+[^"\'<>]*|<title>[^<]*')
Q('CA-CIB handlers', links, 'https://jobs.ca-cib.com/accueil.aspx?LCID=2057', r'https://jobs\.ca-cib\.com/Handlers[^"\'\s<>]*|/[a-z-]+/[a-z-]*offre[^"\'\s<>]*|rss[^"\'\s<>]*')
Q('CA-CIB rss', lambda: get('https://jobs.ca-cib.com/handlers/offerRss.ashx?LCID=2057').text[:1200])
Q('CA-CIB list', links, 'https://jobs.ca-cib.com/offre-de-emploi/liste-toutes-offres.aspx?LCID=2057', r'/offre-de-emploi/emploi-[^"\'\s<>]*')
Q('SC rss', lambda: get('https://jobs.standardchartered.com/services/rss/job/?locale=en_GB&keywords=graduate').text[:1500])
Q('SC job page', jsonld_page, 'https://jobs.standardchartered.com/job/Kwun-Tong-Associate-Director%2C-Team-Manager%2C-Insurance-Specialists/1360744257/')
Q('SC sitemap HK/SG samples', lambda: str([l for l in re.findall(r'<loc>\s*([^<]+?)\s*</loc>', get('https://jobs.standardchartered.com/sitemap.xml').text) if re.search('intern|graduate|trainee|sales|trad', l, re.I)][:25]))
Q('Barclays sitemap samples', lambda: str([l for l in re.findall(r'<loc>\s*([^<]+?)\s*</loc>', get('https://search.jobs.barclays/sitemap.xml').text) if '/job/' in l][:8]))
Q('Optiver job page', jsonld_page, 'https://www.optiver.com/join-us/jobs/technology/amsterdam/ai-engineer/')
Q('Optiver sitemap trading', lambda: str([l for l in re.findall(r'<loc>\s*([^<]+?)\s*</loc>', get('https://optiver.com/sitemap.xml').text) if re.search('trad|intern|graduate', l)][:30]))
Q('Natixis sitemaps', lambda: str(re.findall(r'<loc>\s*([^<]+?)\s*</loc>', get('https://natixis.groupebpce.com/sitemap.xml').text)))
Q('Natixis offers page', links, 'https://natixis.groupebpce.com/?lang=en', r'https?://[^"\'\s<>]*(?:offre|offer|job|career|recrut)[^"\'\s<>]*')
Q('GS higher scripts', links, 'https://higher.gs.com/results', r'https?://[a-z0-9.-]*gs\.com[^"\'\s<>]*api[^"\'\s<>]*|"/_next/static/[^"]+\.js"|graphql[^"\'\s<>]*')
Q('GS higher role page', jsonld_page, 'https://higher.gs.com/roles/134685')
Q('UBS page links', links, 'https://www.ubs.com/global/en/careers/search-jobs.html', r'https?://[a-z0-9.-]*(?:brassring|ubs)\.com[^"\'\s<>]*(?:TGnewUI|jobs|search)[^"\'\s<>]*')
Q('UBS brassring', links, 'https://jobs.ubs.com/TGnewUI/Search/Home/Home?partnerid=25008&siteid=5012', r'<title>[^<]*|RFT[^"]{0,40}|PowerSearch[^"\'\s<>]*')
Q('SMBC careers links', links, 'https://www.smbcgroup.com/emea/careers', r'https?://[^"\'\s<>]*(?:workday|taleo|oracle|tal\.net|successfactors|avature|job|vacanc)[^"\'\s<>]*')
Q('Pictet career link', links, 'https://www.pictet.com/us/en/careers', r'https://career012\.successfactors\.eu[^"\'\s<>]*')
Q('BNP workday', workday, 'bnpparibas.wd3.myworkdayjobs.com', 'bnpparibas', 'BNPP')
Q('BlackRock sitemap jobs', lambda: str([l for l in re.findall(r'<loc>\s*([^<]+?)\s*</loc>', get('https://careers.blackrock.com/sitemap.xml').text) if '/job/' in l][:6]))
Q('Point72 detail', lambda: str([(j['title'], j['location']['name'], j.get('updated_at'), j.get('first_published')) for j in get('https://boards-api.greenhouse.io/v1/boards/point72/jobs').json()['jobs'] if re.search('Hong Kong|Singapore|London|Paris', j['location']['name'])][:30]))
Q('MS students tal', links, 'https://www.morganstanley.com/people-opportunities/students-graduates/programs', r'https?://[^"\'\s<>]*(?:tal\.net|workday|oracle|eightfold|apply)[^"\'\s<>]*')
Q('GS students links', links, 'https://www.goldmansachs.com/careers/students/programs-and-internships', r'https?://[^"\'\s<>]*(?:higher\.gs|apply|oracle|tal\.net|workday)[^"\'\s<>]*')
PROBES = PROBES + R2


# ---------------- Round 3 ----------------
def gs_chunks():
    page = get('https://higher.gs.com/results').text
    chunks = re.findall(r'"(/_next/static/chunks/[^"]+\.js)"', page)
    hits = set()
    for c in chunks:
        js = get('https://higher.gs.com' + c).text
        for m in re.findall(r'https?://[a-zA-Z0-9.-]+(?:/[a-zA-Z0-9_./-]*)?', js):
            if re.search('api|graphql|search|gateway|role', m, re.I):
                hits.add(m)
        for m in re.findall(r'.{60}(?:graphql|operationName|roleSearch|searchRoles).{120}', js):
            hits.add('CTX ' + m)
    return str(sorted(hits)[:40])


def first_text(url, pattern, n=8, width=300):
    r = get(url)
    out = [m[:width] for m in re.findall(pattern, r.text, re.S | re.I)[:n]]
    return f'HTTP {r.status_code} final={r.url} len={len(r.text)} hits={out}'


def eightfold_detail(host, domain):
    d = get(f'https://{host}/api/apply/v2/jobs?domain={domain}&start=0&num=100&location=Singapore&sort_by=timestamp').json()
    pos = d.get('positions', [])
    pid = pos[0]['id'] if pos else None
    det = get(f'https://{host}/api/apply/v2/jobs/{pid}?domain={domain}').json() if pid else {}
    return f'count={d.get("count")} returned={len(pos)} first={pos[0] if pos else None} detail_keys={list(det.keys())} desc_len={len(det.get("job_description") or "")}'


R3 = []
def T(name, fn, *a):
    R3.append(('R3 ' + name, lambda: fn(*a)))

T('GS chunks', gs_chunks)
T('UBS preload', first_text, 'https://jobs.ubs.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid=25008&siteid=5012&PageType=searchResults&SearchType=linkquery&LinkID=15231', r'.{80}(?:JobTitle|"Questions"|jobtitle|reqid).{200}')
T('Natixis recrutement', discover, 'https://recrutement.natixis.com/en')
T('Natixis offers', first_text, 'https://recrutement.natixis.com/en/offres', r'href="([^"]*(?:offre|offer|job)[^"]*)"', 20, 200)
T('Natixis sitemap', sitemap, 'https://recrutement.natixis.com/sitemap.xml')
T('Pictet SF xml', first_text, 'https://career012.successfactors.eu/career?company=banquepict&career_ns=job_listing_summary&resultType=XML', r'<(?:Job-Title|JobTitle|title)[^>]*>[^<]*|<Job .{300}', 8)
T('Pictet SF page', first_text, 'https://career012.successfactors.eu/career?company=banquepict', r'jobTitle[^<]{0,200}|career_job_req_id=\d+[^"]{0,100}|<title>[^<]*', 10)
T('SMBC early', discover, 'https://www.smbcgroup.com/emea/careers/early-careers')
T('SMBC vacancies', discover, 'https://www.smbcgroup.com/emea/careers/current-vacancies')
T('BofA ghr facets', workday_facets, 'ghr.wd1.myworkdayjobs.com', 'ghr', 'lateral-us')
T('BofA campus board', first_text, 'https://bankcampuscareers.tal.net/vx/lang-en-GB/mobile-0/brand-4/candidate/jobboard/vacancy/2/adv/', r'opp/\d+[^"\'<>]*|<title>[^<]*', 30, 150)
T('HSBC eightfold London', eightfold_loc, 'portal.careers.hsbc.com', 'hsbc.com', 'London', '')
T('HSBC eightfold UK', eightfold_loc, 'portal.careers.hsbc.com', 'hsbc.com', 'United Kingdom', '')
T('HSBC eightfold Paris', eightfold_loc, 'portal.careers.hsbc.com', 'hsbc.com', 'Paris', '')
T('HSBC eightfold detail', eightfold_detail, 'portal.careers.hsbc.com', 'hsbc.com')
CDATA_TITLE = r'<title><!\[CDATA\[(.*?)\]\]>'


def sc_rss(query, n):
    t = get('https://jobs.standardchartered.com/services/rss/job/?locale=en_GB' + query).text
    items = re.findall(r'<item>(.*?)</item>', t, re.S)
    return 'items=%d titles=%s item0_tail=%s' % (len(items), re.findall(CDATA_TITLE, t)[:n], items[0][-700:] if items else '')


T('SC rss all', sc_rss, '', 15)
T('SC rss intern', sc_rss, '&keywords=intern', 40)
T('Nomura opp page', first_text, 'https://nomuracampus.tal.net/vx/lang-en-GB/mobile-0/appcentre-1/brand-4/candidate/jobboard/vacancy/1/adv/', r'<a[^>]+href="([^"]*opp/14\d\d[^"]*)"[^>]*>(.*?)</a>', 3, 300)
T('Nomura opp detail', first_text, 'https://nomuracampus.tal.net/vx/lang-en-GB/mobile-0/appcentre-1/brand-4/candidate/so/pm/1/pl/1/opp/1449-2027-Global-Markets-Sales-Trading-Structuring-Summer-Internship-London/en-GB', r'(?:closing|deadline|location|apply)[^<]{0,150}', 12, 200)
T('Nomura board 2', first_text, 'https://nomuracampus.tal.net/vx/lang-en-GB/mobile-0/appcentre-1/brand-4/candidate/jobboard/vacancy/2/adv/', r'opp/\d+[^"\'<>/]*', 40, 120)
def ca_rss():
    t = get('https://jobs.ca-cib.com/handlers/offerRss.ashx?LCID=2057').text
    return 'items=%d categories=%s' % (t.count('<item>'), sorted(set(re.findall(r'<category>([^<]*)</category>', t)))[:80])


T('CA-CIB rss count', ca_rss)
T('Haitong facets', workday_facets, 'htisec.wd3.myworkdayjobs.com', 'htisec', 'hti_careers')
T('JPM all locations', lambda: str([(l.get('Id'), l.get('Name'), l.get('TotalCount')) for l in get('https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber=CX_1001,facetsList=LOCATIONS,limit=1', headers={'Accept': 'application/json'}).json()['items'][0]['locationsFacet']]))
T('JPM london search', oracle_loc, 'jpmc.fa.oraclecloud.com', 'CX_1001', '300000057005324')
T('BNP careers alt', discover, 'https://careers.bnpparibas/')
T('BNP group fr', discover, 'https://group.bnpparibas/emploi-carriere/toutes-offres-emploi')
T('SG page jsonld', first_text, 'https://careers.societegenerale.com/en/job-offers/trainee-sales-assistant-26000M1T-en', r'"validThrough"[^,]*|"datePosted"[^,]*|"employmentType"[^,]*|"addressLocality"[^,]*', 8)
T('Barclays early careers', workday_detail, 'barclays.wd3.myworkdayjobs.com', 'barclays', 'External_Career_Site_Barclays', 'Global Markets')
PROBES = PROBES + R3


# ---------------- Round 4 ----------------
def gs_queries():
    page = get('https://higher.gs.com/results').text
    chunks = re.findall(r'"(/_next/static/chunks/[^"]+\.js)"', page)
    out = []
    for c in chunks:
        js = get('https://higher.gs.com' + c).text
        for m in re.finditer(r'query \w*Roles?\w*\([^`]{0,2500}', js):
            out.append(m.group(0)[:2500])
        for m in re.finditer(r'.{300}searchQueryInput:\{.{600}', js):
            out.append('VARS ' + m.group(0))
        for m in re.finditer(r'.{200}experiences:\[.{300}', js):
            out.append('EXP ' + m.group(0))
    return '\n'.join(dict.fromkeys(out))[:9000]


def raw(url, start_pat, width=2500):
    t = get(url).text
    m = re.search(start_pat, t, re.S)
    return t[m.start():m.start() + width] if m else f'no match; len={len(t)} head={t[:300]!r}'


R4 = []
def U(name, fn, *a):
    R4.append(('R4 ' + name, lambda: fn(*a)))

U('GS queries', gs_queries)
U('Pictet xml job', raw, 'https://career012.successfactors.eu/career?company=banquepict&career_ns=job_listing_summary&resultType=XML', r'<Job[ >]', 3000)
U('SC detail page', first_text, 'https://jobs.standardchartered.com/job/Central-Global-Banking-Intern-Hong-Kong-2027/1369586257/', r'itemprop="[^"]+"[^>]{0,200}|<meta[^>]+(?:og:title|description)[^>]*>|data-careersite-propertyid="[^"]+"|class="jobdescription"', 25, 200)
U('SC sitemap lastmod', raw, 'https://jobs.standardchartered.com/sitemap.xml', r'<url>', 600)
U('CA-CIB listing', first_text, 'https://jobs.ca-cib.com/offre-de-emploi/liste-toutes-offres.aspx?LCID=2057', r'href="([^"]*(?:detailoffre|emploi-|offre-de-emploi/[^"]*\.aspx)[^"]*)"[^>]*>([^<]{0,120})', 15, 250)
U('CA-CIB rss params', lambda: str([(q, get('https://jobs.ca-cib.com/handlers/offerRss.ashx?LCID=2057' + q).text.count('<item>')) for q in ['&nbItems=200', '&nb=200', '&count=200', '&max=200', '&page=2']]))
U('BofA campus jobs', first_text, 'https://bankcampuscareers.tal.net/vx/lang-en-GB/mobile-0/brand-4/candidate/jobboard/vacancy/1/adv/', r'opp/\d+[^"\'<>/]*|<title>[^<]*', 40, 150)
U('BofA campus jobs 3', first_text, 'https://bankcampuscareers.tal.net/vx/lang-en-GB/mobile-0/brand-4/candidate/jobboard/vacancy/3/adv/', r'opp/\d+[^"\'<>/]*|<title>[^<]*', 40, 150)
U('Optiver job page full', first_text, 'https://www.optiver.com/join-us/jobs/institutional-sales/new-york/us-options-sales-trader/', r'"validThrough"[^,]*|"employmentType"[^,]*|"datePosted"[^,]*|"description"\s*:\s*".{0,200}', 6, 260)
U('Oleeo nomura detail meta', raw, 'https://nomuracampus.tal.net/vx/lang-en-GB/mobile-0/appcentre-1/brand-4/candidate/so/pm/1/pl/1/opp/1449-2027-Global-Markets-Sales-Trading-Structuring-Summer-Internship-London/en-GB', r'<meta[^>]+description', 400)
PROBES = PROBES + R4


# ---------------- Round 5 ----------------
GS_API = 'https://api-higher.gs.com/gateway/api/v1/graphql'
GS_FILTERS = """query GetRoleFilters($filterCategories: [RoleSearchFilterCategoryType!] $experiences: [RoleSearchExperience!]!) {
  roleSearchFilters(categories: $filterCategories experiences: $experiences) { filterCategoryType { code description }
    filters { filter count subFilters { filter count subFilters { filter count } } } } }"""
GS_ROLES = """query GetCampusRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: $searchQueryInput) {
  totalCount items { roleId corporateTitle jobTitle jobFunction locations { primary state country city } status division
  jobType { code description } externalSource { sourceId } educationLevel startDate } } }"""


def gs_post(query, variables):
    r = S.post(GS_API, json={'query': query, 'variables': variables}, timeout=25,
               headers={'Content-Type': 'application/json', 'Origin': 'https://higher.gs.com', 'Referer': 'https://higher.gs.com/'})
    return r.status_code, r.text


def gs_filters(exp):
    code, text = gs_post(GS_FILTERS, {'experiences': exp})
    if code != 200:
        return f'HTTP {code} {text[:300]}'
    d = json.loads(text)
    out = []
    for cat in (d.get('data') or {}).get('roleSearchFilters') or []:
        out.append((cat['filterCategoryType']['code'], [(f['filter'], f['count'], [(s['filter'], s['count'], [x['filter'] for x in s.get('subFilters') or []][:12]) for s in f.get('subFilters') or []][:12]) for f in cat['filters']][:10]))
    return str(out)[:4000] + (' errors=' + str(d.get('errors'))[:300] if d.get('errors') else '')


def gs_roles(exp, filters, sort=None):
    v = {'searchQueryInput': {'page': {'pageSize': 20, 'pageNumber': 0}, 'filters': filters, 'experiences': exp, 'searchTerm': ''}}
    if sort:
        v['searchQueryInput']['sort'] = sort
    code, text = gs_post(GS_ROLES, v)
    if code != 200:
        return f'HTTP {code} {text[:400]}'
    d = json.loads(text)
    rs = (d.get('data') or {}).get('roleSearch') or {}
    return f'total={rs.get("totalCount")} items={[(i["jobTitle"], i["corporateTitle"], [(l.get("city"), l.get("country")) for l in i["locations"]], i["status"], i["externalSource"], i["roleId"], i["jobType"], i.get("startDate")) for i in rs.get("items", [])[:6]]} errors={str(d.get("errors"))[:500]}'


R5 = []
def V(name, fn, *a):
    R5.append(('R5 ' + name, lambda: fn(*a)))

V('GS filters campus', gs_filters, ['CAMPUS'])
V('GS filters pro', gs_filters, ['EARLY_CAREER', 'PROFESSIONAL'])
V('GS roles campus nofilter', gs_roles, ['CAMPUS'], [])
V('GS roles campus London', gs_roles, ['CAMPUS'], [{'filterCategoryType': 'LOCATION', 'filters': [{'filter': 'EMEA', 'subFilters': [{'filter': 'United Kingdom', 'subFilters': [{'filter': 'London', 'subFilters': []}]}]}]}])
V('GS role page sitemap', lambda: str([l for l in re.findall(r'<loc>\s*([^<]+?)\s*</loc>', get('https://higher.gs.com/sitemap-roles.xml').text)][:5]))
V('Pictet xml tags', lambda: (lambda t: str(sorted(set(re.findall(r'<([A-Za-z][\w-]*)>', re.search(r'<Job>.*?</Job>', t, re.S).group(0))))) + ' count=' + str(t.count('<Job>')) + ' sample=' + re.sub(r'<Job-Description>.*?</Job-Description>', '', re.search(r'<Job>.*?</Job>', t, re.S).group(0), flags=re.S)[:1500])(get('https://career012.successfactors.eu/career?company=banquepict&career_ns=job_listing_summary&resultType=XML').text))
V('CA-CIB listing offers', first_text, 'https://jobs.ca-cib.com/offre-de-emploi/liste-toutes-offres.aspx?LCID=2057&mode=list', r'.{0,200}idOffre.{0,300}|<a[^>]+href="[^"]*emploi-[^"]*"[^>]*>.{0,300}', 4, 600)
V('BofA opp meta', raw, 'https://bankcampuscareers.tal.net/vx/lang-en-GB/mobile-0/brand-4/candidate/so/pm/1/pl/1/opp/14670-Global-Markets-Credit-2027-Summer-Analyst-London/en-GB', r'<meta[^>]+description', 400)
V('BofA board anchors', first_text, 'https://bankcampuscareers.tal.net/vx/lang-en-GB/mobile-0/brand-4/candidate/jobboard/vacancy/1/adv/', r'<a[^>]+href="([^"]*opp/14670[^"]*)"[^>]*>(.*?)</a>', 2, 300)
V('Oleeo pagination', first_text, 'https://bankcampuscareers.tal.net/vx/lang-en-GB/mobile-0/brand-4/candidate/jobboard/vacancy/1/adv/', r'href="[^"]*(?:page|start|ftq)[^"]*"[^>]*>[^<]{0,40}|of \d+ results|\d+ results', 10, 200)
PROBES = PROBES + R5


# ---------------- Round 6: new employers (auto-validate discovered feeds) ----------------
WD_URL = re.compile(r'https://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)', re.I)
OTHER_ATS = re.compile(r'https?://(?:boards\.greenhouse\.io|job-boards\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com|jobs\.smartrecruiters\.com|careers\.smartrecruiters\.com)/([A-Za-z0-9_-]+)|https?://([a-z0-9-]+)\.eightfold\.ai|https?://([a-z0-9.-]+\.fa\.[a-z0-9.]*oraclecloud\.com)[^"\s]*?(CX_\d+)|https?://(career\d*\.successfactors\.(?:com|eu))/career\?company=([A-Za-z0-9_]+)|https?://([a-z0-9-]+)\.tal\.net|https?://([a-z0-9-]+)\.(?:avature\.net|taleo\.net|icims\.com|csod\.com|jobs2web\.com|brassring\.com|phenompeople\.com)', re.I)


def validate_page(url):
    out = []
    try:
        r = get(url)
    except Exception as exc:
        return f'EXC {type(exc).__name__}: {str(exc)[:150]}'
    out.append(f'HTTP {r.status_code} final={r.url} len={len(r.text)}')
    text = r.text.replace('\\/', '/')
    seen = set()
    for host, wd, site in WD_URL.findall(text):
        key = (host.lower(), wd, site)
        if key in seen or site.lower() in ('wday', 'login', 'cxs'):
            continue
        seen.add(key)
        try:
            out.append(f'  WORKDAY {host}.{wd}/{site}: ' + workday(f'{host}.{wd}.myworkdayjobs.com', host, site)[:300])
        except Exception as exc:
            out.append(f'  WORKDAY {host}.{wd}/{site}: EXC {exc}'[:200])
    others = sorted(set(m.group(0)[:120] for m in OTHER_ATS.finditer(text)))
    if others:
        out.append('  OTHER ATS: ' + str(others[:10]))
    jobs = sorted(set(re.findall(r'https?://[a-z0-9.-]+/[^"\'\s<>]*(?:job|career|vacanc|recruit|opportunit)[^"\'\s<>]*', text, re.I)))
    out.append('  job-ish links: ' + str([j[:110] for j in jobs[:12]]))
    return '\n'.join(out)


R6_PAGES = {
    'DBS': ['https://www.dbs.com/careers/default.page', 'https://www.dbs.com/careers/jobs.page'],
    'OCBC': ['https://www.ocbc.com/group/careers', 'https://www.ocbc.com/group/careers/job-openings'],
    'UOB': ['https://www.uobgroup.com/uobgroup/careers/index.page', 'https://careers.uobgroup.com/'],
    'UOB Kay Hian': ['https://www.uobkayhian.com/careers', 'https://www.utrade.com.sg/careers'],
    'Mizuho': ['https://www.mizuhogroup.com/emea/careers', 'https://www.mizuhogroup.com/asia-pacific/careers', 'https://www.mizuhogroup.com/americas/careers'],
    'Daiwa': ['https://www.daiwacm.com/careers/', 'https://www.uk.daiwacm.com/careers', 'https://www.daiwa-grp.jp/english/recruit/'],
    'BBVA': ['https://careers.bbva.com/', 'https://careers.bbva.com/europe/'],
    'Santander CIB': ['https://www.santandercib.com/careers', 'https://www.santander.com/en/careers'],
    'ING': ['https://careers.ing.com/', 'https://www.ing.jobs/'],
    'NatWest': ['https://jobs.natwestgroup.com/', 'https://www.natwestgroup.com/careers.html'],
    'Guotai Junan Intl': ['https://www.gtjai.com/en/about-us/join-us', 'https://www.gtja.com.hk/'],
    'CMB International / CMS Intl': ['https://www.cmschina.com.hk/', 'https://www.cmbi.com/'],
    'BOCI': ['https://www.bocigroup.com/', 'https://www.bocigroup.com/en/'],
    'ICBC International': ['https://www.icbci.com.hk/', 'https://www.icbcasia.com/'],
    'GF Securities HK': ['https://www.gfgroup.com.hk/', 'https://en.gf.com.cn/'],
    'Shenwan Hongyuan HK': ['https://www.swhyhk.com/', 'https://www.swhyhk.com/en/'],
    'China Galaxy Intl': ['https://www.chinastock.com.hk/', 'https://www.cgisec.com/'],
    'Maybank': ['https://www.maybank.com/en/careers.page', 'https://www.maybankib.com/'],
    'CIMB': ['https://www.cimb.com/en/careers.html', 'https://www.cimb.com/en/careers/career-opportunities.html'],
    'Citadel Securities': ['https://www.citadelsecurities.com/careers/open-opportunities/', 'https://www.citadel.com/careers/open-opportunities/'],
    'Macquarie': ['https://www.macquarie.com/careers', 'https://recruitment.macquarie.com/'],
    'Jefferies': ['https://www.jefferies.com/careers/', 'https://jefferies.tal.net/'],
    'ANZ': ['https://careers.anz.com/'],
    'Commerzbank': ['https://jobs.commerzbank.com/'],
    'Natixis IM / BPCE': ['https://www.im.natixis.com/en-intl/about-us/careers'],
    'Amundi': ['https://jobs.amundi.com/', 'https://about.amundi.com/careers'],
    'AXA IM': ['https://www.axa-im.com/careers'],
    'Schroders': ['https://www.schroders.com/en/global/individual/careers/'],
    'Fidelity International': ['https://careers.fidelityinternational.com/'],
    'Invesco': ['https://careers.invesco.com/'],
    'Millennium': ['https://www.mlp.com/careers/'],
    'Susquehanna': ['https://careers.sig.com/'],
    'Marshall Wace': ['https://www.mwam.com/careers/'],
    'Two Sigma': ['https://careers.twosigma.com/'],
    'QRT': ['https://www.qube-rt.com/careers/'],
    'Mako': ['https://www.mako.com/careers'],
    'Akuna': ['https://akunacapital.com/careers'],
    'Natixis CIB (again)': ['https://natixis.groupebpce.com/en/careers/'],
    'Lazard AM': ['https://www.lazard.com/careers/'],
    'Nikko AM': ['https://www.nikkoam.com/careers'],
    'Eastspring': ['https://www.eastspring.com/careers'],
    'Bank of Singapore': ['https://www.bankofsingapore.com/careers.html'],
    'Hang Seng / BEA': ['https://www.hkbea.com/html/en/bea-about-bea-careers.html'],
    'CCB Asia': ['https://www.asia.ccb.com/hongkong/careers/'],
    'Haitong Bank / AMTD': ['https://www.amtdgroup.com/careers'],
}

R6 = []
for name, urls in R6_PAGES.items():
    for u in urls:
        R6.append((f'R6 {name} | {u}', (lambda u=u: validate_page(u))))

for host, tenant, site in [
    ('dbs.wd3.myworkdayjobs.com', 'dbs', 'DBS_Careers'), ('ocbc.wd102.myworkdayjobs.com', 'ocbc', 'OCBC'),
    ('mizuho.wd1.myworkdayjobs.com', 'mizuho', 'mizuhoemea'), ('mizuho.wd1.myworkdayjobs.com', 'mizuho', 'mizuhoasia'),
    ('ing.wd3.myworkdayjobs.com', 'ing', 'ICSGBLCOR'), ('santander.wd3.myworkdayjobs.com', 'santander', 'SantanderCareers'),
    ('natwestgroup.wd3.myworkdayjobs.com', 'natwestgroup', 'NatWestGroupCareers'), ('jefferies.wd5.myworkdayjobs.com', 'jefferies', 'Jefferies'),
    ('macquarie.wd3.myworkdayjobs.com', 'macquarie', 'Macquarie'), ('schroders.wd3.myworkdayjobs.com', 'schroders', 'Schroders'),
    ('invesco.wd1.myworkdayjobs.com', 'invesco', 'IVZ'), ('fil.wd3.myworkdayjobs.com', 'fil', 'FIL'),
    ('anz.wd3.myworkdayjobs.com', 'anz', 'ANZ'), ('mlp.wd5.myworkdayjobs.com', 'mlp', 'MLPCareers'),
    ('sig.wd1.myworkdayjobs.com', 'sig', 'SIG'), ('cimb.wd3.myworkdayjobs.com', 'cimb', 'CIMB'),
]:
    R6.append((f'R6 WD-guess {tenant}/{site}', (lambda h=host, t=tenant, s=site: workday(h, t, s))))
for b in ['citadelsecurities', 'citadel', 'akunacapital', 'twosigma', 'qubeResearchTechnologies', 'marshallwace', 'mako', 'millennium', 'davincitrading', 'optiverus', 'maven', 'belvederetrading', 'oldmissioncapital', 'gsa', 'g-research', 'gresearch', 'tibra', 'vivcourt', 'eclipsetrading', 'quadeye']:
    R6.append((f'R6 GH {b}', (lambda b=b: greenhouse(b))))
for b in ['citadelsecurities', 'g-research', 'qube-rt', 'mavensecurities', 'cfm', 'eclipse', 'tibra']:
    R6.append((f'R6 Lever {b}', (lambda b=b: lever(b))))
for b in ['DBSBank', 'OCBC', 'UOB', 'Maybank', 'CIMB', 'BBVA', 'ING', 'Amundi', 'AXAInvestmentManagers', 'Macquarie', 'Daiwa', 'Mizuho']:
    R6.append((f'R6 SR {b}', (lambda b=b: smartrecruiters(b))))
PROBES = PROBES + R6


# ---------------- Round 7 ----------------
def rss_count(url):
    r = get(url)
    titles = re.findall(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', r.text, re.S)
    return f'HTTP {r.status_code} final={r.url} type={r.headers.get("content-type")} items={r.text.count("<item>")} titles={[t.strip()[:70] for t in titles[:8]]}'


R7 = []
def W(name, fn, *a):
    R7.append(('R7 ' + name, lambda: fn(*a)))

for name, url in [
    ('Macquarie avature feed', 'https://recruitment.macquarie.com/en_US/careers/SearchJobs/feed/'),
    ('Macquarie avature feed2', 'https://recruitment.macquarie.com/en_US/careers/SearchJobs/feed/?listFilterMode=1&jobRecordsPerPage=100'),
    ('Two Sigma avature feed', 'https://careers.twosigma.com/careers/SearchJobs/feed/'),
    ('Two Sigma openroles feed', 'https://careers.twosigma.com/careers/OpenRoles/feed/'),
    ('Amundi talentsoft rss', 'https://jobs.amundi.com/handlers/offerRss.ashx?LCID=2057'),
    ('HSBC avature feed', 'https://mycareer.hsbc.com/en_GB/external/SearchJobs/feed/'),
]:
    W(name, rss_count, url)
W('Schroders oracle', oracle, 'ekbq.fa.em2.oraclecloud.com', 'CX_2')
W('Schroders oracle facets', oracle_facets, 'ekbq.fa.em2.oraclecloud.com', 'CX_2')
W('ANZ SF xml', lambda: (lambda t: 'count=%d sample=%s' % (t.count('<Job>'), re.findall(r'<JobTitle><!\[CDATA\[(.*?)\]\]>', t)[:6]))(get('https://career10.successfactors.com/career?company=anzbanking&career_ns=job_listing_summary&resultType=XML').text))
W('Millennium eightfold', eightfold, 'mlp.eightfold.ai', 'mlp.com', 'analyst')
W('Mizuho internship page', validate_page, 'https://www.mizuhogroup.com/emea/careers/internship-programme')
W('BBVA careers en', validate_page, 'https://www.bbva.com/en/careers/')
W('BBVA raw workday links', links, 'https://www.bbva.com/es/empleo/', r'https://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com[^"\'\s<>]*')
W('Fidelity tal', links, 'https://careers.fidelityinternational.com/', r'https://fidelityinternational\.tal\.net[^"\'\s<>]*')
W('Akuna json', lambda: (lambda d: f'type={type(d).__name__} n={len(d)} sample={str(d)[:400]}')(get('https://akunacapital.com/wp-content/uploads/akuna_jobs.json').json()))
for tenant, site in [('uob', 'UOB'), ('uob', 'uob'), ('uobgroup', 'UOBGroup'), ('maybank', 'Maybank'), ('daiwa', 'Daiwa'), ('natwest', 'NatWest'), ('rbs', 'RBS_Careers')]:
    W(f'WD guess {tenant}/{site}', workday, f'{tenant}.wd3.myworkdayjobs.com', tenant, site)
for name, url in [
    ('GTJAI careers', 'https://www.gtjai.com/en/careers'), ('GTJAI careers2', 'https://www.gtjai.com/en/about-us/careers'),
    ('CMBI careers', 'https://www.cmbi.com/en/careers'), ('CMBI join', 'https://www.cmbi.com.hk/en/join-us'),
    ('BOCI careers', 'https://www.bocigroup.com/Web/Index/joinus'), ('ICBCI careers', 'https://www.icbci.com.hk/en/careers'),
    ('GF HK careers', 'https://www.gfgroup.com.hk/en/careers'), ('SWHY careers', 'https://www.swhyhk.com/en/careers'),
    ('CGI careers', 'https://www.chinastock.com.hk/en/careers'), ('Maybank careers', 'https://www.maybank.com/en/careers/index.page'),
    ('Maybank IB careers', 'https://www.maybank.com/investment-banking/en/careers.page'), ('UOBKH careers', 'https://www.uobkh.com/careers'),
    ('Daiwa UK careers', 'https://www.uk.daiwacm.com/careers'), ('UOB careers', 'https://www.uobgroup.com/careers/index.page'),
]:
    W(name, validate_page, url)
PROBES = PROBES + R7


# ---------------- Round 8: deep crawl for every remaining employer ----------------
ATS2 = re.compile(r'https?://[a-z0-9.-]*(?:myworkdayjobs\.com|myworkdaysite\.com|oraclecloud\.com|eightfold\.ai|successfactors\.(?:com|eu)|jobs2web\.com|taleo\.net|avature\.net|brassring\.com|smartrecruiters\.com|greenhouse\.io|lever\.co|ashbyhq\.com|tal\.net|icims\.com|phenompeople\.com|beesite\.de|jobvite\.com|workable\.com|teamtailor\.com|recruitee\.com|csod\.com|zhiye\.com|mokahr\.com|hotjob\.cn|51job\.com|dayforcehcm\.com|ultipro\.com|pinpointhq\.com|bamboohr\.com|applytojob\.com|jobs\.net|kenexa\.com|peoplefluent\.com|cornerstoneondemand\.com|talentsoft\.com|softy\.pro|welcomekit\.co|welcometothejungle\.com|jobteaser\.com|clicandhire\.com|flatchr\.io|digitalrecruiters\.com|beetween\.com|erecruiter|hr-manager\.net|jobylon\.com)[^\s"\'<>)]*', re.I)
CAREER_LINK = re.compile(r'(?i)career|carri[eè]re|recrut|recruit|join[-_ ]?us|jobs?\b|vacanc|opportunit|emploi|talent|graduate|招聘|加入|人才|職位|职位|工作機會|招賢|招贤')


def crawl(root, depth=2, max_pages=14):
    from bs4 import BeautifulSoup as BS
    from urllib.parse import urljoin
    seen, queue, out, ats = set(), [(root, 0)], [], {}
    base_host = urlparse(root).hostname.split('.', 1)[-1] if root.count('.') > 1 else urlparse(root).hostname
    while queue and len(seen) < max_pages:
        url, d = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            r = get(url)
        except Exception as exc:
            out.append(f'  {url} EXC {type(exc).__name__}')
            continue
        text = r.text.replace('\\/', '/')
        for m in ATS2.findall(text):
            u = urlparse(m)
            key = u.netloc + '/' + '/'.join(u.path.strip('/').split('/')[:3])
            ats[key] = ats.get(key, 0) + 1
        out.append(f'  {r.status_code} {r.url[:110]} len={len(text)}')
        if d >= depth or r.status_code != 200:
            continue
        try:
            soup = BS(text, 'html.parser')
        except Exception:
            continue
        for a in soup.find_all('a', href=True):
            href = urljoin(r.url, a['href'])
            label = a.get_text(' ', strip=True)[:60]
            h = urlparse(href)
            if h.scheme not in ('http', 'https') or not h.hostname:
                continue
            same = base_host in h.hostname
            if CAREER_LINK.search(href) or CAREER_LINK.search(label):
                if same and href not in seen:
                    queue.append((href.split('#')[0], d + 1))
                elif not same:
                    k = h.netloc + '/' + h.path.strip('/').split('/')[0]
                    ats['LINK ' + k] = ats.get('LINK ' + k, 0) + 1
    return '\n'.join(out[:16]) + '\n  ATS/links: ' + str(sorted(ats.items(), key=lambda x: -x[1])[:25])


ROOTS = {
    'BNP Paribas': ['https://group.bnpparibas/en/', 'https://cib.bnpparibas/', 'https://www.bnpparibas.com/en/'],
    'UBS': ['https://www.ubs.com/global/en/careers.html'],
    'Natixis': ['https://recrutement.natixis.com/en', 'https://natixis.groupebpce.com/', 'https://www.groupebpce.com/carrieres/'],
    'SMBC': ['https://www.smbcgroup.com/emea/careers', 'https://www.smbcgroup.com/asia/careers', 'https://www.smbc.co.jp/global/'],
    'Mizuho': ['https://www.mizuhogroup.com/emea/careers', 'https://www.mizuhogroup.com/asia-pacific/careers'],
    'Daiwa': ['https://www.uk.daiwacm.com/', 'https://www.daiwacm.com.hk/', 'https://www.daiwa.co.jp/english/'],
    'Nomura': ['https://www.nomura.com/europe/careers/', 'https://www.nomura.com/asia/careers/', 'https://www.nomura.com/careers/'],
    'Bank of America': ['https://careers.bankofamerica.com/en-us'],
    'Citadel Securities': ['https://www.citadelsecurities.com/', 'https://www.citadel.com/'],
    'NatWest': ['https://www.natwestgroup.com/', 'https://careers.natwestgroup.com/'],
    'CICC': ['https://www.cicc.com/', 'https://en.cicc.com/', 'https://www.cicc.com.hk/'],
    'Huatai': ['https://www.htsc.com.hk/', 'https://www.htsc.com.cn/'],
    'ICBC': ['https://www.icbcasia.com/', 'https://www.icbci.com.hk/', 'https://www.icbc.com.cn/'],
    'Bank of China': ['https://www.bochk.com/en/home.html', 'https://www.bocigroup.com/', 'https://www.bankofchina.com/uk/'],
    'CCB': ['https://www.asia.ccb.com/', 'https://www.ccbintl.com.hk/'],
    'UOB Kay Hian': ['https://www.uobkayhian.com/', 'https://www.uobkh.com/careers'],
    'Maybank': ['https://www.maybank.com/', 'https://www.maybank2u.com.my/'],
    'CIMB': ['https://www.cimb.com/en/careers.html', 'https://www.cimb.com/'],
    'Guotai Junan Intl': ['https://www.gtjai.com/', 'https://www.gtjai.com/tc/'],
    'CMS / CMB Intl': ['https://www.cmschina.com.hk/', 'https://www.cmbi.com/'],
    'GF Securities HK': ['https://www.gfgroup.com.hk/'],
    'Shenwan Hongyuan HK': ['https://www.swhyhk.com/'],
    'China Galaxy Intl': ['https://www.chinastock.com.hk/'],
    'CITIC Securities': ['https://www.citics.com.hk/', 'https://www.cs.ecitic.com/'],
}
R8 = []
for name, urls in ROOTS.items():
    for u in urls:
        R8.append((f'R8 CRAWL {name} | {u}', (lambda u=u: crawl(u))))


def jpm_paris():
    base = 'https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber=CX_1001,facetsList=LOCATIONS,limit=1,keyword=%22Paris%22'
    it = get(base, headers={'Accept': 'application/json'}).json()['items'][0]
    return str([(l['Id'], l['Name'], l['TotalCount']) for l in it.get('locationsFacet', []) if re.search('France|Paris', l['Name'])]) + f' total={it.get("TotalJobsCount")}'


def sc_page():
    r = get('https://jobs.standardchartered.com/job/Markets-Intern-SG-2027/61050-en_GB?&feedid=363857')
    t = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', r.text))
    hits = re.findall(r'.{0,60}(?:Post|Clos|Date|date|Expir|Apply by).{0,80}', t)
    meta = re.findall(r'<(?:meta|span|div|p)[^>]*(?:date|Date|posted|closing)[^>]*>[^<]{0,80}', r.text)
    return f'HTTP {r.status_code} final={r.url} hits={hits[:12]} meta={meta[:10]}'


def bofa_servlet(q):
    r = get('https://careers.bankofamerica.com/services/jobssearchservlet?' + q)
    try:
        d = r.json()
    except ValueError:
        return f'HTTP {r.status_code} not json {r.text[:200]!r}'
    out = f'HTTP {r.status_code} keys={list(d.keys())}'
    for k, v in d.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            out += f' | {k}: n={len(v)} first={str(v[0])[:600]}'
        elif isinstance(v, (int, str)) and k not in ('divisions', 'areas'):
            out += f' | {k}={str(v)[:80]}'
    return out


def ubs_brassring():
    s2 = requests.Session(); s2.headers.update(S.headers)
    page = s2.get('https://jobs.ubs.com/TGnewUI/Search/Home/Home?partnerid=25008&siteid=5012', timeout=25).text
    token = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page)
    rft = re.search(r'"RFT"\s*:\s*"([^"]+)"|id="CookieValue"[^>]*value="([^"]+)"', page)
    body = {'partnerId': '25008', 'siteId': '5012', 'keyword': '', 'location': 'London', 'keywordCustomSolrFields': 'JobTitle,AutoReq,Department,Description', 'locationCustomSolrFields': 'Location', 'linkId': '', 'Latitude': 0, 'Longitude': 0, 'facetfilterfields': {'Facet': []}, 'powersearchoptions': {'PowerSearchOption': []}, 'SortType': 'LastUpdated', 'pageNumber': 1, 'encryptedSessionValue': ''}
    headers = {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}
    if token:
        headers['RFT'] = token.group(1)
    out = []
    for ep in ['ProcessSortAndShowMoreJobs', 'PowerSearchJobs']:
        r = s2.post('https://jobs.ubs.com/TgNewUI/Search/Ajax/' + ep, json=body, headers=headers, timeout=25)
        out.append(f'{ep}: HTTP {r.status_code} len={len(r.text)} head={r.text[:300]!r}')
    return f'token={bool(token)} rft={bool(rft)} ' + ' || '.join(out)


def natixis_bundle():
    page = get('https://recrutement.natixis.com/en').text
    scripts = re.findall(r'src="([^"]+\.js[^"]*)"', page)
    hits = set()
    for sc in scripts[:8]:
        u = sc if sc.startswith('http') else 'https://recrutement.natixis.com' + ('' if sc.startswith('/') else '/') + sc
        js = get(u).text
        for m in re.findall(r'https?://[a-zA-Z0-9.-]+(?:/[a-zA-Z0-9_./-]*)?', js):
            if re.search('api|offer|job|search|graphql|recrut', m, re.I):
                hits.add(m[:120])
    return f'scripts={scripts[:8]} hits={sorted(hits)[:30]} page_head={page[:400]!r}'


R8 += [
    ('R8 JPM Paris facet', jpm_paris),
    ('R8 SC page dates', sc_page),
    ('R8 BofA servlet all', lambda: bofa_servlet('start=0&rows=5&search=getAllJobs')),
    ('R8 BofA servlet Paris', lambda: bofa_servlet('start=0&rows=5&search=getAllJobs&location=Paris')),
    ('R8 BofA servlet France', lambda: bofa_servlet('start=0&rows=5&search=getAllJobs&country=France')),
    ('R8 UBS brassring', ubs_brassring),
    ('R8 Natixis bundle', natixis_bundle),
]
for tenant, wd, sites in [
    ('nomura', 'wd3', ['Nomura_Experienced', 'NomuraExperienced', 'Experienced', 'External', 'NomuraCareers', 'Nomura_External', 'nomura']),
    ('smbcgroup', 'wd3', ['SMBC_Group', 'External', 'SMBC_Careers', 'SMBCGroup', 'smbc', 'SMBC_EMEA']),
    ('natwestgroup', 'wd3', ['NatWestGroup', 'NWG', 'External', 'NatWest_Group_Careers', 'Careers']),
    ('ubs', 'wd3', ['UBS_Experienced_Professionals', 'UBS_Graduates_Interns', 'External', 'UBS']),
    ('bnpparibas', 'wd3', ['External', 'BNP_Paribas', 'Careers', 'BNPP_Careers']),
    ('mizuho', 'wd1', ['mizuhoemea', 'MizuhoEMEA', 'mizuho_emea', 'mizuhoapac', 'MizuhoAPAC', 'External']),
    ('citadel', 'wd5', ['citadelsecurities', 'Citadel', 'External', 'CitadelSecurities']),
]:
    for site in sites:
        R8.append((f'R8 WD {tenant}/{site}', (lambda t=tenant, w=wd, si=site: workday(f'{t}.{w}.myworkdayjobs.com', t, si))))
for z in ['cicc', 'cicchk', 'htsc', 'huatai', 'gtja', 'gtjai', 'citics', 'cmschina', 'gf', 'swhy', 'chinastock', 'bochk', 'boci', 'icbc', 'icbcasia']:
    R8.append((f'R8 zhiye {z}', (lambda z=z: discover(f'https://{z}.zhiye.com/'))))
PROBES = PROBES + R8


# ---------------- Round 9 ----------------
def grep_page(url, pattern, n=15, width=220):
    r = get(url)
    t = r.text.replace('\\/', '/').replace('&amp;', '&')
    return f'HTTP {r.status_code} final={r.url} len={len(t)} hits={sorted(set(m[:width] for m in re.findall(pattern, t, re.I)))[:n]}'


def sf_xml(host, company):
    r = get(f'https://{host}/career?company={company}&career_ns=job_listing_summary&resultType=XML')
    titles = re.findall(r'<JobTitle><!\[CDATA\[(.*?)\]\]>', r.text)
    vals = re.findall(r'<value>([^<]*)</value>', r.text)
    return f'HTTP {r.status_code} jobs={r.text.count("<Job>")} titles={titles[:8]} values={sorted(set(vals))[:30]}'


def zhiye(host):
    r = get(f'https://{host}/')
    t = r.text
    scripts = re.findall(r'src="([^"]+\.js[^"]*)"', t)
    apis = set(re.findall(r'["\'](/api/[A-Za-z0-9_/]+)', t))
    for sc in scripts[:6]:
        u = sc if sc.startswith('http') else f'https://{host}' + ('' if sc.startswith('/') else '/') + sc
        try:
            js = get(u).text
        except Exception:
            continue
        apis |= set(re.findall(r'["\'](/api/[A-Za-z0-9_/]+)', js))
    links = sorted(set(re.findall(r'href="(/[^"]*(?:job|social|campus|position|zhaopin|list)[^"]*)"', t, re.I)))[:15]
    return f'HTTP {r.status_code} len={len(t)} title={re.findall(r"<title>([^<]*)", t)[:1]} apis={sorted(apis)[:30]} links={links}'


def bofa_all():
    out = []
    for q in ['start=0&rows=200&search=getAllJobs', 'start=200&rows=200&search=getAllJobs']:
        d = get('https://careers.bankofamerica.com/services/jobssearchservlet?' + q).json()
        jl = d.get('jobsList') or []
        locs = {}
        for j in jl:
            for k in ('location', 'additionalLocations', 'city', 'country'):
                if j.get(k):
                    locs[str(j.get(k))[:40]] = locs.get(str(j.get(k))[:40], 0) + 1
        out.append(f'{q}: total={d.get("totalMatches")} n={len(jl)} keys={list(jl[0].keys()) if jl else []} sample_locs={sorted(locs.items(), key=lambda x:-x[1])[:12]}')
    return ' || '.join(out)


def ubs_preload():
    s2 = requests.Session(); s2.headers.update(S.headers)
    page = s2.get('https://jobs.ubs.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid=25008&siteid=5012&PageType=searchResults&SearchType=linkquery&LinkID=15231', timeout=25).text
    m = re.search(r'id="preLoadJSON"[^>]*value="([^"]*)"', page) or re.search(r'preLoad\w*\s*=\s*(\{.{0,400})', page)
    token = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page)
    hits = re.findall(r'.{60}(?:"JobTitle"|Questions|TotalJobsCount|preLoad).{160}', page)[:6]
    out = f'preload={bool(m)} token={bool(token)} hits={hits}'
    headers = {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}
    if token:
        headers['RFT'] = token.group(1)
    for body in [{'partnerId': 25008, 'siteId': 5012, 'keyword': '', 'location': '', 'Latitude': 0, 'Longitude': 0, 'pageNumber': 1, 'FacetFilterFields': {'Facet': []}, 'PowerSearchOptions': {'PowerSearchOption': []}, 'SortType': 'LastUpdated', 'encryptedSessionValue': '', 'KeywordCustomSolrFields': 'AutoReq,JobTitle,Description', 'LocationCustomSolrFields': 'FORMTEXT2,FORMTEXT1'}]:
        r = s2.post('https://jobs.ubs.com/TgNewUI/Search/Ajax/PowerSearchJobs', json=body, headers=headers, timeout=25)
        out += f' || PowerSearch: {r.status_code} {r.text[:500]!r}'
    return out


R9 = [
    ('R9 SMBC sf links', lambda: grep_page('https://www.smbcgroup.com/emea/careers', r'https?://career\d*\.successfactors\.eu/[^"\'\s<>]*')),
    ('R9 Nomura asia sf links', lambda: grep_page('https://www.nomura.com/asia/careers/', r'https?://career\d*\.successfactors\.com/[^"\'\s<>]*')),
    ('R9 CIMB oracle links', lambda: grep_page('https://www.cimb.com/en/careers.html', r'https?://ejox\.fa\.ap1\.oraclecloud\.com/[^"\'\s<>]*')),
    ('R9 Mizuho EMEA careers', lambda: crawl('https://careers.mizuhoemea.com/')),
    ('R9 Mizuho APAC FO', lambda: grep_page('https://www.mizuhogroup.com/asia-pacific/careers', r'https?://mizuhobank\.recruitmentplatform\.com/[^"\'\s<>]*')),
    ('R9 BoC UK vacancies', lambda: crawl('https://bankofchina.current-vacancies.com/Careers')),
    ('R9 CITIC careers', lambda: crawl('https://careers.citics.com/')),
    ('R9 zhiye cicc', lambda: zhiye('cicc.zhiye.com')),
    ('R9 zhiye chinastock', lambda: zhiye('chinastock.zhiye.com')),
    ('R9 BofA all jobs', bofa_all),
    ('R9 UBS preload', ubs_preload),
    ('R9 Natixis bpce', lambda: grep_page('https://recrutement.natixis.com/static/js/main.c48280da.chunk.js', r'https?://[a-z0-9.-]*(?:natixis|bpce)[a-z0-9.-]*\.[a-z]+/?[a-zA-Z0-9_/.-]*', 40, 120)),
    ('R9 HK Talent Engage job', lambda: grep_page('https://www.hkengage.gov.hk/en/jobs/7121213', r'<title>[^<]*|api[^"\'\s<>]{0,80}|"(?:/[a-z]+)*/api/[^"]*"', 20, 160)),
    ('R9 HK Talent Engage search', lambda: crawl('https://www.hkengage.gov.hk/en/jobs', depth=1, max_pages=3)),
]
for host, comp in [('career5.successfactors.eu', 'smbcP'), ('career5.successfactors.eu', 'SMBC'), ('career5.successfactors.eu', 'smbceurope'), ('career4.successfactors.com', 'nomuraP'), ('career4.successfactors.com', 'nomura')]:
    R9.append((f'R9 SF {host} {comp}', (lambda h=host, c=comp: sf_xml(h, c))))
PROBES = PROBES + R9


# ---------------- Round 10 ----------------
def sf_raw(host, company):
    t = get(f'https://{host}/career?company={company}&career_ns=job_listing_summary&resultType=XML').text
    jobs = re.findall(r'<Job>.*?</Job>', t, re.S)
    sample = [re.sub(r'<Job-Description>.*?</Job-Description>', '', j, flags=re.S)[:900] for j in jobs[:2]]
    tags = sorted(set(re.findall(r'<([A-Za-z][\w-]*)>', ''.join(jobs[:20]))))
    return f'jobs={len(jobs)} tags={tags} sample={sample}'


def bofa_paging():
    out = []
    for q in ['start=1&rows=100&search=getAllJobs', 'start=100&rows=100&search=getAllJobs', 'start=2&rows=100&search=getAllJobs', 'start=0&rows=100&search=getAllJobs&country=United%20Kingdom', 'start=0&rows=100&search=getAllJobs&keyword=London']:
        d = get('https://careers.bankofamerica.com/services/jobssearchservlet?' + q).json()
        jl = d.get('jobsList') or []
        out.append(f'{q}: total={d.get("totalMatches")} n={len(jl)} first={[ (j.get("postingTitle"), j.get("city"), j.get("country")) for j in jl[:2]]}')
    d = get('https://careers.bankofamerica.com/services/jobssearchservlet?start=0&rows=200&search=getAllJobs').json()
    j = next((x for x in d['jobsList'] if x.get('country') not in ('United States', None)), d['jobsList'][0])
    out.append('SAMPLE ' + str({k: (str(v)[:120]) for k, v in j.items() if k not in ('jobDescriptionInternal', 'benefits_internal_text', 'benefits_external_text')}))
    return '\n'.join(out)


def ubs_power(location=''):
    s2 = requests.Session(); s2.headers.update(S.headers)
    page = s2.get('https://jobs.ubs.com/TGnewUI/Search/Home/Home?partnerid=25008&siteid=5012', timeout=25).text
    token = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page).group(1)
    headers = {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', 'RFT': token}
    body = {'partnerId': 25008, 'siteId': 5012, 'keyword': '', 'location': location, 'Latitude': 0, 'Longitude': 0, 'pageNumber': 1,
            'FacetFilterFields': {'Facet': []}, 'PowerSearchOptions': {'PowerSearchOption': []}, 'SortType': 'LastUpdated',
            'encryptedSessionValue': '', 'KeywordCustomSolrFields': 'AutoReq,JobTitle,Description', 'LocationCustomSolrFields': 'FORMTEXT2,FORMTEXT1'}
    d = s2.post('https://jobs.ubs.com/TgNewUI/Search/Ajax/PowerSearchJobs', json=body, headers=headers, timeout=25).json()
    jobs = d['Jobs']['Job']
    first = {q['QuestionName']: q['Value'] for q in jobs[0]['Questions']} if jobs else {}
    body['pageNumber'] = 2
    d2 = s2.post('https://jobs.ubs.com/TgNewUI/Search/Ajax/ProcessSortAndShowMoreJobs', json=body, headers=headers, timeout=25).json()
    facets = [(f.get('Name'), [o.get('OptionName') for o in (f.get('Options') or [])][:15]) for f in ((d.get('Facets') or {}).get('Facet') or [])][:8]
    return f'total={d.get("TotalJobsCount")} count={d.get("JobsCount")} pagesize={len(jobs)} page2={len(d2["Jobs"]["Job"])} first={first} link={jobs[0].get("Link") if jobs else None} facets={facets}'


def deep_links(root, pattern, max_pages=20):
    from bs4 import BeautifulSoup as BS
    from urllib.parse import urljoin
    seen, queue, found = set(), [root], set()
    host = urlparse(root).hostname
    while queue and len(seen) < max_pages:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        try:
            r = get(u)
        except Exception:
            continue
        t = r.text.replace('\\/', '/').replace('&amp;', '&')
        found |= set(m[:200] for m in re.findall(pattern, t, re.I))
        for a in BS(t, 'html.parser').find_all('a', href=True):
            h = urljoin(r.url, a['href']).split('#')[0]
            if urlparse(h).hostname == host and CAREER_LINK.search(h):
                queue.append(h)
    return f'pages={len(seen)} found={sorted(found)[:15]}'


def zhiye_api(host):
    out = []
    tries = [('POST', '/api/Jobad/GetJobAdPageList', {'PageIndex': 0, 'PageSize': 10, 'KeyWords': '', 'SpecialType': 0}),
             ('POST', '/api/Jobad/GetJobAdPageList', {'pageIndex': 1, 'pageSize': 10}),
             ('GET', '/api/Jobad/GetJobAdPageList?PageIndex=0&PageSize=10', None),
             ('GET', '/social/jobs', None), ('GET', '/campus/jobs', None), ('GET', '/sitemap.xml', None)]
    for m, path, body in tries:
        try:
            r = S.request(m, f'https://{host}{path}', json=body, timeout=20, headers={'Content-Type': 'application/json'})
            out.append(f'{m} {path}: {r.status_code} {r.text[:200]!r}')
        except Exception as exc:
            out.append(f'{m} {path}: EXC {exc}')
    return ' || '.join(out)


R10 = [
    ('R10 SF smbcP raw', lambda: sf_raw('career5.successfactors.eu', 'smbcP')),
    ('R10 SF SMBC raw', lambda: sf_raw('career5.successfactors.eu', 'SMBC')),
    ('R10 BofA paging', bofa_paging),
    ('R10 UBS power all', lambda: ubs_power('')),
    ('R10 UBS power London', lambda: ubs_power('London')),
    ('R10 Natixis bundle natixis', lambda: grep_page('https://recrutement.natixis.com/static/js/main.c48280da.chunk.js', r'.{40}(?:natixis\.(?:recrutement|mobilite)|/api/|apiUrl|baseURL|offers).{80}', 25, 200)),
    ('R10 Nomura SF deep', lambda: deep_links('https://www.nomura.com/asia/careers/', r'https?://career\d*\.successfactors\.(?:com|eu)/[^"\'\s<>]*')),
    ('R10 Nomura EMEA deep', lambda: deep_links('https://www.nomura.com/europe/careers/', r'https?://[a-z0-9.-]*(?:successfactors|workday|oraclecloud|taleo|avature)[^"\'\s<>]*')),
    ('R10 CIMB oracle deep', lambda: deep_links('https://www.cimb.com/en/careers.html', r'https?://ejox\.fa\.ap1\.oraclecloud\.com/[^"\'\s<>]*')),
    ('R10 Mizuho EMEA sitemap', lambda: sitemap('https://careers.mizuhoemea.com/sitemap.xml')),
    ('R10 Mizuho EMEA job', lambda: sc_like('https://careers.mizuhoemea.com/job/London-FX-GroupFI-Sales-Desk-Analyst-Lond/1375125057/')),
    ('R10 zhiye cicc api', lambda: zhiye_api('cicc.zhiye.com')),
    ('R10 HKTE page apis', lambda: grep_page('https://www.hkengage.gov.hk/en/jobs/7121213', r'["\'](?:https://[a-z.]*hkengage[a-z.]*)?/(?:api|_next|graphql)[^"\']{0,120}["\']|companyName[^,]{0,80}|"employer[^,]{0,80}', 25, 160)),
    ('R10 HKTE search', lambda: grep_page('https://www.hkengage.gov.hk/en/jobs?keyword=CICC', r'/en/jobs/\d+|<title>[^<]*', 20, 80)),
    ('R10 SF maybank', lambda: sf_raw('career2.successfactors.eu', 'maybank')),
    ('R10 UOBKH text', lambda: grep_page('https://www.uobkh.com/careers', r'(?:Position|Job Title|Vacanc|Opening|Hiring)[^<]{0,120}', 15, 160)),
]


def sc_like(url):
    r = get(url)
    t = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', r.text))
    return f'HTTP {r.status_code} hits={re.findall(r".{0,40}(?:Posting Start Date|Posting End Date|Location|Date).{0,60}", t)[:8]}'


PROBES = PROBES + R10


# ---------------- Round 11 ----------------
def zhiye_items(host):
    r = S.post(f'https://{host}/api/Jobad/GetJobAdPageList', json={'PageIndex': 0, 'PageSize': 30, 'KeyWords': '', 'SpecialType': 0}, timeout=25)
    d = r.json()
    data = d.get('Data') or []
    first = {k: str(v)[:100] for k, v in (data[0].items() if data else [])}
    locs = {}
    for it in data:
        for k, v in it.items():
            if re.search('(?i)loc|place|city|addr|area', k) and v:
                locs.setdefault(k, set()).add(str(v)[:40])
    return f'count={d.get("Count")} n={len(data)} first={first} loc_fields={ {k: sorted(v)[:12] for k, v in locs.items()} }'


def natixis_calls():
    js = get('https://recrutement.natixis.com/static/js/main.c48280da.chunk.js').text
    hits = re.findall(r'.{60}(?:fetch\(|axios|\.get\(|\.post\(|XMLHttpRequest|baseURL|/wp-json|/api).{140}', js)
    return str(hits[:15])


def ubs_sites():
    out = []
    for site in ['5012', '5054', '5131']:
        s2 = requests.Session(); s2.headers.update(S.headers)
        page = s2.get(f'https://jobs.ubs.com/TGnewUI/Search/Home/Home?partnerid=25008&siteid={site}', timeout=25).text
        tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page)
        if not tok:
            out.append(f'{site}: no token'); continue
        body = {'partnerId': 25008, 'siteId': int(site), 'keyword': '', 'location': '', 'Latitude': 0, 'Longitude': 0, 'pageNumber': 1,
                'FacetFilterFields': {'Facet': []}, 'PowerSearchOptions': {'PowerSearchOption': []}, 'SortType': 'LastUpdated',
                'encryptedSessionValue': '', 'KeywordCustomSolrFields': 'AutoReq,JobTitle,Description', 'LocationCustomSolrFields': 'FORMTEXT2,FORMTEXT1'}
        d = s2.post('https://jobs.ubs.com/TgNewUI/Search/Ajax/PowerSearchJobs', json=body, headers={'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', 'RFT': tok.group(1)}, timeout=25).json()
        jobs = d['Jobs']['Job']
        qn = sorted({q['QuestionName'] for j in jobs[:5] for q in j['Questions']})
        sample = [({q['QuestionName']: q['Value'] for q in j['Questions'] if q['QuestionName'] in ('jobtitle', 'formtext23', 'formtext2', 'formtext1', 'lastupdated')}) for j in jobs[:4]]
        out.append(f'{site}: count={d.get("JobsCount")} questions={qn} sample={sample}')
    return '\n'.join(out)


R11 = [
    ('R11 zhiye cicc items', lambda: zhiye_items('cicc.zhiye.com')),
    ('R11 zhiye chinastock items', lambda: zhiye_items('chinastock.zhiye.com')),
    ('R11 Natixis calls', natixis_calls),
    ('R11 UBS sites', ubs_sites),
    ('R11 BofA window', lambda: str([(q, len(get('https://careers.bankofamerica.com/services/jobssearchservlet?' + q).json().get('jobsList') or [])) for q in ['start=200&rows=400&search=getAllJobs', 'start=0&rows=1000&search=getAllJobs', 'start=2000&rows=2200&search=getAllJobs']])),
    ('R11 Nomura careers host', lambda: crawl('https://careers.nomura.com/', depth=1, max_pages=4)),
    ('R11 Nomura holdings careers', lambda: crawl('https://www.nomuraholdings.com/en/careers/', depth=1, max_pages=6)),
    ('R11 Nomura taleo', lambda: discover('https://nomura.taleo.net/careersection/')),
    ('R11 BoC UK list', lambda: grep_page('https://bankofchina.current-vacancies.com/', r'href="([^"]*(?:Jobs/Advert|Jobs/[A-Za-z]+/\d+)[^"]*)"[^>]*>([^<]{0,100})|<h\d[^>]*>[^<]{0,100}', 20, 200)),
    ('R11 ICBC London', lambda: crawl('https://www.icbc-uk.com/', depth=1, max_pages=6)),
    ('R11 BOCHK careers', lambda: crawl('https://www.bochk.com/en/aboutus/career.html', depth=1, max_pages=6)),
]
PROBES = PROBES + R11


def natixis_wp():
    out = []
    for u in ['https://recrutement.natixis.com/wp-json/bpce/v1/search/trading/from/0',
              'https://recrutement.natixis.com/wp-json/bpce/v1/search/stage/from/0',
              'https://recrutement.natixis.com/wp-json/bpce/v1/search/%20/from/0',
              'https://recrutement.natixis.com/wp-json/bpce/v1/']:
        try:
            r = get(u)
            out.append(f'{u} -> {r.status_code} {r.headers.get("content-type")} {r.text[:1500]!r}')
        except Exception as exc:
            out.append(f'{u} -> {type(exc).__name__}: {exc}')
    return '\n'.join(out)


def zhiye_detail(host):
    ad = requests.post(f'https://{host}/api/Jobad/GetJobAdPageList', json={'PageIndex': 0, 'PageSize': 2, 'KeyWords': '', 'SpecialType': 0}, headers=S.headers, timeout=25).json()['Data'][0]
    out = [f'keys={sorted(ad)}']
    for path in [f'/zpdetail/{ad.get("JobAdId")}', f'/zpdetail/{ad.get("Id")}', f'/jobdetail/{ad.get("JobAdId")}']:
        r = get(f'https://{host}{path}')
        out.append(f'{path} -> {r.status_code} len={len(r.text)} title_in_page={(ad.get("JobAdName") or "")[:20] in r.text}')
    return '\n'.join(out)


R12 = [
    ('R12 Natixis WP API', natixis_wp),
    ('R12 zhiye detail cicc', lambda: zhiye_detail('cicc.zhiye.com')),
]
PROBES = PROBES + R12


# ---------------- Round 13: BNP Paribas and Natixis (user-supplied pages) ----------------
def all_js(page_url):
    """Main scripts of a React app plus its lazily loaded chunks."""
    base = '{0.scheme}://{0.netloc}'.format(urlparse(page_url))
    page = get(page_url).text
    srcs = [s if s.startswith('http') else base + ('' if s.startswith('/') else '/') + s
            for s in re.findall(r'src="([^"]+\.js[^"]*)"', page)]
    texts = [page] + [get(u).text for u in srcs[:10]]
    chunk_map = {}
    for t in texts:
        for m in re.finditer(r'\{((?:\d+:"[0-9a-f]{6,}",?)+)\}\[e\]\+"\.chunk\.js"', t):
            chunk_map.update(dict(re.findall(r'(\d+):"([0-9a-f]+)"', m.group(1))))
    for num, h in list(chunk_map.items())[:25]:
        texts.append(get(f'{base}/static/js/{num}.{h}.chunk.js').text)
    return srcs, chunk_map, texts


def natixis_api():
    srcs, chunk_map, texts = all_js('https://recrutement.natixis.com/en/our-job-offers?external=false')
    js = '\n'.join(texts[1:])
    urls = sorted(set(re.findall(r'https?://[a-zA-Z0-9.-]+(?:/[a-zA-Z0-9_./${}-]*)?', js)))
    ctx = sorted(set(m[:260] for m in re.findall(r'.{100}(?:wp-json|job-offers|jobOffers|offres|region|getOffers|search/).{150}', js)))
    env = sorted(set(re.findall(r'REACT_APP_[A-Z_]+:"[^"]*"', js)))
    return f'scripts={srcs} chunks={len(chunk_map)} js_len={len(js)}\nENV={env}\nURLS={urls[:80]}\nCTX=' + '\n'.join(ctx[:40])


def bnp_pages():
    out = []
    for u in ['https://group.bnpparibas/en/careers/all-job-offers',
              'https://group.bnpparibas/en/careers/all-job-offers?q=&locations=paris',
              'https://group.bnpparibas/robots.txt', 'https://group.bnpparibas/sitemap.xml',
              'https://bwelcome.hr.bnpparibas/', 'https://bwelcome.hr.bnpparibas/robots.txt',
              'https://careers.bnpparibas.com/', 'https://cib.bnpparibas/careers/']:
        try:
            r = get(u)
            snippet = re.sub(r'\s+', ' ', r.text[:600])
            out.append(f'{u} -> {r.status_code} server={r.headers.get("server")} final={r.url} len={len(r.text)} {snippet!r}')
            if r.status_code == 200:
                links_ = sorted(set(re.findall(r'https?://[a-zA-Z0-9.-]*bnpparibas[a-zA-Z0-9./_-]*(?:offer|job|career|emploi|api)[^"\'\s<>]*', r.text)))[:15]
                out.append(f'    links={links_}')
        except Exception as exc:
            out.append(f'{u} -> {type(exc).__name__}: {exc}')
    return '\n'.join(out)


R13 = [
    ('R13 Natixis API discovery', natixis_api),
    ('R13 BNP pages', bnp_pages),
]
PROBES = PROBES + R13


# ---------------- Round 14 ----------------
def bnp_sitemap():
    out = []
    idx = get('https://bwelcome.hr.bnpparibas/externalcareers/sitemap_index.xml')
    subs = re.findall(r'<loc>\s*([^<]+?)\s*</loc>', idx.text)
    out.append(f'index {idx.status_code} subs={subs[:10]} n={len(subs)}')
    locs = []
    for s in subs[:6]:
        r = get(s)
        l = re.findall(r'<loc>\s*([^<]+?)\s*</loc>', r.text)
        lm = re.findall(r'<lastmod>\s*([^<]+?)\s*</lastmod>', r.text)
        out.append(f'  {s} -> {r.status_code} locs={len(l)} sample={l[:4]} lastmod={lm[:2]}')
        locs += l
    jobs = [u for u in locs if re.search(r'JobDetail|/job/|jobid|Job', u, re.I)]
    out.append(f'job-like={len(jobs)} sample={jobs[:5]}')
    for u in jobs[:2]:
        r = get(u)
        text = re.sub(r'<[^>]+>', ' ', r.text)
        text = re.sub(r'\s+', ' ', text)
        ld = 'application/ld+json' in r.text
        out.append(f'  PAGE {u} -> {r.status_code} len={len(r.text)} jsonld={ld} title={re.findall(r"<title>([^<]*)", r.text)[:1]}')
        for lab in ['Location', 'Lieu', 'City', 'Ville', 'Country', 'Pays', 'Date', 'Contract', 'Contrat', 'Experience', 'Expérience', 'Publication']:
            m = re.search(r'.{0,60}' + lab + r'.{0,120}', text)
            if m:
                out.append(f'     [{lab}] {m.group(0)}')
    return '\n'.join(out)


def natixis_search():
    srcs, chunk_map, texts = all_js('https://recrutement.natixis.com/en/our-job-offers?external=false')
    js = '\n'.join(texts[1:])
    out = []
    out.append('NATIXIS ctx: ' + ' || '.join(sorted(set(m[:220] for m in re.findall(r'.{80}natixis.{120}', js, re.I)))[:12]))
    out.append('JE ctx: ' + ' || '.join(sorted(set(m[:400] for m in re.findall(r'.{200}je\("search/jobs".{180}', js)))[:4]))
    out.append('PARAMS ctx: ' + ' || '.join(sorted(set(m[:500] for m in re.findall(r'.{250}offre_region.{230}', js)))[:3]))
    for base in ['https://recrutement.natixis.com', 'https://recrutement.bpce.fr', 'https://natixis.recrutement.bpce.fr']:
        for kind, kw in [('json', {'json': {'params': {}}}), ('form', {'data': {}})]:
            try:
                r = S.post(f'{base}/wp-json/bpce/v1/search/jobs', timeout=25, **kw)
                out.append(f'POST {base} {kind} -> {r.status_code} {r.headers.get("content-type")} {r.text[:700]!r}')
            except Exception as exc:
                out.append(f'POST {base} {kind} -> {type(exc).__name__}')
    return '\n'.join(out)


R14 = [('R14 BNP bwelcome sitemap', bnp_sitemap), ('R14 Natixis search API', natixis_search)]
PROBES = PROBES + R14


# ---------------- Round 15 ----------------
def bnp_avature():
    out = []
    base = 'https://bwelcome.hr.bnpparibas/en_US/externalcareers'
    for u in [f'{base}/SearchJobs/?jobRecordsPerPage=20&jobOffset=0', f'{base}/SearchJobs/feed/',
              f'{base}/SearchJobs/feed/?jobRecordsPerPage=50', f'{base}/SearchJobs/?search=paris']:
        r = get(u)
        text = r.text
        jobs = sorted(set(re.findall(r'href="(https?://[^"]*JobDetail/[^"]+)"', text)))
        out.append(f'{u} -> {r.status_code} {r.headers.get("content-type")} len={len(text)} jobdetail_links={len(jobs)} sample={jobs[:4]}')
        out.append('   total? ' + str(re.findall(r'.{60}(?:results|résultats|of \d+|jobs found).{60}', re.sub(r'<[^>]+>', ' ', text), re.I)[:3]))
        if 'feed' in u:
            out.append('   feed head: ' + repr(text[:1200]))
        if jobs and 'search=' not in u:
            d = get(jobs[0])
            vis = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', re.sub(r'(?s)<script.*?</script>', ' ', d.text)))
            out.append(f'   DETAIL {d.status_code} jsonld={"ld+json" in d.text} title={re.findall(r"<title>([^<]*)", d.text)[:1]}')
            for lab in ['Location', 'City', 'Country', 'Date', 'Contract', 'Experience', 'Job Reference', 'Posted', 'Primary']:
                m = re.search(r'.{0,40}' + lab + r'.{0,140}', vis)
                if m:
                    out.append(f'     [{lab}] {m.group(0)}')
    return '\n'.join(out)


def natixis_app():
    srcs, chunk_map, texts = all_js('https://recrutement.natixis.com/en/our-job-offers?external=false')
    js = '\n'.join(texts[1:])
    out = ['DEFAULTS: ' + ' || '.join(sorted(set(m[:600] for m in re.findall(r'.{450}from:0,size:9\}', js)))[:2])]
    out.append('JE def: ' + ' || '.join(sorted(set(m[:500] for m in re.findall(r'.{120}"/wp-json/"\)\.concat\("bpce","/v1/"\)\.concat\(e\).{300}', js)))[:2]))
    base = 'https://recrutement.natixis.com/app/wp-json/bpce/v1'
    for kind, kw in [('json-params', {'json': {'params': {'from': 0, 'size': 9}}}), ('json-flat', {'json': {'from': 0, 'size': 9}}),
                     ('form', {'data': {'from': 0, 'size': 9}})]:
        try:
            r = S.post(f'{base}/search/jobs', timeout=25, **kw)
            out.append(f'POST {kind} -> {r.status_code} {r.headers.get("content-type")} {r.text[:900]!r}')
        except Exception as exc:
            out.append(f'POST {kind} -> {type(exc).__name__}')
    for u in ['https://recrutement.natixis.com/app/wp-json/', f'{base}/search/trader/from/0']:
        r = get(u)
        out.append(f'GET {u} -> {r.status_code} {r.headers.get("content-type")} {r.text[:500]!r}')
    return '\n'.join(out)


R15 = [('R15 BNP Avature search', bnp_avature), ('R15 Natixis app API', natixis_app)]
PROBES = PROBES + R15


# ---------------- Round 16 ----------------
def natixis_items():
    base = 'https://recrutement.natixis.com/app/wp-json/bpce/v1/search/jobs'
    out = []
    for ext in [False, True, None]:
        body = {'lang': 'en', 'from': 0, 'size': 50}
        if ext is not None:
            body['external'] = ext
        d = S.post(base, json=body, timeout=25).json()['data']
        out.append(f'external={ext} total={d.get("total")} items={len(d.get("items") or [])}')
    items = S.post(base, json={'lang': 'en', 'external': False, 'from': 50, 'size': 50}, timeout=25).json()['data']['items']
    it = items[0]
    out.append('keys=' + str(sorted(it)))
    out.append('sample=' + json.dumps({k: v for k, v in it.items() if k != 'description'}, ensure_ascii=False)[:1500])
    out.append('locs=' + str([(i.get('title'), i.get('city'), i.get('country'), i.get('place'), i.get('brand')) for i in items[:12]]))
    return '\n'.join(out)


def bnp_variants():
    out = []
    for u in ['https://bwelcome.hr.bnpparibas/externalcareers/SearchJobs', 'https://bwelcome.hr.bnpparibas/en_GB/externalcareers/SearchJobs',
              'https://bwelcome.hr.bnpparibas/fr_FR/externalcareers/SearchJobs', 'https://bwelcome.hr.bnpparibas/externalcareers',
              'https://bwelcome.hr.bnpparibas/portal/15', 'https://bwelcome.hr.bnpparibas/en_US/externalcareers/SearchJobs']:
        r = get(u)
        links = sorted(set(re.findall(r'(?:href|data-url)="([^"]*(?:JobDetail|jobId|SearchJobs)[^"]*)"', r.text)))
        vis = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', re.sub(r'(?s)<(script|style).*?</\1>', ' ', r.text)))
        out.append(f'{u} -> {r.status_code} final={r.url} len={len(r.text)} links={links[:8]} text={vis[:400]!r}')
    return '\n'.join(out)


R16 = [('R16 Natixis items', natixis_items), ('R16 BNP variants', bnp_variants)]
PROBES = PROBES + R16


# ---------------- Round 17 ----------------
def bnp_robots():
    r = get('https://bwelcome.hr.bnpparibas/robots.txt')
    out = ['ROBOTS:\n' + r.text]
    for sm in re.findall(r'Sitemap:\s*(\S+)', r.text):
        idx = get(sm)
        subs = re.findall(r'<loc>\s*([^<]+?)\s*</loc>', idx.text)
        en = [s for s in subs if 'en_' in s or s.count('/') <= 4] or subs[:1]
        for s in en[:2]:
            locs = re.findall(r'<loc>\s*([^<]+?)\s*</loc>', get(s).text)
            jobs = [l for l in locs if re.search(r'/\d{3,}', l)]
            out.append(f'{sm} -> {s}: locs={len(locs)} with-id={len(jobs)} sample={jobs[:5] or locs[:6]}')
    return '\n'.join(out)


R17 = [('R17 BNP robots portals', bnp_robots)]
PROBES = PROBES + R17

if __name__ == '__main__':
    only = sys.argv[1] if len(sys.argv) > 1 else ''
    for name, fn in PROBES:
        if only.lower() in name.lower():
            show(name, fn)
            time.sleep(0.3)
