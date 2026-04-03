"""CLI entry point for job-scout."""

from __future__ import annotations

import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

import yaml
from pydantic import ValidationError

from job_scout.config import DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH, CONFIG_DIR, AppConfig, load_config
from job_scout.db import JobDB
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
    return load_config(DEFAULT_CONFIG_PATH)


def _get_db(cfg: AppConfig | None = None):
    path = cfg.db_path if cfg and cfg.db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return JobDB(path)


@app.command()
def scrape(
    site: str = typer.Option(None, help="Scrape specific site: linkedin, indeed, google, glassdoor, ziprecruiter, bayt"),
    term: str = typer.Option(None, help="Override search term"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Scrape and score but don't persist"),
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

    # Build task list for concurrent execution
    tasks = [
        (site_name, search_term, loc)
        for site_name in sites
        for search_term in terms
        for loc in locations
    ]

    def _run_one(task):
        s_name, s_term, s_loc = task
        scraper = get_scraper(s_name, cfg.scraping)
        params = ScrapeParams(
            search_term=s_term,
            location=s_loc,
            results_wanted=cfg.search.results_per_site,
            hours_old=cfg.search.hours_old,
            distance_miles=cfg.search.distance_miles,
        )
        try:
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
        futures = {pool.submit(_run_one, task): (task, datetime.now()) for task in tasks}
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

            console.print(
                f"[dim]Scraped {site_name}: \"{search_term}\" in {location} — {len(jobs)} jobs[/dim]"
            )

            page_new = 0
            for job in jobs:
                if job.score == 0:
                    continue  # Dealbreaker

                total_found += 1

                if db:
                    is_new, _ = db.upsert_job(job)
                    if is_new:
                        page_new += 1
                        total_new += 1
                        loc = job.location
                        allowed = cfg.scoring.alert_states
                        in_area = loc.is_remote or not allowed or loc.state in (None, *allowed)
                        if job.score >= cfg.scoring.min_alert_score and in_area:
                            new_high_score.append(job)
                elif dry_run and job.score >= cfg.scoring.min_display_score:
                    console.print(
                        f"  [green]{job.score}[/green] | {job.company}: {job.title} | {job.location.display}"
                    )

            if db and run_id:
                db.finish_run(run_id, len(jobs), page_new)

    console.print(f"\n[bold]Done.[/bold] Found {total_found} jobs, {total_new} new.")

    if new_high_score and not dry_run:
        new_high_score.sort(key=lambda j: j.score, reverse=True)
        console.print(f"[bold green]{len(new_high_score)} new high-score matches![/bold green]")
        notifier.notify_new_jobs(new_high_score)

    if db:
        db.close()


@app.command("list")
def list_jobs(
    status: str = typer.Option("new", help="Filter by status: new, applied, rejected, all"),
    min_score: int = typer.Option(None, "--min-score", help="Minimum score filter"),
    company: str = typer.Option(None, help="Filter by company name"),
    limit: int = typer.Option(30, help="Max results"),
):
    """List jobs in a rich table, sorted by score."""
    cfg = _get_config()
    db = _get_db(cfg)
    min_s = min_score if min_score is not None else cfg.scoring.min_display_score

    jobs = db.get_jobs(status=status, min_score=min_s, company=company, limit=limit)
    db.close()

    if not jobs:
        console.print("[dim]No jobs found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=4)
    table.add_column("Score", width=5)
    table.add_column("Company", width=14)
    table.add_column("Title", width=32)
    table.add_column("Location", width=16)
    table.add_column("Posted", width=8)
    table.add_column("Src", width=4)

    for i, job in enumerate(jobs, 1):
        score_style = "green" if job.score >= 55 else ("yellow" if job.score >= 30 else "dim")
        posted = job.date_posted.strftime("%-md ago") if job.date_posted else "?"
        from datetime import date
        if job.date_posted:
            days = (date.today() - job.date_posted).days
            posted = f"{days}d" if days > 0 else "today"

        table.add_row(
            str(i),
            f"[{score_style}]{job.score}[/{score_style}]",
            job.company[:14],
            job.title[:32],
            job.location.display[:16],
            posted,
            job.source.value[:4],
        )

    console.print(table)
    console.print(f"[dim]{len(jobs)} jobs shown (status={status}, min_score={min_s})[/dim]")


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
    console.print(f"[green]Marked #{job_id} ({job.company}: {job.title}) as applied.[/green]")

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
    db.update_status(job_id, "rejected", notes)
    db.close()
    console.print(f"[dim]Job #{job_id} rejected.[/dim]")


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


@app.command()
def schedule(
    install_flag: bool = typer.Option(False, "--install", help="Install launchd schedule"),
    uninstall_flag: bool = typer.Option(False, "--uninstall", help="Remove launchd schedule"),
):
    """Manage launchd scheduling."""
    from job_scout import scheduler

    if install_flag:
        cfg = _get_config()
        path = scheduler.install(cfg.schedule, Path.cwd())
        console.print(f"[green]Installed schedule at {path}[/green]")
        console.print(f"Will run every {cfg.schedule.interval_hours} hours.")
    elif uninstall_flag:
        scheduler.uninstall()
        console.print("[dim]Schedule removed.[/dim]")
    else:
        s = scheduler.status()
        console.print(f"Installed: {s['installed']}")
        console.print(f"Running: {s['running']}")
        console.print(f"Plist: {s['plist_path']}")
        console.print(f"Logs: {s['log_dir']}")


@app.command()
def init(
    full: bool = typer.Option(False, "--full", help="Use the full config template with all options"),
):
    """First-time setup: create config.yaml and initialize DB."""
    target = DEFAULT_CONFIG_PATH

    if target.exists():
        console.print(f"[yellow]config.yaml already exists at {target}[/yellow]")
        console.print("To start fresh, delete it and run init again.")
        console.print("Run [bold]job-scout check[/bold] to validate it.")
        return

    project_dir = Path(__file__).resolve().parent.parent.parent
    template = project_dir / ("config.template.yaml" if full else "config.minimal.yaml")

    if not template.exists():
        console.print(f"[red]Template not found at {template}[/red]")
        console.print("Are you in the job-scout project directory?")
        raise typer.Exit(1)

    shutil.copy(template, target)
    console.print(f"[green]Created {target}[/green]")

    # Init DB directory + file
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    db = _get_db()
    db.close()

    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print("  1. Edit [bold]config.yaml[/bold] — fill in the REQUIRED fields")
    console.print("  2. Run  [bold]job-scout check[/bold] to validate your config")
    console.print("  3. Run  [bold]job-scout scrape --dry-run[/bold] to test")
    console.print("  4. Run  [bold]job-scout schedule --install[/bold] when ready")


@app.command()
def check():
    """Validate config.yaml and test connections."""
    target = DEFAULT_CONFIG_PATH
    if not target.exists():
        console.print("[red]No config.yaml found.[/red] Run [bold]job-scout init[/bold] first.")
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

    # 3. Check for placeholder values
    warnings = []
    if not cfg.profile.name or cfg.profile.name in ("", "Your Name"):
        warnings.append("profile.name is still a placeholder")
    if not cfg.profile.target_title or cfg.profile.target_title in ("", "your target job title"):
        warnings.append("profile.target_title is still a placeholder")
    if not cfg.search.terms or cfg.search.terms == [""] or cfg.search.terms == [""]:
        warnings.append("search.terms is empty")
    if not cfg.search.locations or cfg.search.locations == [""]:
        warnings.append("search.locations is empty")

    if warnings:
        console.print("[yellow]Warnings — these fields need real values:[/yellow]")
        for w in warnings:
            console.print(f"  {w}")
        raise typer.Exit(1)

    # 4. Print summary
    console.print("[green]Config is valid.[/green]")
    console.print(f"  Profile: {cfg.profile.name}")
    console.print(f"  Target: {cfg.profile.target_title}")
    console.print(f"  Search: {len(cfg.search.terms)} term(s), {len(cfg.search.locations)} location(s)")
    console.print(f"  Sites: {', '.join(cfg.search.sites)}")
    console.print(f"  Email alerts: {'enabled' if cfg.notifications.email.enabled else 'disabled'}")
    console.print(f"  Telegram alerts: {'enabled' if cfg.notifications.telegram.enabled else 'disabled'}")

    # 5. Test SMTP if email is enabled
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

    # 6. Test Telegram if enabled
    if cfg.notifications.telegram.enabled:
        tcfg = cfg.notifications.telegram
        if not tcfg.bot_token or not tcfg.chat_id:
            console.print("[yellow]  Telegram enabled but bot_token or chat_id missing.[/yellow]")
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

    console.print()
    console.print("Next: [bold]job-scout scrape --dry-run[/bold]")


@app.command()
def digest():
    """Send daily digest of top job matches via email and/or Telegram."""
    from datetime import timedelta
    cfg = _get_config()
    db = _get_db(cfg)

    # Get jobs from last 24h with score >= min_alert_score
    cutoff = datetime.now() - timedelta(hours=24)
    jobs = db.get_jobs(min_score=cfg.scoring.min_alert_score, limit=50)
    db.close()

    # Filter to recent ones
    recent = [j for j in jobs if j.date_scraped and j.date_scraped >= cutoff]

    if not recent:
        console.print("[dim]No new matches in the last 24h.[/dim]")
        return

    sent_any = False

    # Email digest
    if cfg.notifications.email.enabled:
        lines = [f"job-scout digest — {len(recent)} match(es) in the last 24h\n"]
        for job in recent[:10]:
            salary = job.compensation.display if job.compensation else "No salary"
            lines.append(
                f"[{job.score}] {job.company}: {job.title}\n"
                f"  {job.location.display} | {salary}\n"
                f"  {job.url}\n"
            )
        from job_scout.notify import send_email
        if send_email(
            subject=f"job-scout digest: {len(recent)} match(es)",
            body="\n".join(lines),
            cfg=cfg.notifications.email,
        ):
            sent_any = True

    # Telegram digest
    if cfg.notifications.telegram.enabled:
        from job_scout.notify import send_telegram, _esc_md
        tg_lines = [f"*job\\-scout digest* — {len(recent)} match\\(es\\)\n"]
        for job in recent[:10]:
            salary = job.compensation.display if job.compensation else "No salary"
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            tg_lines.append(
                f"*{job.score}* \\(kw:{kw}\\) \\| [{_esc_md(job.company)}: {_esc_md(job.title)}]({job.url})\n"
                f"  {_esc_md(job.location.display)} \\| {_esc_md(salary)}"
            )
        if send_telegram(
            text="\n".join(tg_lines),
            cfg=cfg.notifications.telegram,
        ):
            sent_any = True

    if sent_any:
        console.print(f"[green]Digest sent — {len(recent)} matches.[/green]")
    else:
        console.print("[red]Failed to send digest. Check notification config.[/red]")


@app.callback()
def main():
    """job-scout — Lightweight job scraping and alerting."""
    pass


if __name__ == "__main__":
    app()
