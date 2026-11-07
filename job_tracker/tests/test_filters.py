import unittest

from tests.helpers import ROOT  # noqa: F401  (adds job_tracker to sys.path)
import tracker
from filters import Classifier

C = Classifier(tracker.CONFIG)


def classify(title, description='', location='London', **extra):
    return C.classify(dict(title=title, description=description, location=location, company='Bank', **extra))


class LegacyRegressionTests(unittest.TestCase):
    """The original 11 regression cases, kept through the rewrite."""

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

    def test_bare_markets_analyst_with_entry_evidence_allowed(self):
        self.assertTrue(tracker.matches(self.job('Markets Analyst', 'Requirements: entry-level role, no prior experience required', 'Paris')))


class GeographyTests(unittest.TestCase):
    def test_target_cities_and_aliases(self):
        for loc, city in [('London  United Kingdom', 'London'), ('LONDON, LONDON, United Kingdom', 'London'),
                          ('Kowloon City, Kowloon, Hong Kong', 'Hong Kong'), ('Central, HK', 'Hong Kong'),
                          ('Singapore, One Raffles Quay', 'Singapore'), ('PARIS, Paris, France', 'Paris'),
                          ('Montrouge', 'Paris'), ('Paris La Défense', 'Paris'), ('Londres', 'London'),
                          ('Singapour', 'Singapore')]:
            self.assertEqual(C.city(loc), city, loc)

    def test_non_target_cities(self):
        for loc in ['New York, NY', 'Glasgow, United Kingdom', 'Frankfurt', 'Mumbai, India', 'Kuala Lumpur', 'New London']:
            self.assertEqual(C.city(loc), '', loc)

    def test_location_only_from_location_field(self):
        # 'London' in the description or footer must not make a NY job eligible.
        c = classify('Sales & Trading Summer Analyst', 'Our offices in London and Paris...', location='New York')
        self.assertFalse(c.eligible)


class EntryLevelTests(unittest.TestCase):
    def test_entry_types(self):
        cases = {
            'Global Markets Off-Cycle Internship 2027': 'internship',
            'Stage - Assistant Vendeur Dérivés Actions (H/F)': 'internship',
            'Stages en Vente/Sales - Clients Institutionnels (f/m/d)': 'internship',
            'VIE - Structuration Taux': 'vie',
            'Alternance - Assistant Trader Change': 'apprenticeship',
            'Graduate Programme 2027 - Global Markets': 'graduate_programme',
            'TRAINEE: Fixed Income Structuring': 'graduate_programme',
            'Junior Equity Sales Trader': 'entry_level',
            'Sales Assistant - Rates': 'entry_level',
        }
        for title, kind in cases.items():
            c = classify(title, location='Paris')
            self.assertTrue(c.eligible, (title, c.reject_reason))
            self.assertEqual(c.entry_type, kind, title)

    def test_bare_title_without_evidence_rejected(self):
        for title in ['Equity Sales Trader', 'Delta One Trader', 'Fixed Income Trader', 'Structurer']:
            c = classify(title)
            self.assertFalse(c.eligible, title)
            self.assertIn('no entry-level evidence', c.reject_reason)

    def test_bare_title_with_entry_level_description_accepted(self):
        c = classify('Equity Sales Trader', 'This entry-level role suits recent graduates.')
        self.assertTrue(c.eligible)
        self.assertEqual(c.entry_type, 'entry_level')

    def test_campus_description_counts_as_evidence(self):
        c = classify('2027 | EMEA | London | Global Markets | New Analyst',
                     'Corporate title: Analyst. Campus programme for students and recent graduates.')
        self.assertTrue(c.eligible, c.reject_reason)


