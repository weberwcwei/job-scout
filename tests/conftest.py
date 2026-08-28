"""Shared fixtures: isolate tests from the developer's real config and data."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_xdg_config(tmp_path, monkeypatch):
    """Point XDG_CONFIG_HOME at an empty temp dir for every test.

    Without this, tests that invoke CLI commands read the developer's real
    ~/.config/job-scout/config.yaml (or fail when it is absent on CI), so
    results depend on the machine running them.
    """
    xdg = tmp_path / "xdg-config"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
