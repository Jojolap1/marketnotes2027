import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests.helpers import ROOT  # noqa: F401
import tracker
from filters import Classifier
from jobs import canonical_url, fingerprints, make_job
from notifier import Telegram, _scrub, format_message
from state import State
from status import CLOSED, OPEN, UNKNOWN, assess_page, from_end_date

C = Classifier(tracker.CONFIG)


class CanonicalUrlTests(unittest.TestCase):
    def test_tracking_params_and_formatting(self):
        a = canonical_url('https://www.Example.com/job/123/?utm_source=x&gh_src=abc#apply')
        b = canonical_url('http://example.com/job/123')
        self.assertEqual(a, b)

    def test_workday_locale_prefix(self):
        self.assertEqual(canonical_url('https://citi.wd5.myworkdayjobs.com/en-US/2/job/London/X_123'),
                         canonical_url('https://citi.wd5.myworkdayjobs.com/2/job/London/X_123'))

    def test_societe_generale_language_suffix(self):
        self.assertEqual(canonical_url('https://careers.societegenerale.com/en/job-offers/trainee-sales-assistant-26000M1T-en'),
                         canonical_url('https://careers.societegenerale.com/en/job-offers/trainee-sales-assistant-26000M1T-fr'))

    def test_identifying_query_params_kept(self):
        self.assertNotEqual(canonical_url('https://x.com/career?company=a&career_job_req_id=1'),
                            canonical_url('https://x.com/career?company=a&career_job_req_id=2'))


class StateTests(unittest.TestCase):
    def job(self, **kw):
        base = dict(id='workday:citi:1', company='Citi', title='FX Sales Summer Analyst 2027', location='London',
                    url='https://citi.wd5.myworkdayjobs.com/2/job/London/FX_1', requisition_id='1')
        base.update(kw)
        return make_job(**base)

    def test_v1_history_is_migrated_and_not_resent(self):
        old = {'greenhouse:imc:4941205101': {'company': 'IMC Trading', 'title': 'Quantitative Trader Intern 2027',
                                             'first_seen': '2026-10-18T10:03:12+00:00'}}
        state = State(old)
        self.assertEqual(state.data['version'], 2)
        job = make_job(id='greenhouse:imc:4941205101', company='IMC Trading', title='Quantitative Trader Intern 2027',
                       location='Singapore', url='https://imc.com/x')
        self.assertTrue(state.is_notified(job, 'Singapore'))

    def test_repo_history_file_migrates(self):
        state = State.load(ROOT / 'seen_jobs.json')
        self.assertGreaterEqual(len(state.data['jobs']), 10)
        self.assertTrue(all(r.get('notified_at') for r in state.data['jobs'].values()))

    def test_same_job_with_changed_url_formatting_or_id_is_not_new(self):
        state = State({})
        state.record(self.job(), 'London', notified=True)
        self.assertTrue(state.is_notified(self.job(url='https://citi.wd5.myworkdayjobs.com/en-US/2/job/London/FX_1?source=LinkedIn'), 'London'))
        self.assertTrue(state.is_notified(self.job(id='sitemap:Citi:https://jobs.citi.com/job/1'), 'London'))

    def test_slightly_changed_title_same_requisition_is_not_new(self):
        state = State({})
        state.record(self.job(), 'London', notified=True)
        renamed = self.job(id='other', url='https://other', title='FX Sales – Summer Analyst (2027)')
        self.assertTrue(state.is_notified(renamed, 'London'))

    def test_same_title_different_url_and_id_is_deduplicated(self):
        state = State({})
        state.record(self.job(), 'London', notified=True)
        repost = self.job(id='x2', url='https://elsewhere/2', requisition_id='', title='FX Sales Summer Analyst 2027 (m/f/d)')
        self.assertTrue(state.is_notified(repost, 'London'))

    def test_same_title_in_other_city_is_new(self):
        state = State({})
        state.record(self.job(), 'London', notified=True)
        other = self.job(id='x3', url='https://elsewhere/3', requisition_id='', location='Paris')
        self.assertFalse(state.is_notified(other, 'Paris'))

    def test_title_match_expires_for_reposts_months_later(self):
        state = State({}, title_dedup_days=30)
        key = state.record(self.job(), 'London', notified=True)
        state.data['jobs'][key]['last_seen'] = (datetime.now(timezone.utc) - timedelta(days=60)).date().isoformat()
        repost = self.job(id='x4', url='https://elsewhere/4', requisition_id='')
        self.assertFalse(state.is_notified(repost, 'London'))

    def test_corrupt_state_is_not_overwritten(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 's.json'
            p.write_text('{broken')
            with self.assertRaises(RuntimeError):
                State.load(p)


class StatusTests(unittest.TestCase):
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)

    def test_http_200_alone_is_unknown(self):
        self.assertEqual(assess_page('Apply now. Job description ...', None, self.now)[0], UNKNOWN)

    def test_closure_notices(self):
        for text in ['Sorry, this job is no longer available.', 'We are no longer accepting applications for this role',
                     'This position has been filled', "Cette offre n'est plus disponible", 'Offre expirée',
                     'Les candidatures sont clôturées']:
            self.assertEqual(assess_page(text, '2027-01-01', self.now)[0], CLOSED, text)

    def test_publication_date_formats(self):
        from status import age_days, parse_date
        self.assertEqual(age_days('Posted Today'), 0)
        self.assertEqual(age_days('Posted 3 Days Ago'), 3)
        self.assertGreaterEqual(age_days('Posted 30+ Days Ago'), 30)
        self.assertEqual(parse_date('Sun, 18 Oct 2026 2:00:00 GMT').date().isoformat(), '2026-10-18')
        self.assertEqual(parse_date('2026/10/14').date().isoformat(), '2026-10-14')
        self.assertEqual(parse_date(1792158709).year, 2026)
        self.assertIsNone(age_days(''))
        self.assertIsNone(age_days('not a date'))

    def test_valid_through(self):
        self.assertEqual(assess_page('Apply', '2026/11/16', self.now)[0], OPEN)
        self.assertEqual(assess_page('Apply', '2026-10-01', self.now)[0], CLOSED)
        self.assertEqual(from_end_date('2026-10-18', self.now)[0], OPEN)  # deadline day itself is still open


