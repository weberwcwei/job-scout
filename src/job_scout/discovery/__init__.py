"""Discovery orchestrator: run sources, merge by name, write to the registry.

``run_discovery`` instantiates every enabled source, gathers their candidate
companies, merges provenance across sources, and upserts them through a
:class:`~job_scout.db.JobDB`. Returns a summary dict of what was found/added.

Discovery is best-effort: a failing source contributes nothing and never
raises; the whole run still completes.
"""

from __future__ import annotations

import logging

from job_scout.config import DiscoveryConfig
from job_scout.db import JobDB
from job_scout.discovery.ats_search import ATSSearchSource
from job_scout.discovery.funding import FundingSource
from job_scout.discovery.vc_portfolios import VCPortfolioSource
from job_scout.models import ATSProvider, Company

log = logging.getLogger("job_scout.discovery")


def _build_sources(config: DiscoveryConfig) -> list:
    return [
        VCPortfolioSource(config),
        FundingSource(config),
        ATSSearchSource(config),
    ]


def _seed_companies(config: DiscoveryConfig) -> list[Company]:
    """Convert `seed_companies` config entries (name|domain|ats|slug) to companies.

    Each entry is a string like "Canva|canva.com|greenhouse|canva". Fields
    after the name are optional; ats/slug are accepted only when both present.
    """
    seeds: list[Company] = []
    for raw in config.seed_companies:
        if not raw or not raw.strip():
            continue
        parts = [p.strip() for p in raw.split("|")]
        name = parts[0] or ""
        if not name:
            continue
        company = Company(name=name, discovered_from=["seed_list"])
        if len(parts) >= 4 and parts[2] and parts[3]:
            try:
                provider = ATSProvider(parts[2].lower())
            except ValueError:
                provider = ATSProvider.UNKNOWN
            if provider != ATSProvider.UNKNOWN:
                company.ats = provider
                company.ats_slug = parts[3]
        if len(parts) >= 2 and parts[1]:
            company.domain = parts[1]
        seeds.append(company)
    return seeds


def _merge(candidates: list[Company]) -> list[Company]:
    """Merge candidates by name, unioning provenance and preferring resolved ATS info."""
    merged: dict[str, Company] = {}
    order: list[str] = []
    for company in candidates:
        key = company.name.lower()
        if key not in merged:
            merged[key] = company
            order.append(key)
            continue
        existing = merged[key]
        # Union provenance, preserve order.
        for source in company.discovered_from:
            if source not in existing.discovered_from:
                existing.discovered_from.append(source)
        # Prefer a resolved ATS/slug/domain over an empty one.
        if company.ats_slug and not existing.ats_slug:
            existing.ats = company.ats
            existing.ats_slug = company.ats_slug
        if company.domain and not existing.domain:
            existing.domain = company.domain
        if company.careers_url and not existing.careers_url:
            existing.careers_url = company.careers_url
    return [merged[k] for k in order]


def run_discovery(config: DiscoveryConfig, db: JobDB, *, dry_run: bool = False) -> dict:
    """Run all discovery sources and persist the merged result.

    Returns a summary dict with keys: sources, candidates, added, updated,
    total (registry size after the run). None of the counts include the
    ``dry_run`` no-op path (they still reflect what would have changed).
    """
    if not config.enabled:
        log.info("discovery disabled; skipping")
        return {"enabled": False, "candidates": 0, "added": 0, "updated": 0}

    candidates: list[Company] = []
    for source in _build_sources(config):
        try:
            found = source.discover()
        except Exception as e:  # noqa: BLE001 - best-effort discovery
            log.warning("discovery source %s failed: %s", source.name, e)
            found = []
        candidates.extend(found)
        log.info("source %s: %d candidates", source.name, len(found))

    # Bootstrap seed list (deterministic; never rate-limited).
    seeds = _seed_companies(config)
    if seeds:
        candidates.extend(seeds)
        log.info("seed list: %d candidates", len(seeds))

    merged = _merge(candidates)

    added = 0
    updated = 0
    for company in merged:
        is_new, _ = db.upsert_company(company)
        if is_new:
            added += 1
        else:
            updated += 1

    total = len(db.get_companies())
    log.info("discovery complete: %d merged, %d added, %d updated", len(merged), added, updated)
    return {
        "enabled": True,
        "candidates": len(merged),
        "added": added,
        "updated": updated,
        "total": total,
    }