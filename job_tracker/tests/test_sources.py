import re
import json
import unittest
from datetime import datetime, timezone

from tests.helpers import context
from sources import fetch_source
from sources.sitemap import parse_detail

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)

WD_FACETS = {'total': 2000, 'jobPostings': [], 'facets': [
    {'facetParameter': 'Country_and_Jurisdiction', 'values': [
        {'id': 'uk', 'descriptor': 'United Kingdom', 'count': 160}, {'id': 'sg', 'descriptor': 'Singapore', 'count': 261},
        {'id': 'us', 'descriptor': 'United States of America', 'count': 900}]},
    {'facetParameter': 'locations', 'values': [{'id': 'l1', 'descriptor': 'London  United Kingdom', 'count': 40}]}]}


def wd_route(method, url, payload):
    if url.endswith('/jobs'):
        if payload['limit'] == 1:
            return WD_FACETS
        assert payload['appliedFacets'] == {'Country_and_Jurisdiction': ['uk', 'sg']}, payload
        page = [
            {'title': 'Markets – Sales and Trading, Summer Analyst, London – United Kingdom, 2027',
             'externalPath': '/job/London/ST_26987141', 'locationsText': 'London  United Kingdom', 'bulletFields': ['26987141']},
            {'title': 'Senior Software Engineer', 'externalPath': '/job/London/SWE_1', 'locationsText': 'London  United Kingdom'},
            {'title': 'FX Sales Graduate 2027', 'externalPath': '/job/Glasgow/FX_2', 'locationsText': 'Glasgow  United Kingdom'},
            {'title': 'Rates Trading Off-Cycle Intern', 'externalPath': '/job/Multi/R_3', 'locationsText': '2 Locations'},
        ] if payload['offset'] == 0 else []
        return {'total': 4 if payload['offset'] == 0 else 0, 'jobPostings': page}
    if 'ST_26987141' in url:
        return {'jobPostingInfo': {'title': 'Markets – Sales and Trading, Summer Analyst, London – United Kingdom, 2027',
                                   'location': 'London  United Kingdom', 'jobDescription': '<p>Penultimate year students.</p>',
                                   'startDate': '2026-10-15', 'endDate': '2026-10-25', 'canApply': True, 'jobReqId': '26987141',
                                   'timeType': 'Full time', 'externalUrl': 'https://citi.wd5.myworkdayjobs.com/2/job/London/ST_26987141'}}
    if 'R_3' in url:
        return {'jobPostingInfo': {'title': 'Rates Trading Off-Cycle Intern', 'location': 'Singapore',
                                   'additionalLocations': ['Hong Kong'], 'jobDescription': 'x', 'endDate': '2026-10-01',
                                   'canApply': True, 'jobReqId': 'R3'}}
    return (404, {})


class WorkdayTests(unittest.TestCase):
    def test_location_facets_listing_screening_and_status(self):
        ctx = context({'citi.wd5': wd_route}, now=NOW)
        jobs = fetch_source({'company': 'Citi', 'platform': 'workday', 'host': 'citi.wd5.myworkdayjobs.com',
                             'tenant': 'citi', 'site': '2', 'countries': ['United Kingdom', 'Singapore']}, ctx)
        by_title = {j['title']: j for j in jobs}
        st = by_title['Markets – Sales and Trading, Summer Analyst, London – United Kingdom, 2027']
        self.assertEqual(st['status'], 'open')
        self.assertIn('2026-10-25', st['status_evidence'])
        self.assertEqual(st['id'], 'workday:citi:26987141')
        self.assertEqual(st['published'], '2026-10-15')
        # Multi-location posting resolved through its detail page; expired end date -> closed.
        self.assertEqual(by_title['Rates Trading Off-Cycle Intern']['status'], 'closed')
        self.assertIn('Hong Kong', by_title['Rates Trading Off-Cycle Intern']['location'])
        # Senior engineer and Glasgow job never got a detail request.
        urls = [u for _, u, _ in ctx.http.calls]
        self.assertFalse(any('SWE_1' in u or 'FX_2' in u for u in urls))

    def test_detail_cache_avoids_repeat_requests(self):
        ctx = context({'citi.wd5': wd_route}, now=NOW)
        src = {'company': 'Citi', 'platform': 'workday', 'host': 'citi.wd5.myworkdayjobs.com', 'tenant': 'citi', 'site': '2',
               'countries': ['United Kingdom', 'Singapore']}
        fetch_source(src, ctx)
        first = len(ctx.http.calls)
        fetch_source(src, ctx)
        self.assertEqual(len(ctx.http.calls) - first, first - 2)  # only listing calls repeat


