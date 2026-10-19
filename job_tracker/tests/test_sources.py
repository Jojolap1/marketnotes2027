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
