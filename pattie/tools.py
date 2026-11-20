"""Pattie's tools, as plain functions. pattie/mcp_server.py exposes them to OpenBot over MCP.

Kept free of MCP so they can be tested directly:

    tools = PattieTools.from_env()
    tools.find_offers("power bank", "Anker power bank", "accessories", specific=True, days_until=1)
    tools.check_policy(29.99, ["Anker Power Bank"], owner_approved=True)

Secrets (Tilion key, later the card and Amazon session from 1Password) are read inside the
tools and never returned to the agent.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .location import Locator
from .policy import Ledger, Policy
from .privacy_cards import PrivacyCards, PrivacyError
from .ranking import cart_link, rank

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import shopping_hours  # noqa: E402  (repo root module)


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    """Nothing loads .env for us; read it without overriding real environment variables."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class PattieTools:
    db_path: str
    prefs: dict
    policy: Policy
    default_tz: str
    home_city: str
    search: Callable[[list], dict]                 # queries -> {query: [products]}
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    memory_user: Optional[str] = None
    cards: Optional[PrivacyCards] = None           # None until PRIVACY_API_KEY is set
    _memory: object = field(default=None, repr=False)

    @classmethod
    def from_env(cls, search: Optional[Callable] = None) -> "PattieTools":
        load_env_file()
        prefs_path = os.environ.get("PATTIE_PREFS", str(PROJECT_ROOT / "demo/anna_susan_prefs.json"))
        if search is None:
            from .offers import amazon_live_search, saved_search
            saved = os.environ.get("PATTIE_SEARCH_RESULTS")  # on-stage fallback: saved results
            search = saved_search(saved) if saved else amazon_live_search
        return cls(
            db_path=os.environ.get("PATTIE_DB", str(PROJECT_ROOT / "pattie.sqlite3")),
            prefs=json.loads(Path(prefs_path).read_text(encoding="utf-8")),
            policy=Policy.load(os.environ.get("PATTIE_POLICY")),
            default_tz=os.environ.get("PATTIE_TZ", "America/Los_Angeles"),
            home_city=os.environ.get("PATTIE_HOME_CITY", "San Francisco"),
            search=search,
            memory_user=os.environ.get("PATTIE_USER", "anna_susan"),
            cards=PrivacyCards.from_env(),
        )

    # --- shared state --------------------------------------------------------

    @property
    def ledger(self) -> Ledger:
        return Ledger(self.db_path)

    @property
    def locator(self) -> Locator:
        return Locator(self.db_path, self.default_tz, self.home_city)

    def _local_now(self) -> tuple[datetime, dict]:
        loc = self.locator.where_am_i(self.clock())
        return self.clock().astimezone(ZoneInfo(loc["timezone"])), loc

    # --- products ------------------------------------------------------------

    def search_products(self, queries: list) -> dict:
        """Raw Amazon results (sold by Amazon.com), one Tilion residential session for all queries."""
        return self.search(queries)

    def find_offers(self, item: str, search_query: str, category: str = "other",
                    specific: bool = True, days_until: Optional[int] = None,
                    owner_asked: bool = False) -> dict:
        """Search and rank for one item: one best pick or three options (PRD flow 4)."""
        results = self.search([search_query]).get(search_query, [])
        spec = {"item": item, "search_query": search_query, "category": category, "specific": specific}
        return rank(spec, results, self.prefs, days_until, owner_asked=owner_asked)

    def build_cart_link(self, asins: list) -> dict:
        return {"cart_link": cart_link(asins), "items": len(asins)}

    # --- money ---------------------------------------------------------------

    def check_policy(self, total: float, titles: list, merchant: str = "amazon",
                     owner_approved: bool = False, urgent: bool = False) -> dict:
        now, _ = self._local_now()
        return self.policy.check(total, merchant, titles, self.ledger, now,
                                 owner_approved=owner_approved, urgent=urgent)

    def card_status(self, merchant: Optional[str] = None) -> dict:
        """Per vendor card: Pattie's ledger and policy limits, plus the card itself at Privacy.com."""
        now, _ = self._local_now()
        out = {}
        for m in ([merchant.lower()] if merchant else self.policy.merchants):
            if self.policy.merchant(m) is None:
                return {"error": f"no Pattie card for {m}", "cards": self.policy.merchants}
            entry = {**self.policy.budget(self.ledger, now, m),
                     "recent_purchases": self.ledger.recent(5, m)}
            if self.cards is None:
                entry["privacy"] = "not connected yet (no PRIVACY_API_KEY): limits shown are Pattie's policy only"
            else:
                try:
                    entry["privacy"] = self.cards.status(m)
                except PrivacyError as e:
                    entry["privacy"] = f"unavailable: {e}"
            out[m] = entry
        return out

    def _set_card(self, merchant: str, pause: bool) -> dict:
        if self.policy.merchant(merchant) is None:
            return {"error": f"no Pattie card for {merchant}", "cards": self.policy.merchants}
        if self.cards is None:
            return {"error": "Privacy.com isn't connected yet (no PRIVACY_API_KEY). "
                             "Pause the card in the Privacy app instead."}
        try:
            card = self.cards.pause(merchant) if pause else self.cards.resume(merchant)
        except PrivacyError as e:
            return {"error": str(e)}
        return {"merchant": merchant.lower(), "state": card["state"], "last_four": card["last_four"]}

    def freeze_card(self, merchant: str) -> dict:
        return self._set_card(merchant, pause=True)

    def unfreeze_card(self, merchant: str) -> dict:
        return self._set_card(merchant, pause=False)

    def checkout(self, asins: list, approved_total: float, titles: list,
                 merchant: str = "amazon", trace_id: Optional[str] = None) -> dict:
        """Place an order with the vendor's Privacy card. Agent checkout isn't connected yet, so
        this always falls back (Amazon: a cart link; DoorDash: the owner orders in the app, paying
        with the DoorDash card saved there), after the same policy check a real checkout does."""
        merchant = merchant.lower()
        verdict = self.check_policy(approved_total, titles, merchant=merchant, owner_approved=True)
        now, _ = self._local_now()
        items = [{"id": a, "title": t} for a, t in zip(asins, titles)]
        # Logged for tracing; status cart_link doesn't count toward limits (nothing was charged).
        self.ledger.record(merchant, approved_total, items, "cart_link", now, trace_id)
        link = cart_link(asins) if merchant == "amazon" else None
        fallback = ("Send the owner the cart link." if link else
                    "Send the owner the restaurant link with the order written out; they pay in the "
                    "DoorDash app with Pattie's DoorDash card.")
        if verdict["result"] != "allow":
            return {"status": "blocked", "policy": verdict, "cart_link": link,
                    "message": f"Not buying: {verdict['reason']}. {fallback}"}
        return {"status": "fallback", "policy": verdict, "cart_link": link,
                "card": self.policy.merchant(merchant)["card"],
                "message": f"Agent checkout isn't connected yet. {fallback}"}

    # --- time and place ------------------------------------------------------

    def where_am_i(self) -> dict:
        return self.locator.where_am_i(self.clock())

    def set_location(self, city: str, timezone_name: str, until: Optional[str] = None,
                     message: str = "") -> dict:
        until_dt = datetime.fromisoformat(until) if until else None
        if until_dt and until_dt.tzinfo is None:
            until_dt = until_dt.replace(tzinfo=ZoneInfo(timezone_name))
        return self.locator.set_from_chat(city, timezone_name, self.clock(), until_dt, message)

    def next_shopping_time(self, urgent: bool = False) -> dict:
        """When to browse Amazon next (Context.md, Rules 2 and 3). Urgent means now."""
        now, loc = self._local_now()
        weight = shopping_hours.shopping_weight(now.hour + now.minute / 60)
        if urgent:
            at = now
        else:
            at = shopping_hours.pick_start_time(loc["timezone"], after=now, within_h=24)
        return {"start_at": at.isoformat(timespec="seconds"), "timezone": loc["timezone"],
                "weight_now": round(weight, 2), "urgent": urgent,
                "wait_minutes": max(0, round((at - now) / timedelta(minutes=1)))}

    # --- memory (Cognee, through pattie/memory.py) ---------------------------

    def _mem(self):
        if self._memory is None:
            from .memory import Memory  # heavy import; only when memory is used
            self._memory = Memory(self.memory_user) if self.memory_user else Memory()
        return self._memory

    def remember(self, note: str) -> dict:
        self._mem().remember(note)
        return {"saved": note}

    def recall(self, question: str) -> dict:
        return {"answer": self._mem().ask(question) or "nothing known"}
