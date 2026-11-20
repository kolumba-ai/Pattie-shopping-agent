"""Spending policy and purchase ledger (ARCHITECTURE.md, "Purchases: Privacy.com cards and spending policy").

    policy = Policy.load()                      # config/policy.json
    ledger = Ledger("pattie.sqlite3")
    verdict = policy.check(total=29.99, merchant="amazon", titles=["Anker Power Bank"],
                           ledger=ledger, now=datetime.now(tz), owner_approved=True)
    verdict["result"]  -> "allow" | "ask" | "wait" | "block"

One Privacy.com card per vendor (Amazon, DoorDash), so every limit except the overall monthly
one is per vendor:
- block: not a vendor with its own card, a blocked kind of product (gift cards, crypto,
  subscriptions), over the vendor's per-purchase limit, or over what's left of the vendor's
  daily or monthly limit, or of the overall monthly limit. Pattie sends a cart link instead.
- wait: quiet hours and not urgent. `retry_at` says when to try again.
- ask: within limits, but needs the owner's yes (always, while auto_approve_threshold is 0).
- allow: within limits and either the owner said yes or it's at or under the threshold.

Privacy.com enforces each card's own hard limit on top of this. This module never sees card details.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = PROJECT_ROOT / "config" / "policy.json"

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS purchase (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id   TEXT,
    merchant   TEXT NOT NULL,
    total      REAL NOT NULL,
    items      TEXT NOT NULL,          -- JSON list of {asin, title, price}
    status     TEXT NOT NULL,          -- pending | placed | failed | cart_link
    order_id   TEXT,
    created_at TEXT NOT NULL           -- ISO time with offset
);
"""
COUNTS_TOWARD_LIMITS = ("pending", "placed")


