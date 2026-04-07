"""CLI entry point for job-scout."""

from __future__ import annotations

import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

import yaml
from pydantic import ValidationError

from job_scout.config import (
    DEFAULT_DB_PATH,
    DATA_DIR,
    XDG_CONFIG_PATH,
    AppConfig,
    load_config,
    resolve_config_path,
    validate_quality,
)
from job_scout.db import JobDB
from job_scout.export import write_csv, write_json
from job_scout.models import ScrapeParams, ScrapeRun, Site
from job_scout.notify import Notifier
from job_scout.scorer import JobScorer
from job_scout.scrapers import get_scraper

app = typer.Typer(name="job-scout", help="Lightweight job scraping and alerting.")
console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def _get_config():
    return load_config(resolve_config_path())


def _get_db(cfg: AppConfig | None = None):
    path = cfg.db_path if cfg and cfg.db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return JobDB(path)


def _filter_alert_jobs(jobs: list, cfg) -> list:
    """Filter jobs by alert_states config: remote, state in allowed list, or unknown state."""
    allowed = cfg.scoring.alert_states
    return [
        j
        for j in jobs
        if j.location.is_remote or not allowed or j.location.state in (None, *allowed)
    ]


@app.command()
def scrape(
    site: str = typer.Option(
        None,
        help="Scrape specific site: linkedin, indeed, google, glassdoor, ziprecruiter, bayt",
    ),
    term: str = typer.Option(None, help="Override search term"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Scrape and score but don't persist"
    ),
):
    """Run scrapers, score jobs, store results, and notify on new matches."""
    cfg = _get_config()
    db = _get_db(cfg) if not dry_run else None
    scorer = JobScorer(cfg.profile)
    notifier = Notifier(cfg.notifications)

    sites = [site] if site else cfg.search.sites
    terms = [term] if term else cfg.search.terms
    locations = cfg.search.locations

    new_high_score: list = []
    total_found = 0
    total_new = 0
    total_filtered = 0

    # Build task list for concurrent execution
    tasks = [
        (site_name, search_term, loc)
        for site_name in sites
        for search_term in terms
        for loc in locations
    ]

    def _run_one(task):
        s_name, s_term, s_loc = task
        params = ScrapeParams(
            search_term=s_term,
            location=s_loc,
            results_wanted=cfg.search.results_per_site,
            hours_old=cfg.search.hours_old,
            distance_miles=cfg.search.distance_miles,
        )
        try:
            scraper = get_scraper(s_name, cfg.scraping)
            jobs = scraper.scrape(params)
        except Exception as e:
            return s_name, s_term, s_loc, [], str(e)
        # Score in worker thread (CPU-bound but fast)
        for job in jobs:
            score, breakdown = scorer.score(job)
            job.score = score
            job.score_breakdown = breakdown
        return s_name, s_term, s_loc, jobs, None

    max_workers = min(cfg.scraping.max_workers, len(tasks)) if tasks else 1
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_run_one, task): (task, datetime.now()) for task in tasks
        }
        for future in as_completed(futures):
            task, started_at = futures[future]
            site_name, search_term, location, jobs, error = future.result()

            run = ScrapeRun(
                site=Site(site_name),
                search_term=search_term,
                location=location,
                started_at=started_at,
            )
            run_id = db.record_run(run) if db else None

            if error:
                console.print(f"[red]Error scraping {site_name}: {error}[/red]")
                if db and run_id:
                    db.finish_run(run_id, 0, 0, error)
                continue

            if jobs:
                console.print(
                    f'[dim]Scraped {site_name}: "{search_term}" in {location} — {len(jobs)} jobs[/dim]'
                )
            else:
                console.print(
                    f'[yellow]Warning: {site_name} returned 0 jobs for "{search_term}" in {location}[/yellow]'
                )

            page_new = 0
            for job in jobs:
                job.search_term = search_term
                if job.score_breakdown.get("dealbreaker"):
                    job.status = "filtered"
                    total_filtered += 1
                    if db:
                        db.upsert_job(job)
                    elif dry_run:
                        console.print(
                            f"  [dim]{job.score}[/dim] | {job.company}: {job.title} | [red]dealbreaker[/red]"
                        )
                    continue

                total_found += 1

                if db:
                    is_new, job_id = db.upsert_job(job)
                    job.id = job_id
                    if is_new:
                        page_new += 1
                        total_new += 1
                        if job.score >= cfg.scoring.min_alert_score:
                            if _filter_alert_jobs([job], cfg):
                                new_high_score.append(job)
                elif dry_run and job.score >= cfg.scoring.min_display_score:
                    console.print(
                        f"  [green]{job.score}[/green] | {job.company}: {job.title} | {job.location.display}"
                    )

            if db and run_id:
                db.finish_run(run_id, len(jobs), page_new)

    parts = [f"Found {total_found} jobs", f"{total_new} new"]
    if total_filtered:
        parts.append(f"{total_filtered} filtered by dealbreakers")
    console.print(f"\n[bold]Done.[/bold] {', '.join(parts)}.")

    if new_high_score and not dry_run:
        new_high_score.sort(key=lambda j: j.score, reverse=True)
        console.print(
            f"[bold green]{len(new_high_score)} new high-score matches![/bold green]"
        )
        notifier.notify_new_jobs(new_high_score)

    if db:
        db.close()


