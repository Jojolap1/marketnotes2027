"""Vacancy application-status evidence.

Three outcomes:
  open     - positive evidence that applications are accepted: the vacancy is
             listed by an official ATS feed that only publishes live postings,
             or structured data gives a future `validThrough` date and the page
             shows no closure notice.
  closed   - an explicit closure notice, an expired `validThrough`/end date,
             the vacancy disappeared from its feed, or the page is gone (404/410).
  unknown  - nothing conclusive. A page loading with HTTP 200 is NOT evidence.
"""
import re
from datetime import datetime, timezone

OPEN, CLOSED, UNKNOWN = 'open', 'closed', 'unknown'

CLOSURE_TEXT = re.compile(
    r"no longer (?:accepting|taking) applications|(?:this|the) (?:job|position|vacancy|role|posting|offer) "
    r"(?:is|has been) (?:no longer available|closed|filled|expired)|position has been filled|applications? (?:are|is) (?:now )?closed|"
    r"job (?:posting )?(?:has )?expired|this job is no longer available|cette offre (?:n'est plus disponible|est (?:cl[ôo]tur[ée]e|expir[ée]e|pourvue))|"
    r"offre (?:expir[ée]e|pourvue|cl[ôo]tur[ée]e)|le poste a [ée]t[ée] pourvu|candidatures? (?:sont )?(?:cl[ôo]tur[ée]es|ferm[ée]es)|"
    r"the job you are looking for (?:is no longer|could not be)|sorry,? this (?:job|position) (?:has|is)",
    re.I)


def parse_date(value):
    """Best-effort parse of ISO-like / slash dates to an aware datetime."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(value).strip().replace('/', '-')
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', text)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 23, 59, tzinfo=timezone.utc)
    except ValueError:
        return None


def closure_notice(text):
    m = CLOSURE_TEXT.search(text or '')
    return m.group(0) if m else ''


def from_end_date(end_value, now=None):
    """(status, evidence) from an application deadline / validThrough value."""
    now = now or datetime.now(timezone.utc)
    end = parse_date(end_value)
    if end is None:
        return None
    if end < now:
        return CLOSED, f'application end date {end.date()} has passed'
    return OPEN, f'application end date {end.date()} is in the future'


def assess_page(text, valid_through=None, now=None):
    """Status of a vacancy detail page from its own content."""
    notice = closure_notice(text[:20000] if text else '')
    if notice:
        return CLOSED, f'page says "{notice}"'
    by_date = from_end_date(valid_through, now)
    if by_date:
        return by_date
    return UNKNOWN, 'no structured closing date; no closure notice on the page'


def label(status):
    return {OPEN: 'Confirmed open', CLOSED: 'Closed', UNKNOWN: 'Unknown (not confirmed)'}.get(status, status)
