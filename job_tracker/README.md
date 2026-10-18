# Job tracker — how it works

```
config.json ──► sources/* (one adapter per feed type) ──► filters.py ──► status ──► state.py ──► notifier.py
                     │ public ATS APIs / sitemaps / RSS          │ classify    open/closed/  dedup     Telegram
                     ▼                                           ▼ EN + FR     unknown      history
               http_client.py (polite, no bypass)          report.py (reports/last_run.md + GitHub job summary)
```

| Module | Role |
|---|---|
| `tracker.py` | Orchestrates one run; `matches(job)` kept for compatibility |
| `sources/workday.py` | Workday CXS API: city/country facets, full pagination, cached details |
| `sources/oracle.py` | Oracle Recruiting Cloud (JPMorgan) by target-city location facets |
| `sources/eightfold.py` | Eightfold portal (HSBC), per-city pagination |
| `sources/feeds.py` | Goldman Sachs role search, SuccessFactors XML (Pictet), RSS (CA-CIB), Oleeo boards (Nomura, BofA) |
| `sources/sitemap.py` | Sitemap + JSON-LD vacancy pages (Société Générale, Standard Chartered, Optiver), incremental |
| `sources/ats.py` | Greenhouse / Lever / Ashby / SmartRecruiters |
| `filters.py` | City, role family, entry type, seniority, experience requirement (English + French) |
| `status.py` | Application status evidence |
| `state.py` | `seen_jobs.json` history (v2), duplicate fingerprints, source health |
| `notifier.py` | Telegram formatting/sending, `--check-telegram` |
| `tools/probe_sources.py` | Manual discovery probe for new feeds (workflow "Source probe") |

## Filtering rules (all configurable in `config.json` → `filters`)

A job is relevant only if **all** hold:

1. **Location field** (never the page text) is in a target city: `cities` (aliases incl. Kowloon, Central HK, La Défense, Montrouge, Londres, Singapour; "New London" excluded).
2. Title has no **senior** marker (`senior_title_patterns`: Senior, VP, Director, Head, MD, Lead, Responsable, confirmé…); `soft_senior_title_patterns` (Associate, Manager, Specialist…) are senior unless the title names a programme (e.g. *Summer Associate Internship*).
3. Title is not **non-front-office** (`non_front_office_patterns`: engineering, IT, compliance, risk, operations, IBD/ECM/DCM, wealth, HR…).
4. Title matches a **role family** (`role_families`).
5. No senior **corporate title** in the description ("Corporate title: Associate/VP…").
6. **Experience**: the most restrictive professional-experience requirement found (EN/FR: "3+ years", "minimum 2 ans d'expérience", "2 à 4 ans"…) must be ≤ `max_years_experience` (default 1). Numbers about degrees, programme/contract length, company history are ignored. Ranges straddling the limit (e.g. 1–2 years) are *borderline*: accepted only for internships/graduate programmes/VIE.
7. **Entry evidence** (`require_entry_signal`): internship / stage / off-cycle / summer, graduate programme / trainee, VIE, apprenticeship / alternance, junior / assistant in the title — or explicit entry-level wording in the description (recent graduates, jeune diplômé, 0–1 year…). Bare titles like "Equity Sales Trader" are rejected.

## Application status

| Status | Evidence |
|---|---|
| `open` | Listed by an official ATS feed that only publishes live postings (Workday, Oracle, Eightfold, Greenhouse, GS role search, SuccessFactors, RSS, Oleeo), or a future `validThrough`/deadline, and no closure notice |
| `closed` | Closure notice on the page (EN/FR), expired end date/deadline, page 404/410 |
| `unknown` | Page parsed but no structured closing date (e.g. Optiver). HTTP 200 alone is never "open" |

`notifications.statuses` controls which statuses alert (default `open` and `unknown`; unknown alerts are labelled *Unknown (not confirmed)*). Closed jobs never alert.

## Duplicate prevention

Each job gets several fingerprints: source id, company+requisition id, canonical URL (tracking params, `www`, Workday locale and SG `-en/-fr` suffix removed) and company+normalized title+city. Any match with history = already seen, so URL formatting changes or minor title edits do not re-alert. A same-title match older than `title_dedup_days` (180) counts as a new repost. Alerts are marked sent only after Telegram confirms delivery; at most `max_per_run` (15) per run, the rest go out next hour.

## Running

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -t .      # offline tests
DRY_RUN=1 python tracker.py                    # live fetch, no alerts, no state change
python tracker.py --check-telegram             # validates secrets without sending
python tracker.py                              # production
```

Secrets (GitHub → Settings → Secrets and variables → Actions): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Adding an employer

Add an entry to `sources` (see existing ones for each `platform`). For a Workday site the
`host/tenant/site` come from its `https://<tenant>.wdN.myworkdayjobs.com/<site>` URL. Run the
"Source probe" workflow to test a new endpoint before enabling it. Sources that cannot be
read legitimately stay listed with `"enabled": false` and a `disabled_reason`, so every
report shows the gap.
