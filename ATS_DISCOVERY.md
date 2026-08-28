# ATS Discovery System — Agent Specification

Status: **implemented (M0–M7 complete, live-verified 2026-08-26)**. All scope
and design decisions are locked. This document is the source of truth for the
job discovery + ATS scraping subsystem. It runs alongside the existing board
scrapers (LinkedIn, Indeed, Jora) and reuses their infrastructure.

## Goal

Replace "which companies should I manually search?" with a system that
continuously discovers companies, determines which are worth monitoring, and
surfaces only relevant new jobs.

The system must not rely on manually maintained company lists or on scraping
job aggregators (LinkedIn, SEEK, Indeed). Instead it discovers promising
companies, resolves their careers infrastructure, queries their ATS directly,
normalises results, deduplicates, and ranks.

## Design principle

```
discover companies -> resolve ATS tenant -> poll ATS API -> normalise -> dedup -> rank
```

Discovery runs infrequently. Known ATS boards are polled frequently. The two
are separate systems. The company database grows automatically.

## Locked decisions

These were resolved with the user before kickoff. Do not revisit them.

1. **Personal-fit input** — reuse `config.yaml` (existing `profile` keywords,
   `target_companies`, dealbreakers, `title_signals`, `search.locations`).
   No new profile schema.
2. **Geography** — both AU-wide and Sydney/NSW. Location is a filter, not
   hardcoded. `search.locations` drives it.
3. **Scale** — "a lot". Keep SQLite (it handles millions of rows). Revisit
   Postgres only if profiling demands it.
4. **Relationship to existing job-scout** — run alongside the existing
   scrapers. Reuse notifier, scheduler, UI, `Location` normalisation,
   `JobDB` patterns, config loading, and respx test conventions.
5. **Funding sources** — VC portfolio pages (Blackbird, AirTree, Square Peg,
   Main Sequence, Startmate) plus RSS feeds (Startup Daily, SmartCompany,
   TechCrunch AU). Crunchbase is paid; skip it.
6. **Search adapter** — DuckDuckGo HTML endpoint
   (`html.duckduckgo.com/html/?q=site:boards.greenhouse.io ...`), in MVP,
   best-effort with graceful degradation when rate-limited. No paid APIs.
7. **Canonical lifecycle** — source records are append-only; the canonical
   job is a projection. Conflicts resolve to the most-recently-updated
   source record. All source records are retained.
8. **Closed detection** — fixed threshold: 3 consecutive missing crawls
   before a job is marked closed.
9. **Repost** — a reopened requisition is a new job record carrying a
   `repost` flag so the user sees it as a repost, not a fresh posting.

## Architecture

Mirror the brief's module layout, adapted to existing codebase conventions
(flat modules under `src/job_scout/`, Pydantic models, sqlite3 storage).

```
src/job_scout/discovery/          # new package — infrequent
    ats_search.py                 # DuckDuckGo site: search adapter
    funding.py                    # VC pages + RSS
    vc_portfolios.py              # Blackbird/AirTree/Square Peg/Main Sequence/Startmate
    fast_growth.py                # extra scope
    workplaces.py                 # extra scope

src/job_scout/registry/           # new package
    companies.py                  # Company model + registry store

src/job_scout/ats/                # new package — frequent polling
    base.py                       # ATSAdapter ABC
    greenhouse.py                 # boards-api.greenhouse.io/v1/boards/<slug>/jobs
    lever.py                      # api.lever.co/v0/postings/<slug>
    ashby.py                      # api.ashbyhq.com/posting-api/job-board/<slug>
    workday.py                    # extra scope (XHR/GraphQL, not a public API)
    smartrecruiters.py            # extra scope

src/job_scout/normalization/      # new package
    company.py
    job.py
    location.py                   # reuse existing Location normalisation
    salary.py

src/job_scout/ranking/            # new package
    company_score.py
    job_score.py

src/job_scout/storage/            # new package
    companies.py
    jobs.py
    job_history.py
```

