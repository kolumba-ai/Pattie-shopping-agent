"""Pattie's audit trail in SingleStore: every step behind every decision, in order.

The hackathon challenge asks for "a shopping agent with an audit trail". For each calendar event
Pattie handles, one trace_id ties together the steps it took and what it saw:

    event_detected   -> the event (title, time, location) and whether it's new or changed
    memory_recalled  -> what Cognee remembered that mattered
    decision_made    -> Claude's decision: type, items with reasons, urgency, food nudges, model
    offers_ranked    -> Amazon products considered, which won and why, Sponsored labels

Connection (git-ignored .env): either SINGLESTOREDB_URL=user:password@host:port/database
or SINGLESTORE_HOST, SINGLESTORE_PORT, SINGLESTORE_USER, SINGLESTORE_PASSWORD,
SINGLESTORE_DATABASE. Without them Pattie runs and records nothing.

    python -m pattie.audit show                 # latest traces
    python -m pattie.audit show "Hackathon"     # the full trail for one event
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

TABLE = "pattie_audit"
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id          BIGINT AUTO_INCREMENT,
    trace_id    VARCHAR(36) NOT NULL,
    recorded_at DATETIME(6) NOT NULL,
    owner       VARCHAR(64) NOT NULL,
    step        VARCHAR(32) NOT NULL,
    event_id    VARCHAR(255),
    event_title VARCHAR(255),
    detail      JSON,
    SORT KEY (recorded_at),
    SHARD KEY (trace_id),
    KEY (id)
)
"""


def _connect():
    import singlestoredb as s2

    url = os.environ.get("SINGLESTOREDB_URL")
    if url:
        return s2.connect(url, autocommit=True)
    host = os.environ.get("SINGLESTORE_HOST")
    if not host:
        return None
    return s2.connect(
        host=host,
        port=int(os.environ.get("SINGLESTORE_PORT", "3306")),
        user=os.environ.get("SINGLESTORE_USER"),
        password=os.environ.get("SINGLESTORE_PASSWORD"),
        database=os.environ.get("SINGLESTORE_DATABASE"),
        autocommit=True,
    )


class AuditTrail:
    def __init__(self, owner: str):
        self.owner = owner
        self.conn = _connect()
        if self.conn is not None:
            with self.conn.cursor() as cur:
                cur.execute(SCHEMA)

    @property
    def enabled(self) -> bool:
        return self.conn is not None

    def new_trace(self) -> str:
        return str(uuid.uuid4())

    def record(self, trace_id: str, step: str, event: Optional[dict] = None,
               detail: Optional[dict] = None) -> None:
        if self.conn is None:
            return
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {TABLE} (trace_id, recorded_at, owner, step, event_id, "
                "event_title, detail) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (trace_id, datetime.now(timezone.utc).replace(tzinfo=None), self.owner, step,
                 (event or {}).get("id"), ((event or {}).get("title") or "")[:255],
                 json.dumps(detail or {}, default=str)),
            )

    def trail(self, title_like: Optional[str] = None, limit_traces: int = 5) -> list:
        """Rows of the latest traces, oldest step first within each trace."""
        if self.conn is None:
            return []
        with self.conn.cursor() as cur:
            where, args = "owner = %s", [self.owner]
            if title_like:
                where += " AND event_title LIKE %s"
                args.append(f"%{title_like}%")
            cur.execute(
                f"SELECT trace_id FROM {TABLE} WHERE {where} GROUP BY trace_id "
                f"ORDER BY MAX(recorded_at) DESC LIMIT {int(limit_traces)}", args)
            traces = [r[0] for r in cur.fetchall()]
            if not traces:
                return []
            marks = ",".join(["%s"] * len(traces))
            cur.execute(
                f"SELECT trace_id, recorded_at, step, event_title, detail FROM {TABLE} "
                f"WHERE trace_id IN ({marks}) ORDER BY trace_id, recorded_at, id", traces)
            return cur.fetchall()


def _summary(step: str, detail) -> str:
    d = detail if isinstance(detail, dict) else json.loads(detail or "{}")
    if step == "event_detected":
        return f"{d.get('kind')} event, starts {d.get('start')}, at {d.get('location') or '-'}"
    if step == "memory_recalled":
        return (d.get("memory") or "nothing relevant").replace("\n", " ")[:160]
    if step == "decision_made":
        items = ", ".join(i.get("item", "") for i in d.get("items", [])) or "nothing"
        return f"{d.get('event_type')}: {items} ({d.get('summary', '')[:100]})"
    if step == "offers_ranked":
        return "; ".join(f"{o.get('item')}: {o.get('mode')}"
                         + (f" -> {o['picks'][0]['title'][:40]}" if o.get('picks') else "")
                         for o in d.get("offers", []))
    return json.dumps(d)[:160]


def main(argv=None) -> None:
    from .watcher import load_env_file

    load_env_file()
    p = argparse.ArgumentParser(description="Pattie audit trail (SingleStore)")
    p.add_argument("--user", default=os.environ.get("PATTIE_USER", "anna_susan"))
    sub = p.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show", help="print the latest decision trails")
    show.add_argument("title", nargs="?", help="only events whose title contains this")
    show.add_argument("--traces", type=int, default=5)
    args = p.parse_args(argv)

    audit = AuditTrail(args.user)
    if not audit.enabled:
        sys.exit("SingleStore isn't configured: add the connection to .env (see pattie/audit.py).")
    current = None
    for trace_id, at, step, title, detail in audit.trail(args.title, args.traces):
        if trace_id != current:
            current = trace_id
            print(f"\n== {title}  (trace {trace_id[:8]})")
        print(f"  {at:%H:%M:%S}  {step:<16} {_summary(step, detail)}")


if __name__ == "__main__":
    main()
