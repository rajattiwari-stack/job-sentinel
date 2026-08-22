# Job Sentinel 🛡️

A zero-cost, production-grade job-hunting agent. Runs **4× daily** (10:00 / 14:00 / 18:00 / 22:00 IST) on GitHub Actions, scans the career pages of security & big-tech companies, filters for **cybersecurity roles** (ZIA, ZPA, Zscaler, Avalor, EDR, SASE, network security, …) with **0–2 years** experience located in **India (any mode)** or **remote-open-to-India/worldwide**, and pushes every *new* job — with its direct apply link — to your **Telegram** the moment it appears.

**Cost: ₹0. Forever.** No servers, no credit card. GitHub Actions is free on public repos, Telegram bots are free.

---

## How it works (architecture)

```
GitHub Actions cron (4× daily, UTC-shifted to IST)
        │
        ▼
src/main.py ── ThreadPool ──► ATS adapters (parallel, isolated per company)
        │                      ├─ Greenhouse  boards-api.greenhouse.io   (JSON)
        │                      ├─ Lever       api.lever.co               (JSON)
        │                      ├─ Ashby       api.ashbyhq.com            (JSON)
        │                      ├─ SmartRecruiters api.smartrecruiters.com(JSON, paginated)
        │                      ├─ Workday     <host>/wday/cxs/…          (JSON, paginated)
        │                      ├─ Amazon      amazon.jobs/search.json     (custom big-tech)
        │                      └─ Microsoft   gcsservices.careers.microsoft.com (custom big-tech)
        ▼
Matcher: keywords (word-boundary safe) → seniority veto → location policy → 0–2 yrs experience
        ▼
SeenStore (state/seen_jobs.json, committed back to repo) → only NEW jobs pass
        ▼
Notifier: Telegram (chunked, HTML-escaped) + optional email fallback
        ▼
tracker.xlsx (Applied? dropdown, your edits preserved) + docs/index.html (GitHub Pages dashboard)
```

**Why ATS APIs instead of HTML scraping?** ~90% of tech companies host jobs on one of five ATS platforms, each with a stable public JSON endpoint. HTML scraping breaks every redesign; these APIs don't. One adapter unlocks *every* company on that platform — so adding a company is 3 lines of YAML, not new code.

---

## Setup (10 minutes, one time)

### 1. Create the Telegram bot
1. In Telegram, message **@BotFather** → `/newbot` → follow prompts → copy the **bot token**.
2. Message **your new bot** anything (e.g., "hi") — this opens the chat.
3. Get your chat id: open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy `"chat":{"id": <NUMBER>}`.

### 2. Create the repo
```bash
# from this folder
git init && git add -A && git commit -m "Job Sentinel v1"
gh repo create job-sentinel --public --source . --push
# (or create a repo on github.com and `git remote add origin … && git push`)
```
> Public repo = unlimited free Actions minutes. Private also works (2,000 free min/month; each run takes ~3–5 min, ~600 min/month — still free).

### 3. Add secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | from getUpdates |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `EMAIL_TO` | *(optional email fallback — e.g. Gmail with an App Password)* |

### 4. (Optional, recommended) Turn on the phone dashboard
Repo → **Settings → Pages → Source: Deploy from a branch → Branch: main, folder: /docs → Save.**
In ~1 minute you get `https://<your-username>.github.io/job-sentinel/` — a searchable, mobile-friendly board of every tracked job with apply links and Applied/Pending badges. (Public URL — it contains only public job links, nothing personal. Skip this step if you prefer.)

### 5. Validate the company list, then launch
```bash
pip install -r requirements.txt
python scripts/validate_companies.py   # fixes wrong ATS slugs BEFORE going live
python -m src.main --dry-run           # see matches locally, nothing sent
```
Then repo → **Actions → Job Sentinel → Run workflow** for the first live run. After that it runs itself 4× daily.

