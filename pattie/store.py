"""SQLite store: which events Pattie has seen, and what it decided about them."""

import json
import sqlite3
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS event (
    id           TEXT PRIMARY KEY,
    fingerprint  TEXT NOT NULL,
    status       TEXT NOT NULL,          -- confirmed | cancelled
    data         TEXT NOT NULL,          -- normalized event JSON
    decision     TEXT,                   -- classifier output JSON, NULL = not decided yet
    decided_at   TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- key/value (sync token) ---
    def get(self, key: str) -> Optional[str]:
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: Optional[str]) -> None:
        if value is None:
            self.db.execute("DELETE FROM kv WHERE key = ?", (key,))
        else:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))
        self.db.commit()

    # --- events ---
    def upsert(self, event: dict) -> str:
        """Save an event. Returns 'new', 'changed', 'cancelled' or 'unchanged'.

        A new or changed event has its decision cleared so it gets classified again.
        """
        row = self.db.execute(
            "SELECT fingerprint, status FROM event WHERE id = ?", (event["id"],)
        ).fetchone()
        if event["status"] == "cancelled":
            if row is None or row["status"] == "cancelled":
                return "unchanged"
            self.db.execute(
                "UPDATE event SET status = 'cancelled', fingerprint = ? WHERE id = ?",
                (event["fingerprint"], event["id"]),
            )
            self.db.commit()
            return "cancelled"
        if row is not None and row["fingerprint"] == event["fingerprint"] \
                and row["status"] == "confirmed":
            return "unchanged"
        self.db.execute(
            "INSERT OR REPLACE INTO event (id, fingerprint, status, data, decision, decided_at) "
            "VALUES (?, ?, 'confirmed', ?, NULL, NULL)",
            (event["id"], event["fingerprint"], json.dumps(event)),
        )
        self.db.commit()
        return "new" if row is None else "changed"

    def undecided(self) -> list:
        rows = self.db.execute(
            "SELECT data FROM event WHERE status = 'confirmed' AND decision IS NULL"
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def save_decision(self, event_id: str, decision: dict, now_iso: str) -> None:
        self.db.execute(
            "UPDATE event SET decision = ?, decided_at = ? WHERE id = ?",
            (json.dumps(decision), now_iso, event_id),
        )
        self.db.commit()

    def all_ids(self) -> set:
        return {r["id"] for r in self.db.execute("SELECT id FROM event").fetchall()}