@app.command("list")
def list_jobs(
    status: str = typer.Option(
        "new", help="Filter by status: new, applied, rejected, filtered, all"
    ),
    min_score: int = typer.Option(None, "--min-score", help="Minimum score filter"),
    company: str = typer.Option(None, help="Filter by company name"),
    limit: int = typer.Option(30, help="Max results"),
):
    """List jobs in a rich table, sorted by score."""
    cfg = _get_config()
    db = _get_db(cfg)
    min_s = min_score if min_score is not None else cfg.scoring.min_display_score
    if status == "filtered" and min_score is None:
        min_s = 0

    jobs = db.get_jobs(status=status, min_score=min_s, company=company, limit=limit)
    db.close()

    if not jobs:
        console.print("[dim]No jobs found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=5)
    table.add_column("Score", width=5)
    table.add_column("Company", width=14)
    table.add_column("Title", width=32)
    table.add_column("Location", width=16)
    table.add_column("Posted", width=8)
    table.add_column("Src", width=4)

    for i, job in enumerate(jobs, 1):
        score_style = (
            "green" if job.score >= 55 else ("yellow" if job.score >= 30 else "dim")
        )
        posted = job.date_posted.strftime("%-md ago") if job.date_posted else "?"
        if job.date_posted:
            days = (date.today() - job.date_posted).days
            posted = f"{days}d" if days > 0 else "today"

        table.add_row(
            str(job.id or i),
            f"[{score_style}]{job.score}[/{score_style}]",
            job.company[:14],
            job.title[:32],
            job.location.display[:16],
            posted,
            job.source.value[:4],
        )

    console.print(table)
    console.print(
        f"[dim]{len(jobs)} jobs shown (status={status}, min_score={min_s})[/dim]"
    )


@app.command()
def export(
    output: Path = typer.Option(..., help="Output file path"),
    fmt: str = typer.Option(
        None,
        "--format",
        help="Output format: csv or json (default: inferred from extension, falls back to csv)",
    ),
    status: str = typer.Option(
        "all", help="Filter by status: new, applied, rejected, filtered, all"
    ),
    min_score: int = typer.Option(None, "--min-score", help="Minimum score filter"),
    company: str = typer.Option(None, help="Company name substring filter"),
    source: str = typer.Option(None, "--source", help="Filter by source site"),
    days: int = typer.Option(
        None,
        help="Last N days by date_posted (mutually exclusive with --since/--until)",
    ),
    since: str = typer.Option(None, help="Start date inclusive, YYYY-MM-DD"),
    until: str = typer.Option(None, help="End date inclusive, YYYY-MM-DD"),
):
    """Export jobs to a CSV or JSON file.

    Jobs with no date_posted always pass date filters.
    """
    from datetime import timedelta

    # Validate mutually exclusive date options
    if days is not None and (since is not None or until is not None):
        console.print("[red]--days and --since/--until are mutually exclusive.[/red]")
        raise typer.Exit(1)

    # Resolve format
    if fmt:
        resolved_fmt = fmt.lower()
    elif output.suffix.lower() == ".json":
        resolved_fmt = "json"
    else:
        resolved_fmt = "csv"

    cfg = _get_config()
    db = _get_db(cfg)
    jobs = db.get_jobs(
        status=status,
        min_score=min_score,
        company=company,
        source=source,
        limit=None,
    )
    db.close()

    # Apply date filtering in Python
    if days is not None:
        cutoff = date.today() - timedelta(days=days)
        jobs = [j for j in jobs if j.date_posted is None or j.date_posted >= cutoff]
    else:
        if since is not None:
            try:
                since_date = date.fromisoformat(since)
            except ValueError:
                console.print(
                    f"[red]Invalid date format: '{since}'. Use YYYY-MM-DD.[/red]"
                )
                raise typer.Exit(1)
            jobs = [
                j for j in jobs if j.date_posted is None or j.date_posted >= since_date
            ]
        if until is not None:
            try:
                until_date = date.fromisoformat(until)
            except ValueError:
                console.print(
                    f"[red]Invalid date format: '{until}'. Use YYYY-MM-DD.[/red]"
                )
                raise typer.Exit(1)
            jobs = [
                j for j in jobs if j.date_posted is None or j.date_posted <= until_date
            ]

    if not jobs:
        console.print("No jobs found matching filters.")
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    writer = write_json if resolved_fmt == "json" else write_csv
    count = writer(jobs, output)
    console.print(f"Exported {count} jobs to {output}")


@app.command()
def view(job_id: int = typer.Argument(..., help="Job ID to view")):
    """Show full details of a job."""
    cfg = _get_config()
    db = _get_db(cfg)
    job = db.get_job(job_id)
    db.close()

    if not job:
        console.print(f"[red]Job #{job_id} not found.[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold]{job.title}[/bold]")
    console.print(f"Company: {job.company}")
    console.print(f"Location: {job.location.display}")
    console.print(f"Score: [green]{job.score}[/green]")
    if job.score_breakdown:
        parts = [f"{k}={v}" for k, v in job.score_breakdown.items()]
        console.print(f"Breakdown: {', '.join(parts)}")
    if job.compensation:
        console.print(f"Salary: {job.compensation.display}")
    console.print(f"Posted: {job.date_posted or '?'}")
    console.print(f"Source: {job.source.value}")
    console.print(f"Status: {job.status}")
    console.print(f"URL: {job.url}")
    if job.description:
        console.print("\n[dim]--- Description (first 500 chars) ---[/dim]")
        console.print(job.description[:500])


@app.command("apply")
def mark_applied(
    job_id: int = typer.Argument(..., help="Job ID"),
    notes: str = typer.Option("", help="Application notes"),
    open_url: bool = typer.Option(False, "--open", help="Open job URL in browser"),
):
    """Mark a job as applied."""
    cfg = _get_config()
    db = _get_db(cfg)
    job = db.get_job(job_id)
    if not job:
        console.print(f"[red]Job #{job_id} not found.[/red]")
        db.close()
        raise typer.Exit(1)

    db.mark_applied(job_id, notes)
    db.close()
    console.print(
        f"[green]Marked #{job_id} ({job.company}: {job.title}) as applied.[/green]"
    )

    if open_url:
        import webbrowser

        webbrowser.open(job.url)


@app.command()
def reject(
    job_id: int = typer.Argument(..., help="Job ID"),
    notes: str = typer.Option("", help="Rejection reason"),
):
    """Mark a job as rejected."""
    cfg = _get_config()
    db = _get_db(cfg)
    job = db.get_job(job_id)
    if not job:
        console.print(f"[red]Job #{job_id} not found.[/red]")
        db.close()
        raise typer.Exit(1)

    db.update_status(job_id, "rejected", notes)
    db.close()
    console.print(f"[dim]Job #{job_id} ({job.company}: {job.title}) rejected.[/dim]")


@app.command()
def stats():
    """Show summary statistics."""
    cfg = _get_config()
    db = _get_db(cfg)
    s = db.get_stats()
    db.close()

    console.print("\n[bold]job-scout Stats[/bold]\n")
    console.print(f"Total jobs: {s['total']}")

    if s["by_status"]:
        table = Table(title="By Status", show_header=True)
        table.add_column("Status")
        table.add_column("Count", justify="right")
        for status, cnt in s["by_status"].items():
            table.add_row(status, str(cnt))
        console.print(table)

    if s["by_source"]:
        table = Table(title="By Source", show_header=True)
        table.add_column("Source")
        table.add_column("Count", justify="right")
        for source, cnt in s["by_source"].items():
            table.add_row(source, str(cnt))
        console.print(table)

    if s["score_distribution"]:
        table = Table(title="Score Distribution", show_header=True)
        table.add_column("Tier")
        table.add_column("Count", justify="right")
        for tier, cnt in s["score_distribution"].items():
            table.add_row(tier, str(cnt))
        console.print(table)

    if s.get("by_search_term"):
        table = Table(title="By Search Term", show_header=True)
        table.add_column("Search Term")
        table.add_column("Jobs", justify="right")
        table.add_column("Avg Score", justify="right")
        for row in s["by_search_term"]:
            table.add_row(
                row["search_term"],
                str(row["count"]),
                str(row["avg_score"]),
            )
        console.print(table)

    zr = s.get("zero_result_runs", {})
    if zr.get("count", 0) > 0:
        console.print(
            f"\n[yellow]Warning: {zr['count']} zero-result run(s) in the last 7 days[/yellow]",
        )
        for run in zr["recent"][:5]:
            console.print(
                f'  [dim]{run["site"]}: "{run["search_term"]}" in {run["location"]} ({run["started_at"]})[/dim]'
            )


@app.command()
def schedule(
    install_flag: bool = typer.Option(
        False, "--install", help="Install launchd schedule"
    ),
    uninstall_flag: bool = typer.Option(
        False, "--uninstall", help="Remove launchd schedule"
    ),
):
    """Manage launchd scheduling."""
    from job_scout import scheduler

    if install_flag:
        cfg = _get_config()
        paths = scheduler.install(cfg.schedule, Path.cwd())
        console.print("[green]Installed schedules:[/green]")
        for path in paths:
            console.print(f"  {path}")
        console.print(f"\nScrape: every {cfg.schedule.interval_hours} hours")
        console.print(
            f"Digest: daily at {cfg.schedule.digest_hour:02d}:{cfg.schedule.digest_minute:02d}"
        )
        console.print(
            f"Report: daily at {cfg.schedule.report_hour:02d}:{cfg.schedule.report_minute:02d}"
        )
    elif uninstall_flag:
        scheduler.uninstall()
        console.print("[dim]All schedules removed.[/dim]")
    else:
        s = scheduler.status()
        log_dir = s.pop("log_dir", "")
        for name, info in s.items():
            status_str = (
                "[green]running[/green]"
                if info["running"]
                else (
                    "[yellow]installed[/yellow]"
                    if info["installed"]
                    else "[dim]not installed[/dim]"
                )
            )
            console.print(f"  {name}: {status_str}")
            if info["installed"]:
                console.print(f"    {info['plist_path']}")
        if log_dir:
            console.print(f"\nLogs: {log_dir}")


@app.command()
def init(
    full: bool = typer.Option(
        False, "--full", help="Use the full config template with all options"
    ),
):
    """First-time setup: create config.yaml and initialize DB."""
    target = XDG_CONFIG_PATH

    if target.exists():
        console.print(f"[yellow]config.yaml already exists at {target}[/yellow]")
        console.print("To start fresh, delete it and run init again.")
        console.print("Run [bold]job-scout check[/bold] to validate it.")
        return

    # Also check CWD for existing config to migrate
    cwd_config = Path("config.yaml")
    if cwd_config.exists():
        console.print(
            "[yellow]Found existing config.yaml in current directory.[/yellow]"
        )
        console.print(f"Moving to {target}...")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(cwd_config), str(target))
        console.print(f"[green]Moved to {target}[/green]")
    else:
        project_dir = Path(__file__).resolve().parent.parent.parent
        template = project_dir / (
            "config.template.yaml" if full else "config.minimal.yaml"
        )

        if not template.exists():
            console.print(f"[red]Template not found at {template}[/red]")
            raise typer.Exit(1)

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(template, target)
        console.print(f"[green]Created {target}[/green]")

    # Init DB directory + file
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = _get_db()
    db.close()

    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print(f"  1. Edit [bold]{target}[/bold] — fill in the REQUIRED fields")
    console.print("  2. Run  [bold]job-scout check[/bold] to validate your config")
    console.print("  3. Run  [bold]job-scout scrape --dry-run[/bold] to test")
    console.print("  4. Run  [bold]job-scout schedule --install[/bold] when ready")


