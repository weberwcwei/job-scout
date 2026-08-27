"""ATS polling orchestration (M6): poll known boards, normalise, score, persist.

``run_poll`` iterates over pollable companies, calls the right ATS adapter,
projects each job to canonical, scores company + job separately, persists
source records and canonical projections, and closes jobs missing from the
last N polls (fixed threshold from config). Returns a summary dict.

``run_resolve`` runs discovery and then resolves pending companies (domain ->
careers -> ATS -> slug) for any registry entry still missing that info.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from job_scout.ats import get_adapter
from job_scout.config import ScrapingConfig
from job_scout.db import JobDB
from job_scout.discovery import run_discovery
from job_scout.models import ATSProvider
from job_scout.normalization.job import merge, project
from job_scout.ranking.company_score import score_company
from job_scout.ranking.job_score import score_job
from job_scout.registry.companies import CompanyResolver

log = logging.getLogger("job_scout.poll")


def run_poll(
    cfg,
    db: JobDB,
    *,
    scraping: ScrapingConfig | None = None,
    dry_run: bool = False,
) -> dict:
    """Poll every company with a resolved ATS slug.

    Returns a summary dict with keys: companies, jobs, new_source,
    new_canonical, closed_at_jobs, closed_canonical.
    """
    scrap = scraping or cfg.scraping
    companies = db.get_pollable_companies()
    summary = {
        "companies": len(companies),
        "jobs": 0,
        "new_source": 0,
        "new_canonical": 0,
        "closed_at_jobs": 0,
        "closed_canonical": 0,
    }

    for company in companies:
        adapter_cls = get_adapter(company.ats)
        if adapter_cls is None:
            log.warning(
                "no adapter for provider %s; skipping %s", company.ats, company.name
            )
            continue
        adapter = adapter_cls(scrap)
        try:
            jobs = adapter.poll(company)
        except Exception as e:  # noqa: BLE001 - a single company must not sink the run
            log.error("poll %s (%s) failed: %s", company.name, company.ats_slug, e)
            continue

        summary["jobs"] += len(jobs)

        company_score, _ = score_company(company, open_jobs=len(jobs))
        crawl_time = datetime.now()
        for job in jobs:
            # Persist source record (append-only). last_seen_at is the crawl
            # time (when we saw it), not the ATS update time, so close
            # detection counts consecutive missing crawls correctly.
            job.last_seen_at = crawl_time
            if not dry_run:
                is_new, _ = db.upsert_ats_job(job)
                if is_new:
                    summary["new_source"] += 1

            # Project + merge into canonical, score and persist.
            candidate = project(job)
            existing = db.get_canonical(candidate.canonical_key)
            canonical = merge(existing, job) if existing else candidate
            # We just saw this job in the current crawl: reopen it. If it was
            # previously closed, surface it as a repost.
            if existing is not None and (
                existing.status == "closed" or existing.closed_at is not None
            ):
                canonical.repost = True
            canonical.status = "open"
            canonical.closed_at = None
            job_score, breakdown = score_job(job, company_score=company_score)
            canonical.updated_at = job.updated_at
            canonical.last_seen_at = crawl_time
            canonical.score = job_score
            canonical.score_breakdown = breakdown
            if existing is None:
                summary["new_canonical"] += 1
            if not dry_run:
                db.upsert_canonical(canonical)

    # Close detection: fixed threshold of consecutive missing crawls. A job
    # last seen at t0 closes on the Nth missing crawl, where the crawl at t0
    # itself is the reference (not counted as missing).
    threshold = getattr(cfg.discovery, "missing_crawls_before_close", 3)
    poll_interval_hours = getattr(cfg.schedule, "poll_interval_hours", 12) or 12
    cutoff = datetime.now() - timedelta(hours=(threshold - 1) * poll_interval_hours)
    if not dry_run:
        summary["closed_at_jobs"] = db.close_ats_jobs_missing_since(cutoff)
        summary["closed_canonical"] = db.close_canonical_jobs_missing_since(cutoff)

    return summary


def run_resolve(
    cfg,
    db: JobDB,
    *,
    scraping: ScrapingConfig | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Run discovery, then resolve any company missing domain/ATS/slug.

    Returns a summary dict: discovered (companies found), resolved, unresolved.
    """
    scrap = scraping or cfg.scraping
    disc = run_discovery(cfg.discovery, db, dry_run=dry_run)

    resolver = CompanyResolver(scrap)
    # Resolve companies that lack full ATS resolution.
    candidates = [
        c
        for c in db.get_companies()
        if (c.ats == ATSProvider.UNKNOWN or not c.ats_slug)
    ]
    if limit is not None:
        candidates = candidates[:limit]

    resolved = 0
    unresolved = 0
    for company in candidates:
        try:
            out = resolver.resolve(company)
        except Exception as e:  # noqa: BLE001 - best-effort resolution
            log.warning("resolve %s failed: %s", company.name, e)
            unresolved += 1
            continue
        if out.ats != ATSProvider.UNKNOWN or out.ats_slug:
            resolved += 1
            if not dry_run:
                db.upsert_company(out)
        else:
            unresolved += 1

    return {
        "discovered": disc,
        "resolved": resolved,
        "unresolved": unresolved,
    }
