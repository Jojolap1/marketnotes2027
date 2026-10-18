import unittest
from unittest.mock import patch
import tracker

class JobFilterTests(unittest.TestCase):
    def job(self, title, description='', location='London'):
        return {'title': title, 'description': description, 'location': location}

    def test_entry_sales_trading(self):
        self.assertTrue(tracker.matches(self.job('Markets Sales and Trading Summer Analyst 2027')))
    def test_hong_kong_trainee(self):
        self.assertTrue(tracker.matches(self.job('TRAINEE: Credit Sales Assistant', location='Hong Kong')))
    def test_two_year_requirement_rejected(self):
        self.assertFalse(tracker.matches(self.job('Junior Credit Sales', 'Requirements: minimum 2 years of experience')))
    def test_three_year_requirement_rejected(self):
        self.assertFalse(tracker.matches(self.job('FX Trading Intern', 'Qualifications: 3+ years of relevant experience')))
    def test_range_experience_rejected(self):
        self.assertFalse(tracker.matches(self.job('Equity Sales Trainee', 'Qualifications: 2-4 years of experience')))
    def test_one_year_allowed(self):
        self.assertTrue(tracker.matches(self.job('Credit Sales Intern', 'Requirements: 1 year of experience')))
    def test_vp_rejected(self):
        self.assertFalse(tracker.matches(self.job('Equity Derivatives Assistant Vice President')))
    def test_developer_rejected(self):
        self.assertFalse(tracker.matches(self.job('Trading Developer Intern')))
    def test_other_city_rejected(self):
        self.assertFalse(tracker.matches(self.job('Sales Trading Intern', location='New York')))
    def test_unqualified_analyst_rejected(self):
        self.assertFalse(tracker.matches(self.job('Credit Analyst', location='Paris')))
    def test_analyst_explicit_entry_allowed(self):
        self.assertTrue(tracker.matches(self.job('Credit Analyst', 'Requirements: entry-level role, no prior experience', 'Paris')))

if __name__ == '__main__':
    unittest.main()
