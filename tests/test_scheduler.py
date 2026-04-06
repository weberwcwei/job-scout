"""Tests for scheduler.py multi-plist support."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from job_scout.config import ScheduleConfig


class TestGeneratePlists:
    def test_returns_three_plists(self):
        from job_scout.scheduler import generate_plists
        schedule = ScheduleConfig()
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))
        assert len(plists) == 3
        labels = list(plists.keys())
        assert "com.user.job-scout.scrape" in labels
        assert "com.user.job-scout.digest" in labels
        assert "com.user.job-scout.report" in labels

    def test_scrape_uses_start_interval(self):
        from job_scout.scheduler import generate_plists
        schedule = ScheduleConfig(interval_hours=4)
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))
        scrape = plists["com.user.job-scout.scrape"]
        assert scrape["StartInterval"] == 4 * 3600
        assert scrape["RunAtLoad"] is True

    def test_digest_uses_calendar_interval(self):
        from job_scout.scheduler import generate_plists
        schedule = ScheduleConfig(digest_hour=10, digest_minute=30)
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))
        digest = plists["com.user.job-scout.digest"]
        assert digest["StartCalendarInterval"] == {"Hour": 10, "Minute": 30}
        assert digest["RunAtLoad"] is False

    def test_report_uses_calendar_interval(self):
        from job_scout.scheduler import generate_plists
        schedule = ScheduleConfig(report_hour=7, report_minute=45)
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))
        report = plists["com.user.job-scout.report"]
        assert report["StartCalendarInterval"] == {"Hour": 7, "Minute": 45}
        assert report["RunAtLoad"] is False

    def test_command_args_correct(self):
        from job_scout.scheduler import generate_plists
        schedule = ScheduleConfig()
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))

        scrape_args = plists["com.user.job-scout.scrape"]["ProgramArguments"]
        assert scrape_args[-2:] == ["job_scout", "scrape"]

        digest_args = plists["com.user.job-scout.digest"]["ProgramArguments"]
        assert digest_args[-2:] == ["job_scout", "digest"]

        report_args = plists["com.user.job-scout.report"]["ProgramArguments"]
        assert report_args[-2:] == ["job_scout", "report"]

    def test_separate_log_files(self):
        from job_scout.scheduler import generate_plists
        schedule = ScheduleConfig()
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))

        stdout_paths = {p["StandardOutPath"] for p in plists.values()}
        # All 3 should have different log paths
        assert len(stdout_paths) == 3


class TestUninstallLegacy:
    @patch("job_scout.scheduler.subprocess.run")
    def test_removes_legacy_plist(self, mock_run):
        from job_scout.scheduler import uninstall, PLIST_DIR, LEGACY_LABEL
        legacy_path = PLIST_DIR / f"{LEGACY_LABEL}.plist"

        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "unlink") as mock_unlink:
            uninstall()

        # Should have attempted to unload and unlink the legacy plist
        unload_calls = [
            c for c in mock_run.call_args_list
            if str(legacy_path) in str(c)
        ]
        assert len(unload_calls) >= 1


class TestPlistLabels:
    def test_label_constants(self):
        from job_scout.scheduler import PLIST_LABELS
        assert PLIST_LABELS["scrape"] == "com.user.job-scout.scrape"
        assert PLIST_LABELS["digest"] == "com.user.job-scout.digest"
        assert PLIST_LABELS["report"] == "com.user.job-scout.report"
