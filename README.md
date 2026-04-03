# job-scout

Automated job scraper that runs every 6 hours, scores matches against your profile, and emails you the best ones. Scrapes LinkedIn, Indeed, and Google Jobs. Zero token cost — pure Python + launchd.

## Prerequisites

- macOS (uses launchd for scheduling)
- Python 3.12 or higher ([download](https://www.python.org/downloads/))
- A Gmail account with 2-Step Verification enabled (for email alerts)

## Quick Setup

```bash
git clone <repo-url>
cd job-scout
chmod +x setup.sh
./setup.sh
```

Then follow the printed steps:

1. Edit `config.yaml` — fill in the **REQUIRED** fields
2. Run `job-scout check` — validate your config
3. Run `job-scout scrape --dry-run` — test it
4. Run `job-scout schedule --install` — automate it

The minimal config only needs four things: your name, target job title, search terms, and locations. Everything else has sensible defaults. For all options, run `job-scout init --full` to start from the full template.

## Gmail App Password

Email alerts are optional. To enable them, uncomment the `notifications.email` section in `config.yaml` and provide a Gmail App Password.

1. Go to your Google Account → Security
2. Make sure **2-Step Verification** is enabled
3. Search for "App Passwords" (or go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords))
4. Create a new app password, name it "job-scout"
5. Copy the 16-character password into `config.yaml` under `notifications.email.app_password`

Run `job-scout check` to verify the SMTP connection works.

## Configuration Guide

`config.yaml` controls everything. The minimal template covers the required fields. For advanced scoring, add these sections (see `config.template.yaml` for the full reference):

### `profile.keywords`
Your skills, split into tiers:
- `critical` (+5 pts each): Skills you absolutely need in a role
- `strong` (+3 pts): Important but not required
- `moderate` (+1.5 pts): Nice to have
- `weak` (+1 pt): Broadly relevant

### `profile.target_companies`
Companies you want to work at, in tiers:
- `tier1` (+15 pts): Dream companies
- `tier2` (+10 pts): Great companies
- `tier3` (+6 pts): Good companies

### `profile.dealbreakers`
Jobs matching these patterns score 0 and are never shown:
- `title_patterns`: e.g. `["intern", "staff"]`
- `description_patterns`: e.g. `["security clearance required"]`

### `profile.title_signals`
Bonus points when job titles contain specific phrases:
```yaml
title_signals:
  - pattern: "machine learning engineer"
    points: 12
  - pattern: "ai engineer"
    points: 8
```

### `scoring.min_alert_score`
Jobs scoring at or above this number trigger an email alert. Default: 45.

### `scoring.alert_states`
Only send alerts for jobs in specific states. Empty list means all states.

## CLI Commands

```bash
# First-time setup
.venv/bin/job-scout init              # create minimal config.yaml
.venv/bin/job-scout init --full       # create full config.yaml with all options
.venv/bin/job-scout check             # validate config and test connections

# Run scrapers and check for new jobs
.venv/bin/job-scout scrape
.venv/bin/job-scout scrape --dry-run  # test without saving

# Browse your job matches
.venv/bin/job-scout list
.venv/bin/job-scout list --status all --min-score 30

# View a job's full details
.venv/bin/job-scout view 42

# Mark a job as applied or rejected
.venv/bin/job-scout apply 42
.venv/bin/job-scout reject 42

# Send today's email digest manually
.venv/bin/job-scout digest

# Show statistics
.venv/bin/job-scout stats

# Manage the launchd schedule
.venv/bin/job-scout schedule --install
.venv/bin/job-scout schedule --uninstall
.venv/bin/job-scout schedule            # show status
```

## What Runs Automatically

After `job-scout schedule --install`, a launchd agent scrapes all job boards on a schedule:

| Job | Schedule | What it does |
|-----|----------|--------------|
| Scraper | Every 6 hours | Scrapes all job boards, emails high-score matches |

To check if it's running:
```bash
launchctl list | grep job-scout
```

To check logs:
```bash
tail -f ~/.local/share/job-scout/logs/stdout.log
```

## Troubleshooting

**`job-scout check` shows errors**
Follow the error messages — they tell you exactly which fields need fixing.

**No results from LinkedIn/Indeed**
Job board scraping can hit rate limits. Wait an hour and try again. If persistent, increase `scraping.delay_min_seconds` in config.yaml.

**Gmail authentication error**
Make sure you're using an App Password (not your Gmail login password). Run `job-scout check` to test the connection.

**`launchctl` says agent not running**
Run `.venv/bin/job-scout schedule --install` from the project directory.

## Notes

- The Indeed scraper uses a well-known public API key embedded in the Indeed mobile app. This is not a personal secret, but Indeed could revoke it at any time.

## License

MIT