ORACLE_LIST = {'items': [{'TotalJobsCount': 2, 'locationsFacet': [
    {'Id': 1, 'Name': 'LONDON, United Kingdom', 'TotalCount': 483}, {'Id': 2, 'Name': 'LONDON, LONDON, United Kingdom', 'TotalCount': 306},
    {'Id': 3, 'Name': 'Hong Kong', 'TotalCount': 103}, {'Id': 4, 'Name': 'New York, NY, United States', 'TotalCount': 900}],
    'requisitionList': []}]}


def oracle_route(method, url, payload):
    if 'facetsList=LOCATIONS' in url:
        return ORACLE_LIST
    if 'recruitingCEJobRequisitionDetails' in url:
        return {'items': [{'ExternalDescriptionStr': '<p>Summer internship for students.</p>', 'ExternalQualificationsStr': '',
                           'ExternalPostedEndDate': None, 'JobSchedule': 'Full time'}]}
    if 'selectedLocationsFacet=1' in url and 'offset=0' in url:
        return {'items': [{'TotalJobsCount': 2, 'requisitionList': [
            {'Id': '210001', 'Title': '2027 Markets Summer Analyst Program - London', 'PrimaryLocation': 'LONDON, United Kingdom',
             'PostedDate': '2026-10-10', 'PostingEndDate': None, 'ShortDescriptionStr': 'Sales and trading'},
            {'Id': '210002', 'Title': 'Rates Trading - Vice President', 'PrimaryLocation': 'LONDON, United Kingdom'}]}]}
    return {'items': [{'TotalJobsCount': 0, 'requisitionList': []}]}


class OracleTests(unittest.TestCase):
    def test_city_facets_and_jobs(self):
        ctx = context({'jpmc.fa': oracle_route}, now=NOW)
        jobs = fetch_source({'company': 'JPMorgan', 'platform': 'oracle', 'host': 'jpmc.fa.oraclecloud.com', 'site': 'CX_1001'}, ctx)
        self.assertEqual([j['title'] for j in jobs], ['2027 Markets Summer Analyst Program - London'])
        self.assertEqual(jobs[0]['url'], 'https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210001')
        self.assertEqual(jobs[0]['status'], 'open')
        listing_calls = [u for _, u, _ in ctx.http.calls if 'selectedLocationsFacet' in u]
        self.assertTrue(any('selectedLocationsFacet=1,' in u for u in listing_calls))   # broadest London facet
        self.assertFalse(any('selectedLocationsFacet=2,' in u or 'selectedLocationsFacet=4,' in u for u in listing_calls))


