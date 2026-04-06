"""Configuration model and YAML loader."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator


class ConfigDiagnostic(BaseModel):
    level: Literal["error", "warning"]
    field: str
    message: str


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
    digest_hour: int = 9
    digest_minute: int = 0
    report_hour: int = 8
    report_minute: int = 50


class AppConfig(BaseModel):
    profile: ProfileConfig
    search: SearchConfig
    scraping: ScrapingConfig = ScrapingConfig()
    scoring: ScoringConfig = ScoringConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    db_path: Path | None = None
    report_dir: Path = Path.home() / ".local" / "share" / "job-scout" / "reports"


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


def load_config(path: Path | str | None = None) -> AppConfig:
    path = Path(path) if path else resolve_config_path()
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


def validate_quality(cfg: AppConfig) -> list[ConfigDiagnostic]:
    """Semantic analysis of a valid AppConfig. Returns structured diagnostics."""
    diags: list[ConfigDiagnostic] = []

    # --- Errors: invalid regex in dealbreaker patterns ---
    for field_name in ("title_patterns", "company_patterns", "description_patterns"):
        patterns = getattr(cfg.profile.dealbreakers, field_name)
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as e:
                diags.append(ConfigDiagnostic(
                    level="error",
                    field=f"profile.dealbreakers.{field_name}",
                    message=f"Invalid regex in {field_name}: {pattern} — {e}",
                ))

    # --- Warnings: placeholder values ---
    if not cfg.profile.name or cfg.profile.name == "Your Name":
        diags.append(ConfigDiagnostic(
            level="warning",
            field="profile.name",
            message="profile.name is still a placeholder",
        ))

    if not cfg.profile.target_title or cfg.profile.target_title == "your target job title":
        diags.append(ConfigDiagnostic(
            level="warning",
            field="profile.target_title",
            message="profile.target_title is still a placeholder",
        ))

    if not cfg.search.terms or all(not t.strip() for t in cfg.search.terms):
        diags.append(ConfigDiagnostic(
            level="warning",
            field="search.terms",
            message="search.terms is empty",
        ))

    if not cfg.search.locations or all(not t.strip() for t in cfg.search.locations):
        diags.append(ConfigDiagnostic(
            level="warning",
            field="search.locations",
            message="search.locations is empty",
        ))

    # --- Warnings: keyword configuration ---
    kw = cfg.profile.keywords
    all_empty = not kw.critical and not kw.strong and not kw.moderate and not kw.weak
    if all_empty:
        diags.append(ConfigDiagnostic(
            level="warning",
            field="profile.keywords",
            message="No keywords defined — all jobs will score low",
        ))
    elif not kw.critical:
        if kw.strong and not kw.moderate and not kw.weak:
            diags.append(ConfigDiagnostic(
                level="warning",
                field="profile.keywords",
                message="Only strong keywords defined with no critical — strong keywords are ignored without critical matches (keyword score will be 0)",
            ))
        elif kw.moderate or kw.weak:
            diags.append(ConfigDiagnostic(
                level="warning",
                field="profile.keywords.critical",
                message="No critical keywords — keyword score capped at 10 regardless of other matches",
            ))

    # --- Warnings: scoring thresholds ---
    max_score = _max_achievable_score(cfg)
    if cfg.scoring.min_alert_score > max_score:
        diags.append(ConfigDiagnostic(
            level="warning",
            field="scoring.min_alert_score",
            message=f"min_alert_score ({cfg.scoring.min_alert_score}) is unreachable — max possible score is {max_score}",
        ))

    if cfg.scoring.min_alert_score < cfg.scoring.min_display_score:
        diags.append(ConfigDiagnostic(
            level="warning",
            field="scoring.min_alert_score",
            message=f"min_alert_score ({cfg.scoring.min_alert_score}) is below min_display_score ({cfg.scoring.min_display_score}) — alerts would fire for hidden jobs",
        ))

    return diags


def _max_achievable_score(cfg: AppConfig) -> int:
    """Compute the max possible score given the config's populated fields."""
    max_score = 10  # recency is always available

    kw = cfg.profile.keywords
    if kw.critical:
        max_score += 55
    elif kw.moderate or kw.weak:
        max_score += 10  # critical gate caps at 10
    # strong-only with no critical: +0

    tiers = cfg.profile.target_companies
    if tiers.tier1 or tiers.tier2 or tiers.tier3:
        max_score += 15

    if cfg.profile.title_signals:
        max_score += 20

    return max_score
