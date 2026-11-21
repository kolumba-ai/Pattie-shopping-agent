"""Tests for the watcher -> Pattie bridge: queue, timing, retries, compact payloads (no network).

    .venv/bin/python -m pytest tests/test_bridge.py
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pattie.bridge import MAX_PAYLOAD, Bridge, DeliveryError, compact  # noqa: E402

NOW = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)


class FakeDelivery:
    """Stands in for pattie.app.AgentDelivery. `fail` makes every delivery raise DeliveryError."""

    def __init__(self, fail=None):
        self.runs, self.fail = [], fail

    def run(self, agent, routine, payload):
        if self.fail:
            raise DeliveryError(self.fail)
        self.runs.append((routine, payload))
        return {"sent": True}


def setup(clock=NOW, fail=None):
    delivery, logs = FakeDelivery(fail), []
    db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False).name
    return Bridge(delivery, db, clock=lambda: clock, log=logs.append), delivery, logs, db


EVENT = {"id": "hack1", "title": "Hackathon SF", "start": "2026-11-21T08:00:00-08:00",
         "end": "2026-11-21T21:00:00-08:00", "location": "Shack15"}
NUDGE = {"meal": "breakfast", "deliver_to": "Shack15", "deliver_at": "2026-11-21T08:15:00-08:00",
         "ask_at": "2026-11-20T20:00:00-08:00", "reason": "Full-day event starts at 8:00AM."}


def line(decision, event=EVENT):
    return json.dumps({"event": event, "decision": decision})


def test_products_are_delivered_now():
    bridge, delivery, _, _ = setup()
    bridge.handle(line({"needs_products": True, "items": [{"item": "power bank"}], "food_nudges": []}))
    routine, payload = delivery.runs[0]
    assert routine == "Calendar event"
    assert payload["kind"] == "calendar_event" and payload["event"]["title"] == "Hackathon SF"


def test_food_nudge_waits_until_ask_at_and_sends_once():
    bridge, delivery, _, _ = setup()
    bridge.handle(line({"needs_products": False, "items": [], "food_nudges": [NUDGE]}))
    assert delivery.runs == [] and bridge.send_due() == 0           # 10am PT: too early
    bridge.clock = lambda: datetime(2026, 11, 21, 4, 1, tzinfo=timezone.utc)  # 8:01pm PT
    assert bridge.send_due() == 1 and bridge.send_due() == 0         # sent once
    routine, payload = delivery.runs[0]
    assert routine == "Food nudge" and payload["nudge"]["meal"] == "breakfast"
    # an edited event replaces its pending nudge instead of adding a second one
    bridge2, _, _, _ = setup()
    bridge2.handle(line({"needs_products": False, "food_nudges": [NUDGE]}))
    bridge2.handle(line({"needs_products": False, "food_nudges": [{**NUDGE, "ask_at": "2026-11-20T21:00:00-08:00"}]}))
    assert bridge2.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_ignores_non_json_output():
    bridge, delivery, _, _ = setup()
    for junk in ["Watching anna@example.com (every 60s)", "       * cheapest: Gift bag $4.99",
                 "{not json", "", '{"other": 1}']:
        bridge.handle(junk)
    assert delivery.runs == [] and bridge.pending() == 0


def test_payload_fits_limit():
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


def test_failed_delivery_is_kept_and_retried():
    bridge, _, logs, _ = setup(fail="the owner hasn't paired with the bot yet")
    bridge.handle(line({"needs_products": True, "items": [], "food_nudges": []}))
    assert "will retry" in logs[-1] and bridge.pending() == 1      # kept for later
    bridge.api = FakeDelivery()                                      # e.g. the owner paired
    assert bridge.send_due() == 1 and bridge.pending() == 0


def test_bridge_works_from_another_thread():
    """pattie.app creates the bridge in the main thread and runs it in the calendar thread."""
    import threading
    bridge, delivery, _, _ = setup()
    errors = []

    def run():
        try:
            bridge.handle(line({"needs_products": True, "items": [], "food_nudges": []}))
            bridge.send_due()
        except Exception as e:
            errors.append(e)
    th = threading.Thread(target=run)
    th.start()
    th.join()
    assert errors == [] and len(delivery.runs) == 1


def test_nothing_needed():
    bridge, delivery, logs, _ = setup()
    bridge.handle(line({"needs_products": False, "items": [], "food_nudges": []}))
    assert delivery.runs == [] and "needs nothing" in logs[-1]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
