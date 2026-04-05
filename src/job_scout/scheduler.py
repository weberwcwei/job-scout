"""launchd plist management for macOS scheduling."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

from job_scout.config import ScheduleConfig

LABEL = "com.user.job-scout"
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = PLIST_DIR / f"{LABEL}.plist"
LOG_DIR = Path.home() / ".local" / "share" / "job-scout" / "logs"


def generate_plist(
    schedule: ScheduleConfig,
    project_dir: Path | None = None,
) -> dict:
    if project_dir is None:
        project_dir = Path.cwd()

    venv_python = project_dir / ".venv" / "bin" / "python"
    python_path = str(venv_python) if venv_python.exists() else sys.executable

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    return {
        "Label": LABEL,
        "ProgramArguments": [python_path, "-m", "job_scout", "scrape"],
        "StartInterval": schedule.interval_hours * 3600,
        "StandardOutPath": str(LOG_DIR / "stdout.log"),
        "StandardErrorPath": str(LOG_DIR / "stderr.log"),
        "RunAtLoad": True,
        "KeepAlive": False,
    }


def install(schedule: ScheduleConfig, project_dir: Path | None = None) -> Path:
    PLIST_DIR.mkdir(parents=True, exist_ok=True)
    plist_data = generate_plist(schedule, project_dir)
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist_data, f)

    # Load the agent
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    subprocess.run(["launchctl", "load", str(PLIST_PATH)], check=True)
    return PLIST_PATH


def uninstall() -> None:
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        PLIST_PATH.unlink()


def status() -> dict:
    installed = PLIST_PATH.exists()
    running = False
    if installed:
        result = subprocess.run(
            ["launchctl", "list", LABEL],
            capture_output=True,
            text=True,
        )
        running = result.returncode == 0
    return {
        "installed": installed,
        "running": running,
        "plist_path": str(PLIST_PATH),
        "log_dir": str(LOG_DIR),
    }
