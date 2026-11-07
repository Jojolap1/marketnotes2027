"""Job alerts and LinkedIn hiring posts read from the user's own mailbox.

group.bnpparibas refuses automated requests, but BNP Paribas mails its job
alerts to the user. The mailbox is read over IMAP with an app password kept
in GitHub secrets (never in code or logs): read-only, only messages from the
configured sender, only the last `days` days. Each link to an advert becomes
a job; the advert itself is not fetched (the site would refuse it).
"""
import email
import imaplib
import logging
import os
import re
from datetime import timedelta
from email.utils import parsedate_to_datetime

from jobs import canonical_url, html_to_text, make_job
from status import OPEN

LOG = logging.getLogger(__name__)
PLACE = re.compile(r'(?:Location|Lieu(?: de travail)?|Localisation|City|Ville|Primary Location)\s*:?\s*([^\n|]{2,80})', re.I)
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
    return html or '<pre>' + re.sub(r'(https?://\S+)', r'<a href="\1">\1</a>', text).replace('\n', '<br>') + '</pre>'


def parse_alert(raw, source, ctx):
    """Jobs listed in one alert email (raw RFC822 bytes). `source` may be one
    entry of a source's `alerts` list (company, senders, link_pattern)."""
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
        # BNP alerts give the title only: the city is read on the advert page (fetch)
        location = ctx.city_of(context[:200]) or ctx.city_of(title)
        extra = {}
        if not location and source.get('city_set_in_alert'):
            # Sites that refuse scripts: the city filter is set in the alert itself
            extra = {'alert_city': 'ville filtrée dans ton alerte'}
        company, job_key = source['company'], canonical_url(url)
        if source.get('title_only', source.get('company_from_text')):
            extra['title_only'] = True     # the alert gives no advert text
        if source.get('company_from_text'):
            # Job boards (LinkedIn, eFinancialCareers): one email lists several
            # employers; the line after the title reads 'Company · City, Country'
            company = _employer(context) or source['company']
            ref = re.search(source.get('job_id_pattern') or r'(?!)', url)
            if ref:
                job_key = ref.group(1)
                url = source.get('job_url', '{}').format(ref.group(1))
        jobs.append(make_job(
            id=f'email:{source["company"]}:{job_key}', company=company, title=title,
            location=location, url=url, published=received, description=context[:1500],
            source=f'job-alert email ({source["company"]})', status=OPEN,
            status_evidence=f'sent in a job alert on {received or "an unknown date"}', **extra))
    return jobs


# --- LinkedIn posts ('We're hiring...') emailed as notifications -------------
POST_LINK = re.compile(r'linkedin\.com/(?:comm/)?(?:feed/update/urn:li:(?:activity|share|ugcPost):(\d+)|posts/[^"?#\s]*?(\d{15,}))', re.I)
HIRING = re.compile(
    r"#?\bhiring\b|\brecrut\w*|\brecruit(?:ing|s)?\b|\bwe(?:'|’| a)re looking for\b|\b(?:is|are) looking for\b|\blooking for an? \w+|"
    r"\bjoin (?:our|my|the) (?:team|desk|group)|\bwe(?:'|’| a)re (?:growing|expanding) (?:our|the)\b|\b(?:dm|message) me\b|"
    r"\bopen (?:role|position)s?\b|\bopenings?\b|\bvacanc\w+|\bapplications? (?:are )?(?:now )?open\b|\bapply\b|"
    r"\b(?:send|share) (?:me )?(?:your|vos|ton) (?:cv|resume)\b|"
    # French: 'on recrute', 'nous cherchons', 'rejoignez-nous', 'stage à pourvoir', 'CV en MP'...
    r"\b(?:nous|on|je) (?:re)?cherch\w*|\b(?:nous sommes|je suis|sommes) à la recherche\b|\bà la recherche d'une? (?:stagiaire|alternant|analyste|vendeur|trader|structureur|junior|v\.?i\.?e)|"
    r"\brejoi(?:ndre|gnez|ns)(?:-nous)? (?:notre|mon|l'|nos)?|\brejoignez-nous\b|\bvenez (?:nous )?rejoindre\b|\bint[ée]grer (?:notre|mon|l')|"
    r"\b(?:notre|mon) [ée]quipe (?:s'agrandit|grandit|recrute)|\boffres? (?:de )?(?:stage|alternance|v\.?i\.?e|poste|emploi)\b|"
    r"\bopportunit[ée]s? (?:de stage|d'alternance|en v\.?i\.?e)|\b(?:stage|poste|v\.?i\.?e|alternance)s? (?:à pourvoir|ouverts?|disponibles?)\b|"
    r"\bcandidatures? (?:sont )?ouvertes\b|\bpostule[rz]\b|\benvoye[rz]-?(?:moi)? (?:vos|votre|ton) cv\b|\bcv (?:en|par) (?:mp|message priv[ée]|dm)\b", re.I)
