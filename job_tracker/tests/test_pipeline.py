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

    def run_tracker(self, telegram=None, dry_run=False, sources=SOURCES, now=None, scheduled=False, page_watches=None, **notif):
        config = config_with(sources)
        config['page_watches'] = page_watches or []
        notif.setdefault('send_window', None)  # window behaviour is tested separately
        notif.setdefault('daily_status_hour', None)  # daily status is tested separately
        config['notifications'] = dict(config['notifications'], **notif)
        return tracker.run(config, self.state, self.cache, self.report, dry_run=dry_run, http=self.http,
                           telegram=telegram or FakeTelegram(), now=now, scheduled=scheduled)

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


    def test_newly_added_source_backfills_old_open_adverts(self):
        from datetime import datetime, timedelta, timezone
        from unittest import mock
        today = datetime.now(timezone.utc).date()
        job = dict(company='Bank', location='London', description='', status='open', status_evidence='', employment_type='',
                   apply_url='', requisition_id='', id='b', title='Rates Sales Intern', url='https://b/b',
                   published=(today - timedelta(days=60)).isoformat())
        source = dict(SOURCES[0], company='Bank', backfill_until=(today + timedelta(days=3)).isoformat())
        with mock.patch.object(tracker, 'fetch_source', lambda s, ctx: [job]):
            tg = FakeTelegram()
            self.run_tracker(tg, sources=[source], max_age_days=14)
            self.assertIn('Rates Sales Intern', '\n'.join(tg.sent))
            expired = dict(source, backfill_until=(today - timedelta(days=1)).isoformat())
            tg2 = FakeTelegram()
            self.run_tracker(tg2, sources=[expired], max_age_days=14)   # already notified anyway
            self.assertEqual(tg2.sent, [])


    def test_new_target_city_backfills_old_open_adverts(self):
        from datetime import datetime, timedelta, timezone
        from unittest import mock
        today = datetime.now(timezone.utc).date()
        old = (today - timedelta(days=60)).isoformat()
        base = dict(company='Bank', description='', status='open', status_evidence='', employment_type='',
                    apply_url='', requisition_id='', published=old)
        jobs = [dict(base, id='d', title='2027 Sales Summer Internship', location='Dubai', url='https://b/d'),
                dict(base, id='l', title='2027 Rates Summer Internship', location='London', url='https://b/l')]
        with mock.patch.object(tracker, 'fetch_source', lambda s, ctx: jobs):
            tg = FakeTelegram()
            self.run_tracker(tg, sources=SOURCES[:1], max_age_days=14, city_backfill={'Dubai': (today + timedelta(days=3)).isoformat()})
        text = '\n'.join(tg.sent)
        self.assertIn('2027 Sales Summer Internship', text)
        self.assertNotIn('2027 Rates Summer Internship', text)


    def test_daily_status_sent_once_in_the_evening(self):
        from datetime import datetime, timezone
        window = {'timezone': 'Asia/Ho_Chi_Minh', 'start_hour': 11, 'end_hour': 22}
        evening = datetime(2026, 10, 28, 14, 30, tzinfo=timezone.utc)    # 21:30 in Vietnam
        tg = FakeTelegram()
        self.run_tracker(tg, now=evening, send_window=window, daily_status_hour=21)
        status = [m for m in tg.sent if 'Point du jour' in m]
        self.assertEqual(len(status), 1)
        self.assertIn('Sites lus sans erreur', status[0])
        tg2 = FakeTelegram()
        self.run_tracker(tg2, now=evening, send_window=window, daily_status_hour=21)
        self.assertFalse(any('Point du jour' in m for m in tg2.sent))      # once a day

    def test_source_that_goes_silent_is_reported(self):
        from unittest import mock
        job = dict(company='Bank', location='London', description='', status='open', status_evidence='', employment_type='',
                   apply_url='', requisition_id='', id='a', title='Rates Sales Intern', url='https://b/a', published='')
        with mock.patch.object(tracker, 'fetch_source', lambda s, ctx: [dict(job, id=f'a{i}', url=f'https://b/{i}') for i in range(6)]):
            self.run_tracker(FakeTelegram(), sources=SOURCES[:1])
        with mock.patch.object(tracker, 'fetch_source', lambda s, ctx: []):
            _, report = self.run_tracker(FakeTelegram(), sources=SOURCES[:1])
        self.assertEqual(report.silent, [SOURCES[0]['company']])


    def test_send_window_holds_alerts_until_next_slot(self):
        from datetime import datetime, timezone
        window = {'timezone': 'Asia/Ho_Chi_Minh', 'start_hour': 11, 'end_hour': 22}
        night = datetime(2026, 10, 18, 18, 30, tzinfo=timezone.utc)    # 01:30 in Vietnam
        tg = FakeTelegram()
        code, _ = self.run_tracker(tg, now=night, send_window=window)
        self.assertEqual((code, tg.sent), (0, []))
        self.assertIn('outside the send window', self.report.read_text())
        self.assertEqual(self.http.calls, [])                          # no source request at night
        day = datetime(2026, 10, 19, 6, 7, tzinfo=timezone.utc)        # 13:07 in Vietnam
        tg2 = FakeTelegram()
        self.run_tracker(tg2, now=day, send_window=window)
        self.assertEqual(len(tg2.sent), 2)

    def test_scheduled_sends_stay_spaced(self):
        from datetime import datetime, timezone
        at = lambda h, m: datetime(2026, 10, 19, h, m, tzinfo=timezone.utc)
        kw = {'max_per_run': 1, 'min_minutes_between_sends': 100}
        sent = []
        for now, scheduled in [(at(6, 7), True), (at(6, 37), True), (at(8, 7), True)]:
            tg = FakeTelegram()
            self.run_tracker(tg, now=now, scheduled=scheduled, **kw)
            sent.append(len(tg.sent))
        self.assertEqual(sent, [1, 0, 1])
        self.assertIn('less than 100 min ago', self.run_tracker(now=at(8, 37), scheduled=True, **kw)[1].note)

    def test_manual_run_ignores_spacing(self):
        from datetime import datetime, timezone
        kw = {'max_per_run': 1, 'min_minutes_between_sends': 100}
        self.run_tracker(now=datetime(2026, 10, 19, 6, 7, tzinfo=timezone.utc), scheduled=True, **kw)
        tg = FakeTelegram()
        self.run_tracker(tg, now=datetime(2026, 10, 19, 6, 20, tzinfo=timezone.utc), scheduled=False, **kw)
        self.assertEqual(len(tg.sent), 1)

    def test_page_watch_alerts_once_on_change(self):
        page = {'html': '<main>Current job offers All offers are on LinkedIn. Spontaneous application</main>'}
        self.http.routes.insert(0, ('sucden.com', lambda m, u, p: page['html']))
        watch = [{'name': 'Sucden', 'url': 'https://www.sucden.com/en/jobs/', 'section_start': 'Current job offers',
                  'section_end': 'Spontaneous application'}]
        tg = FakeTelegram()
        self.run_tracker(tg, page_watches=watch)
        self.assertFalse(any('Sucden' in m for m in tg.sent))          # baseline only
        page['html'] = '<main>Current job offers Junior Sugar Trader - Paris. Spontaneous application</main>'
        tg2 = FakeTelegram()
        self.run_tracker(tg2, page_watches=watch)
        alerts = [m for m in tg2.sent if 'Sucden' in m]
        self.assertEqual(len(alerts), 1)
        self.assertIn('Junior Sugar Trader', alerts[0])
        tg3 = FakeTelegram()
        self.run_tracker(tg3, page_watches=watch)
        self.assertFalse(any('Sucden' in m for m in tg3.sent))         # not repeated
        self.assertIn('Sucden: unchanged', self.report.read_text())

    def test_window_bounds(self):
        from datetime import datetime, timezone
        w = {'timezone': 'Asia/Ho_Chi_Minh', 'start_hour': 11, 'end_hour': 22}
        at = lambda h, m=0: datetime(2026, 10, 19, h, m, tzinfo=timezone.utc)
        self.assertFalse(tracker.in_send_window(w, at(3, 59)))   # 10:59 local
        self.assertTrue(tracker.in_send_window(w, at(4, 0)))     # 11:00 local
        self.assertTrue(tracker.in_send_window(w, at(15, 59)))   # 22:59 local
        self.assertFalse(tracker.in_send_window(w, at(16, 0)))   # 23:00 local
        self.assertTrue(tracker.in_send_window(None, at(16, 0)))


