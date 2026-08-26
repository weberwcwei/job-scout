"""Company quality/growth scoring (0-100), separate from job scoring.

Signals follow ATS_DISCOVERY.md: funding (decaying), VC portfolio backing,
recognised workplace, sustained hiring, multiple relevant openings; negatives
for staffing agencies and inactive careers sites. The score is explainable via
a breakdown dict.
"""

from __future__ import annotations

from datetime import datetime

from job_scout.models import Company

#: Recruiting/staffing agency name fragments that tank a company score.
_STAFFING_HINTS = (
    "recruit", "staffing", "talent", "agency", "hays", "randstad", "robert half",
    "michael page", "peoplebank", "talent international", "manpower", "adecco",
)

#: Recognised-workplace fragments (Best Places to Work, Great Place to Work).
_WORKPLACE_HINTS = ("great place to work", "best place to work", "gptw", "bptw")

#: Funding decay: a funding signal's value halves every N days.
_FUNDING_HALFLIFE_DAYS = 90.0


def score_company(company: Company, *, open_jobs: int = 0) -> tuple[int, dict[str, object]]:
    """Score a company 0-100. Returns (total, breakdown)."""
    breakdown: dict[str, object] = {}
    total = 0.0

    # Funding: decays with time since last_verified_at.
    funding = 0.0
    if company.last_verified_at:
        age_days = max(0.0, (datetime.now() - company.last_verified_at).days)
        funding = 20 * (0.5 ** (age_days / _FUNDING_HALFLIFE_DAYS))
    breakdown["funding"] = round(funding, 1)

    # VC portfolio backing.
    vc = 0
    for source in company.discovered_from:
        if source in ("blackbird", "airtree", "squarepeg", "mainsequence", "startmate"):
            vc = max(vc, 15)
    breakdown["vc"] = vc

    # Recognised workplace.
    workplace = 0
    name_lower = company.name.lower()
    if any(h in name_lower for h in _WORKPLACE_HINTS):
        workplace = 10
    breakdown["workplace"] = workplace

    # Sustained hiring / multiple openings.
    hiring = 0
    if open_jobs >= 5:
        hiring = 20
    elif open_jobs >= 2:
        hiring = 10
    elif open_jobs >= 1:
        hiring = 5
    breakdown["hiring"] = hiring

    # Staffing agency penalty.
    staffing_penalty = 0
    if any(h in name_lower for h in _STAFFING_HINTS):
        staffing_penalty = 25
    breakdown["staffing_penalty"] = staffing_penalty

    total = max(0, min(100, funding + vc + workplace + hiring - staffing_penalty))
    return int(total), breakdown