"""Tests for the stats command."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from typer.testing import CliRunner

from job_scout.cli import app
from job_scout.config import AppConfig
from job_scout.db import JobDB
from job_scout.models import Job, Location, ScrapeRun, Site

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


def _make_job(source_id, *, score=50, search_term=None):
    return Job(
        source=Site.LINKEDIN,
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title="Engineer",
        company="Co",
        location=Location(city="SF", state="CA"),
        description="desc",
        score=score,
        score_breakdown={"keyword": score},
        search_term=search_term,
    )


class TestStatsSearchTermBreakdown:
    def test_shows_search_term_table(self, tmp_path):
        """stats command displays a By Search Term table when data exists."""
        cfg = AppConfig(**MINIMAL_RAW)
        db_path = tmp_path / "test.db"
        setup_db = JobDB(db_path)

        setup_db.upsert_job(_make_job("a1", score=80, search_term="python"))
        setup_db.upsert_job(_make_job("a2", score=60, search_term="python"))
        setup_db.upsert_job(_make_job("a3", score=40, search_term="java"))

        def _make_db(_cfg=None):
            return JobDB(db_path)

        with (
            patch("job_scout.cli._get_config", return_value=cfg),
            patch("job_scout.cli._get_db", side_effect=_make_db),
        ):
            result = runner.invoke(app, ["stats"])

        setup_db.close()

        assert result.exit_code == 0
        assert "By Search Term" in result.output
        assert "python" in result.output
        assert "java" in result.output
        assert "70.0" in result.output  # avg of 80 + 60
        assert "40.0" in result.output  # single java job

    def test_no_search_term_table_when_empty(self, tmp_path):
        """stats command omits the table when no jobs have search_term."""
        cfg = AppConfig(**MINIMAL_RAW)
        db_path = tmp_path / "test.db"
        setup_db = JobDB(db_path)

        # Legacy job without search_term
        setup_db.upsert_job(_make_job("b1", score=50))

        def _make_db(_cfg=None):
            return JobDB(db_path)

        with (
            patch("job_scout.cli._get_config", return_value=cfg),
            patch("job_scout.cli._get_db", side_effect=_make_db),
        ):
            result = runner.invoke(app, ["stats"])

        setup_db.close()

        assert result.exit_code == 0
        assert "By Search Term" not in result.output