### ATS collection priority

1. Documented/public ATS APIs — Greenhouse, Lever, Ashby.
2. Public/internal JSON APIs — SmartRecruiters, others.
3. JSON-LD JobPosting metadata.
4. Static HTML parsing.
5. Browser automation / Playwright — last resort.

Each ATS is an independent adapter outputting the same internal `Job` model.
The rest of the application must not know which ATS a job came from.

### Discovery sources (signals, not authoritative)

- Existing Greenhouse/Lever/Ashby/Workday/SmartRecruiters boards
- Search over ATS domains (DuckDuckGo `site:` queries)
- VC portfolios (Blackbird, AirTree, Square Peg, Main Sequence, Startmate)
- Recent funding announcements (VC pages + RSS)
- Fast-growth lists (Deloitte Tech Fast 50) — extra scope
- Best-workplace/employer lists — extra scope
- Startup directories and accelerator portfolios — extra scope
- Company expansion or hiring news — extra scope

Store why each company was discovered (provenance).

## Canonical schemas

### Company

```json
{
  "id": "...",
  "name": "Example",
  "domain": "example.com",
  "careers_url": "https://example.com/careers",
  "ats": "greenhouse",
  "ats_slug": "example",
  "discovered_from": ["airtree", "funding_news", "greenhouse_search"],
  "last_verified_at": "...",
  "created_at": "..."
}
```

Company resolution chain: `name -> canonical domain -> careers page -> ATS
provider -> ATS tenant/board slug`. Never assume the company name equals the
ATS identifier. Handle brand vs legal name, parent/subsidiary, acquisitions,
regional careers sites, multiple ATSs, and ATS migrations (the last two are
extra scope; MVP detects and records, does not auto-resolve).

### Job

Normalise all ATS responses into a schema similar to:

```json
{
  "source": "greenhouse",
  "source_id": "12345",
  "company_id": "...",
  "title": "Software Engineer",
  "location_text": "Sydney, NSW",
  "city": "Sydney",
  "state": "NSW",
  "country": "Australia",
  "remote": false,
  "hybrid": true,
  "employment_type": "full_time",
  "salary_min": null,
  "salary_max": null,
  "currency": "AUD",
  "description_html": "...",
  "description_text": "...",
  "url": "...",
  "apply_url": "...",
  "posted_at": null,
  "first_seen_at": "...",
  "last_seen_at": "...",
  "updated_at": "...",
  "closed_at": null,
  "status": "open",
  "repost": false
}
```

Reuse the existing `Job` model where fields overlap (`source`, `source_id`,
`title`, `company`, `location`, `description`, `compensation`, dates,
`status`). New fields: `company_id`, `location_text`, `hybrid`,
`description_html`, `apply_url`, `first_seen_at`, `last_seen_at`,
`closed_at`, `repost`. If an ATS provides no posting date, keep
`first_seen_at`; never pretend it is the posting date.

## Job history

Track at minimum: `first_seen_at`, `last_seen_at`, `posted_at`,
`updated_at`, `closed_at`, `status`. Mark a job closed only after 3
consecutive missing crawls.

## Deduplication

Two levels, as in the existing pipeline:

- **Source-level** — `dedup_key` (source + source_id), hard dedup.
- **Canonical** — merge across ATSs using signals: company, title,
  location, requisition ID, description similarity, application URL.

Handle title variants (`Software Engineer II` vs `Software Engineer 2` vs
`Software Engineer II - Sydney`). Never destroy original ATS records when
merging duplicates.

## Scoring

Keep company score and job score separate so a great role at an unknown
company can still rank highly.

**Company signals** (quality/growth):

```
+ recently funded (weight decays with time)
+ Fast 50 / high growth
+ reputable VC portfolio
+ expanding Australian team
+ recognised workplace
+ multiple relevant openings
+ sustained hiring

- no Australian roles
- no relevant jobs for a long period
- recruitment/staffing company
- inactive careers site
```

