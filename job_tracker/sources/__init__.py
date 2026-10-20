"""Source adapters. Each adapter is `fetch(source, ctx) -> list[job dict]`.

Adapters read official, publicly accessible feeds only: public ATS APIs
(Greenhouse, Lever, Ashby, SmartRecruiters, Workday CXS, Oracle Recruiting
Cloud, Eightfold, SuccessFactors, Goldman Sachs role search), official RSS
feeds, Oleeo campus boards
and official XML sitemaps + vacancy pages. None logs in or bypasses access
controls; HTTP 401/403 surfaces as an AccessDenied source failure.
"""
from sources import ats, avature, banks, business_france, eightfold, feeds, oracle, sitemap, workday

ADAPTERS = {
    'greenhouse': ats.greenhouse,
    'lever': ats.lever,
    'ashby': ats.ashby,
    'smartrecruiters': ats.smartrecruiters,
    'bamboohr': ats.bamboohr,
    'workable': ats.workable,
    'workday': workday.fetch,
    'oracle': oracle.fetch,
    'eightfold': eightfold.fetch,
    'sitemap': sitemap.fetch,
    'goldman': feeds.goldman,
    'successfactors_xml': feeds.successfactors_xml,
    'rss': feeds.rss,
    'oleeo': feeds.oleeo,
    'rmk': feeds.rmk,
    'html_cards': feeds.html_cards,
    'talentsoft': feeds.talentsoft,
    'bank_of_america': banks.bank_of_america,
    'brassring': banks.brassring,
    'zhiye': banks.zhiye, 'bpce': banks.bpce, 'avature': avature.fetch, 'business_france': business_france.fetch,
}


def fetch_source(source, ctx):
    platform = source['platform'].lower()
    if platform not in ADAPTERS:
        raise ValueError(f'Unknown platform: {platform}')
    return ADAPTERS[platform](source, ctx)
