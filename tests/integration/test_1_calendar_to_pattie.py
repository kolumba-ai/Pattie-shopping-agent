"""Google Calendar -> watcher -> bridge -> Pattie (agent + Telegram).

Offline: the demo calendar (.ics) goes through the real watcher, a fake classifier, the real bridge
and the real AgentDelivery, with a fake agent and a fake Telegram.
Live: reads the real Google Calendar with the service account.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from conftest import ROOT

TZ = "America/Los_Angeles"
NOW = datetime(2026, 11, 20, 10, 0, tzinfo=ZoneInfo(TZ))   # the day before the demo events


class FakeClassifier:
    """Stands in for Claude: full-day events need a power bank; everything else needs nothing."""

    def decide(self, ev, now):
        from pattie.classifier import food_nudges, urgency_tier
        from pattie.sources import parse_time
        start, end = parse_time(ev["start"], TZ), parse_time(ev["end"], TZ)
        days = (start.date() - now.date()).days
        needs = "hackathon" in ev["title"].lower()
        return {"event_type": "full_day_event" if needs else "other", "needs_products": needs,
                "items": [{"item": "power bank", "reason": "13-hour day", "search_query": "Anker power bank",
                           "category": "accessories", "specific": True}] if needs else [],
                "summary": "Long day" if needs else "Nothing needed", "days_until": days,
                "urgency_tier": urgency_tier(days) if needs else None,
                "food_nudges": food_nudges(ev, start, end)}


class FakeAgent:
    def __init__(self):
        self.events = []

    def event(self, chat_id, kind, payload):
        self.events.append((kind, payload))
        return f"{kind}: {payload['event']['title']}"


class FakeTelegram:
    def __init__(self):
        self.texts = []

    def text(self, chat_id, t):
        self.texts.append((chat_id, t))


def test_demo_calendar_reaches_pattie_in_telegram(tmp_path, monkeypatch):
    monkeypatch.delenv("AMPLITUDE_API_KEY", raising=False)
    from pattie.app import AgentDelivery
    from pattie.bridge import Bridge
    from pattie.telegram import Owner
    from pattie.sources import IcsFileSource
    from pattie.store import Store
    from pattie.watcher import poll

    store = Store(str(tmp_path / "pattie.sqlite3"))
    source = IcsFileSource(str(ROOT / "demo/pattie-demo-calendar.ics"), TZ)
    decisions = poll(source, store, FakeClassifier(), NOW, TZ, out=lambda _m: None)
    titles = {ev["title"] for ev, _ in decisions}
    assert "Hackathon SF" in titles, titles

    agent, telegram = FakeAgent(), FakeTelegram()
    owner = Owner(str(tmp_path / "pattie.sqlite3"), pairing_code="TEST01")
    owner.try_pair(42, "/start TEST01")
    bridge = Bridge(AgentDelivery(agent, telegram, owner), str(tmp_path / "pattie.sqlite3"),
                    clock=lambda: NOW.astimezone(timezone.utc), log=lambda _m: None)
    for ev, d in decisions:
        bridge.handle(json.dumps({"event": ev, "decision": d}))

    events = [p for k, p in agent.events if k == "calendar_event"]
    assert [p["event"]["title"] for p in events] == ["Hackathon SF"]
    assert events[0]["decision"]["items"][0]["item"] == "power bank"
    assert all(len(json.dumps(p)) < 4000 for _, p in agent.events)    # compacted for the agent
    assert telegram.texts == [("42", "calendar_event: Hackathon SF")]  # the owner got the message

    # The breakfast nudge for the 8am hackathon waits until 8pm the night before, then goes once
    assert not any(k == "food_nudge" for k, _ in agent.events)
    bridge.clock = lambda: NOW.replace(hour=20, minute=1).astimezone(timezone.utc)
    assert bridge.send_due() >= 1 and bridge.send_due() == 0
    food = [p for k, p in agent.events if k == "food_nudge"]
    assert food[0]["nudge"]["meal"] == "breakfast" and food[0]["event"]["title"] == "Hackathon SF"


def test_unchanged_calendar_sends_nothing_twice(tmp_path, monkeypatch):
    monkeypatch.delenv("AMPLITUDE_API_KEY", raising=False)
    from pattie.sources import IcsFileSource
    from pattie.store import Store
    from pattie.watcher import poll

    store = Store(str(tmp_path / "pattie.sqlite3"))
    source = IcsFileSource(str(ROOT / "demo/pattie-demo-calendar.ics"), TZ)
    first = poll(source, store, FakeClassifier(), NOW, TZ, out=lambda _m: None)
    second = poll(source, store, FakeClassifier(), NOW + timedelta(minutes=1), TZ, out=lambda _m: None)
    assert first and second == []      # already decided: no repeat proposals


@pytest.mark.live
def test_live_google_calendar_read():
    creds, cal = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"), os.environ.get("PATTIE_CALENDAR_ID")
    if not (creds and cal):
        pytest.skip("needs GOOGLE_APPLICATION_CREDENTIALS and PATTIE_CALENDAR_ID")
    from pattie.sources import GoogleCalendarSource
    from pattie.store import Store
    store = Store(tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False).name)
    src = GoogleCalendarSource(cal, creds, store)
    events = src.fetch_changes()
    assert events, "the calendar returned no events"
    assert store.get(src.token_key), "Google didn't return a sync token"
