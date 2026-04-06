"""Tests for Compensation.display_concise computed property."""

from __future__ import annotations

from job_scout.models import Compensation


def test_display_concise_yearly_range():
    comp = Compensation(min_amount=181000, max_amount=318000)
    assert comp.display_concise == "$181k-$318k"


def test_display_concise_yearly_same():
    comp = Compensation(min_amount=181000, max_amount=181000)
    assert comp.display_concise == "$181k"


def test_display_concise_no_salary():
    comp = Compensation(min_amount=None)
    assert comp.display_concise == ""


def test_display_concise_zero():
    comp = Compensation(min_amount=0)
    assert comp.display_concise == ""


def test_display_concise_small_amount():
    comp = Compensation(min_amount=50, max_amount=75)
    assert comp.display_concise == "$50-$75"


def test_display_concise_min_only():
    comp = Compensation(min_amount=150000, max_amount=None)
    assert comp.display_concise == "$150k"


def test_display_concise_hourly_range():
    """Hourly rate (< 1000) uses raw dollar format."""
    comp = Compensation(min_amount=50, max_amount=75)
    assert comp.display_concise == "$50-$75"


def test_display_concise_max_only():
    """Max amount with no min returns empty (min_amount is falsy)."""
    comp = Compensation(min_amount=None, max_amount=200000)
    assert comp.display_concise == ""
