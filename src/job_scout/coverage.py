"""Coverage measurement (M7): registry health, missed-job rate, score tuning.

The missed-job rate needs a ground-truth reference set. Until one exists, the
report surfaces the observable proxies: registry size by provider, polling
health (companies with open vs closed jobs, stale slugs), and per-component
score means (the Jora lesson — diagnose score gaps per component, not as a
black box).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from job_scout.db import JobDB

log = logging.getLogger("job_scout.coverage")


def registry_health(db: JobDB) -> dict[str, object]:
    """Counts by provider, resolved ratio, and pollability."""
    companies = db.get_companies()
    by_provider: dict[str, int] = {}
    for c in companies:
        by_provider[c.ats.value] = by_provider.get(c.ats.value, 0) + 1
    resolved = sum(1 for c in companies if c.ats_slug)
    pollable = len(db.get_pollable_companies())
    return {
        "total": len(companies),
        "resolved": resolved,
        "pollable": pollable,
        "by_provider": by_provider,
    }


def poll_health(db: JobDB, *, days: int = 14) -> dict[str, object]:
    """How many companies have open/closed jobs; stale last_seen counts."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    companies = db.get_pollable_companies()
    open_jobs = db.conn.execute(
        "SELECT COUNT(*) AS n FROM ats_jobs WHERE status = 'open'"
    ).fetchone()["n"]
    closed_jobs = db.conn.execute(
        "SELECT COUNT(*) AS n FROM ats_jobs WHERE status = 'closed'"
    ).fetchone()["n"]
    canonical_open = db.conn.execute(
        "SELECT COUNT(*) AS n FROM canonical_jobs WHERE status = 'open'"
    ).fetchone()["n"]
    canonical_closed = db.conn.execute(
        "SELECT COUNT(*) AS n FROM canonical_jobs WHERE status = 'closed'"
    ).fetchone()["n"]
    stale = db.conn.execute(
        "SELECT COUNT(*) AS n FROM companies WHERE last_verified_at < ?",
        (cutoff,),
    ).fetchone()["n"]
    return {
        "companies_polled": len(companies),
        "ats_open_jobs": open_jobs,
        "ats_closed_jobs": closed_jobs,
        "canonical_open": canonical_open,
        "canonical_closed": canonical_closed,
        "stale_companies": stale,
    }


def score_report(db: JobDB) -> list[dict[str, object]]:
    """Per-scoring-component means across open canonical jobs.

    Aids tuning: e.g. a persistently near-zero location mean flags that
    location parsing is dropping data, not that jobs are elsewhere.
    """
    rows = db.conn.execute(
        """SELECT
            AVG(json_extract(score_breakdown, '$.relevance')) AS relevance,
            AVG(json_extract(score_breakdown, '$.location')) AS location,
            AVG(json_extract(score_breakdown, '$.tech')) AS tech,
            AVG(json_extract(score_breakdown, '$.seniority')) AS seniority,
            AVG(json_extract(score_breakdown, '$.salary')) AS salary,
            AVG(json_extract(score_breakdown, '$.work')) AS work,
            AVG(json_extract(score_breakdown, '$.company')) AS company,
            AVG(json_extract(score_breakdown, '$.freshness')) AS freshness
        FROM canonical_jobs WHERE status = 'open'"""
    ).fetchone()
    return [
        {"component": k, "mean": round(v, 2) if v is not None else None}
        for k, v in dict(rows).items()
    ]


def coverage_report(db: JobDB, *, days: int = 14) -> dict[str, object]:
    """Combined report used by the `job-scout coverage` command."""
    return {
        "registry": registry_health(db),
        "poll": poll_health(db, days=days),
        "score_means": score_report(db),
    }