"""Where the owner is, and where deliveries go (ARCHITECTURE.md, "Location awareness").

    loc = Locator("pattie.sqlite3", default_tz="America/Los_Angeles", home_city="San Francisco")
    loc.set_from_chat("Austin", "America/Chicago", until=..., message="I'm in Austin until Friday")
    loc.where_am_i(now)   -> {city, timezone, source, valid_until, delivery: {...}}

Priority:
  1. What the owner said in chat, while it's still valid.
  2. A calendar event in progress that has a location (the city comes from the event; the
     time zone stays the default unless the owner said otherwise).
  3. The default time zone (PATTIE_TZ, later the Google Calendar setting) and home city.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS location_fact (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    city        TEXT NOT NULL,
    timezone    TEXT NOT NULL,
    valid_until TEXT,                  -- ISO time; NULL = until replaced
    message     TEXT,                  -- the chat message it came from
    created_at  TEXT NOT NULL
);
"""


class Locator:
    def __init__(self, path: str, default_tz: str, home_city: str = "home",
                 home_address_label: str = "home"):
        ZoneInfo(default_tz)  # fail early on a bad zone name
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.default_tz, self.home_city, self.home_label = default_tz, home_city, home_address_label

    def set_from_chat(self, city: str, timezone: str, now: datetime,
                      until: Optional[datetime] = None, message: str = "") -> dict:
        ZoneInfo(timezone)  # raises for a bad zone name, before anything is saved
        self.db.execute(
            "INSERT INTO location_fact (city, timezone, valid_until, message, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (city, timezone, until.isoformat() if until else None, message, now.isoformat()))
        self.db.commit()
        return self.where_am_i(now)

    def _chat_fact(self, now: datetime) -> Optional[sqlite3.Row]:
        for row in self.db.execute("SELECT * FROM location_fact ORDER BY id DESC").fetchall():
            if row["valid_until"] is None or datetime.fromisoformat(row["valid_until"]) > now:
                return row
        return None

    def _event_in_progress(self, now: datetime) -> Optional[dict]:
        """Calendar events come from the watcher's `event` table, if it exists in this database."""
        try:
            rows = self.db.execute("SELECT data FROM event WHERE status = 'confirmed'").fetchall()
        except sqlite3.OperationalError:
            return None
        for r in rows:
            ev = json.loads(r["data"])
            if ev.get("all_day") or not ev.get("location") or not ev.get("start") or not ev.get("end"):
                continue
            start, end = datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"])
            if start <= now < end:
                return ev
        return None

    def where_am_i(self, now: datetime) -> dict:
        """`now` must be timezone-aware."""
        fact = self._chat_fact(now)
        event = self._event_in_progress(now)
        if fact:
            city, tz, source = fact["city"], fact["timezone"], "chat"
            valid_until = fact["valid_until"]
            why = fact["message"] or "owner said so in chat"
        elif event:
            city, tz, source = event["location"], self.default_tz, "calendar_event"
            valid_until, why = event["end"], f"at '{event['title']}'"
        else:
            city, tz, source, valid_until, why = self.home_city, self.default_tz, "default", None, "default"
        local_now = now.astimezone(ZoneInfo(tz))
        return {
            "city": city,
            "timezone": tz,
            "local_time": local_now.isoformat(timespec="minutes"),
            "source": source,
            "because": why,
            "valid_until": valid_until,
            "delivery": {
                "packages": self.home_label if source == "default" else
                            f"{self.home_label} (ask the owner if they'll be away when it arrives)",
                "food": event["location"] if event else (city if source == "chat" else self.home_label),
            },
        }