class ExperienceTests(unittest.TestCase):
    def exp(self, text):
        return C.experience(text)

    def test_english_requirements(self):
        self.assertEqual(self.exp('You have at least 3 years of experience in sales').category, '2+')
        self.assertEqual(self.exp('Minimum of two years experience on a trading desk').category, '2+')
        self.assertEqual(self.exp('5+ years experience trading bonds').min_years, 5)
        self.assertEqual(self.exp('1-2 years of relevant experience').category, 'borderline')
        self.assertEqual(self.exp('0-1 years of experience').category, '0-1')
        self.assertEqual(self.exp('Up to 1 year of professional experience').category, '0-1')

    def test_french_requirements(self):
        self.assertEqual(self.exp("Vous justifiez de 3 ans d'expérience minimum").category, '2+')
        self.assertEqual(self.exp("Une expérience de 2 à 4 ans en salle des marchés").category, '2+')
        self.assertEqual(self.exp("Vous avez une première expérience (stage)").category, 'entry')
        self.assertEqual(self.exp("Jeune diplômé(e) d'une école de commerce").category, 'entry')

    def test_numbers_that_are_not_experience(self):
        for text in ['Founded over 150 years ago, the bank...',
                     'The programme lasts 2 years with rotations across desks.',
                     'Bac+5 école de commerce, stage de 6 mois.',
                     'You are in the final year of a 4 year degree.',
                     'Contrat VIE de 24 mois.',
                     'Pictet has been present in Singapore for more than 30 years where it employs over 200 people.']:
            self.assertEqual(self.exp(text).category, 'not_stated', text)

    def test_most_restrictive_requirement_wins(self):
        e = self.exp('0-1 years of experience preferred. Must have at least 3 years of experience in FX.')
        self.assertEqual(e.category, '2+')

    def test_minimum_requirement_decides(self):
        # 'More than one year' is excluded; a range whose minimum is <= 1 is kept and labelled borderline.
        for title, text in [('Junior Trader', '1-2 years of experience in trading'),
                            ('Energy Trading Assistant, Analyst', 'This role suits candidates with 0-2 years of experience, recent graduates welcome'),
                            ('Trader Assistant Intern', '1-2 years of experience in trading')]:
            c = classify(title, text)
            self.assertTrue(c.eligible, (title, c.reject_reason))
            self.assertEqual(c.experience.category, 'borderline')
            self.assertIn('borderline', c.experience_label)
        self.assertFalse(classify('Junior Trader', '2-3 years of experience in trading').eligible)

    def test_company_history_numbers_ignored(self):
        self.assertEqual(C.experience('With more than 25 years of experience in the markets, our firm ...').category, 'not_stated')

    def test_experience_threshold_configurable(self):
        import copy
        cfg = copy.deepcopy(tracker.CONFIG)
        cfg['filters']['max_years_experience'] = 3
        loose = Classifier(cfg)
        job = dict(title='Junior Credit Sales', description='minimum 2 years of experience', location='London', company='X')
        self.assertTrue(loose.classify(job).eligible)
        self.assertFalse(C.classify(job).eligible)