class Ledger:
    """Every purchase Pattie attempts, so daily and monthly limits can be checked."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(LEDGER_SCHEMA)

    def record(self, merchant: str, total: float, items: list, status: str, now: datetime,
               trace_id: Optional[str] = None, order_id: Optional[str] = None) -> int:
        cur = self.db.execute(
            "INSERT INTO purchase (trace_id, merchant, total, items, status, order_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trace_id, merchant.lower(), round(total, 2), json.dumps(items), status, order_id,
             now.isoformat()),
        )
        self.db.commit()
        return cur.lastrowid

    def update(self, purchase_id: int, status: str, order_id: Optional[str] = None) -> None:
        self.db.execute("UPDATE purchase SET status = ?, order_id = COALESCE(?, order_id) WHERE id = ?",
                        (status, order_id, purchase_id))
        self.db.commit()

    def spent_since(self, since: datetime, merchant: Optional[str] = None) -> float:
        rows = self.db.execute(
            f"SELECT merchant, total, created_at FROM purchase WHERE status IN "
            f"({','.join('?' * len(COUNTS_TOWARD_LIMITS))})", COUNTS_TOWARD_LIMITS,
        ).fetchall()
        return round(sum(r["total"] for r in rows
                         if datetime.fromisoformat(r["created_at"]) >= since
                         and (merchant is None or r["merchant"] == merchant.lower())), 2)

    def recent(self, limit: int = 10, merchant: Optional[str] = None) -> list:
        rows = self.db.execute(
            "SELECT id, merchant, total, items, status, order_id, created_at FROM purchase "
            "WHERE ? IS NULL OR merchant = ? ORDER BY id DESC LIMIT ?",
            (merchant, merchant.lower() if merchant else None, limit)).fetchall()
        return [{**dict(r), "items": json.loads(r["items"])} for r in rows]


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


class Policy:
    def __init__(self, rules: dict):
        self.rules = rules

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Policy":
        return cls(json.loads(Path(path or DEFAULT_POLICY).read_text(encoding="utf-8")))

    def __getitem__(self, key):
        return self.rules[key]

    @property
    def merchants(self) -> list:
        return list(self.rules["merchants"])

    def merchant(self, name: str) -> Optional[dict]:
        return self.rules["merchants"].get(name.lower())

    # --- helpers -------------------------------------------------------------

    def in_quiet_hours(self, now: datetime) -> bool:
        q = self.rules.get("quiet_hours")
        if not q:
            return False
        start, end, t = _hhmm(q["start"]), _hhmm(q["end"]), now.time()
        return (start <= t or t < end) if start > end else (start <= t < end)

    def quiet_hours_end(self, now: datetime) -> datetime:
        end = _hhmm(self.rules["quiet_hours"]["end"])
        candidate = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        return candidate if candidate > now else candidate + timedelta(days=1)

    def blocked_keyword(self, titles: list) -> Optional[str]:
        for title in titles:
            low = title.lower()
            for kw in self.rules.get("blocked_keywords", []):
                if kw in low:
                    return kw
        return None

    def budget(self, ledger: Ledger, now: datetime, merchant: str) -> dict:
        """What one vendor's card has spent and has left, by Pattie's ledger."""
        m = self.merchant(merchant)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        spent_day = ledger.spent_since(day_start, merchant)
        spent_month = ledger.spent_since(month_start, merchant)
        overall_left = self.rules["overall_monthly_limit"] - ledger.spent_since(month_start)
        return {
            "merchant": merchant.lower(),
            "card": m["card"],
            "spent_today": spent_day,
            "spent_this_month": spent_month,
            "left_today": round(max(m["daily_limit"] - spent_day, 0), 2),
            "left_this_month": round(max(min(m["monthly_limit"] - spent_month, overall_left), 0), 2),
            "per_purchase_limit": m["per_purchase_limit"],
            "monthly_limit": m["monthly_limit"],
            "overall_left_this_month": round(max(overall_left, 0), 2),
        }

    # --- the check -----------------------------------------------------------

    def check(self, total: float, merchant: str, titles: list, ledger: Ledger, now: datetime,
              owner_approved: bool = False, urgent: bool = False) -> dict:
        """Decide whether Pattie may buy. `now` must be timezone-aware, in the owner's local time."""
        out = {"total": round(total, 2), "merchant": merchant.lower()}

        def verdict(result, rule, reason, **extra):
            return {**out, "result": result, "rule": rule, "reason": reason, **extra}

        m = self.merchant(merchant)
        if m is None:
            return verdict("block", "merchants",
                           f"{merchant} has no Pattie card (cards: {', '.join(self.merchants)})")
        budget = self.budget(ledger, now, merchant)
        out["budget"] = budget
        kw = self.blocked_keyword(titles)
        if kw:
            return verdict("block", "blocked_keywords", f"Pattie never buys this kind of product ({kw})")
        if total > m["per_purchase_limit"]:
            return verdict("block", "per_purchase_limit",
                           f"${total:.2f} is over the ${m['per_purchase_limit']} per-purchase limit for {merchant}")
        if total > budget["left_today"]:
            return verdict("block", "daily_limit",
                           f"${total:.2f} is more than the ${budget['left_today']:.2f} left today on the {merchant} card")
        if total > budget["left_this_month"]:
            return verdict("block", "monthly_limit",
                           f"${total:.2f} is more than the ${budget['left_this_month']:.2f} left this month "
                           f"on the {merchant} card")
        if self.in_quiet_hours(now) and not urgent:
            return verdict("wait", "quiet_hours", "it's quiet hours",
                           retry_at=self.quiet_hours_end(now).isoformat())
        if owner_approved:
            return verdict("allow", "owner_approved", "the owner said yes and it's within limits")
        if total <= self.rules["auto_approve_threshold"]:
            return verdict("allow", "auto_approve_threshold",
                           f"at or under the ${self.rules['auto_approve_threshold']} auto-approve threshold")
        return verdict("ask", "needs_yes", "within limits; ask the owner first")

    def price_ok(self, approved_total: float, final_total: float) -> bool:
        """At checkout: the final total may differ from what the owner approved by at most the tolerance."""
        return final_total <= approved_total * (1 + self.rules.get("price_tolerance_pct", 0) / 100)
