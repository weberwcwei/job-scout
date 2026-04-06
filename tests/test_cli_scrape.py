"""Tests for zero-result warnings in the scrape command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from job_scout.cli import app
from job_scout.config import AppConfig

runner = CliRunner()

MINIMAL_RAW = {
    "profile": {
        "name": "Test",
        "target_title": "Software Engineer",
        "keywords": {
            "critical": ["python"],
            "strong": ["backend"],
            "moderate": [],
            "weak": [],
        },
        "target_companies": {"tier1": [], "tier2": [], "tier3": []},
        "title_signals": [],
        "dealbreakers": {
            "title_patterns": [],
            "company_patterns": [],
            "description_patterns": [],
        },
    },
    "search": {
        "terms": ["python"],
        "locations": ["Remote"],
        "sites": ["linkedin"],
    },
}


class TestScrapeZeroResultWarning:
    def test_warns_on_zero_results(self, tmp_path):
        """When a scraper returns 0 jobs (no error), a yellow warning is printed."""
        cfg = AppConfig(**MINIMAL_RAW)
        db_path = tmp_path / "test.db"

        from job_scout.db import JobDB

        setup_db = JobDB(db_path)

        def _make_db(_cfg=None):
            return JobDB(db_path)

        mock_scraper = MagicMock()
        mock_scraper.scrape.return_value = []  # zero results, no error

        with (
            patch("job_scout.cli._get_config", return_value=cfg),
            patch("job_scout.cli._get_db", side_effect=_make_db),
            patch("job_scout.cli.get_scraper", return_value=mock_scraper),
        ):
            result = runner.invoke(app, ["scrape"])

        setup_db.close()

        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "0 jobs" in result.output

    def test_warns_on_zero_results_dry_run(self, tmp_path):
        """Zero-result warning also fires in --dry-run mode (no DB)."""
        cfg = AppConfig(**MINIMAL_RAW)

        mock_scraper = MagicMock()
        mock_scraper.scrape.return_value = []

        with (
            patch("job_scout.cli._get_config", return_value=cfg),
            patch("job_scout.cli.get_scraper", return_value=mock_scraper),
        ):
            result = runner.invoke(app, ["scrape", "--dry-run"])

        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "0 jobs" in result.output

    def test_no_warning_when_results_found(self, tmp_path):
        """When a scraper returns jobs, no zero-result warning appears."""
        from job_scout.models import Job, Location, Site

        cfg = AppConfig(**MINIMAL_RAW)
        db_path = tmp_path / "test.db"

        from job_scout.db import JobDB

        setup_db = JobDB(db_path)

        def _make_db(_cfg=None):
            return JobDB(db_path)

        job = Job(
            source=Site.LINKEDIN,
            source_id="x1",
            url="https://example.com/x1",
            title="Software Engineer",
            company="TestCo",
            location=Location(city="SF", state="CA"),
            description="python backend",
            score=0,
            score_breakdown={},
        )

        mock_scraper = MagicMock()
        mock_scraper.scrape.return_value = [job]

        with (
            patch("job_scout.cli._get_config", return_value=cfg),
            patch("job_scout.cli._get_db", side_effect=_make_db),
            patch("job_scout.cli.get_scraper", return_value=mock_scraper),
        ):
            result = runner.invoke(app, ["scrape"])

        setup_db.close()

        assert result.exit_code == 0
        assert "Warning" not in result.output
