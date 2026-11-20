"""Where calendar events come from.

GoogleCalendarSource  the real calendar, read with a service account. Uses Google's
                      sync tokens, so after the first run each poll only returns
                      events that were added, edited or deleted since the last one.
IcsFileSource         an .ics file (like demo/pattie-demo-calendar.ics), for testing
                      without Google. Returns every event; the store works out what changed.

Both return normalized events:
    {id, status, title, start, end, all_day, location, description, attendees, fingerprint}
"""

import hashlib
import json
from datetime import date, datetime
from typing import List

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _fingerprint(ev: dict) -> str:
    keys = ("status", "title", "start", "end", "location", "description", "attendees")
    blob = json.dumps({k: ev.get(k) for k in keys}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _finish(ev: dict) -> dict:
    ev["fingerprint"] = _fingerprint(ev)
    return ev


class GoogleCalendarSource:
    def __init__(self, calendar_id: str, credentials_file: str, store):
        import warnings
        with warnings.catch_warnings():  # Google libs nag about Python 3.9 end of life
            warnings.simplefilter("ignore", FutureWarning)
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            credentials_file, scopes=SCOPES
        )
        self.svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.calendar_id = calendar_id
        self.store = store
        self.token_key = f"sync_token:{calendar_id}"

    def fetch_changes(self) -> List[dict]:
        from googleapiclient.errors import HttpError

        token = self.store.get(self.token_key)
        try:
            items, next_token = self._list(token)
        except HttpError as e:
            if e.resp.status != 410:  # 410 Gone = sync token expired
                raise
            self.store.set(self.token_key, None)
            items, next_token = self._list(None)
        self.store.set(self.token_key, next_token)
        return [self._normalize(it) for it in items]

    def _list(self, sync_token):
        # syncToken can't be combined with timeMin/timeMax/orderBy, so the first
        # (full) sync reads everything; the watcher filters to the 30-day window.
        params = {"calendarId": self.calendar_id, "singleEvents": True, "maxResults": 250}
        if sync_token:
            params["syncToken"] = sync_token
        items, page = [], None
        while True:
            if page:
                params["pageToken"] = page
            res = self.svc.events().list(**params).execute()
            items += res.get("items", [])
            page = res.get("nextPageToken")
            if not page:
                return items, res.get("nextSyncToken")

    @staticmethod
    def _normalize(it: dict) -> dict:
        start, end = it.get("start", {}), it.get("end", {})
        return _finish({
            "id": it["id"],
            "status": "cancelled" if it.get("status") == "cancelled" else "confirmed",
            "title": it.get("summary", ""),
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
            "all_day": "date" in start and "dateTime" not in start,
            "location": it.get("location", ""),
            "description": it.get("description", ""),
            "attendees": len(it.get("attendees", [])),
        })


class IcsFileSource:
    """Minimal .ics reader: enough for files exported from Google Calendar or our seed file."""

    def __init__(self, path: str, default_tz: str = "America/Los_Angeles"):
        self.path = path
        self.default_tz = default_tz

    def fetch_changes(self) -> List[dict]:
        with open(self.path, encoding="utf-8") as f:
            raw = f.read().replace("\r\n", "\n")
        lines = raw.replace("\n ", "").replace("\n\t", "").split("\n")  # unfold
        events, cur = [], None
        for line in lines:
            if line == "BEGIN:VEVENT":
                cur = {}
            elif line == "END:VEVENT" and cur is not None:
                events.append(self._normalize(cur))
                cur = None
            elif cur is not None and ":" in line:
                name, value = line.split(":", 1)
                key, *params = name.split(";")
                cur[key] = (value, dict(p.split("=", 1) for p in params if "=" in p))
        return events

    def _parse_dt(self, field):
        value, params = field
        if params.get("VALUE") == "DATE" or len(value) == 8:
            return date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat(), True
        tz = ZoneInfo("UTC") if value.endswith("Z") else ZoneInfo(params.get("TZID", self.default_tz))
        dt = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=tz)
        return dt.isoformat(), False

    def _normalize(self, ev: dict) -> dict:
        unescape = lambda s: (s.replace("\\n", "\n").replace("\\,", ",")
                               .replace("\\;", ";").replace("\\\\", "\\"))
        start, all_day = self._parse_dt(ev["DTSTART"])
        end = self._parse_dt(ev["DTEND"])[0] if "DTEND" in ev else start
        return _finish({
            "id": ev["UID"][0],
            "status": "cancelled" if ev.get("STATUS", ("",))[0] == "CANCELLED" else "confirmed",
            "title": unescape(ev.get("SUMMARY", ("",))[0]),
            "start": start,
            "end": end,
            "all_day": all_day,
            "location": unescape(ev.get("LOCATION", ("",))[0]),
            "description": unescape(ev.get("DESCRIPTION", ("",))[0]),
            "attendees": sum(1 for k in ev if k == "ATTENDEE"),
        })


def parse_time(value: str, tz: str) -> datetime:
    """Event start/end (ISO datetime or all-day date) -> aware datetime."""
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo(tz))
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

