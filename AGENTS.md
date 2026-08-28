# AGENTS.md

Guide for AI agents working in the job-scout codebase.

## What this is

job-scout is a macOS CLI tool that scrapes 3 job boards (LinkedIn, Indeed, Jora), scores each posting 0–100 against a user profile, stores results in SQLite, and pushes alerts to Telegram/Slack/Discord/email/macOS notifications. Scheduling uses macOS launchd plists. A Telegram bot lets users log application status via natural language (Gemini LLM).

This is a fork of `weberwcwei/job-scout`. It adds an **ATS discovery subsystem** (spec and status in `ATS_DISCOVERY.md`) that runs alongside the board scrapers: `discover` finds companies (VC portfolios, funding RSS, DuckDuckGo ATS search, seed list), `poll` resolves their ATS tenant and polls Greenhouse/Lever/Ashby directly, normalising results into canonical records with separate company/job scoring. `coverage` reports registry/polling health. A `low_score` status auto-files sub-threshold jobs out of `new`, and the UI list is paginated.

Target platform is macOS. Python 3.12+. No API keys required for scraping (scrapers hit guest/public endpoints; some use hardcoded keys borrowed from JobSpy).

## Commands

The project uses `uv` for dependency management. `setup.sh` creates a `.venv` and installs pinned deps from `requirements.txt` (compiled via `uv pip compile`). Dev dependencies (pytest, ruff) are NOT installed by `setup.sh` — install them separately to run tests/lint:

```bash
# Install dev deps into the existing venv
uv pip install --python .venv/bin/python -e ".[dev,tls,bot]"

# Run tests (asyncio_mode=auto is set in pyproject.toml)
.venv/bin/pytest
.venv/bin/pytest tests/test_db.py          # single file
.venv/bin/pytest tests/test_scrapers.py -k Indeed   # by name

# Lint
.venv/bin/ruff check src tests

# Run the CLI (venv must be activated, or use the full path)
.venv/bin/job-scout check
.venv/bin/job-scout scrape --dry-run
python -m job_scout scrape                  # equivalent to the entry point

# Regenerate pinned requirements.txt after pyproject.toml changes
uv pip compile pyproject.toml -o requirements.txt
```

Tests do NOT hit the network — scrapers are tested with `respx` (HTTPX mocking) and the CLI is tested via `typer.testing.CliRunner` with patched config/DB. Bot and LLM tests mock `google.genai` and `httpx.post`.

## Architecture

### Control flow

`cli.py` is the single Typer app and entry point (`job_scout` console script → `job_scout.cli:app`; `__main__.py` enables `python -m job_scout`). Every command follows the same shape:

1. `_get_config()` — loads YAML from the resolved config path (XDG first, then `./config.yaml`), parses into `AppConfig` (Pydantic), and stashes the resolved path on `cfg._config_path`.
2. `_get_db(cfg)` — derives the SQLite path from the config's profile name and returns a `JobDB` (opens WAL-mode sqlite3, auto-migrates schema).
3. Do the work, then `db.close()`.

The `scrape` command builds a Cartesian product of (sites × search terms × locations) and runs them concurrently in a `ThreadPoolExecutor` (max workers from `scraping.max_workers`). Each worker instantiates a scraper, calls `.scrape(params)`, then scores jobs in-thread. Results are upserted sequentially on the main thread.

### Data flow

```
config.yaml → AppConfig (Pydantic)
    → ScrapingConfig passed to get_scraper() → BaseScraper subclass
        → httpx/curl_cffi fetch → BeautifulSoup/JSON parse → Job models
    → JobScorer.score(job) → (0-100, breakdown dict)
    → JobDB.upsert_job(job)  [dedup_key: source:id hash; content_key: title+company+loc+date+desc hash]
    → Notifier.notify_new_jobs(jobs) → macOS/email/Telegram/Slack/Discord
```

### Key modules