class SeniorityAndRoleTests(unittest.TestCase):
    def test_senior_titles(self):
        for title in ['Senior Equity Sales', 'Vice President, FX Trading', 'Head of Rates Sales', 'Managing Director - Credit Trading',
                      'Directeur Ventes Actions', 'Responsable Structuration', 'Lead Trader', 'Associate, Rates Sales',
                      'Portfolio Manager', 'Executive Director, Structuring', 'Trader confirmé']:
            c = classify(title)
            self.assertFalse(c.eligible, title)
            self.assertEqual(c.seniority, 'senior', title)

    def test_associate_level_programmes_rejected(self):
        # MBA / PhD-level programmes are not 0-1 year roles.
        for title in ['Summer Associate Internship - Sales & Trading', '2027 | APEJ | Hong Kong | Asset Management | Summer Associate',
                      '2027 Markets Quantitative Trading & Research Associate Program – Off-Cycle Internship - Hong Kong']:
            self.assertFalse(classify(title).eligible, title)

    def test_graduate_associate_titles_allowed(self):
        self.assertTrue(classify('Graduate Associate - FX Sales').eligible)

    def test_capital_markets_and_private_markets_excluded(self):
        for title in ['Capital Markets Off Cycle Internship Programme 2027 Paris', '2027 MUFG UK Analyst Programme: Capital Markets',
                      '2027 | EMEA | London | Asset Management, Private Investing, Private Equity | Seasonal/Off-Cycle',
                      '2027 | EMEA | London | Asset Management | Real Estate - Alternatives Investing | Seasonal/Off Cycle',
                      'Securities & Derivatives Analyst 1']:
            self.assertFalse(classify(title, location='Paris').eligible, title)
        self.assertTrue(classify('Capital Markets Sales & Trading Graduate 2027').eligible)

    def test_senior_corporate_title_in_description(self):
        c = classify('Global Markets Sales Analyst', 'Corporate Title: Associate')
        self.assertFalse(c.eligible)

    def test_non_front_office_rejected(self):
        for title in ['Python Engineer, Trading Automation', 'Compliance Intern - Markets', 'Operations Analyst Summer 2027',
                      'Market Risk Intern', 'Trade Support Graduate', 'Investment Banking Summer Analyst',
                      'Software Developer Intern - Trading Systems', 'HR Graduate Programme', 'Private Banking Graduate',
                      'Global Technology Summer Analyst', 'Business Analyst Intern', 'Graduate Trading Applications Specialist',
                      'Intern, Financial Markets Business Management', 'Admin Assistant, FICC Trading, Singapore',
                      'Analyste Crédit - Financements de projets', 'Stage - Analyste Marchés de Capitaux Dette',
                      'Internship: Global Consumer Financial Services, Consumer Secured Lending, Home Financing & Secured Lending Portfolio Management']:
            self.assertFalse(classify(title).eligible, title)

    def test_target_roles_accepted(self):
        for title, family in [('Equity Derivatives Structuring Intern', 'Structuring'),
                              ('FX Sales Graduate 2027', 'Sales & Trading'),
                              ('Rates Trading Off-Cycle Intern', 'Sales & Trading'),
                              ('Asset Management Summer Internship 2027', 'Asset & Portfolio Management'),
                              ('Portfolio Management Graduate Programme', 'Asset & Portfolio Management'),
                              ('Equity Research Summer Analyst', 'Markets / Investment Analyst'),
                              ('Investment Analyst Intern', 'Markets / Investment Analyst'),
                              ('Global Markets Summer Analyst 2027', 'Sales & Trading'),
                              ('Stage en Vente/Sales - Clients Institutionnels', 'Sales & Trading'),
                              ('【2027】衍生品交易员岗(J19701)', 'Sales & Trading')]:
            c = classify(title)
            self.assertTrue(c.eligible, (title, c.reject_reason))
            self.assertEqual(c.role_family, family, title)

    def test_navigation_text_is_not_used_for_title(self):
        # A generic careers page title is not a vacancy.
        self.assertFalse(classify('Careers', 'Sales Trading Internship Graduate London').eligible)


if __name__ == '__main__':
    unittest.main()


class DubaiAndPrivateCreditTradingTests(unittest.TestCase):
    def test_dubai_is_a_target_city(self):
        for location in ('Dubai, United Arab Emirates', 'DIFC', 'Dubaï', 'Dubai International Financial Centre'):
            self.assertEqual(C.city(location), 'Dubai', location)
        self.assertEqual(C.city('Abu Dhabi, United Arab Emirates'), '')
        self.assertTrue(classify('Global Markets Summer Analyst 2027', location='Dubai').eligible)
        self.assertFalse(classify('Markets Intern UAE 2027 (UAE National)', location='Dubai').eligible)

    def test_private_credit_trading_is_markets(self):
        c = classify('2027 - Global Markets - Summer Internship (Securitised Products and Private Credit Trading / Structuring) – London')
        self.assertTrue(c.eligible)
        self.assertFalse(classify('Private Credit Investment Summer Internship Programme - London').eligible)


class AnalystTitleTests(unittest.TestCase):
    def test_analyst_title_counts_as_entry_level_unless_experience_or_seniority_says_otherwise(self):
        self.assertTrue(classify('Trading Analyst', location='Singapore').eligible)
        self.assertTrue(classify('Foreign Exchange and Rates Sales Analyst', location='Paris').eligible)
        self.assertFalse(classify('Senior Trading Analyst', location='Singapore').eligible)
        self.assertFalse(classify('Trading Analyst', 'Minimum 3 years of experience in oil trading.', location='Singapore').eligible)
        self.assertFalse(classify('Trading Analyst II', location='Singapore').eligible)


