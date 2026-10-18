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

**Freshness:** only jobs published in the last `notifications.max_age_days` (14) days are alerted.
A job whose source gives no publication date (e.g. Oleeo boards, Standard Chartered) is still sent,
marked "date not provided by the source". Dates come from the feeds (Workday posting date or
"Posted N days ago", Oracle `PostedDate`, Greenhouse `first_published`, JSON-LD `datePosted`, RSS `pubDate`…).

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

## Source coverage (validated by live dry runs on GitHub Actions, 2026-10-18)

"Listed" = vacancies returned by the official feed for the target countries/cities;
"Relevant" = jobs passing all filters in that run.

| Employer | Official source | Listed | Relevant | Status evidence |
|---|---|---|---|---|
| JPMorgan | Oracle Recruiting Cloud API (CX_1001), London/HK/SG facets | 732 | 11 | feed = live requisitions |
| Goldman Sachs | higher.gs.com role search API (campus + early career) | 225 | 17 | feed status POSTED |
| Morgan Stanley | Workday `ms/External` | 142 | 1 | Workday canApply + end date |
| Citi | Workday `citi/2` | 560 | 3 | Workday canApply + end date |
| Barclays | Workday `barclays/External_Career_Site_Barclays` | 138 | 9 | Workday |
| Deutsche Bank | Workday `db/DBWebsite` | 103 | 4 | Workday |
| HSBC | Eightfold portal API (HK, SG, London, Paris) | 338 | 6 | feed = live postings |
| MUFG | Workday `mufgub/MUFG-Careers` | 165 | 4 | Workday |
| Nomura | Oleeo campus board | 28 | 5 | board + application deadline |
| Bank of America | Oleeo campus board (campus programmes only) | 50 | 1 | board |
| Société Générale | sitemap + JSON-LD | 653 | 5 open, 4 closed | `validThrough` |
| Standard Chartered | sitemap + vacancy pages | 1201 | 5 | unknown (no closing date published) |
| Crédit Agricole CIB | Talentsoft RSS (20 most recent) | 20 | 0 | feed |
| CITIC CLSA (CITIC Securities) | Workday `citicclsa/External` | 213 | 0 | Workday |
| RBC, CIBC, Haitong Intl, BlackRock | Workday | 28 / 8 / 1 / 37 | 0 | Workday |
| Pictet | SuccessFactors XML | 54 | 0 | feed |
| Optiver | sitemap + JSON-LD | 160 | 1 | unknown |
| IMC, Flow Traders, Jane Street, Jump, HRT, DRW, Tower, Point72, Squarepoint, Virtu, XTX | Greenhouse API | 1,262 | 16 | feed |

**Added 2026-10-18 (second batch, validated by a live dry run — 51 sources ok, 0 failed):**
DBS Bank, OCBC / Bank of Singapore, UOB, Santander CIB, ING, BBVA, Invesco, Fidelity International
(Workday + Oleeo campus board), Eastspring (Workday); Schroders (Oracle); ANZ (SuccessFactors XML);
Millennium (Eightfold); Amundi, Macquarie, Two Sigma (RSS, 20 latest offers only); Optiver (now its
Greenhouse board), Mako, Akuna, Old Mission, Eclipse Trading (Greenhouse).

**Not covered (documented, not bypassed):** Citadel Securities (HTTP 403), NatWest Markets (HTTP 403),
Mizuho (EMEA/APAC: no public feed; public Workday site is US-only), Daiwa, UOB Kay Hian, Maybank IB,
CIMB, Guotai Junan International, China Merchants Securities International / CMBI, BOC International,
ICBC International, GF Securities (HK), Shenwan Hongyuan (HK), China Galaxy International (no public
job feed — static pages, 404 or JavaScript-only); BNP Paribas (HTTP 403 Akamai), UBS (BrassRing app,
no public feed), Natixis CIB (JavaScript-only site), SMBC (no discoverable feed), CICC (HTTP 521),
Huatai (HTTP 403), ICBC / Bank of China / CCB (no public English feed). They stay in
`config.json` as disabled sources, so every report lists them.

**Partial:** Bank of America and Nomura (campus programmes only; BofA's public Workday site lists
US roles only), Crédit Agricole CIB (RSS shows only the 20 latest offers — new ones are caught
hourly, older backlog is not), JPMorgan Paris (no Paris location facet exposed; London/HK/SG covered).
