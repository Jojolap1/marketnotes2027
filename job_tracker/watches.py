"""Page watches: employers that publish no job list (Sucden). The text of one
section of their careers page is fingerprinted; a change triggers a short
Telegram message so the page can be checked by hand."""
import hashlib
import html
import logging

from jobs import html_to_text

LOG = logging.getLogger('watches')


def section_text(page_html, start, end):
    text = html_to_text(page_html)
    i = text.find(start)
    if i < 0:
        raise ValueError(f'section "{start}" not found on the page (site changed?)')
    j = text.find(end, i + len(start)) if end else -1
    return text[i:j if j > 0 else i + 3000].strip()


def check(watches, http, state):
    """Returns (changes, notes): changes = [(watch, digest, text)] not yet notified."""
    seen = state.data.setdefault('page_watches', {})
    changes, notes = [], []
    for w in watches or []:
        name = w['name']
        try:
            response = http.get(w['url'])
            response.raise_for_status()
            text = section_text(response.text, w['section_start'], w.get('section_end', ''))
        except Exception as exc:  # a broken watch never stops the run
            notes.append(f'{name}: FAILED ({type(exc).__name__}: {exc})')
            continue
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
        if name not in seen:
            seen[name] = digest  # first run: baseline only
            notes.append(f'{name}: baseline recorded')
        elif seen[name] != digest:
            changes.append((w, digest, text))
            notes.append(f'{name}: section changed')
        else:
            notes.append(f'{name}: unchanged')
    return changes, notes


def message(watch, text):
    return (f'🔔 <b>{html.escape(watch["name"])}</b>: the job section of its careers page has changed '
            f'(this employer publishes no job list, so check it by hand).\n\n'
            f'{html.escape(text[:900])}')


def mark_sent(state, watch, digest):
    state.data.setdefault('page_watches', {})[watch['name']] = digest
