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
CREATE TABLE IF NOT EXISTS nudge (
    id       TEXT PRIMARY KEY,          -- event id + meal, so an edited event replaces its nudge
    ask_at   TEXT NOT NULL,
    payload  TEXT NOT NULL,
    sent_at  TEXT
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

    def handle(self, line: str) -> None:
        msg = json.loads(line)
        ev, d = msg.get("event", {}), msg.get("decision", {})
        title = ev.get("title", "?")
        if d.get("needs_products"):
            payload = {"kind": "calendar_event", "event": ev, "decision": d}
            self._run(self.routine_event, payload, f"products for {title!r}")
        for n in d.get("food_nudges", []):
            nid = f"{ev.get('id')}:{n['meal']}"
            payload = {"kind": "food_nudge", "event": {k: ev.get(k) for k in ("id", "title", "start", "end", "location")},
                       "nudge": n}
            self.db.execute("INSERT OR REPLACE INTO nudge (id, ask_at, payload, sent_at) VALUES (?, ?, ?, NULL)",
                            (nid, n["ask_at"], json.dumps(payload)))
            self.db.commit()
            self.log(f"scheduled {n['meal']} nudge for {title!r} at {n['ask_at']}")
        if not d.get("needs_products") and not d.get("food_nudges"):
            self.log(f"skip {title!r}: needs nothing")

    def send_due(self) -> int:
        now, sent = self.clock(), 0
        for nid, ask_at, payload in self.db.execute(
                "SELECT id, ask_at, payload FROM nudge WHERE sent_at IS NULL").fetchall():
            if datetime.fromisoformat(ask_at) <= now:
                if self._run(self.routine_food, json.loads(payload), f"food nudge {nid}"):
                    self.db.execute("UPDATE nudge SET sent_at = ? WHERE id = ?", (now.isoformat(), nid))
                    self.db.commit()
                    sent += 1
        return sent

    def _run(self, routine: str, payload: dict, what: str) -> bool:
        try:
            self.api.run(self.agent, routine, payload)
            self.log(f"started {routine!r}: {what}")
            return True
        except OpenBotError as e:
            self.log(f"could not start {routine!r} ({what}): {e}")
            return False


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
