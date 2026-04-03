# job-scout

Automated job scraper that runs every 6 hours, scores matches against your profile, and emails you the best ones. Scrapes LinkedIn, Indeed, and Google Jobs. Zero token cost — pure Python + launchd.

## Prerequisites

- macOS (uses launchd for scheduling)
- Python 3.12 or higher ([download](https://www.python.org/downloads/))
- A Gmail account with 2-Step Verification enabled

## Quick Setup

```bash
git clone <repo-url>
cd job-scout
chmod +x setup.sh
./setup.sh
```

`setup.sh` will:
1. Install Python dependencies in a local `.venv`
2. Open `config.yaml` for you to fill in
3. Install the launchd schedule (runs every 6 hours automatically)

## Gmail App Password

job-scout sends email alerts via Gmail SMTP. It needs an **App Password** — not your regular Gmail password.

1. Go to your Google Account → Security
2. Make sure **2-Step Verification** is enabled
3. Search for "App Passwords" (or go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords))
4. Create a new app password, name it "job-scout"
5. Copy the 16-character password into `config.yaml` under `notifications.email.app_password`

## Configuration Guide

After setup, `config.yaml` controls everything. Key sections:

### `search.terms`
Job titles to search for. Be specific — "machine learning engineer" gets better results than "engineer".

### `scoring.keywords`
Your skills, split into tiers:
- `critical` (+5 pts each): Skills you absolutely need in a role
- `strong` (+3 pts): Important but not required
- `moderate` (+1.5 pts): Nice to have
- `weak` (+1 pt): Broadly relevant

### `scoring.target_companies`
Companies you want to work at, in tiers:
- `tier1` (+15 pts): Dream companies
- `tier2` (+10 pts): Great companies
- `tier3` (+6 pts): Good companies

### `scoring.dealbreakers`
Jobs matching these patterns score 0 and are never shown:
- `title_patterns`: e.g. `["intern", "staff"]`
- `description_patterns`: e.g. `["us citizen only", "no visa sponsorship"]`

### `scoring.min_alert_score`
Jobs scoring at or above this number trigger an email alert. Default: 45.

### `notifications.email`
Fill in your Gmail address and App Password. Set `enabled: true`.

## CLI Commands

```bash
# Run scrapers and check for new jobs
.venv/bin/job-scout scrape

# Test scrapers without saving results
.venv/bin/job-scout scrape --dry-run

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

After `./setup.sh`, two launchd agents run in the background:

| Job | Schedule | What it does |
|-----|----------|--------------|
| Scraper | Every 6 hours | Scrapes all job boards, emails high-score matches |
| Digest | Daily at 9 AM | Emails a summary of the last 24 hours |

To check if they're running:
```bash
launchctl list | grep job-scout
```

To check logs:
```bash
tail -f ~/.local/share/job-scout/logs/stdout.log
```

## Troubleshooting

**No results from LinkedIn/Indeed**
Job board scraping can hit rate limits. Wait an hour and try again. If persistent, increase `scraping.delay_min_seconds` in config.yaml.

**Gmail authentication error**
Make sure you're using an App Password (not your Gmail login password). 2-Step Verification must be enabled on your Google account.

**`launchctl` says agent not running**
Re-run `./setup.sh` or run `.venv/bin/job-scout schedule --install` from the project directory.

**Empty `config.yaml` errors**
Make sure you've filled in all required fields: `profile.name`, `search.terms`, and `notifications.email.*`.

## Notes

- The Indeed scraper uses a well-known public API key embedded in the Indeed mobile app. This is not a personal secret, but Indeed could revoke it at any time.

## License

MIT
