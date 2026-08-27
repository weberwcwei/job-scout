"""Job normalisation: canonical key, title variants, and canonical projection.

The canonical key is a stable fingerprint that lets the same role found on
multiple ATS boards (or reposted under a variant title) collapse into one
canonical record. Source records are never destroyed — the canonical record
is a projection that references them.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from job_scout.models import ATSJob, CanonicalJob

#: Title tokens stripped before keying (normalises "II"/"2"/"- Sydney").
_LEVEL_TOKENS = {
    "i", "ii", "iii", "iv", "v", "sr", "jr", "2", "3", "4", "5",
    "junior", "senior", "lead", "principal", "staff", "mid", "entry",
}

#: Location/remote suffixes stripped before keying.
_SUFFIX_TOKENS = {
    "remote", "hybrid", "sydney", "melbourne", "australia", "au", "nsw",
    "vic", "qld", "wa", "sa", "tas", "act", "nt",
}


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, drop level/location tokens."""
    text = re.sub(r"[^a-z0-9 ]", " ", title.lower())
    tokens = [t for t in text.split() if t not in _LEVEL_TOKENS and t not in _SUFFIX_TOKENS]
    return " ".join(tokens).strip()


def canonical_key(job: ATSJob) -> str:
    """Stable fingerprint for a role: company + normalised title + city/remote."""
    company = re.sub(r"[^a-z0-9]", "", job.company.lower())
    title = normalize_title(job.title)
    loc = job.location.city or ""
    loc = re.sub(r"[^a-z0-9]", "", loc.lower())
    if job.location.is_remote:
        loc = "remote"
    raw = f"{company}|{title}|{loc}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def project(job: ATSJob) -> CanonicalJob:
    """Project a source record into a canonical record (fields only)."""
    return CanonicalJob(
        canonical_key=canonical_key(job),
        company_id=job.company_id,
        company=job.company,
        title=job.title,
        location_text=job.location_text,
        location=job.location,
        hybrid=job.hybrid,
        employment_type=job.employment_type,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        currency=job.currency,
        description_html=job.description_html,
        description=job.description,
        url=job.url,
        apply_url=job.apply_url,
        posted_at=job.posted_at,
        first_seen_at=job.first_seen_at,
        last_seen_at=job.last_seen_at,
        updated_at=job.updated_at,
        repost=job.repost,
        source_ids=[f"{job.source.value}:{job.source_id}"],
    )


def merge(canonical: CanonicalJob, source: ATSJob) -> CanonicalJob:
    """Merge a source record into an existing canonical record.

    Prefers the most recently updated source for mutable fields, keeps the
    earliest first_seen_at, latest last_seen_at, unions source_ids, and makes
    `repost` sticky.
    """
    merged = canonical.model_copy(deep=True)
    if source.updated_at and (
        not merged.updated_at or _cmp_dt(source.updated_at, merged.updated_at) >= 0
    ):
        merged.updated_at = source.updated_at
        merged.title = source.title
        merged.url = source.url
        merged.apply_url = source.apply_url or merged.apply_url
        merged.location_text = source.location_text or merged.location_text
        merged.location = source.location
        merged.hybrid = source.hybrid
        merged.employment_type = source.employment_type or merged.employment_type
        merged.salary_min = source.salary_min if source.salary_min is not None else merged.salary_min
        merged.salary_max = source.salary_max if source.salary_max is not None else merged.salary_max
        merged.currency = source.currency or merged.currency
        merged.description_html = source.description_html or merged.description_html
        merged.description = source.description or merged.description
        merged.posted_at = source.posted_at or merged.posted_at
    if source.first_seen_at and (
        not merged.first_seen_at or _cmp_dt(source.first_seen_at, merged.first_seen_at) < 0
    ):
        merged.first_seen_at = source.first_seen_at
    if source.last_seen_at and _cmp_dt(source.last_seen_at, merged.last_seen_at) > 0:
        merged.last_seen_at = source.last_seen_at
    merged.repost = merged.repost or source.repost
    source_ref = f"{source.source.value}:{source.source_id}"
    if source_ref not in merged.source_ids:
        merged.source_ids.append(source_ref)
    return merged


def _cmp_dt(a: datetime, b: datetime) -> int:
    """Compare two datetimes, treating aware/naive mismatches as naive."""
    if a.tzinfo is not None:
        a = a.replace(tzinfo=None)
    if b.tzinfo is not None:
        b = b.replace(tzinfo=None)
    return (a > b) - (a < b)