class AtsTests(unittest.TestCase):
    def test_greenhouse(self):
        ctx = context({'boards-api.greenhouse.io': {'jobs': [
            {'id': 1, 'title': 'Graduate Trader', 'location': {'name': 'Hong Kong'}, 'absolute_url': 'https://x/1',
             'content': '&lt;p&gt;Graduates&lt;/p&gt;', 'first_published': '2026-10-01T00:00:00Z'}]}})
        jobs = fetch_source({'company': 'Flow Traders', 'platform': 'greenhouse', 'board': 'flowtraders'}, ctx)
        self.assertEqual(jobs[0]['id'], 'greenhouse:flowtraders:1')
        self.assertEqual(jobs[0]['description'], 'Graduates')
        self.assertEqual(jobs[0]['status'], 'open')

    def test_workable_merges_locations_of_one_posting(self):
        ctx = context({'apply.workable.com': {'jobs': [
            {'shortcode': 'A1', 'title': '2027 Trading and Research Summer Internship', 'city': 'London', 'country': 'United Kingdom',
             'published_on': '2026-04-30', 'url': 'https://apply.workable.com/capula/j/A1/'},
            {'shortcode': 'A1', 'title': '2027 Trading and Research Summer Internship', 'city': 'Singapore', 'country': 'Singapore'}]}})
        jobs = fetch_source({'company': 'Capula', 'platform': 'workable', 'board': 'capula'}, ctx)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['location'], 'London, United Kingdom; Singapore, Singapore')
        self.assertEqual(jobs[0]['id'], 'workable:capula:A1')

    def test_bamboohr_lists_and_reads_detail_of_candidates(self):
        def route(method, url, payload):
            if url.endswith('/careers/list'):
                return {'result': [
                    {'id': '7', 'jobOpeningName': 'Graduate Equity Sales Trader', 'employmentStatusLabel': 'Full-Time',
                     'location': {'city': 'London', 'state': None}},
                    {'id': '8', 'jobOpeningName': 'Office Manager', 'location': {'city': 'London'}}]}
            return {'result': {'jobOpening': {'description': '<p>Graduates welcome</p>', 'datePosted': '2026-10-15'}}}
        ctx = context({'panmure.bamboohr.com': route}, now=NOW)
        jobs = fetch_source({'company': 'Panmure Liberum', 'platform': 'bamboohr', 'board': 'panmure'}, ctx)
        self.assertEqual([j['id'] for j in jobs], ['bamboohr:panmure:7', 'bamboohr:panmure:8'])
        self.assertEqual(jobs[0]['description'], 'Graduates welcome')
        self.assertEqual(jobs[0]['url'], 'https://panmure.bamboohr.com/careers/7')
        self.assertEqual(len([u for _, u, _ in ctx.http.calls if '/detail' in u]), 1)

    def test_avature_undated_listing_read_fully_and_cached(self):
        def art(i, title, office):
            return (f'<article class="article article--result clearfix"><h3><a class="link" '
                    f'href="https://mq/en_US/careers/JobDetail?jobId={i}"> {title} </a></h3>'
                    f'<p class="icon">ID</p><p> {i} </p><p> {office} </p></article>')
        pages = {0: [art(1, 'Analyst - Commodity Markets Graduate', 'London Office'), art(2, 'Graduate Trader', 'Sydney Office')],
                 2: [art(3, 'Fixed Income Sales Intern', 'Singapore Office'), art(4, 'HR Manager', 'London Office')], 4: []}
        def route(method, url, payload):
            if 'JobDetail' in url:
                return '<html>Job Description Graduate programme for recent graduates.</html>'
            return '<section>' + ''.join(pages[int(url.rsplit('jobOffset=', 1)[1])]) + '</section>'
        src = {'company': 'Macquarie', 'platform': 'avature', 'base_url': 'https://mq/en_US/careers', 'listing_refresh_hours': 4}
        ctx = context({'mq/': route}, now=NOW)
        jobs = fetch_source(src, ctx)
        self.assertEqual(sorted(j['title'] for j in jobs), ['Analyst - Commodity Markets Graduate', 'Fixed Income Sales Intern'])
        self.assertEqual(sorted(j['id'] for j in jobs), ['avature:1:Macquarie', 'avature:3:Macquarie'])
        self.assertEqual(len([u for _, u, _ in ctx.http.calls if 'jobOffset' in u]), 3)   # whole listing read
        ctx.http.calls.clear()
        fetch_source(src, ctx)
        self.assertEqual([u for _, u, _ in ctx.http.calls if 'jobOffset' in u], [])          # listing reused (4 h cache)

    def test_avature_table_layout_and_plain_links(self):
        rows = ('<table><tbody><tr><th><a class="link fw--500" data-map="job-detail-link" '
                'href="https://nbc/en_CA/careers/JobDetail/X/36299" title="T">Fixed Income Trading Intern</a></th>'
                '<td> London, United Kingdom </td><td>On-Site</td></tr>'
                '<tr><th><a class="link" href="https://nbc/en_CA/careers/JobDetail/Y/36300">Banking Advisor</a></th>'
                '<td>Toronto, Ontario</td></tr></tbody></table>')
        def route(method, url, payload):
            if 'JobDetail' in url:
                return '<html>Job Description Internship for students.</html>'
            return rows if url.endswith('jobOffset=0') else '<table></table>'
        ctx = context({'nbc/': route}, now=NOW)
        jobs = fetch_source({'company': 'National Bank of Canada', 'platform': 'avature', 'base_url': 'https://nbc/en_CA/careers'}, ctx)
        self.assertEqual([j['title'] for j in jobs], ['Fixed Income Trading Intern'])
        self.assertIn('London', jobs[0]['location'])

    def test_avature_stops_at_date_horizon(self):
        def page(dates):
            return '<section>' + ''.join(
                f'<div class="article article--result"><h3><a class="link" href="https://t/JobDetail/x/{d}{i}">Ops {i}</a></h3>'
                f'<ul><li class="list-item list-item-jobCreationDate">{d}</li></ul></div>' for i, d in enumerate(dates)) + '</section>'
        ctx = context([('jobOffset=0', page(['15-10-2026'] * 20)), ('jobOffset=20', page(['20-09-2026'] * 20)),
                       ('jobOffset=40', page(['01-09-2026'] * 20))], now=NOW)
        fetch_source({'company': 'TotalEnergies', 'platform': 'avature', 'base_url': 'https://t'}, ctx)
        requested = [u for _, u, _ in ctx.http.calls if 'SearchJobs' in u]
        self.assertEqual(len(requested), 2)          # page 2 is all older than 16 days: stop there

    def test_avature_listing_and_detail(self):
        def art(n, title, country, date='16-10-2026'):
            return (f'<div class="article article--result" id="article--{n}"><h3><a class="link" '
                    f'href="https://jobs.totalenergies.com/en_US/careers/JobDetail/x/{n}">\n {title}\n</a></h3>'
                    f'<ul><li class="list-item list-item-jobCreationDate">{date}</li> '
                    f'<li class="list-item list-item-jobCountry">{country}</li></ul></div>')
        listing = ('<section id="jobs">' + art(1, 'Graduate Programme - Trading Analyst', 'United Kingdom')
                   + art(2, 'Technicien maintenance', 'France') + art(3, 'Trading Intern', 'Brazil') + '</section>')
        detail = ('<html><body>Job Description Graduate Programme - Trading Analyst Country United Kingdom City LONDON '
                  'Area London Employer company TotalEnergies Gas & Power Ltd Domain Trading Type of contract Graduate '
                  'Experience Less than 1 year Context & Environment Join our trading floor.</body></html>')
        ctx = context([('SearchJobs/?jobRecordsPerPage=20&jobOffset=0', listing), ('SearchJobs/', '<section></section>'),
                       ('JobDetail/x/1', detail)])
        jobs = fetch_source({'company': 'TotalEnergies', 'platform': 'avature', 'base_url': 'https://jobs.totalenergies.com/en_US/careers',
                             'countries': ['United Kingdom', 'France']}, ctx)
        self.assertEqual([j['title'] for j in jobs], ['Graduate Programme - Trading Analyst'])
        self.assertEqual(jobs[0]['location'], 'London, United Kingdom')
        self.assertEqual(jobs[0]['published'], '2026-10-16')
        self.assertEqual(jobs[0]['employment_type'], 'Graduate')
        self.assertIn('Experience: Less than 1 year', jobs[0]['description'])

    def test_business_france_vie(self):
        page = '<script>window.__NUXT__={config:{MAPS:{API_KEY:"AIzaXYZ"},API_KEY:"pub\\u002Fkey="}}</script>'
        def api(method, url, payload):
            items = [
                {'id': 245487, 'organizationName': 'SOCIETE GENERALE', 'missionTitle': 'Assistant Trader Equity Derivatives (H/F)',
                 'missionDuration': 12, 'cityNameEn': 'LONDRES', 'countryNameEn': 'UNITED KINGDOM', 'reference': 'VIE245487',
                 'startBroadcastDate': '2026-10-15T00:00:00', 'endBroadcastDate': '2099-01-13T00:00:00',
                 'missionDescription': 'Support the trading desk.'},
                {'id': 2, 'organizationName': 'X', 'missionTitle': 'Junior Trader', 'cityNameEn': 'NEW YORK', 'countryNameEn': 'UNITED STATES'},
            ] if payload['skip'] == 0 else []
            return {'result': items}
        ctx = context([('mon-vie-via.businessfrance.fr/en/offres/recherche', page), ('civiweb-api-prd', api)])
        jobs = fetch_source({'company': 'Business France (VIE)', 'platform': 'business_france',
                             'countries': ['United Kingdom', 'Singapore']}, ctx)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job['company'], 'Societe Generale')
        self.assertEqual(job['title'], 'Assistant Trader Equity Derivatives (H/F) (VIE)')
        self.assertEqual(job['location'], 'Londres, United Kingdom')
        self.assertEqual(job['published'], '2026-10-15')
        self.assertEqual(job['url'], 'https://mon-vie-via.businessfrance.fr/offres/245487')
        self.assertEqual(ctx.http.calls[1][2], {'limit': 100, 'skip': 0})
        from filters import Classifier
        import tracker
        self.assertTrue(Classifier(tracker.CONFIG).classify(job).eligible)

    def test_successfactors_xml_skips_broken_job(self):
        xml = ('<?xml version="1.0"?><Jobs>'
               '<Job><JobTitle>Graduate Trader R&D</JobTitle><ReqId>1</ReqId><>213</><Location>London</Location>'
               '<Job-Description>Graduate programme.</Job-Description></Job>'
               '<Job><JobTitle>Graduate Trading Analyst</JobTitle><ReqId>2</ReqId><>9</><Location>Paris</Location></Job>'
               '<Job><JobTitle>Broken <b></JobTitle></Job></Jobs>')
        ctx = context({'career5.successfactors.eu': xml})
        jobs = fetch_source({'company': 'RWE', 'platform': 'successfactors_xml', 'host': 'career5.successfactors.eu',
                             'sf_company': 'rweProd'}, ctx)
        self.assertEqual(sorted(j['title'] for j in jobs), ['Graduate Trader R&D', 'Graduate Trading Analyst'])

    def test_bpce_natixis_search_api(self):
        def route(method, url, payload):
            items = [
                {'job_number': '22301', 'title': 'Internship - 6 months - Equity Derivatives Sales F/H', 'date': '2026-10-12',
                 'link': {'url': '/en/job/internship-equity-derivatives-sales'}, 'contract': ['Internship'],
                 'brand': ['Natixis CIB France'], 'description': '<p>Students in Master 2.</p>',
                 'postulate_link': {'url': 'https://ekez.fa.em2.oraclecloud.com/apply/22301'},
                 'localisations': [{'city': 'Paris', 'country': 'France'}]},
                {'job_number': '22255', 'title': 'Internal Audit - Data investigation', 'date': '2026-10-07',
                 'link': {'url': '/en/job/audit'}, 'localisations': [{'city': 'Paris', 'country': 'France'}]},
                {'job_number': '22400', 'title': 'Rates Trading Intern', 'date': '2026-10-07',
                 'link': {'url': '/en/job/ny'}, 'localisations': [{'city': 'New York', 'country': 'United States'}]},
            ] if payload['from'] == 0 else []
            return {'data': {'total': 3, 'items': items}}
        ctx = context({'recrutement.natixis.com': route})
        jobs = fetch_source({'company': 'Natixis CIB', 'platform': 'bpce', 'base_url': 'https://recrutement.natixis.com'}, ctx)
        self.assertEqual([j['id'] for j in jobs], ['bpce:22301'])
        self.assertEqual(jobs[0]['url'], 'https://recrutement.natixis.com/en/job/internship-equity-derivatives-sales')
        self.assertEqual(jobs[0]['location'], 'Paris, France')
        self.assertEqual(jobs[0]['published'], '2026-10-12')
        self.assertEqual(jobs[0]['employment_type'], 'Internship')

    def test_eightfold_pagination(self):
        def route(method, url, payload):
            if '/jobs/77' in url:
                return {'job_description': '<p>Internship for students</p>', 'apply_redirect_url': 'https://apply/77'}
            if 'location=Paris' in url and 'start=0' in url:
                return {'count': 11, 'positions': [{'id': i, 'name': f'Back Office Clerk {i}', 'locations': ['PARIS, Paris, France']} for i in range(10)]}
            if 'location=Paris' in url and 'start=10' in url:
                return {'count': 11, 'positions': [{'id': 77, 'name': 'Stages en Vente/Sales - Clients Institutionnels (f/m/d)',
                                                    'locations': ['PARIS, Paris, France'], 'display_job_id': '63779', 't_create': 1792158709,
                                                    'canonicalPositionUrl': 'https://portal.careers.hsbc.com/careers/job/77'}]}
            return {'count': 0, 'positions': []}
        ctx = context({'portal.careers.hsbc.com': route})
        jobs = fetch_source({'company': 'HSBC', 'platform': 'eightfold', 'host': 'portal.careers.hsbc.com', 'domain': 'hsbc.com',
                             'locations': ['Paris']}, ctx)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['id'], 'eightfold:portal.careers.hsbc.com:63779')
        self.assertEqual(jobs[0]['description'], 'Internship for students')

    def test_goldman(self):
        def route(method, url, payload):
            exp = payload['variables']['searchQueryInput']['experiences']
            items = [{'roleId': '187040_GS_CAMPUS', 'corporateTitle': 'Analyst', 'status': 'POSTED',
                      'jobTitle': '2027 | EMEA | London | Global Banking & Markets | Summer Analyst',
                      'locations': [{'city': 'London', 'country': 'United Kingdom'}], 'externalSource': {'sourceId': '187040'},
                      'startDate': '2026-10-16T10:00:00Z', 'jobType': {'description': 'Internship'}},
                     {'roleId': '9_GS_CAMPUS', 'corporateTitle': 'Analyst', 'status': 'POSTED',
                      'jobTitle': '2027 | Americas | New York | Global Banking & Markets | Summer Analyst',
                      'locations': [{'city': 'New York', 'country': 'United States'}], 'externalSource': {'sourceId': '9'}}]
            if exp == ['CAMPUS'] and payload['variables']['searchQueryInput']['page']['pageNumber'] == 0:
                return {'data': {'roleSearch': {'totalCount': 2, 'items': items}}}
            return {'data': {'roleSearch': {'totalCount': 0, 'items': []}}}
        ctx = context({'api-higher.gs.com': route})
        jobs = fetch_source({'company': 'Goldman Sachs', 'platform': 'goldman'}, ctx)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['url'], 'https://higher.gs.com/roles/187040')
        from filters import Classifier
        import tracker
        self.assertTrue(Classifier(tracker.CONFIG).classify(jobs[0]).eligible)

    def test_successfactors_xml(self):
        xml = ('<Jobs><Job><JobTitle><![CDATA[Asset Management Intern]]></JobTitle><ReqId>42</ReqId>'
               '<Job-Description><![CDATA[<p>Internship</p>]]></Job-Description>'
               '<filter1><label>Country</label><value>United Kingdom</value></filter1>'
               '<filter2><label>City</label><value>London</value></filter2>'
               '<filter3><label>Experience level</label><value>Students</value></filter3></Job></Jobs>')
        ctx = context({'career012': xml})
        jobs = fetch_source({'company': 'Pictet', 'platform': 'successfactors_xml', 'host': 'career012.successfactors.eu',
                             'sf_company': 'banquepict'}, ctx)
        self.assertEqual(jobs[0]['location'], 'London, United Kingdom')
        self.assertIn('career_job_req_id=42', jobs[0]['url'])

    def test_rss(self):
        rss = ('<rss><channel><item><link>https://jobs.ca-cib.com/Pages/Offre/detailoffre.aspx?idOffre=1&amp;LCID=2057</link>'
               '<category>Types of Jobs/Corporate &amp; Investment Banking</category><category>VIE Programme</category>'
               '<category>London</category><title>2026-1 - VIE - Credit Trading Assistant H/F</title>'
               '<description>&lt;b&gt;Contract&lt;/b&gt; VIE</description></item></channel></rss>')
        ctx = context({'ca-cib': rss})
        jobs = fetch_source({'company': 'Crédit Agricole CIB', 'platform': 'rss', 'feeds': ['https://jobs.ca-cib.com/rss']}, ctx)
        self.assertEqual(jobs[0]['title'], 'VIE - Credit Trading Assistant H/F')
        self.assertIn('VIE', jobs[0]['employment_type'])

    def test_rss_location_from_teamtailor_tags_and_link_slug(self):
        tt = ('<rss xmlns:tt="https://teamtailor.com/locations"><channel><item><title>Equity Sales Trader Graduate</title>'
              '<link>https://pareto.teamtailor.com/jobs/1-equity</link><tt:locations><tt:location><tt:city>London</tt:city>'
              '</tt:location></tt:locations></item></channel></rss>')
        earcu = ('<rss><channel><item><title>Graduate Sales Trader</title>'
                 '<link>https://careers.investec.co.uk/jobs/vacancy/graduate-sales-trader-14238-london---30-gresham-street/14256/description/</link>'
                 '</item></channel></rss>')
        ctx = context({'teamtailor': tt, 'investec': earcu})
        jobs = fetch_source({'company': 'Pareto Securities', 'platform': 'rss', 'feeds': ['https://pareto.teamtailor.com/jobs.rss']}, ctx)
        self.assertEqual(jobs[0]['location'], 'London')
        src = {'company': 'Investec', 'platform': 'rss', 'feeds': ['https://careers.investec.co.uk/jobs/rss'], 'location_in_link': True}
        self.assertEqual(len(fetch_source(src, ctx)), 1)
        self.assertEqual(fetch_source(dict(src, location_in_link=False), ctx), [])

    def test_rmk_city_searches_drop_homonym_cities(self):
        page = ('<a class="jobTitle-link" href="/job/London-%28UK%29-2027-Global-Markets-Summer-Internship-ENG/606718117/">2027 Global Markets Summer Internship</a>'
                '<a class="jobTitle-link" href="/job/London-%28UK%29-2027-Global-Markets-Summer-Internship-ENG/606718117/">2027 Global Markets Summer Internship</a>'
                '<a class="jobTitle-link" href="/job/London-Graduate-Trader-ON-N6A-4K1/600000001/">Graduate Trader</a>')
        detail = '<span itemprop="addressLocality">London</span><span itemprop="description"><p>Students welcome</p></span></div>'
        ctx = context([('/job/', detail), ('/search/', page)], now=NOW)
        src = {'company': 'Scotiabank', 'platform': 'rmk', 'base_url': 'https://jobs.scotiabank.com',
               'location_searches': ['London'], 'exclude_url_pattern': r'-(?:ON|QC|BC|AB)-'}
        jobs = fetch_source(src, ctx)
        self.assertEqual([j['id'] for j in jobs], ['rmk:Scotiabank:606718117'])
        self.assertEqual(jobs[0]['location'], 'London')
        self.assertEqual(jobs[0]['description'], 'Students welcome')

    def test_html_cards_opens_only_target_city_candidates(self):
        card = ('<a href="/careers/career-opportunities/{id}"><div><h4 class="h4">{t}</h4><div class="MuiStack-root">'
                '<div class="MuiTypography-root MuiTypography-h6 x">{c}</div></div></div></a>')
        page = (card.format(id='a-vn1', t='APAC Prime Services Intern (VN1)', c='Hong Kong')
                + card.format(id='b-vn2', t='Cost Accountant', c='London')
                + card.format(id='c-vn3', t='Graduate Trader', c='Chicago'))
        ctx = context([('vn1', '<main>Internship for students</main>'), ('career-opportunities', page)], now=NOW)
        src = {'company': 'Marex', 'platform': 'html_cards', 'listing_url': 'https://www.marex.com/careers/career-opportunities',
               'card_pattern': r'href="(?P<url>/careers/career-opportunities/[^"]+)"><div[^>]*><h4[^>]*>(?P<title>.*?)</h4>.*?MuiTypography-h6[^"]*">(?P<location>[^<]*)<'}
        jobs = fetch_source(src, ctx)
        self.assertEqual([j['title'] for j in jobs], ['APAC Prime Services Intern (VN1)'])
        self.assertEqual(jobs[0]['location'], 'Hong Kong')
        self.assertEqual(len([u for _, u, _ in ctx.http.calls if 'vn' in u]), 1)

    def test_talentsoft_reads_every_list_page_until_it_wraps(self):
        def card(i, title, place):
            return (f'<div class="ts-offer-card Layer"><h3><a class="ts-offer-card__title-link" href="/job/job-x_{i}.aspx" '
                    f'title="2026-{i}">{title}</a></h3><ul><li>2026-{i}</li><li>14/10/2026</li><li>Internship</li><li>{place}</li></ul></div>')
        pages = {1: card(1, 'Global Markets Sales Intern', 'United Kingdom, London') + card(2, 'HR Intern', 'France, Paris'),
                 2: card(3, 'Rates Trading VIE', 'Singapore'), 3: card(1, 'Global Markets Sales Intern', 'United Kingdom, London')}
        def route(method, url, payload):
            if 'list-of-jobs' in url:
                return '<main>' + pages[int(re.search(r'page=(\d+)', url).group(1))] + '</main>'
            return '<main>Internship for students</main>'
        ctx = context({'jobs.ca-cib.com': route}, now=NOW)
        jobs = fetch_source({'company': 'Crédit Agricole CIB', 'platform': 'talentsoft', 'base_url': 'https://jobs.ca-cib.com'}, ctx)
        self.assertEqual(sorted(j['title'] for j in jobs), ['Global Markets Sales Intern', 'Rates Trading VIE'])
        self.assertEqual({j['location'] for j in jobs}, {'London', 'Singapore'})
        self.assertEqual(jobs[0]['published'], '2026-10-14')
        self.assertEqual(len([u for _, u, _ in ctx.http.calls if 'list-of-jobs' in u]), 3)

    def test_oleeo(self):
        board = ('<a href="/vx/x/opp/1449-2027-Global-Markets-Summer-Internship-London/en-GB">2027 Global Markets Summer Internship - London</a>'
                 '<a href="/vx/x/opp/1450-2027-IB-Summer-Internship-London/en-GB">2027 Investment Banking Summer Internship - London</a>'
                 '<a href="/vx/x/opp/1451-2027-Global-Markets-Summer-Internship-Paris/en-GB">2027 Global Markets Summer Internship - Paris</a>')
        detail_open = '<meta name="description" content="Title: X. Location: London. Application Deadline: 01/12/2026, 23:55"><main>Apply</main>'
        detail_closed = '<meta name="description" content="Title: X. Location: Paris. Application Deadline: 01/09/2026, 23:55"><main>Apply</main>'
        ctx = context([('opp/1449', detail_open), ('opp/1451', detail_closed), ('jobboard', board)], now=NOW)
        jobs = fetch_source({'company': 'Nomura', 'platform': 'oleeo', 'boards': ['https://nomuracampus.tal.net/vx/jobboard/vacancy/1/adv/']}, ctx)
        by_id = {j['requisition_id']: j for j in jobs}
        self.assertEqual(set(by_id), {'1449', '1451'})      # IB filtered before any detail request
        self.assertEqual(by_id['1449']['status'], 'open')
        self.assertIn('2026-12-01', by_id['1449']['status_evidence'])
        self.assertEqual(by_id['1451']['status'], 'closed')

    def test_access_denied_is_raised_not_retried(self):
        from http_client import AccessDenied
        ctx = context({'group.bnpparibas': (403, 'Access Denied')})
        with self.assertRaises(AccessDenied):
            fetch_source({'company': 'BNP', 'platform': 'sitemap', 'sitemap_url': 'https://group.bnpparibas/sitemap.xml',
                          'job_url_pattern': '/job'}, ctx)


