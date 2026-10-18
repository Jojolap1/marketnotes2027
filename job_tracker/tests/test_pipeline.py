import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import FakeHttp, FakeTelegram, config_with
import tracker

GREENHOUSE = {'jobs': [
    {'id': 1, 'title': 'Graduate Trader', 'location': {'name': 'Hong Kong'}, 'absolute_url': 'https://gh/1', 'content': 'Graduates welcome'},
    {'id': 2, 'title': 'Trading Intern', 'location': {'name': 'London'}, 'absolute_url': 'https://gh/2', 'content': ''},
    {'id': 3, 'title': 'Senior Trader', 'location': {'name': 'London'}, 'absolute_url': 'https://gh/3', 'content': ''},
    {'id': 4, 'title': 'Trading Intern', 'location': {'name': 'Chicago'}, 'absolute_url': 'https://gh/4', 'content': ''},
]}
SOURCES = [
    {'company': 'Flow Traders', 'platform': 'greenhouse', 'board': 'flowtraders'},
    {'company': 'Broken Bank', 'platform': 'greenhouse', 'board': 'broken'},
    {'company': 'Blocked Bank', 'platform': 'greenhouse', 'board': 'blocked'},
    {'company': 'Unavailable Bank', 'platform': 'official', 'enabled': False, 'disabled_reason': 'HTTP 403'},
]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        self.state, self.cache, self.report = d / 'seen.json', d / 'cache.json', d / 'report.md'
        self.http = FakeHttp([('boards/flowtraders', GREENHOUSE), ('boards/broken', (500, 'oops')), ('boards/blocked', (403, 'no'))])

    def tearDown(self):
        self.dir.cleanup()

    def run_tracker(self, telegram=None, dry_run=False, sources=SOURCES, **notif):
        config = config_with(sources)
        config['notifications'] = dict(config['notifications'], **notif)
        return tracker.run(config, self.state, self.cache, self.report, dry_run=dry_run, http=self.http,
                           telegram=telegram or FakeTelegram())

    def test_first_run_alerts_then_no_duplicates(self):
        tg = FakeTelegram()
        code, report = self.run_tracker(tg)
        self.assertEqual(code, 0)
        self.assertEqual(len(tg.sent), 2)
        self.assertTrue(any('Graduate Trader' in m for m in tg.sent))
        tg2 = FakeTelegram()
        code, _ = self.run_tracker(tg2)
        self.assertEqual(tg2.sent, [])
        saved = json.loads(self.state.read_text())
        self.assertEqual(sum(1 for r in saved['jobs'].values() if r['notified_at']), 2)

    def test_source_failures_are_isolated_and_reported(self):
        code, report = self.run_tracker()
        text = self.report.read_text()
        self.assertIn('Broken Bank | greenhouse | FAILED', text)
        self.assertIn('Blocked Bank | greenhouse | FAILED: access denied', text)
        self.assertIn('Unavailable Bank | official | skipped: HTTP 403', text)
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved['sources']['Broken Bank']['consecutive_failures'], 1)

    def test_all_sources_failing_raises_and_keeps_state(self):
        self.state.write_text(json.dumps({'greenhouse:x:1': {'company': 'X', 'title': 'Y', 'first_seen': 'z'}}))
        with self.assertRaises(RuntimeError):
            self.run_tracker(sources=SOURCES[1:])
        self.assertIn('greenhouse:x:1', self.state.read_text())
        self.assertNotIn('"version"', self.state.read_text())

    def test_dry_run_sends_nothing_and_writes_no_state(self):
        tg = FakeTelegram()
        self.run_tracker(tg, dry_run=True)
        self.assertEqual(tg.sent, [])
        self.assertFalse(self.state.exists())
        self.assertIn('DRY RUN', self.report.read_text())

    def test_missing_secrets_sends_nothing_and_fails_loudly(self):
        code, _ = self.run_tracker(FakeTelegram(configured=False))
        self.assertEqual(code, 2)
        saved = json.loads(self.state.read_text())
        self.assertFalse(any(r.get('notified_at') for r in saved['jobs'].values()))

    def test_failed_send_stays_pending_for_next_run(self):
        code, _ = self.run_tracker(FakeTelegram(fail_titles=['Graduate Trader']))
        self.assertEqual(code, 3)
        tg = FakeTelegram()
        self.run_tracker(tg)
        self.assertEqual(len(tg.sent), 1)
        self.assertIn('Graduate Trader', tg.sent[0])

    def test_cap_per_run_spreads_alerts(self):
        tg = FakeTelegram()
        self.run_tracker(tg, max_per_run=1)
        self.assertEqual(len(tg.sent), 1)
        tg2 = FakeTelegram()
        self.run_tracker(tg2, max_per_run=1)
        self.assertEqual(len(tg2.sent), 1)
        self.assertNotEqual(tg.sent[0], tg2.sent[0])

    def test_closed_and_unknown_status_rules(self):
        from unittest import mock
        import sources
        closed = dict(id='x:1', company='Bank', title='FX Sales Intern', location='London', url='https://b/1',
                      description='', status='closed', status_evidence='expired', published='', employment_type='', apply_url='', requisition_id='')
        unknown = dict(closed, id='x:2', url='https://b/2', title='Rates Sales Intern', status='unknown', status_evidence='')
        with mock.patch.object(tracker, 'fetch_source', lambda s, ctx: [closed, unknown]):
            tg = FakeTelegram()
            self.run_tracker(tg, sources=SOURCES[:1])
            self.assertEqual(len(tg.sent), 1)
            self.assertIn('Rates Sales Intern', tg.sent[0])
            self.assertIn('Unknown (not confirmed)', tg.sent[0])
            tg2 = FakeTelegram()
            self.run_tracker(tg2, sources=SOURCES[:1], statuses=['open'])
            self.assertEqual(tg2.sent, [])

    def test_duplicate_vacancy_from_two_sources_alerts_once(self):
        from unittest import mock
        a = dict(id='workday:citi:99', company='Citi', title='FX Sales Summer Analyst 2027', location='London', url='https://citi.wd5/2/job/x_99',
                 description='', status='open', status_evidence='', published='', employment_type='', apply_url='', requisition_id='99')
        b = dict(a, id='sitemap:Citi:https://jobs.citi.com/job/london/fx/99', url='https://jobs.citi.com/job/london/fx/99')
        with mock.patch.object(tracker, 'fetch_source', lambda s, ctx: [a, b]):
            tg = FakeTelegram()
            self.run_tracker(tg, sources=SOURCES[:1])
        self.assertEqual(len(tg.sent), 1)


    def test_only_recent_jobs_alerted_but_undated_jobs_still_sent(self):
        from datetime import datetime, timedelta, timezone
        from unittest import mock
        today = datetime.now(timezone.utc).date()
        base = dict(company='Bank', location='London', description='', status='open', status_evidence='',
                    employment_type='', apply_url='', requisition_id='')
        jobs = [dict(base, id='a', title='FX Sales Intern', url='https://b/a', published=(today - timedelta(days=3)).isoformat()),
                dict(base, id='b', title='Rates Sales Intern', url='https://b/b', published=(today - timedelta(days=30)).isoformat()),
                dict(base, id='c', title='Credit Trading Intern', url='https://b/c', published='')]
        with mock.patch.object(tracker, 'fetch_source', lambda s, ctx: jobs):
            tg = FakeTelegram()
            self.run_tracker(tg, sources=SOURCES[:1], max_age_days=14)
        text = '\n'.join(tg.sent)
        self.assertIn('FX Sales Intern', text)
        self.assertIn('Credit Trading Intern', text)
        self.assertIn('date not provided by the source', text)
        self.assertNotIn('Rates Sales Intern', text)
        self.assertIn('published more than 14 days ago: 1', self.report.read_text())


