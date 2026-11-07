"""Job relevance classification (English and French).

`classify(job, config)` returns a Classification describing city, role
family, entry type, experience requirement and seniority, plus a final
`eligible` decision with human-readable reasons. Only the job title,
location and the job description proper are inspected: callers must not
pass page navigation or footer text as the description.
"""
import re
import unicodedata
from dataclasses import dataclass, field

WORD_NUMBERS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
    'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'un': 1, 'une': 1, 'deux': 2, 'trois': 3, 'quatre': 4, 'cinq': 5,
    'sept': 7, 'huit': 8, 'neuf': 9, 'dix': 10,
}
NUM = r'(\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten|un|une|deux|trois|quatre|cinq|sept|huit|neuf|dix)'

# Experience requirement expressions. Each captures a lower bound (group 1)
# and optionally an upper bound (group 2).
EXPERIENCE_PATTERNS = [
    # 2-4 years / 2 to 4 years / 2–4 yrs / de 2 à 4 ans / entre 2 et 4 ans
    re.compile(r'(?:\bde\s+|\bentre\s+)?\b' + NUM + r'\s*(?:-|–|—|to|à|a|et)\s*' + NUM + r'\s*\+?\s*(?:years?|yrs?|ans?|ann[ée]es?)\b', re.I),
    # 3+ years / 3 years / 3 ans / at least 3 years / minimum 3 ans
    re.compile(r'\b' + NUM + r'\s*\+?\s*(?:years?|yrs?|ans?|ann[ée]es?)\b', re.I),
]
# A requirement only counts when the surrounding text talks about
# professional experience.
EXPERIENCE_CONTEXT = re.compile(
    r'experience|expérience|experiencia|worked|working in|work history|in (?:a|the) (?:similar|comparable|relevant) role|'
    r'post[- ]qualification|pqe|track record|industry|professional|in the (?:financial|banking) (?:industry|sector)|on a (?:trading|sales) desk',
    re.I)
# Context showing the number refers to something other than professional
# experience (education, programme length, company history, age...).
NON_EXPERIENCE_CONTEXT = re.compile(
    r'\b(?:degree|bachelor|master|msc|mba|phd|doctorat|licence|bac\s*\+|universit|school|[ée]cole|study|studies|[ée]tudes|'
    r'graduat(?:e|ing) (?:in|by)|final[- ]year|penultimate|programme lasts|program lasts|rotation|contract|contrat|durée|duration|'
    r'mission|v\.?i\.?e|founded|history|heritage|since \d{4}|for over|depuis plus de|old\b|âge|ans révolus|anniversary|'
    r'internship of|stage de|apprenticeship of|alternance de|visa|warranty|years? ago)', re.I)
ENTRY_PHRASES = re.compile(
    r"\b(?:entry[- ]level|no (?:prior |previous )?(?:work |professional )?experience (?:is )?(?:required|necessary|needed)|"
    r"recent graduates?|new graduates?|fresh graduates?|graduating (?:in|by) \d{4}|currently (?:enrolled|pursuing|studying)|"
    r"final[- ]year students?|penultimate[- ]year|(?:0|zero)\s*(?:-|–|to)\s*(?:1|one)\s*years?|less than (?:1|one) years?|"
    r"jeunes? dipl[ôo]m[ée]s?|d[ée]butants? accept[ée]s?|premi[èe]re exp[ée]rience|aucune exp[ée]rience (?:requise|n[ée]cessaire)|"
    r"[ée]tudiants? en (?:derni[èe]re|fin d)|en cours de (?:formation|scolarit[ée]))\b", re.I)


