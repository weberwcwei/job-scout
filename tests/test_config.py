"""Tests for configuration models and backward compatibility."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from job_scout.config import (
    DATA_DIR,
    DEFAULT_DB_PATH,
    LOG_DIR,
    AppConfig,
    ScrapingConfig,
    ScoringConfig,
    SearchConfig,
    TelegramConfig,
    _sanitize,
    derive_profile_name,
    resolve_config_path,
    resolve_data_paths,
)


class TestScrapingConfigDefaults:
    def test_max_pages_default(self):
        cfg = ScrapingConfig()
        assert cfg.max_pages == 3

    def test_proxies_default(self):
        cfg = ScrapingConfig()
        assert cfg.proxies == []

    def test_max_workers_default(self):
        cfg = ScrapingConfig()
        assert cfg.max_workers == 3

    def test_use_tls_fingerprinting_default(self):
        cfg = ScrapingConfig()
        assert cfg.use_tls_fingerprinting is False


class TestProxyMigration:
    def test_proxy_string_migrates_to_proxies_list(self):
        cfg = ScrapingConfig(**{"proxy": "http://user:pass@host:8080"})
        assert cfg.proxies == ["http://user:pass@host:8080"]

    def test_proxy_null_migrates_to_empty_list(self):
        cfg = ScrapingConfig(**{"proxy": None})
        assert cfg.proxies == []

    def test_proxies_list_takes_precedence(self):
        cfg = ScrapingConfig(**{"proxies": ["http://a", "http://b"]})
        assert cfg.proxies == ["http://a", "http://b"]

    def test_proxy_ignored_when_proxies_present(self):
        cfg = ScrapingConfig(**{"proxy": "http://old", "proxies": ["http://new"]})
        assert cfg.proxies == ["http://new"]


class TestScoringConfigAlertStates:
    def test_full_name_normalized_to_abbreviation(self):
        cfg = ScoringConfig(alert_states=["California", "New York"])
        assert cfg.alert_states == ["CA", "NY"]

    def test_abbreviation_passthrough(self):
        cfg = ScoringConfig(alert_states=["CA", "TX"])
        assert cfg.alert_states == ["CA", "TX"]

    def test_lowercase_code_uppercased(self):
        cfg = ScoringConfig(alert_states=["ca"])
        assert cfg.alert_states == ["CA"]

    def test_mixed_formats(self):
        cfg = ScoringConfig(alert_states=["California", "TX", "new york"])
        assert cfg.alert_states == ["CA", "TX", "NY"]

    def test_empty_list(self):
        cfg = ScoringConfig(alert_states=[])
        assert cfg.alert_states == []

    def test_unknown_passthrough(self):
        cfg = ScoringConfig(alert_states=["Ontario"])
        assert cfg.alert_states == ["Ontario"]

    def test_whitespace_stripped(self):
        cfg = ScoringConfig(alert_states=["  CA  ", " California "])
        assert cfg.alert_states == ["CA", "CA"]


class TestSearchConfigDefaults:
    def test_sites_includes_new_scrapers(self):
        cfg = SearchConfig(terms=["eng"], locations=["US"])
        assert "glassdoor" in cfg.sites
        assert "ziprecruiter" in cfg.sites
        assert "bayt" not in cfg.sites  # opt-in only


class TestBackwardCompat:
    def test_minimal_config_loads(self):
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
        }
        cfg = AppConfig(**raw)
        assert cfg.scraping.max_pages == 3
        assert cfg.scraping.proxies == []
        assert cfg.scraping.max_workers == 3
        assert cfg.scraping.use_tls_fingerprinting is False

    def test_old_proxy_config_loads(self):
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
            "scraping": {"proxy": "http://myproxy:8080"},
        }
        cfg = AppConfig(**raw)
        assert cfg.scraping.proxies == ["http://myproxy:8080"]


class TestTelegramConfig:
    def test_defaults(self):
        cfg = TelegramConfig()
        assert cfg.enabled is False
        assert cfg.bot_token == ""
        assert cfg.chat_id == ""

    def test_from_dict(self):
        cfg = TelegramConfig(enabled=True, bot_token="123:ABC", chat_id="999")
        assert cfg.enabled is True
        assert cfg.bot_token == "123:ABC"
        assert cfg.chat_id == "999"

    def test_telegram_in_notifications(self):
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
            "notifications": {
                "telegram": {
                    "enabled": True,
                    "bot_token": "123:ABC",
                    "chat_id": "42",
                }
            },
        }
        cfg = AppConfig(**raw)
        assert cfg.notifications.telegram.enabled is True
        assert cfg.notifications.telegram.bot_token == "123:ABC"
        assert cfg.notifications.telegram.chat_id == "42"


class TestResolveConfigPath:
    def test_returns_xdg_path_when_it_exists(self, tmp_path):
        xdg = tmp_path / "config.yaml"
        xdg.touch()
        with patch("job_scout.config.XDG_CONFIG_PATH", xdg):
            assert resolve_config_path() == xdg

    def test_falls_back_to_cwd_when_xdg_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").touch()
        missing = tmp_path / "nonexistent" / "config.yaml"
        with patch("job_scout.config.XDG_CONFIG_PATH", missing):
            assert resolve_config_path() == Path("config.yaml")

    def test_returns_xdg_path_when_neither_exists(self, tmp_path):
        missing = tmp_path / "nonexistent" / "config.yaml"
        cwd_missing = tmp_path / "also_nonexistent" / "config.yaml"
        with (
            patch("job_scout.config.XDG_CONFIG_PATH", missing),
            patch("job_scout.config.DEFAULT_CONFIG_PATH", cwd_missing),
        ):
            assert resolve_config_path() == missing

    def test_xdg_takes_precedence_over_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").touch()
        xdg = tmp_path / "xdg" / "config.yaml"
        xdg.parent.mkdir()
        xdg.touch()
        with patch("job_scout.config.XDG_CONFIG_PATH", xdg):
            assert resolve_config_path() == xdg

    def test_xdg_config_home_env_respected(self, tmp_path, monkeypatch):
        """XDG_CONFIG_DIR should use XDG_CONFIG_HOME when set."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # Re-import to pick up env change
        import importlib
        import job_scout.config as cfg_mod

        importlib.reload(cfg_mod)
        assert cfg_mod.XDG_CONFIG_DIR == tmp_path / "job-scout"
        # Clean up: reload again without the env var to restore
        monkeypatch.delenv("XDG_CONFIG_HOME")
        importlib.reload(cfg_mod)


