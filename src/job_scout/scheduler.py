"""launchd plist management for macOS scheduling."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

from job_scout.config import ScheduleConfig

LABEL_PREFIX = "com.user.job-scout"
LEGACY_LABEL = "com.user.job-scout"  # old single-plist label
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
LOG_DIR = Path.home() / ".local" / "share" / "job-scout" / "logs"

PLIST_LABELS = {
    "scrape": f"{LABEL_PREFIX}.scrape",
    "digest": f"{LABEL_PREFIX}.digest",
    "report": f"{LABEL_PREFIX}.report",
}


def _get_python(project_dir: Path) -> str:
    venv_python = project_dir / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _generate_plist(
    label: str,
    command_args: list[str],
    schedule_key: str,
    schedule_value,
    log_prefix: str,
) -> dict:
    """Generate a launchd plist dict."""
    return {
        "Label": label,
        "ProgramArguments": command_args,
        schedule_key: schedule_value,
        "StandardOutPath": str(LOG_DIR / f"{log_prefix}-stdout.log"),
        "StandardErrorPath": str(LOG_DIR / f"{log_prefix}-stderr.log"),
        "RunAtLoad": schedule_key == "StartInterval",  # Only scrape runs at load
        "KeepAlive": False,
    }


def generate_plists(
    schedule: ScheduleConfig,
    project_dir: Path | None = None,
) -> dict[str, dict]:
    """Generate all 3 plist dicts."""
    if project_dir is None:
        project_dir = Path.cwd()

    python_path = _get_python(project_dir)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    return {
        PLIST_LABELS["scrape"]: _generate_plist(
            label=PLIST_LABELS["scrape"],
            command_args=[python_path, "-m", "job_scout", "scrape"],
            schedule_key="StartInterval",
            schedule_value=schedule.interval_hours * 3600,
            log_prefix="scrape",
        ),
        PLIST_LABELS["digest"]: _generate_plist(
            label=PLIST_LABELS["digest"],
            command_args=[python_path, "-m", "job_scout", "digest"],
            schedule_key="StartCalendarInterval",
            schedule_value={"Hour": schedule.digest_hour, "Minute": schedule.digest_minute},
            log_prefix="digest",
        ),
        PLIST_LABELS["report"]: _generate_plist(
            label=PLIST_LABELS["report"],
            command_args=[python_path, "-m", "job_scout", "report"],
            schedule_key="StartCalendarInterval",
            schedule_value={"Hour": schedule.report_hour, "Minute": schedule.report_minute},
            log_prefix="report",
        ),
    }


def install(schedule: ScheduleConfig, project_dir: Path | None = None) -> list[Path]:
    """Install all 3 plists. Returns list of plist paths."""
    PLIST_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up legacy single plist if present
    legacy_path = PLIST_DIR / f"{LEGACY_LABEL}.plist"
    if legacy_path.exists():
        subprocess.run(["launchctl", "unload", str(legacy_path)], capture_output=True)
        legacy_path.unlink()

    plists = generate_plists(schedule, project_dir)
    paths = []

    for label, plist_data in plists.items():
        path = PLIST_DIR / f"{label}.plist"
        # Unload if previously loaded
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        with open(path, "wb") as f:
            plistlib.dump(plist_data, f)
        subprocess.run(["launchctl", "load", str(path)], check=True)
        paths.append(path)

    return paths


def uninstall() -> None:
    """Remove all plists including legacy single plist."""
    # Remove new plists
    for label in PLIST_LABELS.values():
        path = PLIST_DIR / f"{label}.plist"
        if path.exists():
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
            path.unlink()

    # Remove legacy plist
    legacy_path = PLIST_DIR / f"{LEGACY_LABEL}.plist"
    if legacy_path.exists():
        subprocess.run(["launchctl", "unload", str(legacy_path)], capture_output=True)
        legacy_path.unlink()


def status() -> dict[str, dict]:
    """Return per-plist status."""
    result = {}
    for name, label in PLIST_LABELS.items():
        path = PLIST_DIR / f"{label}.plist"
        installed = path.exists()
        running = False
        if installed:
            check = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True,
                text=True,
            )
            running = check.returncode == 0
        result[name] = {
            "label": label,
            "installed": installed,
            "running": running,
            "plist_path": str(path),
        }
    result["log_dir"] = str(LOG_DIR)
    return result
