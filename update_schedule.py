"""
Reads config.yaml and updates the cron schedule in the GitHub Actions workflow
to match the configured time and timezone.

Usage: python update_schedule.py
"""

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"
WORKFLOW_PATH = Path(__file__).parent / ".github" / "workflows" / "daily-post.yml"


def get_utc_cron(hour: int, minute: int, timezone: str) -> str:
    """Convert a local time + timezone to a UTC cron expression."""
    # Use today's date to calculate the UTC offset (accounts for DST)
    tz = ZoneInfo(timezone)
    local_time = datetime.now(tz).replace(hour=hour, minute=minute, second=0)
    utc_time = local_time.astimezone(ZoneInfo("UTC"))
    return f"{utc_time.minute} {utc_time.hour} * * *"


def main():
    if not CONFIG_PATH.exists():
        print("config.yaml not found, using defaults.")
        return

    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f) or {}

    schedule = config.get("schedule", {})
    hour = schedule.get("hour", 9)
    minute = schedule.get("minute", 0)
    timezone = schedule.get("timezone", "America/New_York")

    cron = get_utc_cron(hour, minute, timezone)
    print(f"Local time: {hour:02d}:{minute:02d} {timezone}")
    print(f"UTC cron:   {cron}")

    # Update the workflow file
    if not WORKFLOW_PATH.exists():
        print(f"Workflow file not found at {WORKFLOW_PATH}")
        return

    content = WORKFLOW_PATH.read_text()
    # Replace the cron line
    updated = re.sub(
        r'- cron: ".*"',
        f'- cron: "{cron}"',
        content,
    )

    if updated != content:
        WORKFLOW_PATH.write_text(updated)
        print(f"Updated {WORKFLOW_PATH}")
        print("Commit and push to apply the new schedule.")
    else:
        print("Schedule already up to date.")


if __name__ == "__main__":
    main()