class TitleOnlySourceTests(unittest.TestCase):
    """LinkedIn / eFinancialCareers alerts carry no advert text."""

    def classify(self, title, **extra):
        from filters import Classifier
        from tracker import load_config
        job = {'title': title, 'company': 'X', 'location': 'Hong Kong', 'description': 'X · Hong Kong'}
        job.update(extra)
        return Classifier(load_config()).classify(job)

    def test_bare_analyst_title_needs_an_explicit_junior_signal(self):
        for title in ('Research Analyst', 'Investment Analyst', 'Analyst, Money Market Dealer, Global Market Division'):
            c = self.classify(title, title_only=True)
            self.assertFalse(c.eligible, title)
            self.assertTrue(c.reject_reason.startswith('title only'), c.reject_reason)
            # with the advert text available (other sources) the Analyst rule still applies
            self.assertTrue(self.classify(title).eligible, title)

    def test_explicit_junior_titles_still_pass(self):
        for title in ('2027 Public Markets Equities Graduate Trainee (HK)', 'Junior Trader', 'Investment Analyst Intern',
                      'Graduate Analyst - Global Markets', 'Summer Analyst 2027 - Sales & Trading'):
            self.assertTrue(self.classify(title, title_only=True).eligible, title)

    def test_title_only_offer_borrows_the_official_advert_text(self):
        from jobs import borrow_descriptions
        from filters import Classifier
        from tracker import load_config
        official = {'title': 'Investment Analyst', 'company': 'Fidelity International', 'location': 'Hong Kong',
                    'url': 'https://fil.example/job/1',
                    'description': 'You will support the team. Requirements: at least 4 years of experience in equity research. ' * 3}
        linkedin = {'title': 'Investment Analyst', 'company': 'Fidelity International', 'location': 'Hong Kong',
                    'description': 'Fidelity International · Hong Kong', 'title_only': True}
        other = {'title': 'Research Analyst', 'company': 'Neuberger', 'location': 'Hong Kong',
                 'description': '', 'title_only': True}
        official['published'] = '2026-09-01'
        self.assertEqual(borrow_descriptions([linkedin, official, other]), 1)
        self.assertEqual(linkedin['published'], '2026-09-01')
        self.assertFalse(linkedin['title_only'])
        c = Classifier(load_config()).classify(linkedin)
        self.assertEqual(c.reject_reason, 'requires 4+ years of experience')
        self.assertTrue(other['title_only'])


class CompanyAliasTests(unittest.TestCase):
    def test_dotted_and_spaced_names_share_a_key(self):
        from jobs import company_key, configure_companies
        configure_companies(['JPMorgan', 'Societe Generale', 'ING'])
        self.assertEqual(company_key('J.P. Morgan'), 'jpmorgan')
        self.assertEqual(company_key('JPMorgan Chase & Co.'), 'jpmorgan')
        self.assertEqual(company_key('Ingenico'), 'ingenico')   # short names need an exact match


class BareTitleWithAdvertTests(unittest.TestCase):
    """User's rule (2026-11-07): a bare title is sent when the advert asks for <= 1 year."""
    TEXT = ('Marex is a diversified global financial services platform. The Cross Commodities Sales team covers '
            'corporate and institutional clients across energy, metals and agricultural markets in Asia. You will '
            'support the desk with client coverage, pricing requests, market commentary and trade booking, working '
            'closely with traders and structurers. ') * 2

    def classify(self, description, **extra):
        from filters import Classifier
        from tracker import load_config
        job = {'title': 'Cross Commodities Sales (VN2878)', 'company': 'Marex', 'location': 'Singapore',
               'description': description}
        job.update(extra)
        return Classifier(load_config()).classify(job)

    def test_no_experience_requirement_is_sent(self):
        c = self.classify(self.TEXT)
        self.assertTrue(c.eligible, c.reject_reason)
        self.assertIn('Level not stated', c.level_label)

    def test_experience_requirements_still_reject(self):
        self.assertFalse(self.classify(self.TEXT + ' You have 3+ years of experience in commodities sales.').eligible)
        self.assertFalse(self.classify(self.TEXT + ' Proven track record of client origination is essential.').eligible)
        self.assertFalse(self.classify(self.TEXT, title_only=True).eligible)   # no advert text read
        self.assertFalse(self.classify('Short text.').eligible)
        self.assertTrue(self.classify(self.TEXT + ' 1 year of experience is a plus.').eligible)
