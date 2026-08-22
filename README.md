# Job Sentinel 🛡️

A zero-cost, production-grade job-hunting agent. Runs **4× daily** (10:00 / 14:00 / 18:00 / 22:00 IST) on GitHub Actions, scans 300+ security & big-tech job boards, filters for **cybersecurity roles** (ZIA, ZPA, Zscaler, Avalor, EDR, SASE, network security, …) with **0–3 years** experience located in **India (any mode)** or **remote-open-to-India/worldwide**, and pushes every *new* job — with its direct apply link — to your **Telegram** the moment it appears.

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
Matcher: seniority veto → keyword must hit TITLE/DEPT → location → 0–2 yrs
        ▼
SeenStore (state/seen_jobs.json, committed back to repo) → only NEW jobs pass
        ▼
Notifier: Telegram (chunked, HTML-escaped) + optional email fallback
        ▼
Self-healer: any company that failed OR returned 0 postings is re-resolved
             against the ATS APIs; a live board rewrites companies.yaml
        ▼
tracker.xlsx (Applied? dropdown, your edits preserved) + docs/index.html (GitHub Pages dashboard)
```

**Getting a company list into the scraper.** A list of names and careers links
isn't usable as-is — `scripts/discover_ats.py` resolves names to real ATS
boards and `merge_companies.py` folds them into the registry without
clobbering hand-verified entries. See [Day-2 operations](#day-2-operations).

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

**Scanning several hundred boards.** Measured: a full sweep of all ~250 boards
takes **~7 minutes** at 12 workers (timed over a home connection, so an upper
bound versus a GitHub runner) against a 45-minute job timeout. Sharding is
therefore **off** — it would cost alert latency for headroom the measurement
says we don't need, and applying early matters.

If the roster grows several-fold, set `scan.shards` to split it across
consecutive runs (4 shards × 4 runs/day = full coverage daily, each run short).
The rotation is implemented and tested; it just isn't needed yet.

Where the time actually goes, per the same measurement:

| ATS | boards | total | why |
|---|---|---|---|
| SmartRecruiters | 40 | 1694s | one detail request per posting, all serialized behind a single host |
| Greenhouse | 152 | 1117s | one request per board — cheap, just many of them |
| Workday | 15 | 943s | search × pages, then a detail request per posting |
| Lever | 37 | 257s | one request per board |

Workday and SmartRecruiters dominate despite being 22% of the roster, which is
why both have per-company fetch budgets.

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
| "Remote (US only)" traps | **Allowlist, not blocklist.** A remote posting qualifies only if its location says India, says global, or says nothing specific. Rejecting known-bad regions fails open — `Palo Alto`, `Seattle`, `Foster City, CA`, `Helsinki, Finland` name no country, state, or "US", and all sailed through. Enumerating every city on earth is not a strategy |
| "8+ years" senior roles | Experience parser reads ranges, "X+", "minimum of X", takes the smallest stated requirement, keeps ≤3 |
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
| **A company migrating ATS** | Self-healing (below) re-resolves the board mid-run and rewrites `companies.yaml` |

| Wrong company's board (impostor slug) | Board ownership verified against the ATS — see [Board identity](#board-identity--the-impostor-problem) |

**Tests:** `python -m pytest tests/ -v` — 90 tests covering the riskiest logic: keyword boundaries, location policy, experience parsing, seniority veto, ranking, shard rotation, board identity, digest rate-limiting, and self-healing file rewrites.

---

## Follow-up reminders

Applications die from silence more than from rejection. Any row you marked
`Applied? = Yes` in `tracker.xlsx` that has been quiet for 10 days gets one
reminder. Nothing to set up — it reads the tracker you already keep.

## Board identity — the impostor problem

Slug guessing fails in a uniquely dangerous way: it produces a **live board full
of real jobs that belongs to a different company**. Nothing errors and the
postings look plausible. Real examples caught in this repo's own registry:

| Configured as | The board is actually |
|---|---|
| `greenhouse/css` — CSS Corp | **CloudKitchens** |
| `greenhouse/ultimate` — UKG | **Ultimate Heating & Air, Inc** |
| `greenhouse/carbon` — Carbon Black | **Carbon, Inc.** |
| `greenhouse/purestorage` — Pure Storage | **Everpure** |
| `greenhouse/linkedin` — LinkedIn | **LI Test Company** (a test board) |

Greenhouse and SmartRecruiters both publish the board's owner, which settles it:

```bash
python scripts/validate_companies.py --identity
```

Mismatches are **reported, never auto-removed** — a legitimate rebrand
("Abnormal Security" → "Abnormal") looks identical to an impostor by string
comparison, and deleting a real board is worse than printing a line to check.

## Self-healing

Companies migrate ATS. When they do, the old endpoint usually doesn't error —
it just stops having jobs. **Zscaler and Palo Alto Networks, the two most
relevant employers on this list, both sat at zero postings** until this was
built: SmartRecruiters answers HTTP 200 with `{"totalFound": 0}` for a slug it
has never heard of, so nothing threw, nothing alerted, and the log read
`0 postings` exactly like a company with no openings.

After every scan, any company that hard-failed **or returned zero postings** is
re-resolved: slug guesses across all four ATS APIs, then its careers page
(the only route to a Workday board, whose host and path aren't derivable from
a name). If a live board is found, `config/companies.yaml` is rewritten in
place, the change is committed by the workflow, and you get a Telegram message:

```
🔧 Self-healing — board(s) repaired:
• Zscaler: smartrecruiters/Zscaler → greenhouse/zscaler (346 postings, via slug-guess)
```

Deliberate limits:
- Healing only ever **replaces a dead board with a live one**. It never disables
  a company — an employer with genuinely no openings looks identical to a
  migrated one, and guessing wrong there would silently stop watching them.
- At most 12 probes per run, so a systemic outage (network down, everything at
  zero) can't turn into hundreds of requests.
- Every repair is announced. Silent self-modification would be a worse failure
  mode than the bug it fixes.
- The edit is line-based and atomic, and preserves your comments and `priority`.
  `tests/test_healer.py` reparses the file after every repair and asserts the
  untouched entries survived byte-for-byte — an earlier block-regex version
  welded the next company onto the healed one and still produced valid YAML.

## Legal & etiquette
This reads the same public JSON endpoints each company's own careers page calls, at low volume (4 polite runs/day with delays and backoff). Don't lower the politeness delays or add hundreds of companies to a single run without spreading schedules.

## Roadmap ideas
- Per-profile Telegram channels (security vs data-eng feeds)
- A daily HTML digest artifact
- LinkedIn/Naukri aggregator adapters (they require auth/ToS review — deliberately excluded from v1)
- An LLM re-ranking pass (Claude API) to score description fit against your resume
