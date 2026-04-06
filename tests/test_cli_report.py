"""Tests for the report CLI command."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from job_scout.config import AppConfig, ScoringConfig
from job_scout.db import JobDB
from job_scout.models import Compensation, CompInterval, Job, Location, Site


def _make_job(source_id, *, score=60, state="CA", company="TestCo", title="ML Engineer", comp=True):
    return Job(
        source=Site.LINKEDIN,
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=title,
        company=company,
        location=Location(city="San Jose", state=state),
        description="desc",
        score=score,
        score_breakdown={"keyword": score},
        status="new",
        date_scraped=datetime.now(),
        compensation=Compensation(min_amount=180000, max_amount=250000, interval=CompInterval.YEARLY) if comp else None,
    )


class TestReportCommand:
    def test_creates_report_file(self, tmp_path):
        """report() creates a markdown file at the expected path."""
        from typer.testing import CliRunner
        from job_scout.cli import app

        db_path = tmp_path / "test.db"
        report_dir = tmp_path / "reports"
        runner = CliRunner()

        # Set up DB with a high-score job
        db = JobDB(db_path)
        db.upsert_job(_make_job("r1", score=70))
        db.close()

        # Mock config
        mock_cfg = MagicMock(spec=AppConfig)
        mock_cfg.db_path = db_path
        mock_cfg.scoring = ScoringConfig(min_alert_score=55, alert_states=[])
        mock_cfg.report_dir = report_dir

        with patch("job_scout.cli._get_config", return_value=mock_cfg):
            result = runner.invoke(app, ["report"])

        assert result.exit_code == 0
        assert "Report saved" in result.output
        # Check file exists
        reports = list(report_dir.glob("*.md"))
        assert len(reports) == 1

    def test_report_contains_high_match_section(self, tmp_path):
        """Report markdown includes High Match table."""
        from typer.testing import CliRunner
        from job_scout.cli import app

        db_path = tmp_path / "test.db"
        report_dir = tmp_path / "reports"
        runner = CliRunner()

        db = JobDB(db_path)
        db.upsert_job(_make_job("r2", score=70, company="NVIDIA", title="ML Eng"))
        db.close()

        mock_cfg = MagicMock(spec=AppConfig)
        mock_cfg.db_path = db_path
        mock_cfg.scoring = ScoringConfig(min_alert_score=55, alert_states=[])
        mock_cfg.report_dir = report_dir

        with patch("job_scout.cli._get_config", return_value=mock_cfg):
            runner.invoke(app, ["report"])

        report_file = list(report_dir.glob("*.md"))[0]
        content = report_file.read_text()
        assert "## High Match" in content
        assert "NVIDIA" in content
        assert "ML Eng" in content
        assert "[apply]" in content

    def test_report_contains_trend_table(self, tmp_path):
        """Report includes 7-Day Trend section."""
        from typer.testing import CliRunner
        from job_scout.cli import app

        db_path = tmp_path / "test.db"
        report_dir = tmp_path / "reports"
        runner = CliRunner()

        db = JobDB(db_path)
        db.upsert_job(_make_job("r3", score=60))
        db.close()

        mock_cfg = MagicMock(spec=AppConfig)
        mock_cfg.db_path = db_path
        mock_cfg.scoring = ScoringConfig(min_alert_score=55, alert_states=[])
        mock_cfg.report_dir = report_dir

        with patch("job_scout.cli._get_config", return_value=mock_cfg):
            runner.invoke(app, ["report"])

        report_file = list(report_dir.glob("*.md"))[0]
        content = report_file.read_text()
        assert "## 7-Day Trend" in content

    def test_report_empty_results(self, tmp_path):
        """report() with no matching jobs still runs without error."""
        from typer.testing import CliRunner
        from job_scout.cli import app

        db_path = tmp_path / "test.db"
        report_dir = tmp_path / "reports"
        runner = CliRunner()

        db = JobDB(db_path)
        db.close()  # Empty DB

        mock_cfg = MagicMock(spec=AppConfig)
        mock_cfg.db_path = db_path
        mock_cfg.scoring = ScoringConfig(min_alert_score=55, alert_states=[])
        mock_cfg.report_dir = report_dir

        with patch("job_scout.cli._get_config", return_value=mock_cfg):
            result = runner.invoke(app, ["report"])

        assert result.exit_code == 0

    def test_report_medium_only_no_high(self, tmp_path):
        """Report with medium-score jobs but no high-score jobs omits High Match section."""
        from typer.testing import CliRunner
        from job_scout.cli import app

        db_path = tmp_path / "test.db"
        report_dir = tmp_path / "reports"
        runner = CliRunner()

        db = JobDB(db_path)
        db.upsert_job(_make_job("m1", score=45, company="MediumCo"))
        db.upsert_job(_make_job("m2", score=42, company="MediumCo2"))
        db.close()

        mock_cfg = MagicMock(spec=AppConfig)
        mock_cfg.db_path = db_path
        mock_cfg.scoring = ScoringConfig(min_alert_score=55, alert_states=[])
        mock_cfg.report_dir = report_dir

        with patch("job_scout.cli._get_config", return_value=mock_cfg):
            runner.invoke(app, ["report"])

        report_file = list(report_dir.glob("*.md"))[0]
        content = report_file.read_text()
        assert "## High Match" not in content
        assert "## Worth Review" in content
        assert "MediumCo" in content

    def test_report_dir_created_if_missing(self, tmp_path):
        """report_dir is created even when it does not exist."""
        from typer.testing import CliRunner
        from job_scout.cli import app

        db_path = tmp_path / "test.db"
        report_dir = tmp_path / "deeply" / "nested" / "reports"
        runner = CliRunner()

        db = JobDB(db_path)
        db.upsert_job(_make_job("rdir1", score=70))
        db.close()

        mock_cfg = MagicMock(spec=AppConfig)
        mock_cfg.db_path = db_path
        mock_cfg.scoring = ScoringConfig(min_alert_score=55, alert_states=[])
        mock_cfg.report_dir = report_dir

        with patch("job_scout.cli._get_config", return_value=mock_cfg):
            result = runner.invoke(app, ["report"])

        assert result.exit_code == 0
        assert report_dir.exists()
        assert len(list(report_dir.glob("*.md"))) == 1
