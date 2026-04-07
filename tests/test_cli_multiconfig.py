"""Tests for --config global option and multi-config plumbing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from typer.testing import CliRunner

from job_scout.cli import app, _get_config, _get_db
from job_scout.config import DEFAULT_DB_PATH, DATA_DIR, AppConfig


runner = CliRunner()

MINIMAL_RAW = {
    "profile": {"name": "Test", "target_title": "Engineer"},
    "search": {"terms": ["swe"], "locations": ["US"]},
}


def _write_config(path: Path, raw=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(raw or MINIMAL_RAW))


class TestConfigFlag:
    def test_config_flag_loads_custom_path(self, tmp_path):
        config_file = tmp_path / "custom.yaml"
        _write_config(config_file)

        with patch("job_scout.cli._config_override", config_file):
            cfg = _get_config()

        assert cfg.profile.name == "Test"
        assert cfg._config_path == config_file

    def test_no_config_flag_uses_default(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        _write_config(config_file)

        with (
            patch("job_scout.cli._config_override", None),
            patch("job_scout.cli.resolve_config_path", return_value=config_file),
        ):
            cfg = _get_config()

        assert cfg.profile.name == "Test"


class TestGetDbMultiConfig:
    def test_get_db_uses_resolved_paths(self, tmp_path):
        """Named config gets its own DB file."""
        config_file = tmp_path / "frontend.yaml"
        _write_config(config_file)

        cfg = AppConfig(**MINIMAL_RAW)
        cfg._config_path = config_file

        with patch("job_scout.cli.JobDB") as mock_db:
            _get_db(cfg)

        expected_db = DATA_DIR / "frontend.db"
        mock_db.assert_called_once_with(expected_db)

    def test_get_db_default_config_uses_default_path(self, tmp_path):
        """config.yaml → default profile → DEFAULT_DB_PATH."""
        config_file = tmp_path / "config.yaml"
        _write_config(config_file)

        cfg = AppConfig(**MINIMAL_RAW)
        cfg._config_path = config_file

        with patch("job_scout.cli.JobDB") as mock_db:
            _get_db(cfg)

        mock_db.assert_called_once_with(DEFAULT_DB_PATH)

    def test_get_db_no_args_uses_default(self):
        """_get_db() with no args returns DEFAULT_DB_PATH."""
        with patch("job_scout.cli.JobDB") as mock_db:
            _get_db()

        mock_db.assert_called_once_with(DEFAULT_DB_PATH)

    def test_get_db_explicit_db_path_wins(self, tmp_path):
        """cfg.db_path overrides auto-derivation."""
        config_file = tmp_path / "frontend.yaml"
        custom_db = tmp_path / "mydb.db"

        cfg = AppConfig(**{**MINIMAL_RAW, "db_path": str(custom_db)})
        cfg._config_path = config_file

        with patch("job_scout.cli.JobDB") as mock_db:
            _get_db(cfg)

        mock_db.assert_called_once_with(custom_db)


class TestInitWithConfigFlag:
    def test_init_creates_config_at_custom_path(self, tmp_path):
        target = tmp_path / "my-search.yaml"

        with patch("job_scout.cli._get_db") as mock_db:
            mock_db.return_value = MagicMock()
            result = runner.invoke(app, ["--config", str(target), "init"])

        assert result.exit_code == 0
        assert target.exists()
        assert "--config" in result.output

    def test_init_default_still_works(self, tmp_path):
        xdg_target = tmp_path / "config.yaml"

        with (
            patch("job_scout.cli.XDG_CONFIG_PATH", xdg_target),
            patch("job_scout.cli._get_db") as mock_db,
        ):
            mock_db.return_value = MagicMock()
            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert xdg_target.exists()


class TestCheckRespectsConfigFlag:
    def test_check_validates_specified_config(self, tmp_path):
        config_file = tmp_path / "myconfig.yaml"
        _write_config(config_file)

        with patch("job_scout.cli._config_override", config_file):
            result = runner.invoke(app, ["check"])

        assert result.exit_code == 0 or result.exit_code == 2  # 2 = warnings
        assert "Config is valid" in result.output