class NotifierTests(unittest.TestCase):
    def test_message_contains_required_fields_and_escapes_html(self):
        job = make_job(id='1', company='Société Générale', title='TRAINEE: Sales <Assistant> & Co', location='Hong Kong',
                       url='https://careers.societegenerale.com/en/job-offers/x-26000M1T-en?a=1&b=2', published='2026/10/14',
                       employment_type='Trainee', status=OPEN, status_evidence='application end date 2026-11-16 is in the future')
        c = C.classify(job)
        self.assertTrue(c.eligible)
        text = format_message(job, c)
        for needle in ['Société Générale', 'TRAINEE: Sales &lt;Assistant&gt; &amp; Co', 'Hong Kong', 'Graduate programme',
                       'Employment type: Trainee', 'Published: 2026-10-14', 'Confirmed open', 'Why it matches',
                       'href="https://careers.societegenerale.com/en/job-offers/x-26000M1T-en?a=1&amp;b=2"']:
            self.assertIn(needle, text)

    def test_unknown_status_is_labelled(self):
        job = make_job(id='1', company='Optiver', title='Graduate Trader', location='London', url='https://o/1', status=UNKNOWN)
        self.assertIn('Unknown (not confirmed)', format_message(job, C.classify(job)))

    def test_token_is_scrubbed_from_errors(self):
        token = '123456:ABC-def_ghi'
        msg = _scrub(f'HTTPSConnectionPool: /bot{token}/sendMessage failed', token)
        self.assertNotIn(token, msg)
        self.assertNotIn('ABC-def', _scrub('url /bot999:XYZabc/getMe', ''))

    def test_check_without_secrets_never_calls_api(self):
        with mock.patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': ''}):
            session = mock.Mock()
            ok, message = Telegram(session=session).check()
            self.assertFalse(ok)
            session.post.assert_not_called()

    def test_rate_limit_is_retried(self):
        responses = [mock.Mock(status_code=429, content=b'1', json=lambda: {'ok': False, 'parameters': {'retry_after': 1}}),
                     mock.Mock(status_code=200, content=b'1', json=lambda: {'ok': True, 'result': {}})]
        session = mock.Mock()
        session.post.side_effect = responses
        with mock.patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 't:x', 'TELEGRAM_CHAT_ID': '1'}):
            tg = Telegram(session=session, sleep=lambda s: None)
            self.assertTrue(tg.send('hi'))
        self.assertEqual(session.post.call_count, 2)


if __name__ == '__main__':
    unittest.main()