class ConfigTests(unittest.TestCase):
    REQUIRED = {'greenhouse': ['board'], 'lever': ['board'], 'ashby': ['board'], 'smartrecruiters': ['board'], 'bamboohr': ['board'], 'workable': ['board'],
                'workday': ['host', 'tenant', 'site'], 'oracle': ['host'], 'eightfold': ['host', 'domain'],
                'sitemap': ['sitemap_url', 'job_url_pattern'], 'goldman': [], 'successfactors_xml': ['host', 'sf_company'],
                'rss': ['feeds'], 'oleeo': ['boards'], 'rmk': ['base_url', 'location_searches'], 'html_cards': ['listing_url', 'card_pattern'], 'talentsoft': ['base_url'], 'email_alerts': ['senders', 'link_pattern'],
 'web_search': ['queries'],
                'bank_of_america': [], 'brassring': ['host', 'partner_id', 'site_ids'], 'zhiye': ['host'], 'bpce': ['base_url'], 'avature': ['base_url'], 'business_france': []}

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


class TelegramCheckTests(unittest.TestCase):
    """A Telegram outage must not stop the run; a refused token must."""

    def check(self, status, body):
        import os
        from unittest import mock
        from notifier import Telegram

        class Resp:
            def __init__(self):
                self.status_code, self.content = status, json.dumps(body).encode()

            def json(self):
                return body

        class Session:
            def post(self, *a, **k):
                return Resp()

        with mock.patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': '123:abc', 'TELEGRAM_CHAT_ID': '42'}):
            return Telegram(session=Session(), sleep=lambda s: None).check()

    def test_server_error_is_transient(self):
        ok, message = self.check(502, {'ok': False, 'description': 'Bad Gateway'})
        self.assertTrue(ok)
        self.assertIn('unreachable', message)

    def test_refused_token_fails(self):
        ok, message = self.check(401, {'ok': False, 'description': 'Unauthorized'})
        self.assertFalse(ok)
        self.assertIn('rejected', message)