@app.command()
def check():
    """Validate config.yaml and test connections."""
    target = resolve_config_path()
    if not target.exists():
        console.print(
            "[red]No config.yaml found.[/red] Run [bold]job-scout init[/bold] first."
        )
        raise typer.Exit(1)

    # 1. Parse YAML
    try:
        with open(target) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        console.print(f"[red]YAML syntax error:[/red] {e}")
        raise typer.Exit(1)

    if not raw:
        console.print("[red]config.yaml is empty.[/red] Fill in the required fields.")
        raise typer.Exit(1)

    # 2. Validate with Pydantic
    try:
        cfg = AppConfig(**raw)
    except ValidationError as e:
        console.print("[red]Config errors:[/red]")
        for err in e.errors():
            field = " -> ".join(str(x) for x in err["loc"])
            console.print(f"  [bold]{field}[/bold]: {err['msg']}")
        raise typer.Exit(1)

    # 3. Quality diagnostics
    diags = validate_quality(cfg)
    warnings = [d for d in diags if d.level == "warning"]
    errors = [d for d in diags if d.level == "error"]

    # 4. Print summary
    console.print("[green]Config is valid.[/green]")
    console.print(f"  Profile: {cfg.profile.name}")
    console.print(f"  Target: {cfg.profile.target_title}")
    console.print(
        f"  Search: {len(cfg.search.terms)} term(s), {len(cfg.search.locations)} location(s)"
    )
    console.print(f"  Sites: {', '.join(cfg.search.sites)}")
    console.print(
        f"  Email alerts: {'enabled' if cfg.notifications.email.enabled else 'disabled'}"
    )
    console.print(
        f"  Telegram alerts: {'enabled' if cfg.notifications.telegram.enabled else 'disabled'}"
    )
    console.print(
        f"  Slack alerts: {'enabled' if cfg.notifications.slack.enabled else 'disabled'}"
    )
    console.print(
        f"  Discord alerts: {'enabled' if cfg.notifications.discord.enabled else 'disabled'}"
    )

    # 5. Render diagnostics (warnings first, errors last)
    if warnings:
        console.print("\n[yellow]Quality warnings:[/yellow]")
        for d in warnings:
            console.print(f"  {d.field}: {d.message}")

    if errors:
        console.print("\n[red]Config errors:[/red]")
        for d in errors:
            console.print(f"  {d.field}: {d.message}")

    # 6. Test connections (skip if errors present)
    if errors:
        raise typer.Exit(1)

    # 6a. Test SMTP if email is enabled
    if cfg.notifications.email.enabled:
        ecfg = cfg.notifications.email
        if not ecfg.username or not ecfg.app_password:
            console.print("[yellow]  Email enabled but credentials missing.[/yellow]")
        else:
            console.print("  Testing SMTP connection...", end="")
            try:
                import smtplib

                smtp = smtplib.SMTP(ecfg.smtp_host, ecfg.smtp_port, timeout=10)
                smtp.starttls()
                smtp.login(ecfg.username, ecfg.app_password)
                smtp.quit()
                console.print(" [green]OK[/green]")
            except Exception as e:
                console.print(f" [red]FAILED[/red]: {e}")

    # 6b. Test Telegram if enabled
    if cfg.notifications.telegram.enabled:
        tcfg = cfg.notifications.telegram
        if not tcfg.bot_token or not tcfg.chat_id:
            console.print(
                "[yellow]  Telegram enabled but bot_token or chat_id missing.[/yellow]"
            )
        else:
            console.print("  Testing Telegram bot...", end="")
            try:
                import httpx

                resp = httpx.get(
                    f"https://api.telegram.org/bot{tcfg.bot_token}/getMe",
                    timeout=10,
                )
                if resp.status_code == 200 and resp.json().get("ok"):
                    bot_name = resp.json()["result"].get("username", "?")
                    console.print(f" [green]OK[/green] (@{bot_name})")
                else:
                    console.print(f" [red]FAILED[/red]: {resp.text}")
            except Exception as e:
                console.print(f" [red]FAILED[/red]: {e}")

    # 6c. Check Slack webhook URL format if enabled
    if cfg.notifications.slack.enabled:
        scfg = cfg.notifications.slack
        if not scfg.webhook_url:
            console.print("[yellow]  Slack enabled but webhook_url is empty.[/yellow]")
        elif not scfg.webhook_url.startswith("https://hooks.slack.com/services/"):
            console.print(
                "[yellow]  Slack webhook URL doesn't match expected format.[/yellow]"
            )
        else:
            console.print("  Slack webhook URL: [green]OK[/green]")

    # 6d. Check Discord webhook URL format if enabled
    if cfg.notifications.discord.enabled:
        dcfg = cfg.notifications.discord
        if not dcfg.webhook_url:
            console.print(
                "[yellow]  Discord enabled but webhook_url is empty.[/yellow]"
            )
        elif not dcfg.webhook_url.startswith("https://discord.com/api/webhooks/"):
            console.print(
                "[yellow]  Discord webhook URL doesn't match expected format.[/yellow]"
            )
        else:
            console.print("  Discord webhook URL: [green]OK[/green]")

    console.print()
    console.print("Next: [bold]job-scout scrape --dry-run[/bold]")

    if warnings:
        raise typer.Exit(2)