**Job signals** (personal fit):

```
role relevance
Sydney / NSW / remote eligibility
technology/domain
seniority
salary
hybrid/remote preference
company score
job freshness
keywords/skills
```

## Scope

### In scope (MVP)

- Discovery: ATS-domain search (DuckDuckGo), VC portfolios, funding — one
  source each.
- ATS adapters: Greenhouse, Lever, Ashby (structured APIs only).
- Company registry: name, domain, careers_url, ats, ats_slug, provenance,
  last_verified_at.
- Company resolution: domain -> careers page -> ATS detection -> slug.
- Job normalisation to the canonical schema.
- Storage: SQLite, reusing `JobDB` patterns.
- Job history: first_seen / last_seen / closed detection (3 missing crawls).
- Dedup: source-level + canonical.
- Basic company score + basic job score, separate.
- CLI wiring: `discover` and `poll` commands.
- Seed list of ~50 known AU ATS boards to bootstrap tenant discovery.

### Extra scope (stretch)

- Workday + SmartRecruiters adapters.
- JSON-LD fallback, static HTML parsing, Playwright.
- More discovery sources: Fast 50, workplace lists, startup directories,
  expansion news.
- Historical analytics, coverage measurement.
- Better location parsing, salary extraction.
- LLM/embedding classification (dedup similarity, company resolution).
- Personalised ranking, alerts (reuse existing notifier).
- Funding decay weighting.

### Out of scope

- Scraping LinkedIn/SEEK/Indeed (explicitly excluded; existing scrapers
  keep running alongside).
- Application automation, resume/cover-letter generation.
- Interview scheduling.
- Multi-user / SaaS.
- Browser automation in MVP.
- SERP-scraping legal engineering (flag, do not build around it).

## Milestones

| # | Milestone | Acceptance criteria |
|---|---|---|
| M0 | Foundations | Schema + Company/Job models + SQLite storage + config load |
| M1 | ATS adapters | Greenhouse/Lever/Ashby return normalised Jobs; respx-mocked tests green |
| M2 | Discovery v1 | VC portfolios + funding + DuckDuckGo search produce companies with provenance |
| M3 | Company resolution | Domain -> careers page -> ATS -> slug pipeline, verified against seed list |
| M4 | Normalisation + dedup + history | Canonical records created; first/last_seen tracked; closed detection works |
| M5 | Scoring | Company and job scores computed separately, explainable breakdown |
| M6 | Orchestration | `discover` (infrequent) vs `poll` (frequent) via launchd |
| M7 | Coverage + tuning | Missed-job rate measured against a reference set; weights tuned |

## Risks and mitigations

- **Tenant discovery is the riskiest link.** Bootstrap M2/M3 with a manually
  seeded list of ~50 known AU ATS boards. Automated discovery matures on top.
- **Search has no free API.** DuckDuckGo HTML endpoint is rate-limited and
  ToS-grey. Treat it as best-effort; never a hard dependency. Discovery
  degrades gracefully to VC portfolios + funding + seed list.
- **Workday is not a public JSON API.** It is XHR/GraphQL behind bot
  protection. The brief labels it "public/internal JSON API"; correct that
  assumption during implementation.
- **Search is discovery-only.** Once a slug is in the registry, poll the ATS
  API directly. Search is the bootstrap, not the pipeline.

## Conventions

- Reuse existing infrastructure: `config.yaml` + `AppConfig`, `JobDB`
  patterns, notifier, scheduler, UI, `Location` normalisation, respx test
  patterns, `CliRunner` CLI tests.
- ATS adapters output the same internal `Job` model.
- Scrapers never raise to the caller; record errors on a run table (same as
  the existing `scrape` command).
- Tests never hit the network (respx for HTTP).
- Optional deps degrade gracefully via try/except ImportError.
- Australian English in prose; US spelling inside code identifiers and
  package names (`normalization`, `organization`) is preserved.
