"""Tests for configuration models and backward compatibility."""

from __future__ import annotations

from pathlib import Path

from job_scout.config import AppConfig, ScrapingConfig, SearchConfig, TelegramConfig


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