> ⚠️ First run notifies *everything* currently open that matches (could be 30–60 jobs, capped at 60/run). Every later run sends only what's new.

---

## Your Excel tracker (`tracker.xlsx`)

Every run, the bot appends new jobs to **`tracker.xlsx`** in the repo root:

| Job ID | Date Found | Company | Position (clickable link) | Location | Experience | Matched Keywords | **Applied?** | Applied Date | Notes | Link |
|---|---|---|---|---|---|---|---|---|---|---|

- **Applied?** is a **Yes/No dropdown**; picking **Yes turns the whole row green**.
- A **Summary sheet** live-counts Total / Applied / Pending with Excel formulas.
- **Your edits are sacred**: the bot merges by Job ID — your Yes/No, dates, and notes survive every update. Rows you delete stay deleted.
- Newest jobs always appear **on top**.

**How to update it:** clone the repo once (`git clone …`), then whenever you want to mark applications:
```bash
git pull          # get the latest jobs the bot added
# open tracker.xlsx in Excel, mark Applied? = Yes, save
git commit -am "applied to 3 roles" && git push
```
(Or edit on any machine with GitHub Desktop — pull, edit, push.) Pull before editing to avoid conflicts with the bot's commits.

## Day-2 operations

**Add a company** — edit `config/companies.yaml`:
```yaml
  - name: SomeCompany
    ats: greenhouse        # find the ATS from their careers URL (see file header)
    slug: somecompany
    priority: high         # high|medium|low — ranks the alert, never filters
```
Then run `python scripts/validate_companies.py`. That's the whole change.

**Add a hundred companies** — you usually have a list of *names and careers-page
links*, which is not something a scraper can use. `discover_ats.py` converts it:

```bash
# 1. resolve names -> real ATS boards (probes 4 JSON APIs, keeps only live hits)
python scripts/discover_ats.py --input my_companies.json \
       --out discovered.yaml --unresolved unresolved.csv

# 2. merge into the registry WITHOUT clobbering hand-verified entries
python scripts/merge_companies.py --curated config/companies.yaml \
       --discovered discovered.yaml --out config/companies.yaml

# 3. confirm every board actually serves jobs
python scripts/validate_companies.py --quiet
```

Expect roughly **1 in 5 names to resolve**. That is not a bug: most companies
aren't on a public-API ATS at all, and the rest publish only through LinkedIn
or a bespoke careers page with no stable JSON behind it. `unresolved.csv` lists
every miss with its reason so nothing disappears quietly.

Two traps the discovery step handles, both of which produce *plausible-looking*
garbage rather than errors:

- **SmartRecruiters answers HTTP 200 with `totalFound: 0` for any string you
  put in the URL** — real customer or not. Status code proves nothing there, so
  only a non-empty board counts as a hit.
- **Slug collisions.** "Apollo Hospitals" and "Apollo Diagnostics" both guess
  `apollo`, which on Greenhouse is Apollo GraphQL. When two companies land on
  one board, at most one is right and there's no way to tell which — so neither
  is kept.

**Scanning several hundred boards** — a single run may not fit in the Actions
window. Set `scan.shards` in `config/settings.yaml` to split the roster across
consecutive runs (4 shards × 4 runs/day = full coverage daily, each run short).
`shards: 1` scans everything every run.

**Change the experience window** — `max_experience_years` in the active profile.
If you raise it above ~4, also trim `exclude_titles`: at 0–2 years a "Senior"
title is disqualifying, at 5+ it is the target.

**Change roles later (data engineer, SWE, …)** — `config/settings.yaml` already contains ready-made `data_engineering` and `software_engineering` profiles. Flip `active_profile`, or run a second profile in the same workflow by adding a step: `python -m src.main --profile data_engineering`.

**Change schedule** — edit the cron lines in `.github/workflows/scrape.yml` (GitHub cron is **UTC**; IST = UTC+5:30).

**Pause a company** — add `enabled: false` to its YAML entry.

