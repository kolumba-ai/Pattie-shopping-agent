"""The watcher records every step of a decision in the audit trail (fake trail, no SingleStore)."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pattie.sources import ZoneInfo  # noqa: E402
from pattie.watcher import poll  # noqa: E402


class FakeAudit:
    enabled = True

    def __init__(self):
        self.rows = []

    def record(self, trace, step, ev, detail):
        self.rows.append((trace, step, ev["title"], detail))


class OneEvent:
    def fetch_changes(self):
        return [{"id": "e1", "status": "confirmed", "title": "Hackathon SF",
                 "start": "2026-11-21T08:00:00-08:00", "end": "2026-11-21T21:00:00-08:00",
                 "all_day": False, "location": "SF", "description": "", "attendees": 0,
                 "fingerprint": "f1"}]


class Store:
    def __init__(self):
        self.ev = None

    def upsert(self, ev):
        self.ev = ev
        return "new"

    def undecided(self):
        return [self.ev]

    def save_decision(self, *a):
        pass


class Classifier:
    def decide(self, ev, now):
        return {"event_type": "full_day_event", "needs_products": True, "summary": "long day",
                "items": [{"item": "power bank", "reason": "13 hours"}], "days_until": 1,
                "urgency_tier": "asap", "food_nudges": [], "memory": "old power bank broke",
                "model": "test"}


def test_one_trace_per_event_with_every_step():
    audit = FakeAudit()
    now = datetime(2026, 11, 20, 12, tzinfo=ZoneInfo("America/Los_Angeles"))
    poll(OneEvent(), Store(), Classifier(), now, "America/Los_Angeles", out=lambda m: None,
         offers_fn=lambda d: [{"item": "power bank", "mode": "one", "picks": []}],
         user="anna_susan", audit=audit)
    steps = [r[1] for r in audit.rows]
    assert steps == ["event_detected", "memory_recalled", "decision_made", "offers_ranked"]
    assert len({r[0] for r in audit.rows}) == 1          # one trace id for the whole chain
    assert "memory" not in audit.rows[2][3]               # memory is its own step
