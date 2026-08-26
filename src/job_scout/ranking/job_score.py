"""Job relevance scoring for ATS jobs (0-100), kept separate from company score.

Signals follow ATS_DISCOVERY.md: role relevance, Sydney/NSW/remote eligibility,
technology/domain, seniority, salary, hybrid/remote preference, company score,
freshness, keywords/skills. Company score feeds in as one component so a great
role at an unknown company can still rank highly.
"""

from __future__ import annotations

from datetime import datetime

from job_scout.models import ATSJob

#: Location/remote eligibility: jobs in these states or remote pass the filter.
_AU_STATES = {"nsw", "vic", "qld", "wa", "sa", "tas", "act", "nt"}

#: Seniority keywords -> points.
_SENIORITY = {
    "principal": 5, "staff": 5, "lead": 4, "senior": 3, "mid": 2, "junior": 1,
}


def score_job(job: ATSJob, *, company_score: int = 0) -> tuple[int, dict]:
    """Score an ATS job 0-100. Returns (total, breakdown)."""
    breakdown: dict[str, object] = {}

    # Role relevance (0-25): title keyword weight.
    title = job.title.lower()
    relevance = 0
    if "engineer" in title or "developer" in title:
        relevance += 15
    if "software" in title or "backend" in title or "full" in title:
        relevance += 5
    if "data" in title or "machine" in title or "ai" in title:
        relevance += 10
    if "product" in title or "design" in title:
        relevance += 8
    relevance = min(25, relevance)
    breakdown["relevance"] = relevance

    # Location eligibility (0-20): AU + Sydney/NSW/remote preferred.
    location = 0
    if job.location.is_remote:
        location = 20
    elif job.location.country == "AU":
        location = 15
        state = (job.location.state or "").lower()
        if state in ("nsw", "vic"):
            location = 18
    breakdown["location"] = location

    # Technology/domain (0-15): keywords in description.
    text = f"{job.title} {job.description}".lower()
    tech = 0
    for kw in ("python", "golang", "go", "rust", "typescript", "react", "aws", "gcp", "k8s"):
        if kw in text:
            tech += 3
    tech = min(15, tech)
    breakdown["tech"] = tech

    # Seniority (0-5).
    seniority = 0
    for level, points in _SENIORITY.items():
        if level in title:
            seniority = max(seniority, points)
            break
    breakdown["seniority"] = seniority

    # Salary (0-10): presence + magnitude.
    salary = 0
    if job.salary_max:
        if job.salary_max >= 150000:
            salary = 10
        elif job.salary_max >= 110000:
            salary = 7
        elif job.salary_max >= 80000:
            salary = 4
        else:
            salary = 2
    breakdown["salary"] = salary

    # Hybrid/remote preference (0-5).
    work = 5 if (job.hybrid or job.location.is_remote) else 0
    breakdown["work"] = work

    # Company score (0-10): scaled.
    company = min(10, company_score // 10)
    breakdown["company"] = company

    # Freshness (0-10).
    freshness = 10
    if job.posted_at:
        posted = job.posted_at
        if posted.tzinfo is not None:
            posted = posted.replace(tzinfo=None)
        age_days = (datetime.now() - posted).days
        if age_days > 30:
            freshness = 1
        elif age_days > 14:
            freshness = 3
        elif age_days > 7:
            freshness = 5
        elif age_days > 3:
            freshness = 7
    breakdown["freshness"] = freshness

    total = max(
        0,
        min(
            100,
            int(relevance)
            + int(location)
            + int(tech)
            + int(seniority)
            + int(salary)
            + int(work)
            + int(company)
            + int(freshness),
        ),
    )
    return int(total), breakdown