class TestScheduleConfigFields:
    def test_defaults(self):
        """New schedule fields have correct defaults."""
        from job_scout.config import ScheduleConfig

        s = ScheduleConfig()
        assert s.interval_hours == 6
        assert s.digest_hour == 9
        assert s.digest_minute == 0
        assert s.report_hour == 8
        assert s.report_minute == 50

    def test_custom_values(self):
        """Custom schedule values parse correctly."""
        from job_scout.config import ScheduleConfig

        s = ScheduleConfig(
            interval_hours=4,
            digest_hour=10,
            digest_minute=30,
            report_hour=7,
            report_minute=0,
        )
        assert s.digest_hour == 10
        assert s.digest_minute == 30
        assert s.report_hour == 7

    def test_start_hour_end_hour_removed(self):
        """start_hour and end_hour no longer exist as fields."""
        from job_scout.config import ScheduleConfig

        s = ScheduleConfig()
        assert not hasattr(s, "start_hour")
        assert not hasattr(s, "end_hour")

    def test_extra_fields_ignored(self):
        """Existing configs with start_hour/end_hour don't break (Pydantic ignores extras)."""
        from job_scout.config import ScheduleConfig

        # This simulates an old config.yaml that still has start_hour/end_hour
        s = ScheduleConfig(interval_hours=6, start_hour=8, end_hour=23)
        assert s.interval_hours == 6


