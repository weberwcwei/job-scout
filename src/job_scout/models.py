"""Pydantic data models for jobs, scrape runs, and search params."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field


class Site(str, Enum):
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    GOOGLE = "google"
    GLASSDOOR = "glassdoor"
    ZIPRECRUITER = "ziprecruiter"
    BAYT = "bayt"


class JobType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"


class CompInterval(str, Enum):
    YEARLY = "yearly"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"
    HOURLY = "hourly"


class Compensation(BaseModel):
    min_amount: float | None = None
    max_amount: float | None = None
    currency: str = "USD"
    interval: CompInterval | None = None

    @computed_field
    @property
    def display(self) -> str:
        if not self.min_amount:
            return ""
        parts = [f"${self.min_amount:,.0f}"]
        if self.max_amount and self.max_amount != self.min_amount:
            parts.append(f"- ${self.max_amount:,.0f}")
        if self.interval:
            parts.append(f"/{self.interval.value}")
        return " ".join(parts)

    @computed_field
    @property
    def display_concise(self) -> str:
        """Concise salary: '$181k-$318k'. Empty string when unavailable."""
        if not self.min_amount:
            return ""
        fmt = (
            (lambda v: f"${v / 1000:.0f}k")
            if self.min_amount >= 1000
            else (lambda v: f"${v:.0f}")
        )
        parts = [fmt(self.min_amount)]
        if self.max_amount and self.max_amount != self.min_amount:
            parts.append(fmt(self.max_amount))
        return "-".join(parts)


class Location(BaseModel):
    city: str | None = None
    state: str | None = None
    country: str | None = "US"
    is_remote: bool = False

    @computed_field
    @property
    def display(self) -> str:
        parts = [p for p in [self.city, self.state] if p]
        if self.is_remote:
            parts.append("(Remote)")
        return ", ".join(parts) if parts else "Unknown"


class Job(BaseModel):
    """Core job posting model used across all scrapers."""

    model_config = ConfigDict(populate_by_name=True)

    id: int | None = None
    source: Site
    source_id: str
    url: str
    title: str
    company: str
    location: Location = Field(default_factory=Location)
    description: str = ""
    job_type: list[JobType] = Field(default_factory=list)
    compensation: Compensation | None = None
    date_posted: date | None = None
    date_scraped: datetime = Field(default_factory=datetime.now)
    score: int = 0
    score_breakdown: dict = Field(default_factory=dict)
    status: str = "new"
    notes: str = ""
    applied_date: date | None = None
    search_term: str | None = None

    @computed_field
    @property
    def dedup_key(self) -> str:
        raw = f"{self.source.value}:{self.source_id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ScrapeRun(BaseModel):
    """Metadata for a single scrape execution."""

    id: int | None = None
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = None
    site: Site
    search_term: str
    location: str
    jobs_found: int = 0
    jobs_new: int = 0
    error: str | None = None


class ScrapeParams(BaseModel):
    """Parameters passed to a scraper."""

    search_term: str
    location: str
    results_wanted: int = 25
    hours_old: int = 72
    distance_miles: int = 50
