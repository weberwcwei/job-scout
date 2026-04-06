"""Tests for _filter_alert_jobs helper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_job(*, state=None, is_remote=False):
    """Create a mock job with location attributes."""
    job = MagicMock()
    job.location.state = state
    job.location.is_remote = is_remote
    return job


def _make_cfg(*, alert_states=None):
    """Create a mock config with scoring.alert_states."""
    cfg = MagicMock()
    cfg.scoring.alert_states = alert_states or []
    return cfg


class TestFilterAlertJobs:
    def test_remote_always_passes(self):
        from job_scout.cli import _filter_alert_jobs
        jobs = [_make_job(is_remote=True, state="TX")]
        cfg = _make_cfg(alert_states=["CA"])
        result = _filter_alert_jobs(jobs, cfg)
        assert len(result) == 1

    def test_state_in_allowed_passes(self):
        from job_scout.cli import _filter_alert_jobs
        jobs = [_make_job(state="CA"), _make_job(state="NY")]
        cfg = _make_cfg(alert_states=["CA", "WA"])
        result = _filter_alert_jobs(jobs, cfg)
        assert len(result) == 1
        assert result[0].location.state == "CA"

    def test_state_not_in_allowed_filtered(self):
        from job_scout.cli import _filter_alert_jobs
        jobs = [_make_job(state="TX")]
        cfg = _make_cfg(alert_states=["CA"])
        result = _filter_alert_jobs(jobs, cfg)
        assert len(result) == 0

    def test_empty_alert_states_passes_all(self):
        from job_scout.cli import _filter_alert_jobs
        jobs = [_make_job(state="TX"), _make_job(state="FL")]
        cfg = _make_cfg(alert_states=[])
        result = _filter_alert_jobs(jobs, cfg)
        assert len(result) == 2

    def test_none_state_passes(self):
        """Jobs with unknown state (None) should pass through."""
        from job_scout.cli import _filter_alert_jobs
        jobs = [_make_job(state=None)]
        cfg = _make_cfg(alert_states=["CA"])
        result = _filter_alert_jobs(jobs, cfg)
        assert len(result) == 1

    def test_mixed_jobs(self):
        from job_scout.cli import _filter_alert_jobs
        jobs = [
            _make_job(is_remote=True, state="TX"),  # remote -> pass
            _make_job(state="CA"),  # allowed -> pass
            _make_job(state="TX"),  # not allowed -> filtered
            _make_job(state=None),  # unknown -> pass
        ]
        cfg = _make_cfg(alert_states=["CA", "WA"])
        result = _filter_alert_jobs(jobs, cfg)
        assert len(result) == 3

    def test_empty_jobs_list(self):
        from job_scout.cli import _filter_alert_jobs
        result = _filter_alert_jobs([], _make_cfg())
        assert result == []
