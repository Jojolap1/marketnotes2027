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
| `sources/oracle.py` | Oracle Recruiting Cloud (JPMorgan, Schroders, CIMB) by target-city location facets |
| `sources/eightfold.py` | Eightfold portal (HSBC), per-city pagination |
| `sources/feeds.py` | Goldman Sachs role search, SuccessFactors XML (Pictet), RSS (CA-CIB), Oleeo boards (Nomura, BofA) |
| `sources/sitemap.py` | Sitemap + JSON-LD vacancy pages (Société Générale, Standard Chartered, Optiver), incremental |
| `sources/banks.py` | Bank of America search service, UBS BrassRing, Beisen zhiye (CICC, China Galaxy), BPCE search API (Natixis) |
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

**Send window:** GitHub drops many scheduled runs, so the workflow is triggered every 30 min from 11:07 to 22:37 Vietnam time; a scheduled run sends only if the last send slot is at least `notifications.min_minutes_between_sends` (100) min old (manual runs always send), and
`notifications.send_window` (Asia/Ho_Chi_Minh, 11h-22h) stops any run outside that window from
sending; new jobs then wait for the next slot.

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

**Added 2026-10-19 (third batch, validated by a live dry run):**

| Employer | Official source | Listed | Notes |
|---|---|---|---|
| JPMorgan Paris | Oracle facets found by keyword search (Paris was missing from the default facet list) | — | Paris S&T internships now found |
| Bank of America (all roles, worldwide) | careers.bankofamerica.com search service | 2,079 | London/Paris/HK/SG; plus the Oleeo campus board |
| UBS | jobs.ubs.com BrassRing search (experienced + graduates/interns sites) | 598 | country-only adverts labelled "office assumed" |
| Nomura (experienced) | careers.nomura.com sitemap | 499 | plus the Oleeo campus board |
| SMBC (EMEA) / SMBC (Asia) | SuccessFactors XML | 175 / 262 | |
| Mizuho (EMEA) | careers.mizuhoemea.com sitemap | 32 | |
| CIMB (incl. CIMB Securities) | Oracle Recruiting Cloud (11 career sites) | — | Malaysia-heavy; only target cities kept |
| CICC / China Galaxy (Chinastock) | Beisen zhiye job-ad API (Chinese ads) | 483 / 105 | Chinese city/role keywords |
| Natixis CIB | recrutement.natixis.com search API (the site's own JSON search) | 179 | Paris, Hong Kong, Singapore, London offers |
| Standard Chartered | vacancy pages now give "Posting Start/End Date" | 1,185 | dates + closing date parsed |

**Not covered (documented, not bypassed):** BNP Paribas, Citadel Securities, NatWest Markets, Huatai
(HTTP 403 to automated requests — anti-bot protection, not bypassed; BNP's candidate portal
bwelcome.hr.bnpparibas has no public job list either); Daiwa, UOB Kay Hian, Maybank IB, Guotai Junan
International, CMBI, BOC International, ICBC International, GF Securities (HK), Shenwan Hongyuan (HK),
ICBC / Bank of China / CCB (no online job portal with listings — static pages or email applications).
They stay in `config.json` as disabled sources, so every report lists them.

**Partial:** Crédit Agricole CIB, Amundi, Macquarie, Two Sigma (RSS shows only the 20 latest offers —
new ones are caught every run, older backlog is not).
