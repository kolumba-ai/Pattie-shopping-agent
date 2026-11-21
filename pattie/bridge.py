"""Hand the calendar watcher's decisions to Pattie, with a queue and retries.

    python -m pattie.watcher --json | python -m pattie.app      (pattie/app.py runs the bridge)

For every JSON line {"event": ..., "decision": ...} from the watcher:
- needs products -> a "calendar event" delivery now
- food nudges    -> a "food nudge" delivery at each nudge's ask_at time
- neither        -> nothing

Deliveries go to an outbox table and are retried until they succeed, so nothing is lost while the
owner hasn't paired yet or Telegram is down. `delivery.run(agent, routine, payload)` does the
work (pattie.app.AgentDelivery: run the agent, message the owner); it raises DeliveryError to retry.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id       TEXT PRIMARY KEY,          -- event id + kind (+ meal): an edited event replaces its entry
    routine  TEXT NOT NULL,
    due_at   TEXT NOT NULL,             -- now for product needs, ask_at for food nudges
    payload  TEXT NOT NULL,
    sent_at  TEXT,
    tries    INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
"""


class DeliveryError(Exception):
    pass


OpenBotError = DeliveryError   # old name, kept for anything still importing it


MAX_PAYLOAD = 3900  # keeps each event the agent reads short


def compact(payload: dict, limit: int = MAX_PAYLOAD) -> str:
    """Shrink a payload so the agent reads a short event: keep the event, the item types and reasons, and
    short versions of the top picks; drop detail step by step until it fits. Pattie can always call
    find_offers again for full results."""
    ev = payload.get("event", {})
    out = {"kind": payload.get("kind"),
           "event": {k: ev.get(k) for k in ("id", "title", "start", "end", "location") if ev.get(k)}}
    if "nudge" in payload:
        out["nudge"] = payload["nudge"]
    d = payload.get("decision")
    if d:
        out["decision"] = {k: d.get(k) for k in ("event_type", "summary", "urgency_tier", "days_until")
                           if d.get(k) is not None}
        out["decision"]["items"] = [{k: i.get(k) for k in ("item", "reason", "search_query", "category")}
                                    for i in d.get("items", [])]
        offers = []
        for o in d.get("offers", []):
            offers.append({"item": o.get("item"), "mode": o.get("mode"),
                           "picks": [{"asin": p.get("asin"), "title": (p.get("title") or "")[:70],
                                      "price": p.get("price"), "rating": p.get("rating"),
                                      "label": p.get("label"), "sponsored": p.get("sponsored"),
                                      "action": p.get("action")} for p in o.get("picks", [])[:3]]})
        out["offers"] = offers
    steps = [
        lambda o: None,
        lambda o: [p.pop("title", None) for off in o.get("offers", []) for p in off["picks"]],
        lambda o: o.pop("offers", None),
        lambda o: [i.pop("reason", None) for i in o.get("decision", {}).get("items", [])],
    ]
    for step in steps:
        step(out)
        text = json.dumps(out, ensure_ascii=False)
        if len(text) <= limit:
            return text
    return text[:limit]  # last resort; should not happen with real events


class Bridge:
    def __init__(self, api, db_path: str, agent: str = "Pattie",
                 routine_event: str = "Calendar event", routine_food: str = "Food nudge",
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), log=print):
        self.api, self.agent = api, agent
        self.routine_event, self.routine_food = routine_event, routine_food
        self.clock, self.log = clock, log
        self.db = sqlite3.connect(db_path)
        self.db.executescript(SCHEMA)

    def _queue(self, entry_id: str, routine: str, due_at: str, payload: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO outbox (id, routine, due_at, payload, sent_at, tries) "
                        "VALUES (?, ?, ?, ?, NULL, 0)", (entry_id, routine, due_at, json.dumps(payload)))
        self.db.commit()

    def handle(self, line: str) -> None:
        """Queue what this decision needs. Delivery happens in send_due, with retries, so nothing is
        lost while the owner hasn't paired yet or Telegram is down."""
        line = line.strip()
        if not line.startswith("{"):
            return  # the watcher's human-readable output; only JSON lines are decisions
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            self.log(f"ignored a line that isn't valid JSON: {line[:80]}")
            return
        if "decision" not in msg:
            return
        ev, d = msg.get("event", {}), msg.get("decision", {})
        title = ev.get("title", "?")
        if d.get("needs_products"):
            self._queue(f"{ev.get('id')}:products", self.routine_event, self.clock().isoformat(),
                        {"kind": "calendar_event", "event": ev, "decision": d})
            self.log(f"queued products for {title!r}")
        for n in d.get("food_nudges", []):
            payload = {"kind": "food_nudge", "event": {k: ev.get(k) for k in ("id", "title", "start", "end", "location")},
                       "nudge": n}
            self._queue(f"{ev.get('id')}:food:{n['meal']}", self.routine_food, n["ask_at"], payload)
            self.log(f"scheduled {n['meal']} nudge for {title!r} at {n['ask_at']}")
        if not d.get("needs_products") and not d.get("food_nudges"):
            self.log(f"skip {title!r}: needs nothing")
        self.send_due()

    def send_due(self) -> int:
        now, sent = self.clock(), 0
        for eid, routine, due_at, payload in self.db.execute(
                "SELECT id, routine, due_at, payload FROM outbox WHERE sent_at IS NULL ORDER BY due_at").fetchall():
            if datetime.fromisoformat(due_at) > now:
                continue
            error = self._run(routine, json.loads(payload), eid)
            if error is None:
                self.db.execute("UPDATE outbox SET sent_at = ? WHERE id = ?", (now.isoformat(), eid))
                sent += 1
            else:
                self.db.execute("UPDATE outbox SET tries = tries + 1, last_error = ? WHERE id = ?", (error, eid))
            self.db.commit()
        return sent

    def pending(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0]

    def _run(self, routine: str, payload: dict, what: str) -> Optional[str]:
        """None on success, otherwise the error (the entry stays queued and is retried)."""
        try:
            self.api.run(self.agent, routine, payload)
            self.log(f"started {routine!r}: {what}")
            return None
        except DeliveryError as e:
            self.log(f"could not start {routine!r} ({what}), will retry: {e}")
            getattr(self.api, "_ids", {}).clear()
            return str(e)


def main() -> None:
    from .app import main as app_main
    app_main()   # the bridge runs inside pattie.app now


if __name__ == "__main__":
    main()