- `config.py` — Pydantic models for all YAML sections. `AppConfig` has a `PrivateAttr` `_config_path` that must be set by `load_config()`; it's used everywhere for per-profile path derivation. `validate_quality()` produces semantic warnings (unreachable scores, placeholder names, bad regex) separate from Pydantic validation.
- `models.py` — `Job`, `Location`, `Compensation`, `ScrapeParams`, `ScrapeRun`, enums. `Location` has a `model_validator` that normalizes city/state/country (full names → codes, shifts misplaced fields, strips "Remote" from city when `is_remote`). `Job.dedup_key` and `Job.content_key` are computed fields (SHA256, 16 hex chars).
- `db.py` — `JobDB` wraps a sqlite3 connection. Schema is in `SCHEMA_SQL`; `_migrate()` adds columns (`search_term`, `content_key`) to old DBs. `upsert_job` does two-level dedup: exact `dedup_key`, then soft `content_key` match (skips short descriptions <100 chars). `batch_update_scores` uses explicit BEGIN/rollback.
- `scorer.py` — `JobScorer.score()` returns `(total, breakdown)`. Dealbreakers short-circuit to `(0, {"dealbreaker": True})`. Keyword score is gated: 0 critical hits → capped at 10. Components: keyword (0-55), company (0-15), title signals (0-20), recency (0-10).
- `scrapers/` — `BaseScraper` (ABC) provides `_make_client`, `_get_with_retry`/`_post_with_retry` (429 backoff, 5xx retry), `_delay`, `_is_dup`, proxy round-robin. `get_scraper(site, config)` is the registry. Each scraper subclasses and sets `site: Site`. Indeed uses a GraphQL API; LinkedIn and Jora scrape HTML.
- `scrapers/tls.py` — Optional `curl_cffi` adapter (`job-scout[tls]` extra) impersonating browser TLS fingerprints. Falls back to httpx on ImportError.
- `ui.py` — Stdlib-only local web UI (`job-scout ui`). `ThreadingHTTPServer` serves static assets from `web/` (index.html, app.css, app.js) plus a JSON API: `GET /api/meta` (status counts + sources), `GET /api/jobs` (`status`, `source`, `q`, `sort`, `limit`, `offset`), `GET /api/jobs/<id>`, `PATCH /api/jobs/<id>` (status/notes; status validated against the editable values). sqlite3 is not thread-safe, so a fresh `JobDB` is opened per request via `_create_server`'s handler; `serve()` binds a free port by incrementing from `--port`. Frontend is vanilla JS (no framework): chips for all statuses with live counts, search/source/sort controls, per-row status select, detail `<dialog>`, paginated list with "Load more". Dark theme documented in DESIGN.md.
- `notify.py` — `Notifier` fan-outs to all enabled channels. Module-level `send_*` functions do the actual HTTP/SMTP. Each channel has its own escape helper (`_esc_md` for Telegram MarkdownV2, `_esc_slack`, `_esc_discord`, `_esc` for AppleScript).
- `scheduler.py` — Generates and installs launchd plists (scrape/digest/report/discover/poll + optional bot daemon). One plist per task per profile. Profile name derived from config filename (`config.yaml` → "default"; `frontend.yaml` → "frontend"). Legacy single-plist is auto-cleaned on install.
- `bot.py` — `TelegramBot` long-polls one or more bot tokens, routes messages to per-profile DBs by `chat_id`, calls `llm.parse_status_update`, applies status changes, replies in MarkdownV2. Persist per-token update offsets to `~/.local/share/job-scout/bot/`.
- `llm.py` — Gemini-powered NL parsing of job status updates. Has a strict system prompt with explicit injection-defense rules. Truncates input to 500 chars. Validates LLM output against an allowlist of statuses. Requires `google-genai` (`job-scout[bot]` extra) and `GEMINI_API_KEY` env var or `bot.gemini_api_key`.
- `ats/` — ATS adapters for Greenhouse/Lever/Ashby. `ATSAdapter` ABC (`ats/base.py`) fetches a board's public JSON and normalises into `ATSJob`; each adapter (`ats/greenhouse.py`, `ats/lever.py`, `ats/ashby.py`) maps its API shape. `ats/__init__.py` maps `ATSProvider` → adapter class.
- `discovery/` — Company discovery sources. `DiscoverySource` ABC (`discovery/base.py`); `vc_portfolios.py` (scrape VC pages), `funding.py` (RSS headlines), `ats_search.py` (DuckDuckGo `site:` over board domains, best-effort), and `discovery/__init__.py` orchestrator that merges by name, unions provenance, and upserts. `seed_companies` config bootstraps known boards deterministically.
- `registry/companies.py` — `CompanyResolver`: name → domain → careers page → ATS → board slug. Never assumes name == slug; extracts slug only from a board URL actually seen.
- `normalization/job.py` — canonical job keys (`normalize_title`, `canonical_key`), `project` (ATSJob → CanonicalJob), `merge` (most-recently-updated wins, earliest first_seen, latest last_seen, sticky repost, tz-safe comparisons).
- `ranking/` — `company_score.py` (funding decay, VC backing, hiring, staffing penalty) and `job_score.py` (relevance, location, tech, seniority, salary, work, company, freshness), kept separate.
- `poller.py` — `run_poll` (iterate pollable companies → adapter → persist source + canonical, score, reopen seen jobs, close missing after N crawls) and `run_resolve` (discovery + resolution for pending companies).
- `coverage.py` — registry health, polling stats, per-component score means for the `coverage` command.