**A company went silent?** — its slug probably changed (ATS migration). `validate_companies.py` finds it in seconds; the run summary in Actions logs also lists per-company failures.

---

## Reliability & edge cases (what's engineered in)

| Concern | Handling |
|---|---|
| One company's API breaks | Per-company isolation — logged & reported, run continues |
| Rate limits / flaky networks | Retries with exponential backoff + jitter; `Retry-After` honored; per-host politeness delay; hard timeouts |
| Duplicate notifications | SHA-256 fingerprint per job (ATS id + title + location) stored in `state/seen_jobs.json`, committed atomically back to the repo |
| Telegram outage | Jobs are marked "seen" **only after** delivery succeeds → automatic retry next run (at-least-once delivery) |
| Telegram 4096-char limit | Messages chunked on job boundaries; titles HTML-escaped |
| "Remote (US only)" traps | Region-lock detection: global/APAC/India remote passes; US/EMEA/LATAM-locked remote is rejected — **including** the common forms that never say "US only" (`Remote - Texas, USA`, `Remote, Ohio`, `Remote (Anywhere in the US)`) |
| "8+ years" senior roles | Experience parser reads ranges, "X+", "minimum of X", takes the smallest stated requirement, keeps ≤2 |
| Senior roles that state no years | Title veto (`exclude_titles`): Senior/Staff/Principal/Lead/Manager/Architect/… are dropped outright. The experience parser only sees numbers a posting bothered to write down; plenty of senior posts write none |
| A low number hiding in a senior post | Kept (never silently dropped) but the alert is tagged — `min 2 yrs (also asks 8 — verify)` |
| Unstated experience | Kept, tagged `unspecified`; senior-sounding titles flagged for your review |
| False keyword hits | Word-boundary regex — `EDR` never matches inside "redraw", `ZIA` never inside other words |
| **Security-vendor boilerplate** | The keyword must hit the **title or department**, never the description alone. Every Zscaler posting — Procurement, Employee Relations, Account Executive — carries an "About us" blurb naming zero trust / cloud security / SASE, so a description-anywhere match scored a procurement job exactly like a security role. Description hits still *rank* a job; they can't *qualify* one. Relax with `require_role_match: false` |
| Runaway pagination | Hard page caps on SmartRecruiters/Workday |
| One employer eating the whole run | Per-company detail-fetch budgets (Workday 40, SmartRecruiters 60), India/remote postings spent first |
| Roster outgrowing the run window | `scan.shards` in settings.yaml splits companies across consecutive runs, sliced by a stable name hash so edits don't reshuffle coverage |
| Politeness under concurrency | Per-host delay reserves its slot under a lock — an unlocked read-check-sleep silently stops delaying at exactly the concurrency where it matters |
| Concurrent runs racing on state | Actions `concurrency` group + rebase-before-push |
| Notification floods | 60 jobs/run cap; overflow rolls to the next run |
| State file growing forever | 90-day pruning on every save |
| Total systemic failure | Non-zero exit (→ red ❌ + GitHub email) only if >50% of companies fail |
| **A board that returns 200 with zero jobs** | The silent killer — nothing throws, the log says "0 postings", and the company is dead for months. `validate_companies.py` reports empty boards as failures, not as OK |

**Tests:** `python -m pytest tests/ -v` — 32 tests covering the riskiest logic (keyword boundaries, location policy, experience parsing, seniority veto, ranking).

---

## Legal & etiquette
This reads the same public JSON endpoints each company's own careers page calls, at low volume (4 polite runs/day with delays and backoff). Don't lower the politeness delays or add hundreds of companies to a single run without spreading schedules.

## Roadmap ideas
- Per-profile Telegram channels (security vs data-eng feeds)
- A daily HTML digest artifact
- LinkedIn/Naukri aggregator adapters (they require auth/ToS review — deliberately excluded from v1)
- An LLM re-ranking pass (Claude API) to score description fit against your resume