class ConfigTests(unittest.TestCase):
    REQUIRED = {'greenhouse': ['board'], 'lever': ['board'], 'ashby': ['board'], 'smartrecruiters': ['board'],
                'workday': ['host', 'tenant', 'site'], 'oracle': ['host', 'site'], 'eightfold': ['host', 'domain'],
                'sitemap': ['sitemap_url', 'job_url_pattern'], 'goldman': [], 'successfactors_xml': ['host', 'sf_company'],
                'rss': ['feeds'], 'oleeo': ['boards']}

    def test_sources_are_valid(self):
        import re
        from sources import ADAPTERS
        names = [s['company'] for s in tracker.CONFIG['sources']]
        self.assertEqual(len(names), len(set(names)), 'company names must be unique')
        for s in tracker.CONFIG['sources']:
            if not s.get('enabled', True):
                self.assertTrue(s.get('disabled_reason'), s['company'])
                continue
            self.assertIn(s['platform'], ADAPTERS, s['company'])
            for key in self.REQUIRED[s['platform']]:
                self.assertIn(key, s, s['company'])
            if 'job_url_pattern' in s:
                re.compile(s['job_url_pattern'])

    def test_all_banks_from_brief_are_listed(self):
        names = ' '.join(s['company'] for s in tracker.CONFIG['sources'])
        for bank in ['Société Générale', 'BNP Paribas', 'Citi', 'JPMorgan', 'Goldman Sachs', 'Morgan Stanley', 'Bank of America',
                     'HSBC', 'UBS', 'Deutsche Bank', 'Barclays', 'Standard Chartered', 'Crédit Agricole CIB', 'Natixis', 'MUFG',
                     'Nomura', 'SMBC', 'RBC', 'CIBC', 'CICC', 'CITIC', 'ICBC', 'Bank of China', 'China Construction Bank' if False else 'CCB',
                     'Haitong', 'Huatai', 'IMC Trading', 'Optiver', 'Flow Traders', 'BlackRock', 'Pictet',
                     'DBS', 'OCBC', 'UOB', 'UOB Kay Hian', 'Mizuho', 'Daiwa', 'BBVA', 'Santander', 'ING', 'NatWest',
                     'Guotai Junan', 'China Merchants Securities', 'BOC International', 'ICBC International', 'GF Securities',
                     'Shenwan Hongyuan', 'China Galaxy', 'Maybank', 'CIMB', 'Citadel Securities']:
            self.assertIn(bank, names)


if __name__ == '__main__':
    unittest.main()
