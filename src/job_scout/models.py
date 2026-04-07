"""Pydantic data models for jobs, scrape runs, and search params."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


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


# --- Location normalization lookups ---

US_STATES: dict[str, str] = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
    "puerto rico": "PR",
    "guam": "GU",
    "us virgin islands": "VI",
    "american samoa": "AS",
    "northern mariana islands": "MP",
}

_STATE_CODES: set[str] = set(US_STATES.values())

COUNTRIES: dict[str, str] = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "canada": "CA",
    "united kingdom": "GB",
    "uk": "GB",
    "australia": "AU",
    "germany": "DE",
    "france": "FR",
    "india": "IN",
    "japan": "JP",
    "china": "CN",
    "brazil": "BR",
    "mexico": "MX",
    "spain": "ES",
    "italy": "IT",
    "netherlands": "NL",
    "sweden": "SE",
    "switzerland": "CH",
    "singapore": "SG",
    "ireland": "IE",
    "israel": "IL",
    "south korea": "KR",
    "new zealand": "NZ",
    "united arab emirates": "AE",
    "uae": "AE",
    "saudi arabia": "SA",
    "qatar": "QA",
    "bahrain": "BH",
    "kuwait": "KW",
    "oman": "OM",
    "jordan": "JO",
    "egypt": "EG",
    "lebanon": "LB",
    "pakistan": "PK",
    "georgia": "GE",
}

_COUNTRY_CODES: set[str] = set(COUNTRIES.values())


class Location(BaseModel):
    city: str | None = None
    state: str | None = None
    country: str | None = "US"
    is_remote: bool = False

    @model_validator(mode="after")
    def _normalize(self) -> Location:
        # Rule 1: Strip whitespace; empty/whitespace-only → None
        if self.city is not None:
            self.city = self.city.strip() or None
        if self.state is not None:
            self.state = self.state.strip() or None
        if self.country is not None:
            self.country = self.country.strip() or None

        # Rule 2: "Remote" in city when is_remote is already set
        if self.city and self.city.lower() == "remote" and self.is_remote:
            self.city = None

        # Rules 3-5: Field reclassification (order matters)
        # Rule 4 first: shifted fields (city=state_name, state=country_name)
        if (
            self.city
            and self.state
            and self.city.lower() in US_STATES
            and self.state.lower() in COUNTRIES
        ):
            self.country = COUNTRIES[self.state.lower()]
            self.state = US_STATES[self.city.lower()]
            self.city = None
        else:
            # Rule 3: Country name in city field (only when state is None)
            if self.city and self.state is None and self.city.lower() in COUNTRIES:
                self.country = COUNTRIES[self.city.lower()]
                self.city = None

            # Rule 5: Country name in state field (skip if also a US state name)
            if (
                self.state
                and self.state.lower() in COUNTRIES
                and self.state.lower() not in US_STATES
            ):
                self.country = COUNTRIES[self.state.lower()]
                self.state = None

        # Rule 6: Normalize state → 2-letter abbreviation
        if self.state:
            state_lower = self.state.lower()
            # Strip metro suffixes: "Texas Metropolitan Area" → "texas"
            for suffix in (" metropolitan area", " metro area"):
                if state_lower.endswith(suffix):
                    state_lower = state_lower[: -len(suffix)].strip()
                    break
            if state_lower in US_STATES:
                self.state = US_STATES[state_lower]
            elif self.state.upper() in _STATE_CODES:
                self.state = self.state.upper()

        # Rule 7: Normalize country → 2-letter code
        if self.country:
            country_lower = self.country.lower()
            if country_lower in COUNTRIES:
                self.country = COUNTRIES[country_lower]
            elif self.country.upper() in _COUNTRY_CODES:
                self.country = self.country.upper()

        return self

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