# Candidates looking for a job are not hiring ('je recherche un stage', #OpenToWork)
SEEKER = re.compile(
    r"#?open ?to ?work\b|\b(?:i am|i'm|i’m|currently) (?:actively )?(?:looking|seeking|searching) for (?:an? |my )?(?:new |next )?(?:internship|opportunit|position|role|job|placement)|"
    r"\bje (?:suis (?:actuellement )?à la )?recherche (?:d'|un |une |activement )(?:stage|alternance|emploi|poste|opportunit\w*|cdi|v\.?i\.?e)\b|"
    r"\b(?:je suis |actuellement )?à la recherche d'(?:un stage|une alternance|un emploi|un poste|une opportunit\w*|un v\.?i\.?e)\b", re.I)
STRONG_HIRING = re.compile(r"#?\bhiring\b|\brecrut\w*|\brecruiting\b|\bjoin (?:our|my|the) (?:team|desk)|\brejoignez|\bnous (?:re)?cherch|\bon (?:re)?cherch", re.I)
AUTHOR = re.compile(r"([A-Z][^\n.!?:]{1,70}?)\s+(?:posted|shared|reposted|commented|is hiring|a publié|a partagé|a republié|a commenté|recrute)\b")
SENTENCE = re.compile(r'(?<=[.!?])\s+|\s+[|•·¶]\s+')
BREAK = re.compile(r'<br\s*/?>|</(?:p|div|li|tr|td|h\d)>', re.I)


def parse_posts(raw, source, ctx):
    """Hiring posts in a LinkedIn notification / digest / message email.

    A post becomes a job only when it says it is hiring and one of its
    sentences names a targeted role: that sentence is the title, the post text
    is the description (entry-level and experience checks), the city is read
    in the post. LinkedIn itself is never fetched: the post link is the URL."""
    message = email.message_from_bytes(raw)
    try:
        received = parsedate_to_datetime(message.get('Date')).date().isoformat()
    except (TypeError, ValueError):
        received = ''
    html = BREAK.sub(' ¶ ', _html(message))   # keep the post's line breaks as sentence ends
    # Each link spans its whole <a ...>...</a>, so the post text inside belongs to it
    links = [(html.rfind('<', 0, m.start()), _tag_end(html, m.end()), m.group(1) or m.group(2))
             for m in POST_LINK.finditer(html)]
    blocks = []
    if links:
        ids = list(dict.fromkeys(i for _, _, i in links))
        spans = {i: (min(a for a, _, j in links if j == i), max(b for _, b, j in links if j == i)) for i in ids}
        for n, i in enumerate(ids):
            lo = spans[ids[n - 1]][1] if n else 0
            hi = spans[ids[n + 1]][0] if n + 1 < len(ids) else len(html)
            a, b = spans[i]
            # A post's text sits around its link(s), never across another post
            block = html_to_text(html[max(lo, a - 2500):min(hi, b + 2500)])
            blocks.append((f'linkedin:{i}', f'https://www.linkedin.com/feed/update/urn:li:activity:{i}/', block))
    else:
        # Messages (InMail) carry no post link: the email itself is the post
        key = 'message:' + (re.sub(r'\W', '', message.get('Message-ID') or '')[-40:] or received)
        thread = re.search(r'https://www\.linkedin\.com/(?:comm/)?messaging/[^"\s]*', html)
        blocks.append((key, thread.group(0).split('?')[0] if thread else 'https://www.linkedin.com/messaging/',
                       html_to_text(html)))
    return post_jobs(blocks, source, ctx, received, f'hiring post emailed by LinkedIn on {received or "an unknown date"}')


def post_jobs(blocks, source, ctx, published, evidence):
    """(key, url, text) posts -> jobs: kept only when the post says it is
    hiring and a sentence next to those words names a targeted role (that
    sentence is the title, the post text the description)."""
    jobs = []
    for key, url, text in blocks:
        ctx.stat(source['company'], 'posts')
        if not HIRING.search(text) or (SEEKER.search(text) and not STRONG_HIRING.search(text)):
            continue
        ctx.stat(source['company'], 'hiring_posts')
        # 'recruiting' would read as a recruiter job (non front-office title)
        # and the role must sit next to the hiring words, not elsewhere in a digest
        near = [(max(0, m.start() - 400), m.end() + 400) for m in HIRING.finditer(text)]
        title, pos = '', 0
        for s in SENTENCE.split(text):
            start, pos = text.find(s, pos), pos + len(s)
            s = _hiring_verb(s.strip(' ¶'))[:160]
            if (len(s) >= 12 and len(re.findall(r'\w+', s)) >= 3 and any(a <= start <= b for a, b in near)
                    and ctx.title_worth_fetching(s)):
                title = s
                break
        # The sentence announcing the hire names a back-office role ('We're
        # hiring in Dubai: Tax Consultant Intern'): a 'tax structuring' word
        # elsewhere does not make it a markets job
        announce = [_hiring_verb(x) for x in SENTENCE.split(text) if HIRING.search(x)]
        if not title or any(p.search(x) and not ctx.title_worth_fetching(x)
                            for x in announce for p in ctx.classifier.non_front_office):
            continue
        author = AUTHOR.search(text)
        company = author.group(1).strip() if author else source['company']
        # A post often says 'graduate' / 'stage' / 'junior' in another sentence
        # than the role: read the level around the title so junior posts are kept
        at = text.find(title[:40])
        level = _level_word(ctx, title) or _level_word(ctx, text[max(0, at - 300):at + 500])
        jobs.append(make_job(
            id=f'post:{key}', company=company, title=title,
            location=ctx.city_of(text), url=url, published=published,
            description=re.sub(r'\s*¶\s*', ' ', text).strip()[:3000],
            employment_type=(f'{level} — ' if level else '') + 'LinkedIn post (read it for how to apply)',
            source=f'LinkedIn post ({source["company"]})', status='unknown', status_evidence=evidence, not_an_advert=True,
            apply_email=', '.join(_contact_emails(text))))
    return jobs


EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}', re.I)


def _contact_emails(text):
    """Addresses the post gives to send a CV to (not LinkedIn's own)."""
    found = []
    for address in EMAIL.findall(text or ''):
        address = address.rstrip('.').lower()
        if not re.search(r'linkedin\.com$|noreply|no-reply|example\.', address) and address not in found:
            found.append(address)
    return found[:2]


def _level_word(ctx, text):
    """First internship / graduate / junior word in the text, as written."""
    for patterns in ctx.classifier.entry.values():
        for pattern in patterns:
            found = pattern.search(text or '')
            if found:
                return found.group(0)
    return ''


def _tag_end(html, pos):
    end = html.find('</a>', pos)
    return end + 4 if end >= 0 else pos


def _hiring_verb(sentence):
    sentence = re.sub(r'\brecruit\w*', 'hiring', sentence, flags=re.I)
    return re.sub(r'\brecrut\w*', 'recherche', sentence, flags=re.I)


def _employer(context):
    """Employer named right after the title in job-board alerts."""
    head = re.split(r'\s+(?:·|•|\||–|-)\s+', context.strip(), maxsplit=1)
    if len(head) == 2 and 1 < len(head[0]) <= 80:
        return head[0].strip()
    return ''


def fetch(source, ctx):
    """One IMAP session for the source; `alerts` lists several employers
    (company, senders, link_pattern, city_set_in_alert, fetch_advert),
    otherwise the source itself is the single employer."""
    address = os.environ.get(source.get('address_env', 'ALERT_EMAIL'), '')
    password = os.environ.get(source.get('password_env', 'ALERT_EMAIL_APP_PASSWORD'), '')
    if not address or not password:
        ctx.stat(source['company'], 'mailbox_not_configured')
        return []
    alerts = [dict(source, **a) for a in source['alerts']] if source.get('alerts') else [source]
    since = (ctx.now - timedelta(days=int(source.get('days', 14)))).strftime('%d-%b-%Y')
    box = imaplib.IMAP4_SSL(source.get('imap_host', 'imap.gmail.com'))
    jobs = {}
    try:
        box.login(address, password)
        box.select('INBOX', readonly=True)
        emails = 0
        for alert in alerts:
            found = {}
            for sender in alert['senders']:
                status, data = box.search(None, 'FROM', f'"{sender}"', 'SINCE', since)
                if status == 'OK':
                    for num in data[0].split():
                        found[num] = True
            emails += len(found)
            for num in found:
                status, data = box.fetch(num, '(BODY.PEEK[])')   # PEEK: leaves the mail unread
                if status == 'OK' and data and isinstance(data[0], tuple):
                    parse = parse_posts if alert.get('posts') else parse_alert
                    for job in parse(data[0][1], alert, ctx):
                        job['_alert'] = alert
                        jobs.setdefault(job['id'], job)
        LOG.info('%s: %d alert emails since %s', source['company'], emails, since)
        ctx.stat(source['company'], 'alert_emails', emails)
        ctx.stat(source['company'], 'listed', len(jobs))
    finally:
        try:
            box.logout()
        except Exception:
            pass
    for job in jobs.values():
        alert = job.pop('_alert')
        if (not job['location'] and alert.get('fetch_advert', True)
                and ctx.title_worth_fetching(job['title'])):
            job['location'], job['description'] = _advert_place(ctx, job['url'], job['description'])
    return list(jobs.values())


def _advert_place(ctx, url, description):
    """City (and text) of the advert page an alert links to; '' when unreachable."""
    try:
        response = ctx.http.get(url)
    except Exception as exc:  # the site may refuse scripts: keep the job, location unknown
        LOG.info('  advert page unreachable (%s)', type(exc).__name__)
        return '', description
    host = re.sub(r'^https?://([^/?#]+).*$', r'\1', response.url or url)
    if response.status_code != 200:
        LOG.info('  advert page %s: HTTP %s', host, response.status_code)
        return '', description
    text = html_to_text(re.sub(r'(?s)<(script|style)\b.*?</\1>', ' ', response.text))
    labelled = [m.group(1) for m in PLACE.finditer(text)]
    city = next((ctx.city_of(v) for v in labelled if ctx.city_of(v)), '')
    LOG.info('  advert page %s: HTTP 200, location fields %r -> %r', host, [v[:40] for v in labelled[:3]], city)
    return city or (labelled[0][:80] if labelled else ''), text[:6000]
