"""Tests for the watcher -> OpenBot bridge, with a fake OpenBot API (no network).

    .venv/bin/python -m pytest tests/test_bridge.py
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pattie.openbot_bridge import Bridge, OpenBotAPI  # noqa: E402

NOW = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)


class FakeOpenBot:
    def __init__(self):
        self.runs = []

    def __call__(self, method, url, headers, body=None):
        assert headers == {"Authorization": "Bearer test-token"}
        if method == "GET" and url.endswith("/v1/agents"):
            return [{"id": "agent-1", "name": "Pattie", "routines": [
                {"id": "r-event", "name": "Calendar event", "active": True},
                {"id": "r-food", "name": "Food nudge", "active": True}]}]
        assert method == "POST"
        self.runs.append((url, json.loads(body["payload"])))
        return {"ok": True}


def setup(clock=NOW):
    auto = Path(tempfile.mkdtemp())
    (auto / "url").write_text("http://127.0.0.1:41234\n")
    (auto / "headers").write_text("Authorization: Bearer test-token\n")
    fake = FakeOpenBot()
    logs = []
    db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False).name
    return Bridge(OpenBotAPI(str(auto), http=fake), db, clock=lambda: clock, log=logs.append), fake, logs, db


EVENT = {"id": "hack1", "title": "Hackathon SF", "start": "2026-11-21T08:00:00-08:00",
         "end": "2026-11-21T21:00:00-08:00", "location": "Shack15"}
NUDGE = {"meal": "breakfast", "deliver_to": "Shack15", "deliver_at": "2026-11-21T08:15:00-08:00",
         "ask_at": "2026-11-20T20:00:00-08:00", "reason": "Full-day event starts at 8:00AM."}


def line(decision, event=EVENT):
    return json.dumps({"event": event, "decision": decision})


def test_products_start_the_event_routine():
    bridge, fake, _, _ = setup()
    bridge.handle(line({"needs_products": True, "items": [{"item": "power bank"}], "food_nudges": []}))
    url, payload = fake.runs[0]
    assert url == "http://127.0.0.1:41234/v1/agents/agent-1/routines/r-event/run"
    assert payload["kind"] == "calendar_event" and payload["event"]["title"] == "Hackathon SF"


def test_food_nudge_waits_until_ask_at_and_sends_once():
    bridge, fake, _, db = setup()
    bridge.handle(line({"needs_products": False, "items": [], "food_nudges": [NUDGE]}))
    assert fake.runs == [] and bridge.send_due() == 0           # 10am PT: too early
    bridge.clock = lambda: datetime(2026, 11, 21, 4, 1, tzinfo=timezone.utc)  # 8:01pm PT
    assert bridge.send_due() == 1 and bridge.send_due() == 0    # sent once
    url, payload = fake.runs[0]
    assert url.endswith("/routines/r-food/run") and payload["nudge"]["meal"] == "breakfast"
    # an edited event replaces its pending nudge instead of adding a second one
    bridge2, fake2, _, _ = setup()
    bridge2.handle(line({"needs_products": False, "food_nudges": [NUDGE]}))
    bridge2.handle(line({"needs_products": False, "food_nudges": [{**NUDGE, "ask_at": "2026-11-20T21:00:00-08:00"}]}))
    assert bridge2.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_ignores_non_json_output():
    bridge, fake, _, _ = setup()
    for junk in ["Watching anna@example.com (every 60s)", "       * cheapest: Gift bag $4.99",
                 "{not json", "", '{"other": 1}']:
        bridge.handle(junk)
    assert fake.runs == [] and bridge.pending() == 0


def test_payload_fits_openbot_limit():
    from pattie.openbot_bridge import MAX_PAYLOAD, compact
    picks = [{"asin": f"TEST{n:06d}", "title": "Very long product title " * 10, "price": 19.99,
              "rating": "4.5", "label": "cheapest", "sponsored": False, "action": "order",
              "image": "https://example.com/" + "x" * 200, "why": "reason " * 40} for n in range(3)]
    decision = {"needs_products": True, "event_type": "full_day_event", "summary": "Long day",
                "items": [{"item": f"item {n}", "reason": "because " * 30, "search_query": "q",
                           "category": "accessories"} for n in range(5)],
                "offers": [{"item": f"item {n}", "mode": "three", "picks": picks, "skipped": ["x" * 80] * 20}
                           for n in range(5)]}
    text = compact({"kind": "calendar_event", "event": EVENT, "decision": decision})
    assert len(text) <= MAX_PAYLOAD
    data = json.loads(text)
    assert data["event"]["title"] == "Hackathon SF" and len(data["decision"]["items"]) == 5
    bridge, fake, _, _ = setup()
    bridge.handle(line(decision))
    assert len(fake.runs) == 1   # sent, compacted


def test_nothing_needed_and_openbot_not_ready():
    bridge, fake, logs, _ = setup()
    bridge.handle(line({"needs_products": False, "items": [], "food_nudges": []}))
    assert fake.runs == [] and "needs nothing" in logs[-1]
    auto = Path(tempfile.mkdtemp())
    missing = Bridge(OpenBotAPI(str(auto), http=FakeOpenBot()),
                     tempfile.NamedTemporaryFile(suffix=".db", delete=False).name, log=logs.append)
    missing.handle(line({"needs_products": True, "items": [], "food_nudges": []}))
    assert "Local scripts" in logs[-1] and missing.pending() == 1   # explained, and kept for later
    # once OpenBot's local API is turned on, the queued decision is delivered
    (auto / "url").write_text("http://127.0.0.1:41234" + chr(10))
    (auto / "headers").write_text("Authorization: Bearer test-token" + chr(10))
    assert missing.send_due() == 1 and missing.pending() == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