SG_PAGE = '''<html><head><title>Trainee</title><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"TRAINEE: Sales Assistant","description":"<p>Trainee contract. Students.</p>","datePosted":"2026/10/14","validThrough":"2026/11/16",
"employmentType":"Trainee","jobLocation":{"@type":"Place","address":{"addressLocality":"Hong Kong","addressCountry":"HK"}}}</script></head>
<body><nav>London Paris Singapore Senior Director 10 years experience</nav><h1>TRAINEE: Sales Assistant</h1><a href="/apply">Apply</a></body></html>'''


class SitemapTests(unittest.TestCase):
    def test_sitemap_keeps_one_language_per_offer(self):
        from sources.sitemap import drop_translations
        urls = {'https://x/en/job-offers/vie-fixed-income-esales-26000KN1-en': '2026-10-19',
                'https://x/offres-d-emploi/vie-fixed-income-esales-26000KN1-fr': '2026-10-19',
                'https://x/offres-d-emploi/stage-vendeur-actions-26000ZZ9-fr': '2026-10-13'}
        kept = drop_translations(urls, r'-([0-9A-Z]{8})-(?:en|fr)/?$', '-en')
        self.assertEqual(sorted(kept), ['https://x/en/job-offers/vie-fixed-income-esales-26000KN1-en',
                                        'https://x/offres-d-emploi/stage-vendeur-actions-26000ZZ9-fr'])

    def test_jsonld_detail_ignores_navigation(self):
        d = parse_detail(SG_PAGE, 'https://careers.societegenerale.com/en/job-offers/trainee-sales-assistant-26000M1T-en', {})
        self.assertEqual(d['title'], 'TRAINEE: Sales Assistant')
        self.assertEqual(d['location'], 'Hong Kong, HK')
        self.assertNotIn('10 years', d['description'])
        self.assertEqual(d['status'], 'open')

    def test_business_line_suffix_in_structured_title_removed(self):
        page = SG_PAGE.replace('"title":"TRAINEE: Sales Assistant"',
                               '"title":"TRAINEE: Sales Assistant - Corporate & Investment banking - Hong Kong, Hong Kong"')
        d = parse_detail(page, 'u', {})
        self.assertEqual(d['title'], 'TRAINEE: Sales Assistant')

    def test_successfactors_text_dates(self):
        page = ('<html><body><span itemprop="title">Markets Intern SG 2027</span><div itemprop="description">'
                'Employment Type: Intern Posting Start Date: 27/08/2026 Posting End Date: 31/12/2099 Job Description: x</div></body></html>')
        d = parse_detail(page, 'https://jobs.standardchartered.com/job/Singapore-Markets-Intern-SG-2027/1/', {'location_from_slug': True})
        self.assertEqual((d['published'], d['valid_through']), ('2026-08-27', '2099-12-31'))
        self.assertEqual(d['status'], 'open')

    def test_closed_page_detected(self):
        page = SG_PAGE.replace('<h1>', '<p>This job is no longer available</p><h1>')
        self.assertEqual(parse_detail(page, 'u', {})['status'], 'closed')

    def test_incremental_fetching_and_slug_screening(self):
        sitemap = ('<urlset>' + ''.join(
            f'<url><loc>https://careers.societegenerale.com/en/job-offers/{slug}</loc><lastmod>2026-10-1{i}</lastmod></url>'
            for i, slug in enumerate(['trainee-sales-assistant-26000M1T-en', 'trainee-fx-trading-26000AAA1-en',
                                      'senior-software-engineer-26000BBB1-en', 'trainee-credit-sales-26000CCC1-en'])) + '</urlset>')
        ctx = context([('sitemap.xml', sitemap), ('job-offers/', SG_PAGE)], now=NOW)
        src = {'company': 'Société Générale', 'platform': 'sitemap', 'sitemap_url': 'https://careers.societegenerale.com/sitemap.xml',
               'job_url_pattern': '^/en/job-offers/', 'max_fetch_per_run': 2}
        first = fetch_source(src, ctx)
        pages = [u for _, u, _ in ctx.http.calls if 'job-offers' in u]
        self.assertEqual(len(pages), 2)                                   # per-run budget respected
        self.assertFalse(any('software' in u for u in pages))            # screened by slug
        self.assertTrue(pages[0].endswith('26000CCC1-en'))               # newest lastmod first
        second = fetch_source(src, ctx)
        pages = [u for _, u, _ in ctx.http.calls if 'job-offers' in u]
        self.assertEqual(len(pages), 3)                                   # continues with the backlog only
        self.assertEqual((len(first), len(second)), (2, 3))


if __name__ == '__main__':
    unittest.main()