@app.command()
def rescore(
    status: str = typer.Option(None, help="Filter by status: new, applied, rejected"),
    site: str = typer.Option(None, "--site", help="Filter by source site"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show changes without persisting"
    ),
):
    """Re-score all jobs using current config (instant feedback loop for config tuning)."""
    from job_scout.scheduler import LOG_DIR

    cfg = _get_config()
    db = _get_db(cfg)
    scorer = JobScorer(cfg.profile)

    jobs = db.get_jobs(status=status, source=site, limit=None)
    total = len(jobs)

    updates: list[tuple[int, int, dict]] = []
    status_updates: list[tuple[int, str]] = []
    for job in jobs:
        old_score = job.score
        new_score, breakdown = scorer.score(job)
        if new_score != old_score:
            updates.append((job.id, new_score, breakdown))
        # Track dealbreaker status transitions
        is_dealbreaker = breakdown.get("dealbreaker", False)
        if is_dealbreaker and job.status == "new":
            status_updates.append((job.id, "filtered"))
        elif not is_dealbreaker and job.status == "filtered":
            status_updates.append((job.id, "new"))

    if not updates and not status_updates:
        console.print(f"Rescored {total} jobs — no changes.")
        db.close()
        return

    if not dry_run:
        if updates:
            db.batch_update_scores(updates)
        for job_id, new_status in status_updates:
            db.update_status(job_id, new_status)

    # Build a lookup for old scores
    old_scores = {job.id: job.score for job in jobs}
    job_lookup = {job.id: job for job in jobs}

    # Print summary
    prefix = "[DRY RUN] " if dry_run else ""
    console.print(f"{prefix}Rescored {total} jobs ({len(updates)} changed)")

    scored_updates = []
    if updates:
        deltas = [new_score - old_scores[row_id] for row_id, new_score, _ in updates]
        avg_shift = sum(deltas) / len(deltas)
        min_delta = min(deltas)
        max_delta = max(deltas)

        scored_updates = [
            (row_id, old_scores[row_id], new_score, breakdown)
            for row_id, new_score, breakdown in updates
        ]
        scored_updates.sort(key=lambda x: abs(x[2] - x[1]), reverse=True)

        console.print(
            f"  Avg shift: {avg_shift:+.1f} | Range: {min_delta:+d} to {max_delta:+d}"
        )
        console.print()

        # Table of top 25
        table = Table(show_header=True, header_style="bold")
        table.add_column("Score", width=7)
        table.add_column("Company", width=16)
        table.add_column("Title", width=36)

        for row_id, old, new, _ in scored_updates[:25]:
            job = job_lookup[row_id]
            score_style = "green" if new > old else "red"
            table.add_row(
                f"[{score_style}]{old}→{new}[/{score_style}]",
                job.company[:16],
                job.title[:36],
            )

        console.print(table)

    if status_updates:
        newly_filtered = sum(1 for _, s in status_updates if s == "filtered")
        newly_unfiltered = sum(1 for _, s in status_updates if s == "new")
        if newly_filtered:
            console.print(f"  {newly_filtered} job(s) now filtered by dealbreakers")
        if newly_unfiltered:
            console.print(
                f"  {newly_unfiltered} job(s) un-filtered (dealbreaker removed)"
            )

    # Write log file
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"rescore-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"
    with open(log_path, "w") as f:
        f.write(f"{prefix}Rescored {total} jobs ({len(updates)} changed)\n")
        if updates:
            f.write(
                f"Avg shift: {avg_shift:+.1f} | Range: {min_delta:+d} to {max_delta:+d}\n\n"
            )
        for row_id, old, new, breakdown in scored_updates:
            job = job_lookup[row_id]
            f.write(f"{old}→{new} | {job.company}: {job.title} | {breakdown}\n")

    console.print(f"\nFull results: {log_path}")

    db.close()