class TestAppConfigReportDir:
    def test_default_report_dir(self):
        """AppConfig has report_dir with correct default."""
        from pathlib import Path
        from job_scout.config import AppConfig

        # Need minimal valid config
        cfg = AppConfig(
            profile={
                "name": "Test",
                "target_title": "SWE",
                "keywords": {},
                "target_companies": {},
            },
            search={"terms": ["python"], "locations": ["Remote"]},
        )
        expected = Path.home() / ".local" / "share" / "job-scout" / "reports"
        assert cfg.report_dir == expected

    def test_custom_report_dir(self):
        """report_dir can be overridden."""
        from pathlib import Path
        from job_scout.config import AppConfig

        cfg = AppConfig(
            profile={
                "name": "Test",
                "target_title": "SWE",
                "keywords": {},
                "target_companies": {},
            },
            search={"terms": ["python"], "locations": ["Remote"]},
            report_dir="/tmp/my-reports",
        )
        assert cfg.report_dir == Path("/tmp/my-reports")


class TestLoadConfig:
    def test_missing_file_raises_system_exit(self, tmp_path):
        from job_scout.config import load_config

        missing = tmp_path / "nonexistent.yaml"
        with pytest.raises(SystemExit, match="Config not found"):
            load_config(missing)

    def test_valid_file_loads(self, tmp_path):
        from job_scout.config import load_config
        import yaml

        config_file = tmp_path / "config.yaml"
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
        }
        config_file.write_text(yaml.dump(raw))
        cfg = load_config(config_file)
        assert cfg.profile.name == "Test"

    def test_invalid_config_raises_system_exit(self, tmp_path):
        from job_scout.config import load_config
        import yaml

        config_file = tmp_path / "config.yaml"
        # Missing required 'search' field
        raw = {"profile": {"name": "Test", "target_title": "Engineer"}}
        config_file.write_text(yaml.dump(raw))
        with pytest.raises(SystemExit, match="Invalid config"):
            load_config(config_file)


class TestMaxAchievableScore:
    def test_full_config(self):
        from job_scout.config import _max_achievable_score

        cfg = AppConfig(
            profile={
                "name": "Test",
                "target_title": "SWE",
                "keywords": {"critical": ["python"]},
                "target_companies": {"tier1": ["Google"]},
                "title_signals": [{"pattern": "engineer", "points": 15}],
            },
            search={"terms": ["python"], "locations": ["US"]},
        )
        # 10 (recency) + 55 (keywords) + 15 (company) + 20 (title) = 100
        assert _max_achievable_score(cfg) == 100

    def test_no_keywords(self):
        from job_scout.config import _max_achievable_score

        cfg = AppConfig(
            profile={"name": "Test", "target_title": "SWE"},
            search={"terms": ["python"], "locations": ["US"]},
        )
        # 10 (recency) only
        assert _max_achievable_score(cfg) == 10

    def test_strong_only_no_keywords(self):
        from job_scout.config import _max_achievable_score

        cfg = AppConfig(
            profile={
                "name": "Test",
                "target_title": "SWE",
                "keywords": {"strong": ["python"]},
            },
            search={"terms": ["python"], "locations": ["US"]},
        )
        # strong-only without critical: keyword contribution = 0
        assert _max_achievable_score(cfg) == 10

    def test_moderate_weak_no_critical(self):
        from job_scout.config import _max_achievable_score

        cfg = AppConfig(
            profile={
                "name": "Test",
                "target_title": "SWE",
                "keywords": {"moderate": ["aws"], "weak": ["docker"]},
            },
            search={"terms": ["python"], "locations": ["US"]},
        )
        # 10 (recency) + 10 (capped keywords) = 20
        assert _max_achievable_score(cfg) == 20


