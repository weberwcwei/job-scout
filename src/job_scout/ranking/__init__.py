"""Ranking package: separate company and job scoring (see ATS_DISCOVERY.md M5)."""

from __future__ import annotations

from job_scout.ranking.company_score import score_company
from job_scout.ranking.job_score import score_job

__all__ = ["score_company", "score_job"]