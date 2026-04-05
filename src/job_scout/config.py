"""Configuration model and YAML loader."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator


class KeywordConfig(BaseModel):
    critical: list[str] = []
    strong: list[str] = []
    moderate: list[str] = []
    weak: list[str] = []


class CompanyTiers(BaseModel):
    tier1: list[str] = []
    tier2: list[str] = []
    tier3: list[str] = []


class DealbreakersConfig(BaseModel):
    title_patterns: list[str] = []
    company_patterns: list[str] = []
    description_patterns: list[str] = []


class TitleSignal(BaseModel):
    pattern: str
    points: int


class ProfileConfig(BaseModel):
    name: str
    target_title: str
    keywords: KeywordConfig = KeywordConfig()
    target_companies: CompanyTiers = CompanyTiers()
    dealbreakers: DealbreakersConfig = DealbreakersConfig()
    target_levels: list[str] = ["Senior", "Staff", "Lead", "Principal"]
    title_signals: list[TitleSignal] = []


class SearchConfig(BaseModel):
    terms: list[str]
    locations: list[str]
    sites: list[str] = ["linkedin", "indeed", "google", "glassdoor", "ziprecruiter"]
    results_per_site: int = 25
    hours_old: int = 72
    distance_miles: int = 50


class ScrapingConfig(BaseModel):
    delay_min_seconds: float = 3.0
    delay_max_seconds: float = 8.0
    request_timeout: int = 15
    max_retries: int = 2
    max_pages: int = 3
    fetch_descriptions: bool = True
    proxies: list[str] = []
    use_tls_fingerprinting: bool = False
    max_workers: int = 3

    @model_validator(mode="before")
    @classmethod
    def _migrate_proxy(cls, data):
        if isinstance(data, dict) and "proxy" in data and "proxies" not in data:
            p = data.pop("proxy")
            if p:
                data["proxies"] = [p]
        return data


class ScoringConfig(BaseModel):
    min_alert_score: int = 55
    min_display_score: int = 20
    alert_states: list[str] = []


class MacOSNotifyConfig(BaseModel):
    enabled: bool = True
    sound: str = "Ping"
    group_id: str = "com.user.job-scout"


class EmailConfig(BaseModel):
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    username: str = ""
    app_password: str = ""
    to_address: str = ""


class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class NotificationsConfig(BaseModel):
    macos: MacOSNotifyConfig = MacOSNotifyConfig()
    email: EmailConfig = EmailConfig()
    telegram: TelegramConfig = TelegramConfig()


class ScheduleConfig(BaseModel):
    interval_hours: int = 6
    start_hour: int = 8
    end_hour: int = 23


class AppConfig(BaseModel):
    profile: ProfileConfig
    search: SearchConfig
    scraping: ScrapingConfig = ScrapingConfig()
    scoring: ScoringConfig = ScoringConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    db_path: Path | None = None


CONFIG_DIR = Path.home() / ".local" / "share" / "job-scout"
XDG_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "job-scout"
XDG_CONFIG_PATH = XDG_CONFIG_DIR / "config.yaml"
DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "job-scout" / "job-scout.db"


def resolve_config_path() -> Path:
    """Return config path: XDG location first, CWD fallback for backwards compat."""
    if XDG_CONFIG_PATH.exists():
        return XDG_CONFIG_PATH
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return XDG_CONFIG_PATH


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise SystemExit(
            f"Config not found at {path}. Run `job-scout init` first."
        )
    with open(path) as f:
        raw = yaml.safe_load(f)
    try:
        return AppConfig(**raw)
    except Exception as e:
        from pydantic import ValidationError

        if isinstance(e, ValidationError):
            lines = ["Invalid config.yaml:"]
            for err in e.errors():
                loc = " -> ".join(str(x) for x in err["loc"])
                lines.append(f"  {loc}: {err['msg']}")
            lines.append("\nRun `job-scout check` for details.")
            raise SystemExit("\n".join(lines))
        raise