def normalize_text(text):
    """Lowercase ASCII-folded text with collapsed whitespace."""
    text = unicodedata.normalize('NFKD', str(text or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', text).strip().lower()


def _num(token):
    token = token.strip().lower()
    return int(token) if token.isdigit() else WORD_NUMBERS.get(token)


@dataclass
class Experience:
    min_years: object = None   # int or None
    max_years: object = None
    category: str = 'not_stated'   # not_stated | entry | 0-1 | borderline | 2+
    evidence: str = ''


@dataclass
class Classification:
    eligible: bool = False
    city: str = ''
    role_family: str = ''
    entry_type: str = ''           # internship | graduate_programme | vie | apprenticeship | entry_level | ''
    seniority: str = 'unknown'     # junior | senior | unknown
    experience: Experience = field(default_factory=Experience)
    reasons: list = field(default_factory=list)
    reject_reason: str = ''

    @property
    def level_label(self):
        labels = {'internship': 'Internship', 'graduate_programme': 'Graduate programme', 'vie': 'VIE',
                  'apprenticeship': 'Apprenticeship / alternance', 'entry_level': 'Entry level',
                  'not_stated': 'Level not stated (no experience required in the advert)'}
        return labels.get(self.entry_type, 'Unspecified level')

    @property
    def experience_label(self):
        e = self.experience
        if e.category == 'not_stated':
            return 'not stated'
        if e.category == 'entry':
            return '0–1 year (entry-level wording)'
        if e.min_years is None:
            return e.category
        span = f'{e.min_years}–{e.max_years}' if e.max_years not in (None, e.min_years) else f'{e.min_years}'
        return f'{span} year(s) required ({e.category})'


MIN_ADVERT_TEXT = 400
# Experience asked for without a number of years
VAGUE_EXPERIENCE = re.compile(
    r"\b(?:extensive|significant|substantial|considerable|proven|solid|strong|demonstrable|demonstrated|several years(?: of)?)\s+"
    r"(?:\w+\s+){0,3}(?:experience|track record)\b|\bproven track record\b|\bseasoned\b|\bexperienced (?:sales|trader|professional)\b|"
    r"\bexp[ée]rience (?:significative|confirm[ée]e|solide|probante)\b|\bplusieurs ann[ée]es d'exp[ée]rience\b", re.I)
EXPLICIT_JUNIOR = re.compile(r'\bjunior\b|\bjr\.?\b|\bentry[- ]level\b|\bd[ée]butant|\bnew grad|\bgraduate|\bfresh(?:er|man)?\b|\b20\d\d\b', re.I)


def _compile(patterns):
    return [re.compile(p, re.I) for p in patterns]


class Classifier:
    def __init__(self, config):
        f = config.get('filters', config)
        self.max_years = int(f.get('max_years_experience', 1))
        self.require_entry_signal = bool(f.get('require_entry_signal', True))
        self.cities = {city: _compile(pats) for city, pats in f['cities'].items()}
        self.roles = {fam: _compile(pats) for fam, pats in f['role_families'].items()}
        self.role_exclusions = _compile(f.get('role_exclusion_patterns', []))
        self.senior = _compile(f['senior_title_patterns'])
        # Words such as 'Associate' or 'Manager' are senior unless the title
        # also names an internship/graduate/trainee programme.
        self.soft_senior = _compile(f.get('soft_senior_title_patterns', []))
        self.non_front_office = _compile(f['non_front_office_patterns'])
        self.entry = {kind: _compile(pats) for kind, pats in f['entry_types'].items()}
        self.senior_desc = _compile(f.get('senior_description_patterns', []))
        self.excluded_companies = {normalize_text(c) for c in f.get('excluded_companies', [])}

    # -- individual dimensions -------------------------------------------
    def city(self, location):
        for city, pats in self.cities.items():
            if any(p.search(location or '') for p in pats):
                return city
        return ''

    def role_family(self, title):
        if any(p.search(title) for p in self.role_exclusions):
            return ''
        for fam, pats in self.roles.items():
            if any(p.search(title) for p in pats):
                return fam
        return ''

    def entry_type(self, text):
        for kind, pats in self.entry.items():
            if any(p.search(text or '') for p in pats):
                return kind
        return ''

    def experience(self, description):
        """Most restrictive professional-experience requirement in the text."""
        text = re.sub(r'\s+', ' ', description or '')
        found = []
        consumed = []
        for pattern in EXPERIENCE_PATTERNS:
            for m in pattern.finditer(text):
                if any(a <= m.start() < b for a, b in consumed):
                    continue
                lo = _num(m.group(1))
                hi = _num(m.group(2)) if m.re.groups > 1 and m.group(2) else lo
                if lo is None or lo > 15:  # larger numbers are company history, not requirements
                    continue
                before = text[max(0, m.start() - 70):m.start()]
                after = text[m.end():m.end() + 70]
                window = before + ' ' + m.group(0) + ' ' + after
                near = before[-35:] + ' ' + after[:45]
                if not EXPERIENCE_CONTEXT.search(near):
                    continue
                # Numbers about education, programme length or history are not
                # experience requirements, unless 'experience' immediately follows.
                if NON_EXPERIENCE_CONTEXT.search(near) and not re.match(
                        r'\s*(?:of\s+)?(?:\w+\s+){0,3}(?:experience|exp[ée]rience)', after, re.I):
                    continue
                consumed.append((m.start(), m.end()))
                found.append((lo, hi, window.strip()))
        if found:
            lo, hi, evidence = max(found, key=lambda x: (x[0], x[1] or 0))
            if hi is not None and hi < lo:
                hi = lo
            if (hi if hi is not None else lo) <= self.max_years:
                cat = '0-1'
            elif lo <= self.max_years:
                cat = 'borderline'
            else:
                cat = '2+'
            return Experience(lo, hi, cat, evidence[:160])
        m = ENTRY_PHRASES.search(text)
        if m:
            return Experience(None, None, 'entry', m.group(0))
        return Experience()

    # -- decision ----------------------------------------------------------
    def classify(self, job):
        c = Classification()
        title = re.sub(r'\s+', ' ', job.get('title') or '').strip()
        description = job.get('description') or ''
        location = job.get('location') or ''
        if normalize_text(job.get('company')) in self.excluded_companies:
            c.reject_reason = 'company excluded in config'
            return c
        c.city = self.city(location) or job.get('alert_city', '')
        if not c.city:
            c.reject_reason = f'location not targeted ({location[:60] or "unknown"})'
            return c
        if any(p.search(title) for p in self.senior):
            c.seniority = 'senior'
            c.reject_reason = 'senior title'
            return c
        if any(p.search(title) for p in self.non_front_office):
            c.reject_reason = 'non front-office title'
            return c
        title_entry = self.entry_type(title)
        if not title_entry or title_entry == 'entry_level':
            if any(p.search(title) for p in self.soft_senior):
                c.seniority = 'senior'
                c.reject_reason = 'experienced-hire title (associate/manager)'
                return c
        c.role_family = self.role_family(title)
        if not c.role_family:
            c.reject_reason = 'role not targeted'
            return c
        # Explicit corporate title / grade fields in the description.
        if any(p.search(description) for p in self.senior_desc):
            c.seniority = 'senior'
            c.reject_reason = 'senior corporate title in description'
            return c
        c.entry_type = title_entry or self.entry_type(job.get('employment_type') or '')
        c.experience = self.experience(description)
        exp = c.experience
        # Only a *minimum* above the threshold excludes a job ("1-2 years" requires 1).
        if exp.category == '2+':
            c.reject_reason = f'requires {exp.min_years}+ years of experience'
            return c
        if not c.entry_type and exp.category in ('entry', '0-1'):
            c.entry_type = 'entry_level'
        # Title-only sources (LinkedIn / eFinancialCareers alerts, LinkedIn job
        # pages): no advert text to check the experience asked, and a bare
        # 'Analyst' / 'Assistant' title is often an experienced hire there
        if (job.get('title_only') and c.entry_type == 'entry_level'
                and not EXPLICIT_JUNIOR.search(title) and exp.category not in ('entry', '0-1')):
            c.reject_reason = 'title only (no advert text): Analyst title without an explicit junior signal'
            return c
        if not c.entry_type:
            # Bare titles ("Cross Commodities Sales"): accepted when the advert
            # text was read and asks for no more than 1 year of experience
            # (user's rule, 2026-11-07); without advert text a bare title is
            # not evidence of a junior role.
            advert = len(description) >= MIN_ADVERT_TEXT and not job.get('title_only')
            if advert and VAGUE_EXPERIENCE.search(description):
                c.reject_reason = 'experience required (advert asks for proven / extensive experience)'
                return c
            if advert:
                c.entry_type = 'not_stated'
            elif self.require_entry_signal:
                c.reject_reason = 'no entry-level evidence (title or description)'
                return c
        c.seniority = 'junior'
        c.eligible = True
        c.reasons = [
            f'{c.role_family} role',
            c.level_label,
            f'in {c.city}',
            f'experience: {c.experience_label}',
        ]
        return c