class TestDbPath:
    def test_db_path_none_by_default(self):
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
        }
        cfg = AppConfig(**raw)
        assert cfg.db_path is None

    def test_db_path_custom(self):
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
            "db_path": "/tmp/custom.db",
        }
        cfg = AppConfig(**raw)
        assert cfg.db_path == Path("/tmp/custom.db")


class TestDeriveProfileName:
    def test_explicit_name(self):
        assert derive_profile_name(Path("whatever.yaml"), "my-search") == "my-search"

    def test_from_stem(self):
        assert derive_profile_name(Path("frontend.yaml")) == "frontend"

    def test_config_default(self):
        assert derive_profile_name(Path("config.yaml")) == "default"

    def test_nested_config_default(self):
        assert derive_profile_name(Path("/some/dir/config.yaml")) == "default"

    def test_sanitize_special_chars(self):
        assert _sanitize("My Search!!") == "my-search"

    def test_sanitize_spaces(self):
        assert _sanitize("hello world") == "hello-world"

    def test_sanitize_preserves_valid(self):
        assert _sanitize("my-search_2") == "my-search_2"

    def test_sanitize_strips_leading_trailing_dashes(self):
        assert _sanitize("--test--") == "test"

    def test_sanitize_all_special_chars_returns_default(self):
        assert _sanitize("!!!") == "default"

    def test_sanitize_empty_string_returns_default(self):
        assert _sanitize("") == "default"

    def test_explicit_name_sanitized(self):
        assert (
            derive_profile_name(Path("x.yaml"), "My Cool Search!") == "my-cool-search"
        )


class TestResolveDataPaths:
    def _make_cfg(self, **kwargs):
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
        }
        raw.update(kwargs)
        return AppConfig(**raw)

    def test_default_profile_uses_existing_paths(self):
        cfg = self._make_cfg()
        paths = resolve_data_paths(Path("config.yaml"), cfg)
        assert paths.profile_name == "default"
        assert paths.db == DEFAULT_DB_PATH
        assert paths.logs == LOG_DIR
        assert paths.reports == cfg.report_dir

    def test_named_profile_uses_namespaced_paths(self):
        cfg = self._make_cfg()
        paths = resolve_data_paths(Path("frontend.yaml"), cfg)
        assert paths.profile_name == "frontend"
        assert paths.db == DATA_DIR / "frontend.db"
        assert paths.logs == LOG_DIR / "frontend"
        assert paths.reports == cfg.report_dir / "frontend"

    def test_explicit_db_wins(self):
        cfg = self._make_cfg(db_path="/tmp/custom.db")
        paths = resolve_data_paths(Path("frontend.yaml"), cfg)
        assert paths.db == Path("/tmp/custom.db")

    def test_config_name_overrides_stem(self):
        cfg = self._make_cfg(config_name="backend-jobs")
        paths = resolve_data_paths(Path("frontend.yaml"), cfg)
        assert paths.profile_name == "backend-jobs"
        assert paths.db == DATA_DIR / "backend-jobs.db"


class TestAppConfigNewFields:
    def test_config_name_optional(self):
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
        }
        cfg = AppConfig(**raw)
        assert cfg.config_name is None

    def test_config_name_set(self):
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
            "config_name": "my-profile",
        }
        cfg = AppConfig(**raw)
        assert cfg.config_name == "my-profile"

    def test_config_path_private_attr(self):
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
        }
        cfg = AppConfig(**raw)
        assert cfg._config_path is None
        cfg._config_path = Path("/test/config.yaml")
        assert cfg._config_path == Path("/test/config.yaml")


class TestLoadConfigStashesPath:
    def test_load_config_sets_config_path(self, tmp_path):
        import yaml as _yaml
        from job_scout.config import load_config

        config_file = tmp_path / "myconfig.yaml"
        raw = {
            "profile": {"name": "Test", "target_title": "Engineer"},
            "search": {"terms": ["swe"], "locations": ["US"]},
        }
        config_file.write_text(_yaml.dump(raw))
        cfg = load_config(config_file)
        assert cfg._config_path == config_file