@app.command()
def digest():
    """Send daily digest of top job matches via email, Telegram, Slack, and/or Discord."""
    from datetime import timedelta
    from job_scout.notify import send_email, send_telegram, send_slack, send_discord, _esc_md, _esc_slack, _esc_discord

    cfg = _get_config()
    db = _get_db(cfg)

    cutoff = datetime.now() - timedelta(hours=24)
    jobs = db.get_jobs(
        status="new", min_score=cfg.scoring.min_alert_score, since=cutoff, limit=None
    )
    jobs = _filter_alert_jobs(jobs, cfg)
    stats = db.get_alert_stats(score_threshold=cfg.scoring.min_alert_score)
    db.close()

    if not jobs:
        console.print("[dim]No new matches in the last 24h.[/dim]")
        return

    display_jobs = jobs[:10]
    sent_any = False

    # Email digest
    if cfg.notifications.email.enabled:
        lines = [f"job-scout digest — {len(jobs)} match(es) in the last 24h\n"]
        for job in display_jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"#{job.id} " if job.id else ""
            loc_line = f"  {job.location.display}"
            if salary:
                loc_line += f" | {salary}"
            lines.append(
                f"[{job.score}] (kw:{kw}) {id_tag}{job.company}: {job.title}\n"
                f"{loc_line}\n"
                f"  {job.url}\n"
            )
        lines.append(
            f"\n\U0001f4ca {stats['total_new']} unreviewed | {stats['scraped_24h']} scraped today"
        )
        if send_email(
            subject=f"job-scout digest: {len(jobs)} match(es)",
            body="\n".join(lines),
            cfg=cfg.notifications.email,
        ):
            sent_any = True

    # Telegram digest
    if cfg.notifications.telegram.enabled:
        tg_lines = [f"*job\\-scout digest* — {len(jobs)} match\\(es\\)\n"]
        for job in display_jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"\\#{job.id} " if job.id else ""
            loc_line = f"  {_esc_md(job.location.display)}"
            if salary:
                loc_line += f" \\| {_esc_md(salary)}"
            tg_lines.append(
                f"*{job.score}* \\(kw:{kw}\\) \\| {id_tag}[{_esc_md(job.company)}: {_esc_md(job.title)}]({job.url})\n"
                f"{loc_line}"
            )
        tg_lines.append(
            f"\n\U0001f4ca {_esc_md(str(stats['total_new']))} unreviewed \\| {_esc_md(str(stats['scraped_24h']))} scraped today"
        )
        if send_telegram(
            text="\n".join(tg_lines),
            cfg=cfg.notifications.telegram,
        ):
            sent_any = True

    # Slack digest
    if cfg.notifications.slack.enabled:
        sl_lines = [f"*job-scout digest* — {len(jobs)} match(es)\n"]
        for job in display_jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"#{job.id} " if job.id else ""
            loc_line = f"  {_esc_slack(job.location.display)}"
            if salary:
                loc_line += f" | {_esc_slack(salary)}"
            sl_lines.append(
                f"*{_esc_slack(job.company)}: {_esc_slack(job.title)}*\n"
                f"Score: {job.score} | keywords: {kw} | {id_tag}{loc_line}\n"
                f"{job.url}"
            )
        sl_lines.append(
            f"\n\U0001f4ca {stats['total_new']} unreviewed | {stats['scraped_24h']} scraped today"
        )
        if send_slack(
            text="\n".join(sl_lines),
            cfg=cfg.notifications.slack,
        ):
            sent_any = True

    # Discord digest
    if cfg.notifications.discord.enabled:
        dc_lines = [f"**job-scout digest** — {len(jobs)} match(es)\n"]
        for job in display_jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"#{job.id} " if job.id else ""
            loc_line = f"  {_esc_discord(job.location.display)}"
            if salary:
                loc_line += f" | {_esc_discord(salary)}"
            dc_lines.append(
                f"**{_esc_discord(job.company)}: {_esc_discord(job.title)}**\n"
                f"Score: {job.score} | keywords: {kw} | {id_tag}{loc_line}\n"
                f"{job.url}"
            )
        dc_lines.append(
            f"\n\U0001f4ca {stats['total_new']} unreviewed | {stats['scraped_24h']} scraped today"
        )
        if send_discord(
            text="\n".join(dc_lines),
            cfg=cfg.notifications.discord,
        ):
            sent_any = True

    if sent_any:
        console.print(f"[green]Digest sent — {len(jobs)} matches.[/green]")
    else:
        console.print("[red]Failed to send digest. Check notification config.[/red]")


