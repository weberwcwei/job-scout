"""Configuration model and YAML loader."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


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
    sites: list[str] = ["linkedin", "indeed", "google"]
    results_per_site: int = 25
    hours_old: int = 72
    distance_miles: int = 50


class ScrapingConfig(BaseModel):
    delay_min_seconds: float = 3.0
    delay_max_seconds: float = 8.0
    request_timeout: int = 15
    max_retries: int = 2
    fetch_descriptions: bool = True
    proxy: str | None = None


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


class NotificationsConfig(BaseModel):
    macos: MacOSNotifyConfig = MacOSNotifyConfig()
    email: EmailConfig = EmailConfig()


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


CONFIG_DIR = Path.home() / ".local" / "share" / "job-scout"
DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "job-scout" / "job-scout.db"


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
