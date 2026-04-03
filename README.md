<p align="center">
  <img src="assets/banner.png" alt="job-scout" width="600">
</p>

# job-scout

Get job alerts on your phone — automatically. job-scout scrapes LinkedIn, Indeed, Google Jobs, and more every 6 hours, scores each match 0–100 against your profile, and sends the best ones to your Telegram or email. Free, no API keys, runs on your Mac.

## What You'll Get

Every few hours, you'll get a message like this on Telegram:

```
job-scout — 3 new match(es)

85 (kw:42) | #142 Google: ML Engineer
  San Francisco, CA (Remote) | $180k–$250k

72 (kw:35) | #143 Stripe: Backend Engineer
  Seattle, WA | $160k–$220k

68 (kw:30) | #144 Notion: Software Engineer
  New York, NY | No salary
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
git clone <repo-url>
cd job-scout
chmod +x setup.sh
./setup.sh
```

### Step 2: Generate your config

Copy the prompt below and paste it into [ChatGPT](https://chat.openai.com) or any AI chatbot, along with your resume (paste the text or upload the PDF). Save the output as `config.yaml` in the project folder.

<details>
<summary>Click to copy the setup prompt</summary>

```
I need you to generate a job-scout config.yaml file based on my resume.
Read my resume below, then fill in the config template that follows.

Rules:
- Extract my name and target job title from the resume
- Map my skills into keyword tiers:
  - critical: my top 3-5 core technical skills (the ones in every job I'd want)
  - strong: important skills I use regularly
  - moderate: skills I have but aren't central to my identity
  - weak: general/soft skills or tools I've used occasionally
- For target_companies: suggest companies that match my experience level and industry
  - tier1: 3-5 dream companies
  - tier2: 3-5 great companies
  - tier3: 3-5 good companies
- For dealbreakers: add title patterns that don't match my level (e.g. "intern" if I'm senior)
- For title_signals: add job title phrases that closely match what I want, with higher points for closer matches
- For search.terms: generate 2-4 job title search queries based on my experience
- For search.locations: ask me where I want to work if not obvious from resume
- Leave the notifications section commented out (I'll fill in secrets myself)
- Output ONLY the valid YAML, no explanation

Here is the config template:

profile:
  name: ""
  target_title: ""

  keywords:
    critical: []
    strong: []
    moderate: []
    weak: []

  target_companies:
    tier1: []
    tier2: []
    tier3: []

  dealbreakers:
    title_patterns: []
    company_patterns: []
    description_patterns: []

  target_levels: []

  title_signals: []
    # example:
    # - pattern: "machine learning engineer"
    #   points: 12

search:
  terms: []
  locations: []
  sites:
    - linkedin
    - indeed
    - google
    - glassdoor
    - ziprecruiter
  results_per_site: 25
  hours_old: 72
  distance_miles: 50

scoring:
  min_alert_score: 45
  min_display_score: 20

# Uncomment and fill in to enable notifications:
# notifications:
#   email:
#     enabled: true
#     smtp_host: smtp.gmail.com
#     smtp_port: 587
#     username: you@gmail.com
#     app_password: xxxx-xxxx-xxxx-xxxx
#     to_address: you@gmail.com
#   telegram:
#     enabled: true
#     bot_token: ""
#     chat_id: ""

schedule:
  interval_hours: 6
  start_hour: 8
  end_hour: 23

Now here is my resume:
[PASTE YOUR RESUME HERE]
```

</details>

### Step 3: Set up notifications (optional)

**Telegram** (recommended):
1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → copy the bot token
2. Message [@userinfobot](https://t.me/userinfobot) → copy your chat ID
3. Uncomment the `notifications.telegram` section in `config.yaml` and paste both values

**Email** (Gmail):
1. Go to [Google App Passwords](https://myaccount.google.com/apppasswords) (requires 2-Step Verification)
2. Create a new app password named "job-scout"
3. Uncomment the `notifications.email` section in `config.yaml` and paste the 16-character password

### Step 4: Run it

```bash
job-scout check                # validate your config
job-scout scrape --dry-run     # test it — see results without saving
job-scout schedule --install   # automate: scrape every 6 hours
```

You're done! Jobs will start flowing to your phone.

## Daily Use

```bash
job-scout list                 # see your latest job matches
job-scout view 42              # full details for a job
job-scout apply 42             # mark a job as applied
job-scout stats                # see your numbers
job-scout digest               # send today's top matches now
```

## Turn It Off

```bash
job-scout schedule --uninstall
```

## Troubleshooting

**`job-scout check` shows errors** — Follow the error messages, they tell you which fields need fixing.

**No results from scrapers** — Job board scraping can hit rate limits. Wait an hour and try again.

**Gmail authentication error** — Make sure you're using an App Password (not your Gmail login password).

**Schedule not running** — Run `job-scout schedule --install` from the project directory.

## License

MIT

## Acknowledgments

Inspired by [JobSpy](https://github.com/speedyapply/JobSpy) (MIT license).
