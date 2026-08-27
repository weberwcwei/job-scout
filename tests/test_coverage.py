"""Tests for M7: coverage measurement and seed comppany bootstrapping."""

from __future__ import annotations

from datetime import datetime

from job_scout.config import DiscoveryConfig
from job_scout.db import JobDB
from job_scout.models import ATSJob, ATSProvider, Company, Location, Site


def _setup(db: JobDB) -> None:
    db.upsert_company(
        Company(name="Canva", ats=ATSProvider.GREENHOUSE, ats_slug="canva")
    )
    db.upsert_company(
        Company(name="UnknownCo", ats=ATSProvider.UNKNOWN, ats_slug=None)
    )


class TestSeedCompanies:
    def test_parses_seed_entries(self):
        from job_scout.discovery import _seed_companies

        cfg = DiscoveryConfig(
            enabled=True,
            seed_companies=[
                "Canva|canva.com|greenhouse|canva",
                "Atlassian|atlassian.com|lever|atlassian",
                "JustAName",
                "broken|entry",  # no ats/slug -> name only
            ],
        )
        companies = _seed_companies(cfg)
        by_name = {c.name: c for c in companies}
        assert by_name["Canva"].ats == ATSProvider.GREENHOUSE
        assert by_name["Canva"].ats_slug == "canva"
        assert by_name["Canva"].domain == "canva.com"
        assert by_name["Atlassian"].ats == ATSProvider.LEVER
        assert by_name["JustAName"].ats == ATSProvider.UNKNOWN
        assert by_name["broken"].ats == ATSProvider.UNKNOWN
        assert all("seed_list" in c.discovered_from for c in companies)

    def test_run_discovery_includes_seeds(self, tmp_path):
        from unittest.mock import patch

        from job_scout.discovery import run_discovery

        db = JobDB(tmp_path / "t.db")
        cfg = DiscoveryConfig(enabled=True, seed_companies=["Canva|canva.com|greenhouse|canva"])
        with patch("job_scout.discovery._build_sources", return_value=[]):
            summary = run_discovery(cfg, db)

        assert summary["added"] == 1
        c = db.get_company_by_name("Canva")
        assert c is not None
        assert c.ats_slug == "canva"
        db.close()


class TestCoverageReport:
    def test_registry_health(self, tmp_path):
        from job_scout.coverage import registry_health

        db = JobDB(tmp_path / "t.db")
        _setup(db)
        health = registry_health(db)
        assert health["total"] == 2
        assert health["resolved"] == 1
        assert health["pollable"] == 1
        db.close()

    def test_poll_health(self, tmp_path):
        from job_scout.coverage import poll_health

        db = JobDB(tmp_path / "t.db")
        _setup(db)
        db.upsert_ats_job(
            ATSJob(
                source=Site.GREENHOUSE,
                source_id="j1",
                company_id=1,
                company="Canva",
                title="Engineer",
                url="https://x",
                location=Location(city="Sydney"),
                updated_at=datetime.now(),
            )
        )
        health = poll_health(db)
        assert health["ats_open_jobs"] == 1
        db.close()

    def test_coverage_report_shape(self, tmp_path):
        from job_scout.coverage import coverage_report

        db = JobDB(tmp_path / "t.db")
        _setup(db)
        report = coverage_report(db)
        assert "registry" in report
        assert "poll" in report
        assert "score_means" in report
        db.close()