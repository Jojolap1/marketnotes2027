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
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

OPEN, CLOSED, UNKNOWN = 'open', 'closed', 'unknown'
MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}

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
    raw = str(value).strip()
    if raw.isdigit() and len(raw) >= 10:
        return parse_date(int(raw))
    # Workday listing text: 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', 'Posted 30+ Days Ago'
    rel = re.match(r'(?i)posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)', raw)
    if rel:
        days = 0 if rel.group(1).lower() == 'today' else 1 if rel.group(1).lower() == 'yesterday' else int(rel.group(2))
        return datetime.now(timezone.utc) - timedelta(days=days)
    if re.match(r'[A-Za-z]{3},\s+\d', raw):  # RFC 822 (RSS pubDate)
        try:
            return parsedate_to_datetime(raw).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None
    named = re.match(r'(\d{1,2})[-\s]([A-Za-z]{3})[A-Za-z]*[-\s,]+(\d{4})', raw) or re.match(r'([A-Za-z]{3})[A-Za-z]*\s+(\d{1,2}),?\s+(\d{4})', raw)
    if named:
        a, b, year = named.groups()
        day, mon = (a, b) if a.isdigit() else (b, a)
        month = MONTHS.get(mon[:3].lower())
        if month:
            try:
                return datetime(int(year), month, int(day), 23, 59, tzinfo=timezone.utc)
            except ValueError:
                return None
    text = raw.replace('/', '-')
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', text)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 23, 59, tzinfo=timezone.utc)
    except ValueError:
        return None


def age_days(published, now=None):
    """Whole days since publication, or None when no usable date exists."""
    d = parse_date(published)
    if d is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, (now - d).days)


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
