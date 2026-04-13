<p align="center">
  <img src="assets/banner.png" alt="job-scout" width="600">
</p>

<p align="center">
  <strong>Stop refreshing job boards. Let your Mac do the searching.</strong>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/platform-macOS-lightgrey?logo=apple" alt="macOS">
  <img src="https://img.shields.io/badge/no_API_keys-required-brightgreen" alt="No API keys required">
</p>

<p align="center">
  <sub>Scrapes <b>LinkedIn</b> · <b>Indeed</b> · <b>Google Jobs</b> · <b>Glassdoor</b> · <b>ZipRecruiter</b> · <b>Bayt</b></sub>
</p>

---

## Changelog

**2026-04-12**
- Telegram bot: natural-language job status queries via Gemini LLM, multi-profile support with `--config`, hardened prompt against injection
- SMTP timeout to prevent indefinite hangs on email notifications

**2026-04-09**
- Content-based deduplication: catches identical job postings with different source IDs (Indeed key rotation). New `dedup` command to clean existing duplicates

**2026-04-07**
- Location normalization: auto-corrects scraped city/state/country data via model validator
- Daily report is emailed with the report file attached when email notifications are enabled

**2026-04-06**
- Multi-config (`--config`), Slack/Discord webhooks, `report` and `export` commands, digest stats footer, multi-plist scheduler, zero-result warnings

**Week of 2026-03-30**
- `rescore` command, dealbreaker storage, config quality warnings, XDG config path

---

job-scout scrapes 6 job boards every few hours, scores each match 0–100 against your profile, and sends the best ones to your Telegram, Slack, Discord, or email. Free, no API keys, runs on your Mac.

### Why job-scout?

- **You miss new posts** — Job boards bury good matches under promoted listings. job-scout checks every 6 hours so you see them first.
- **You waste time scrolling** — Instead of checking 6 sites manually, get one alert with only jobs that match your skills.
- **You can't compare across sites** — job-scout ranks every job on one 0–100 scale so the best match wins, regardless of where it was posted.
- **You lose track of applications** — Every job gets an ID. Mark it applied, add notes, check your stats.

## What You'll Get

Every few hours, you'll get a message like this on Telegram:

```
job-scout — 3 new match(es)

85 (kw:42) | #142 Google: ML Engineer
  San Francisco, CA (Remote) | $180k–$250k

72 (kw:35) | #143 Stripe: Backend Engineer
  Seattle, WA | $160k–$220k

68 (kw:30) | #144 Notion: Software Engineer
  New York, NY
```

See a job you like? Apply on the site, then log it:

```bash
job-scout apply 142
```

That's it. Your applied jobs are tracked with timestamps so you never lose track.

## Get Started