## Conventions

- **Profile isolation**: Every config file gets its own DB, log dir, report dir, and launchd labels, derived from the filename (or `config_name` if set). `config.yaml` is the "default" profile and uses the original base paths. `--config X.yaml` / `-c X.yaml` is the global Typer callback option that threads through all commands.
- **Private config path**: `AppConfig._config_path` is a Pydantic `PrivateAttr` — not serialized, set manually by `load_config`. If you construct `AppConfig` directly in tests, set `cfg._config_path` yourself or path resolution will fall back to defaults.
- **Status values**: `new`, `applied`, `interview`, `offer`, `rejected`, `filtered`, `low_score`, `expired`. `filtered` is set automatically when a dealbreaker matches; `low_score` is set automatically when a job scores below `scoring.low_score_threshold`; `rescore` transitions jobs between `new`/`low_score`/`filtered` as scores and dealbreakers change. `update_status` stamps `applied_date` for `applied`/`interview`/`offer`.
- **Dedup is two-level**: `dedup_key` (source:value_id, hard dedup) + `content_key` (cross-source content hash, soft dedup, only for descriptions >100 chars). `dedup` command backfills missing `content_key`s then removes duplicates keeping the highest-scored row.
- **Config validation has two stages**: Pydantic schema validation (hard errors) and `validate_quality()` (soft warnings/errors about unreachable scores, placeholder values, bad regex). `check` command runs both and exits 2 on warnings, 1 on errors.
- **Scrapers never raise to the caller**: the `scrape` CLI command wraps each task in try/except and records errors on the `scrape_runs` table. Zero-result runs (no error, 0 jobs) trigger a yellow warning and are tracked in `stats`.
- **Test fixtures**: `tmp_path` + `JobDB(tmp_path / "test.db")` for DB tests. `respx.mock` for HTTP. `CliRunner` + `patch("job_scout.cli._get_config"/"_get_db"/"get_scraper")` for CLI tests. A `MINIMAL_RAW` dict is the canonical minimal config in CLI tests.
- **Optional extras**: `tls` (curl_cffi), `bot` (google-genai). Both degrade gracefully via try/except ImportError with logged warnings.
- **Imports of optional deps are deferred** (inside functions) so the core CLI works without `[tls]` or `[bot]` installed.

## Gotchas

- `requirements.txt` is pinned/compiled — do NOT edit it directly. Change `pyproject.toml` then run `uv pip compile pyproject.toml -o requirements.txt`.
- `setup.sh` installs only base deps (not `[dev]`). Running `pytest` right after setup will fail with "No module named pytest". Install dev extras first.
- `config.yaml` is gitignored. Templates are `config.minimal.yaml` and `config.template.yaml`. `job-scout init` copies minimal; `--full` copies the full template.
- The Indeed scraper uses a hardcoded API key borrowed from JobSpy (in `scrapers/constants.py`). This is not a secret — it's embedded in the upstream app's client.
- `Location` normalization rules have a specific order (rules 3-5 in `models.py`); don't reorder without re-reading the validator. Rule 4 (shifted fields) must run before rules 3 and 5.
- `ScrapingConfig` has a `model_validator(mode="before")` that migrates the old `proxy` singular field to `proxies` list — keep this when touching the model.
- `ScoringConfig.alert_states` normalizes full state names to 2-letter codes in a `model_validator(mode="after")` that imports `US_STATES` from `models` (deferred import to avoid a cycle).
- The bot's offset persistence moved to per-token files (`_offset_file` uses the last 8 chars of the bot token). Older tests may patch `OFFSET_FILE` directly — check `_make_bot_with_mock_configs` in `test_bot.py` for the canonical setup.
- `report` command hardcodes a `min_score=40` for the "worth review" tier (not configurable).
- `schedule --install` also installs a bot daemon plist if the profile has Telegram + a Gemini key. Uninstalling the default profile removes the bot plist; non-default profiles leave it alone.

## XDG paths

- Config: `$XDG_CONFIG_HOME/job-scout/config.yaml` (default `~/.config/job-scout/config.yaml`), CWD `config.yaml` as fallback.
- Data: `~/.local/share/job-scout/` — `job-scout.db` (default profile), `<profile>.db` (named profiles), `logs/`, `reports/`, `bot/`.
- Plists: `~/Library/LaunchAgents/com.user.job-scout.{scrape,digest,report}.plist` (default) or `com.user.job-scout.<profile>.{task}.plist` (named).
