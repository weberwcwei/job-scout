"""Job fit scoring engine."""

from __future__ import annotations

import re
from datetime import date

from job_scout.config import ProfileConfig
from job_scout.models import Job


class JobScorer:
    def __init__(self, profile: ProfileConfig):
        self.profile = profile
        self._title_breakers = [
            re.compile(p) for p in profile.dealbreakers.title_patterns
        ]
        self._company_breakers = [
            re.compile(p) for p in profile.dealbreakers.company_patterns
        ]
        self._desc_breakers = [
            re.compile(p) for p in profile.dealbreakers.description_patterns
        ]

    def score(self, job: Job) -> tuple[int, dict]:
        """Score a job 0-100. Returns (total, breakdown). 0 = dealbreaker."""
        if self._check_dealbreakers(job):
            return 0, {"dealbreaker": True}

        breakdown = {}

        # Keyword match (0-55)
        breakdown["keyword"] = self._score_keywords(job)

        # Company match (0-15)
        breakdown["company"] = self._score_company(job)

        # Title relevance (0-20)
        breakdown["title"] = self._score_title(job)

        # Recency (0-10)
        breakdown["recency"] = self._score_recency(job)

        total = max(0, min(100, sum(breakdown.values())))
        return total, breakdown

    def _check_dealbreakers(self, job: Job) -> bool:
        if any(p.search(job.title) for p in self._title_breakers):
            return True
        if any(p.search(job.company) for p in self._company_breakers):
            return True
        if job.description and any(
            p.search(job.description) for p in self._desc_breakers
        ):
            return True
        return False

    def _score_keywords(self, job: Job) -> int:
        text = f"{job.title} {job.description}".lower()
        kw = self.profile.keywords

        critical_hits = sum(1 for k in kw.critical if k.lower() in text)
        strong_hits = sum(1 for k in kw.strong if k.lower() in text)
        moderate_hits = sum(1 for k in kw.moderate if k.lower() in text)
        weak_hits = sum(1 for k in kw.weak if k.lower() in text)

        # Gate: 0 critical hits = keyword score capped at 10
        if critical_hits == 0:
            return min(10, moderate_hits * 2 + weak_hits)

        score = (
            min(critical_hits * 5, 25)
            + min(strong_hits * 3, 18)
            + min(int(moderate_hits * 1.5), 9)
            + min(weak_hits, 3)
        )
        return min(55, score)

    def _score_company(self, job: Job) -> int:
        company = job.company.lower()
        tiers = self.profile.target_companies
        if any(c.lower() in company for c in tiers.tier1):
            return 15
        if any(c.lower() in company for c in tiers.tier2):
            return 10
        if any(c.lower() in company for c in tiers.tier3):
            return 6
        return 0

    def _score_title(self, job: Job) -> int:
        title = job.title.lower()
        best = 0
        for sig in self.profile.title_signals:
            if sig.pattern.lower() in title:
                best = max(best, sig.points)
        return min(20, best)

    def _score_recency(self, job: Job) -> int:
        if not job.date_posted:
            return 3
        days_old = (date.today() - job.date_posted).days
        if days_old <= 1:
            return 10
        if days_old <= 3:
            return 8
        if days_old <= 7:
            return 5
        if days_old <= 14:
            return 3
        return 1