@app.command()
def report():
    """Generate a daily markdown report of top job matches."""
    from datetime import timedelta

    cfg = _get_config()
    db = _get_db(cfg)

    cutoff = datetime.now() - timedelta(hours=24)
    jobs = db.get_jobs(status="new", min_score=40, since=cutoff, limit=None)
    jobs = _filter_alert_jobs(jobs, cfg)

    threshold = cfg.scoring.min_alert_score
    high = [j for j in jobs if j.score >= threshold][:20]
    medium = [j for j in jobs if 40 <= j.score < threshold][:20]
    medium_total = sum(1 for j in jobs if 40 <= j.score < threshold)

    stats = db.get_alert_stats(score_threshold=threshold)
    trend = db.get_daily_trend(days=7, score_threshold=threshold)
    db.close()

    now = datetime.now().astimezone()
    report_dir = cfg.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{now.strftime('%Y-%m-%d')}.md"

    lines = [
        "---",
        "title: Job Hunt Daily Report",
        f"generated: {now.isoformat()}",
        "---",
        "",
        f"# Job Hunt Report — {now.strftime('%A, %B %-d, %Y')}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Scraped (24h) | {stats['scraped_24h']} |",
        f"| High match (>={threshold}) | {stats['high_count']} |",
        f"| Worth review (40-{threshold - 1}) | {stats['medium_count']} |",
        f"| Total unreviewed | {stats['total_new']} |",
        "",
    ]

    if high:
        lines.extend(
            [
                f"## High Match (score >= {threshold})",
                "",
                "| Score | Title | Company | Location | Comp | Link |",
                "|-------|-------|---------|----------|------|------|",
            ]
        )
        for j in high:
            salary = j.compensation.display_concise if j.compensation else ""
            loc = j.location.display
            lines.append(
                f"| {j.score} | {j.title} | {j.company} | {loc} | {salary} | [apply]({j.url}) |"
            )
        lines.append("")

    if medium:
        lines.extend(
            [
                f"## Worth Review (40-{threshold - 1})",
                "",
                "| Score | Title | Company | Location | Comp | Link |",
                "|-------|-------|---------|----------|------|------|",
            ]
        )
        for j in medium:
            salary = j.compensation.display_concise if j.compensation else ""
            loc = j.location.display
            lines.append(
                f"| {j.score} | {j.title} | {j.company} | {loc} | {salary} | [apply]({j.url}) |"
            )
        if medium_total > 20:
            lines.append(f"*(showing top 20 of {medium_total})*")
        lines.append("")

    if trend:
        lines.extend(
            [
                "## 7-Day Trend",
                "",
                f"| Date | Total | High (>={threshold}) | Medium (40-{threshold - 1}) |",
                "|------|-------|-------------|----------------|",
            ]
        )
        for row in trend:
            lines.append(
                f"| {row['date']} | {row['total']} | {row['high']} | {row['medium']} |"
            )
        lines.append("")

    report_path.write_text("\n".join(lines))
    console.print(f"[green]Report saved to {report_path}[/green]")


@app.callback()
def main():
    """job-scout — Lightweight job scraping and alerting."""
    pass


if __name__ == "__main__":
    app()