You need a Mac with Python 3.12+ ([download](https://www.python.org/downloads/)).

### Step 1: Install

```bash
git clone https://github.com/weberwcwei/job-scout.git
cd job-scout
chmod +x setup.sh
./setup.sh
```

After setup, either activate the virtual environment first:

```bash
source .venv/bin/activate
job-scout check
```

Or run commands directly without activating:

```bash
.venv/bin/job-scout check
```

All examples below use `job-scout` and assume the venv is activated.

### Step 2: Generate your config

Copy the prompt below and paste it into [ChatGPT](https://chat.openai.com) or any AI chatbot, along with your resume (paste the text or upload the PDF). Save the output as `config.yaml` in the project folder.

<details>
<summary>Click to copy the setup prompt</summary>

```
I need you to generate a job-scout config.yaml file based on my resume.
Read my resume below, then produce a config that EXACTLY matches the structure and
format specified. The app will crash on any deviation. Follow every rule precisely.

STRICT FORMAT RULES (the app validates all of these — violations cause errors):

1. REQUIRED TOP-LEVEL SECTIONS (in this exact order):
   profile, search, scoring, schedule
   Do NOT include: scraping, notifications, or db_path sections.

2. profile.name — my full name as a quoted string
3. profile.target_title — a single job title string (e.g. "Senior ML Engineer")

4. profile.keywords — four tiers, each a YAML list of plain lowercase strings:
   - critical: 8-15 keywords — my core technical identity (skills in every job I'd want)
   - strong: 10-20 keywords — important skills I use regularly, frameworks, specific tools
   - moderate: 8-15 keywords — skills I have but aren't central to my identity
   - weak: 4-8 keywords — general terms, broad categories, tools I've used occasionally

5. profile.target_companies — three tiers, each a YAML list of company name strings:
   - tier1: 10-25 dream companies in my field
   - tier2: 10-20 great companies
   - tier3: 5-10 good companies

6. profile.dealbreakers — three categories, each a YAML list of REGEX PATTERN strings.
   Every pattern MUST:
   - Start with (?i) for case-insensitive matching
   - Use \b for word boundaries where appropriate
   - Be a valid Python regex
   Required patterns:
   - title_patterns: regex for seniority levels that don't match me
     Example: "(?i)\\bintern\\b"
   - company_patterns: regex for companies to exclude (staffing firms, etc.)
     Example: "(?i)\\bInfosys\\b"
   - description_patterns: MUST include these exact patterns:
     - "(?i)no\\s+(h[- ]?1b|visa|sponsorship)"
     - "(?i)must\\s+be\\s+(us|u\\.s\\.)\\s+citizen"
     - "(?i)security\\s+clearance\\s+required"
     - "(?i)\\bc2c\\b"
     - "(?i)\\bcorp.to.corp\\b"
     Add more if relevant to my profile.

7. profile.target_levels — YAML list from ONLY these values:
   "Entry", "Junior", "Mid", "Senior", "Lead"
   Pick levels that match my experience.

8. profile.title_signals — a YAML list of objects, each with exactly two keys:
   - pattern: a lowercase string (job title phrase)
   - points: an integer from 1 to 12 (higher = closer match to what I want)
   Generate 8-15 signals. Exact format per item:
     - pattern: "machine learning engineer"
       points: 10

9. search.terms — 5-10 quoted job title search strings
10. search.locations — list of location strings (ask me if not obvious from resume)
11. search.sites MUST be exactly:
    - linkedin
    - indeed
    - google
    - glassdoor
    - ziprecruiter
12. search.results_per_site: 25
13. search.hours_old: 72
14. search.distance_miles: 50

15. scoring.min_alert_score: 45
16. scoring.min_display_score: 20

17. schedule.interval_hours: 6

OUTPUT REQUIREMENTS:
- Output ONLY valid YAML. No explanation, no markdown fences, no comments.
- Use 2-space indentation throughout.
- All string values containing special characters must be quoted.
- Do NOT include notifications, scraping, or db_path sections.

Now here is my resume:
[PASTE YOUR RESUME HERE]
```

</details>

### Step 3: Set up notifications (optional)

Pick any channel — or use several at once.

**Slack** (easiest):
1. Create an [Incoming Webhook](https://api.slack.com/messaging/webhooks) for your workspace
2. Add to `config.yaml`:
   ```yaml
   notifications:
     slack:
       enabled: true
       webhook_url: "https://hooks.slack.com/services/T.../B.../..."
   ```

**Discord**:
1. In your Discord channel, go to Settings → Integrations → Webhooks → New Webhook → Copy URL
2. Add to `config.yaml`:
   ```yaml
   notifications:
     discord:
       enabled: true
       webhook_url: "https://discord.com/api/webhooks/ID/TOKEN"
   ```

**Telegram**:
1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → copy the bot token
2. Message [@userinfobot](https://t.me/userinfobot) → copy your chat ID
3. Uncomment the `notifications.telegram` section in `config.yaml` and paste both values

**Email** (Gmail):
1. Go to [Google App Passwords](https://myaccount.google.com/apppasswords) (requires 2-Step Verification)
2. Create a new app password named "job-scout"
3. Uncomment the `notifications.email` section in `config.yaml` and paste the password **without dashes** (Google shows `xxxx-xxxx-xxxx-xxxx`, enter it as `xxxxxxxxxxxxxxxx`)

### Step 4: Run it

```bash
job-scout check                # validate your config
job-scout scrape --dry-run     # test it — see results without saving
job-scout schedule --install   # automate: scrape, digest, and daily report
```

You're done! Jobs will start flowing to your phone.

## Daily Use

```bash
job-scout list                 # see your latest job matches
job-scout view 42              # full details for a job
job-scout apply 42             # mark a job as applied
job-scout rescore              # re-score all jobs after config changes
job-scout dedup --dry-run      # preview duplicate cleanup
job-scout dedup                # remove content-identical duplicates
job-scout stats                # see your numbers
job-scout digest               # send today's top matches now
job-scout report               # generate and email a daily report
job-scout export -o jobs.csv   # export jobs to CSV or JSON
```

## Multiple Searches

Want to run separate searches — e.g., frontend roles vs. backend, or searches for different people? Use the `--config` flag:

```bash
job-scout --config frontend.yaml init        # create a new config
job-scout --config frontend.yaml check       # validate it
job-scout --config frontend.yaml scrape      # run it — separate DB, logs, everything
job-scout --config frontend.yaml schedule --install  # automate on its own schedule
```

Each config file gets its own database, scheduler plists, log directory, and notification prefix (derived from the filename). Your existing setup (`config.yaml`) continues to work exactly as before — no migration needed.

You can also set an explicit profile name in the config to avoid filename-based derivation:

```yaml
config_name: backend-jobs
```

## Your Daily Routine

Once scheduled, job-scout runs silently in the background. Your day looks like this:

1. **Morning** — Check your notifications. See 3 new matches scored 70+.
2. **Spot a good one** — Run `job-scout view 142` for full details.
3. **Apply on the site** — Then `job-scout apply 142` to log it.
4. **End of week** — Run `job-scout stats` to see how your search is going.

No browser tabs. No doom-scrolling. Just the jobs that match your profile, delivered to your phone.

## Turn It Off

```bash
job-scout schedule --uninstall
```

## Troubleshooting

**`job-scout check` shows errors** — Follow the error messages, they tell you which fields need fixing.

**No results from scrapers** — Job board scraping can hit rate limits. Wait an hour and try again.

**Gmail authentication error** — Make sure you're using an App Password (not your Gmail login password).

**Schedule not running** — Run `job-scout schedule --install` from the project directory.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=weberwcwei/job-scout&type=Date)](https://star-history.com/#weberwcwei/job-scout&Date)

## License

MIT

## Acknowledgments

Inspired by [JobSpy](https://github.com/speedyapply/JobSpy) (MIT license).
