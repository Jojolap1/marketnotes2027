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

if __name__ == '__main__':
    only = sys.argv[1] if len(sys.argv) > 1 else ''
    for name, fn in PROBES:
        if only.lower() in name.lower():
            show(name, fn)
            time.sleep(0.3)
