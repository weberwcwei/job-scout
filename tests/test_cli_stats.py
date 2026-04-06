"""Tests for zero-result warnings in the stats command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from job_scout.cli import app
from job_scout.config import AppConfig
from job_scout.db import JobDB
from job_scout.models import ScrapeRun, Site

from datetime import datetime

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


class TestStatsZeroResultWarning:
    def test_shows_zero_result_warning(self, tmp_path):
        """stats command displays a warning when there are zero-result runs."""
        cfg = AppConfig(**MINIMAL_RAW)
        db_path = tmp_path / "test.db"
        setup_db = JobDB(db_path)

        # Record a zero-result run
        run = ScrapeRun(
            site=Site.LINKEDIN,
            search_term="python",
            location="Remote",
            started_at=datetime.now(),
        )
        run_id = setup_db.record_run(run)
        setup_db.finish_run(run_id, jobs_found=0, jobs_new=0)

        def _make_db(_cfg=None):
            return JobDB(db_path)

        with (
            patch("job_scout.cli._get_config", return_value=cfg),
            patch("job_scout.cli._get_db", side_effect=_make_db),
        ):
            result = runner.invoke(app, ["stats"])

        setup_db.close()

        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "zero-result run" in result.output.lower()

    def test_no_warning_when_all_runs_have_results(self, tmp_path):
        """stats command shows no zero-result warning when all runs returned jobs."""
        cfg = AppConfig(**MINIMAL_RAW)
        db_path = tmp_path / "test.db"
        setup_db = JobDB(db_path)

        run = ScrapeRun(
            site=Site.LINKEDIN,
            search_term="python",
            location="Remote",
            started_at=datetime.now(),
        )
        run_id = setup_db.record_run(run)
        setup_db.finish_run(run_id, jobs_found=10, jobs_new=5)

        def _make_db(_cfg=None):
            return JobDB(db_path)

        with (
            patch("job_scout.cli._get_config", return_value=cfg),
            patch("job_scout.cli._get_db", side_effect=_make_db),
        ):
            result = runner.invoke(app, ["stats"])

        setup_db.close()

        assert result.exit_code == 0
        assert "zero-result" not in result.output.lower()
