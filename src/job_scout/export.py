"""Export jobs to CSV or JSON files."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from job_scout.models import Job

FIELDS = [
    "id",
    "score",
    "company",
    "title",
    "location",
    "salary",
    "date_posted",
    "source",
    "status",
    "url",
]


def _job_to_row(job: Job) -> dict:
    """Convert a Job to a flat dict for export."""
    return {
        "id": job.id or "",
        "score": job.score,
        "company": job.company,
        "title": job.title,
        "location": job.location.display if job.location else "",
        "salary": job.compensation.display if job.compensation else "",
        "date_posted": job.date_posted.isoformat() if job.date_posted else "",
        "source": job.source.value,
        "status": job.status,
        "url": job.url,
    }


def write_csv(jobs: list[Job], path: Path) -> int:
    """Write jobs to CSV. Returns row count."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for job in jobs:
            writer.writerow(_job_to_row(job))
    return len(jobs)


def write_json(jobs: list[Job], path: Path) -> int:
    """Write jobs to JSON. Returns row count."""
    rows = []
    for job in jobs:
        row = _job_to_row(job)
        row["score_breakdown"] = job.score_breakdown
        rows.append(row)
    with open(path, "w") as f:
        json.dump(rows, f, indent=2)
    return len(jobs)
