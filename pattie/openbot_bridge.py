"""Hand the watcher's decisions to the Pattie agent in OpenBot (ARCHITECTURE.md, "Routines").

    python -m pattie.watcher --json | python -m pattie.openbot_bridge

For every JSON line {"event": ..., "decision": ...} from the watcher:
- needs products -> start the "Calendar event" routine now, with the event and decision as payload
- food nudges    -> saved, and the "Food nudge" routine starts at each nudge's ask_at time
- neither        -> nothing (the event needs nothing)

Routines are started through OpenBot's local automation API, which listens on 127.0.0.1 inside
the OpenBot host and is turned on per agent (agent settings -> Permissions -> Local scripts).
OpenBot writes its address to <automation>/url and its auth header to <automation>/headers.

Settings:
    OPENBOT_AUTOMATION   folder with url and headers (default /openbot-automation)
    PATTIE_AGENT         agent name in OpenBot (default "Pattie")
    PATTIE_ROUTINE_EVENT routine name for product needs (default "Calendar event")
    PATTIE_ROUTINE_FOOD  routine name for food nudges (default "Food nudge")
    PATTIE_DB            SQLite file for scheduled nudges (default pattie.sqlite3)
"""

from __future__ import annotations

import json
import os
import select
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
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


class OpenBotError(Exception):
    pass


class OpenBotAPI:
    """Start routines by agent and routine name through OpenBot's local automation API."""

    def __init__(self, automation_dir: str, http: Optional[Callable] = None):
        self.dir = Path(automation_dir)
        self.http = http or self._http
        self._ids: dict = {}

    def _conn(self) -> tuple[str, dict]:
        url_file, headers_file = self.dir / "url", self.dir / "headers"
        if not url_file.exists():
            raise OpenBotError(f"{url_file} not found: turn on Permissions -> Local scripts "
                               "for the Pattie agent in OpenBot")
        headers = {}
        for line in headers_file.read_text().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip()] = v.strip()
        return url_file.read_text().strip().rstrip("/"), headers

    @staticmethod
    def _http(method: str, url: str, headers: dict, body: Optional[dict] = None):
        req = urllib.request.Request(url, method=method, headers={**headers, "Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise OpenBotError(f"OpenBot {e.code}: {e.read().decode(errors='replace')[:200]}") from None
        except urllib.error.URLError as e:
            raise OpenBotError(f"OpenBot unreachable: {e.reason}") from None

    def routine_ids(self, agent: str, routine: str) -> tuple[str, str]:
        key = (agent, routine)
        if key not in self._ids:
            base, headers = self._conn()
            for a in self.http("GET", f"{base}/v1/agents", headers):
                if a["name"].lower() == agent.lower():
                    for r in a.get("routines", []):
                        if r["name"].lower() == routine.lower():
                            self._ids[key] = (a["id"], r["id"])
                            break
                    else:
                        raise OpenBotError(f"agent {agent!r} has no routine {routine!r} "
                                           f"(has: {[r['name'] for r in a.get('routines', [])]})")
                    break
            else:
                raise OpenBotError(f"no agent {agent!r} with Local scripts turned on")
        return self._ids[key]

    def run(self, agent: str, routine: str, payload: dict) -> dict:
        agent_id, routine_id = self.routine_ids(agent, routine)
        base, headers = self._conn()
        return self.http("POST", f"{base}/v1/agents/{agent_id}/routines/{routine_id}/run", headers,
                         {"payload": json.dumps(payload, ensure_ascii=False)})


class Bridge:
    def __init__(self, api: OpenBotAPI, db_path: str, agent: str = "Pattie",
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
        lost while OpenBot is restarting or its local API isn't turned on yet."""
        msg = json.loads(line)
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
        except OpenBotError as e:
            self.log(f"could not start {routine!r} ({what}), will retry: {e}")
            self.api._ids.clear()  # routines may have been renamed or re-created
            return str(e)


def main() -> None:
    api = OpenBotAPI(os.environ.get("OPENBOT_AUTOMATION", "/openbot-automation"))
    bridge = Bridge(api, os.environ.get("PATTIE_DB", "pattie.sqlite3"),
                    agent=os.environ.get("PATTIE_AGENT", "Pattie"),
                    routine_event=os.environ.get("PATTIE_ROUTINE_EVENT", "Calendar event"),
                    routine_food=os.environ.get("PATTIE_ROUTINE_FOOD", "Food nudge"),
                    log=lambda m: print(f"[bridge] {m}", file=sys.stderr, flush=True))
    while True:
        ready, _, _ = select.select([sys.stdin], [], [], 30)  # wake at least every 30s for due nudges
        if ready:
            line = sys.stdin.readline()
            if not line:
                break  # watcher exited
            if line.strip():
                bridge.handle(line)
        bridge.send_due()
    bridge.send_due()


if __name__ == "__main__":
    main()
