"""§2 — the app runs on Asia/Kolkata, the host runs UTC.

One helper, used everywhere. A UTC/IST slip writes 10 PM entries to the wrong
date and nobody notices for weeks.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def today_ist():
    """Today in IST, as the "YYYY-MM-DD" string every date field in §3 uses."""
    return datetime.now(IST).strftime("%Y-%m-%d")
