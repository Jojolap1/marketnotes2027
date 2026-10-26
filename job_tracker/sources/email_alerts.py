"""Employer job-alert emails read from the user's own mailbox (BNP Paribas).

group.bnpparibas refuses automated requests, but BNP Paribas mails its job
alerts to the user. The mailbox is read over IMAP with an app password kept
in GitHub secrets (never in code or logs): read-only, only messages from the
configured sender, only the last `days` days. Each link to an advert becomes
a job; the advert itself is not fetched (the site would refuse it).
"""
import email
import imaplib
import os
import re
from datetime import timedelta
from email.utils import parsedate_to_datetime

from jobs import canonical_url, html_to_text, make_job
from status import OPEN

LINK = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
# Navigation links of the alert email, not adverts
NOT_AN_ADVERT = re.compile(r'unsubscri|d[ée]sabonn|d[ée]sinscri|view all|see all|voir (?:toutes|tous|plus)|manage|g[ée]rer|'
                           r'privacy|confidentialit|preferences|pr[ée]f[ée]rences|log ?in|connexion|mot de passe|password', re.I)


def _html(message):
    parts = message.walk() if message.is_multipart() else [message]
    html = text = ''
    for part in parts:
        ctype = part.get_content_type()
        if ctype not in ('text/html', 'text/plain'):
            continue
        body = part.get_payload(decode=True) or b''
        decoded = body.decode(part.get_content_charset() or 'utf-8', 'replace')
        if ctype == 'text/html':
            html += decoded
        else:
            text += decoded
    return html or '<pre>' + re.sub(r'(https?://\S+)', r'<a href="\1">\1</a>', text) + '</pre>'


def parse_alert(raw, source, ctx):
    """Jobs listed in one alert email (raw RFC822 bytes)."""
    message = email.message_from_bytes(raw)
    try:
        received = parsedate_to_datetime(message.get('Date')).date().isoformat()
    except (TypeError, ValueError):
        received = ''
    html = _html(message)
    link_ok = re.compile(source['link_pattern'], re.I)
    jobs = []
    links = list(LINK.finditer(html))
    for i, match in enumerate(links):
        url, title = match.group(1).replace('&amp;', '&'), html_to_text(match.group(2))
        if not link_ok.search(url) or len(title) < 6 or NOT_AN_ADVERT.search(title):
            continue
        # The lines after the title name the city (and contract); stop at the
        # next link so the next offer's city is never borrowed.
        end = links[i + 1].start() if i + 1 < len(links) else match.end() + 600
        context = html_to_text(html[match.end():min(end, match.end() + 600)])
        location = ctx.city_of(context[:200]) or ctx.city_of(title) or context[:120]
        jobs.append(make_job(
            id=f'email:{source["company"]}:{canonical_url(url)}', company=source['company'], title=title,
            location=location, url=url, published=received, description=context[:1500],
            source='employer job-alert email', status=OPEN,
            status_evidence=f'sent by the employer in a job alert on {received or "an unknown date"}'))
    return jobs


def fetch(source, ctx):
    address = os.environ.get(source.get('address_env', 'ALERT_EMAIL'), '')
    password = os.environ.get(source.get('password_env', 'ALERT_EMAIL_APP_PASSWORD'), '')
    if not address or not password:
        ctx.stat(source['company'], 'mailbox_not_configured')
        return []
    since = (ctx.now - timedelta(days=int(source.get('days', 14)))).strftime('%d-%b-%Y')
    box = imaplib.IMAP4_SSL(source.get('imap_host', 'imap.gmail.com'))
    try:
        box.login(address, password)
        box.select('INBOX', readonly=True)
        found = {}
        for sender in source['senders']:
            status, data = box.search(None, 'FROM', f'"{sender}"', 'SINCE', since)
            if status == 'OK':
                for num in data[0].split():
                    found[num] = True
        jobs = {}
        for num in found:
            status, data = box.fetch(num, '(BODY.PEEK[])')   # PEEK: leaves the mail unread
            if status == 'OK' and data and isinstance(data[0], tuple):
                for job in parse_alert(data[0][1], source, ctx):
                    jobs.setdefault(job['id'], job)
        ctx.stat(source['company'], 'alert_emails', len(found))
        ctx.stat(source['company'], 'listed', len(jobs))
        return list(jobs.values())
    finally:
        try:
            box.logout()
        except Exception:
            pass
