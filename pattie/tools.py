"""Pattie's tools, as plain functions. pattie/agent.py calls them directly; pattie/mcp_server.py exposes them over MCP.

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
        """Approve a purchase the owner said yes to. If the spending policy allows it, the agent
        places the order itself in its signed-in browser (off by default: PATTIE_CHECKOUT_MODE=cart_link; the vendor login lived in OpenBot's
        vault), then calls record_order. Otherwise it gets a cart link to send the owner."""
        merchant = merchant.lower()
        verdict = self.check_policy(approved_total, titles, merchant=merchant, owner_approved=True)
        now, loc = self._local_now()
        items = [{"id": a, "title": t} for a, t in zip(asins, titles)]
        link = cart_link(asins) if merchant == "amazon" else None
        if verdict["result"] != "allow":
            self.ledger.record(merchant, approved_total, items, "cart_link", now, trace_id)
            return {"status": "blocked", "policy": verdict, "cart_link": link,
                    "message": f"Not buying: {verdict['reason']}. Send the owner the cart link instead."}
        if os.environ.get("PATTIE_CHECKOUT_MODE", "cart_link") != "agent_browser":
            # Default: the owner places the order. Amazon told this account that AI-agent access
            # violates its Conditions of Use, so the agent never signs in or checks out on Amazon.
            self.ledger.record(merchant, approved_total, items, "cart_link", now, trace_id)
            return {"status": "cart_link", "policy": verdict, "cart_link": link,
                    "message": "Approved by the spending policy. Send the owner this cart link: one tap adds "
                               "the items to their own Amazon cart, and they place the order themselves. "
                               "Don't open Amazon in your browser."}
        limit = round(approved_total * (1 + self.policy["price_tolerance_pct"] / 100), 2)
        purchase_id = self.ledger.record(merchant, approved_total, items, "pending", now, trace_id)
        steps = ([f"Open https://www.amazon.com/dp/{a} in your browser and click Add to Cart." for a in asins]
                 if merchant == "amazon" else ["Open the restaurant page in your browser and add the items."]) + [
            "Go to checkout. If you're asked to sign in, use the saved login from the Vault; if Amazon asks "
            "for a one-time code or a password reset, stop and ask the owner to finish that step.",
            f"Use the default delivery address ({loc['delivery']['packages']}) and the default payment method.",
            f"Before placing the order, read the order total. Place the order only if it is at most ${limit:.2f}. "
            "If it's higher, don't place it; call record_order with status 'failed' and send the cart link.",
            "Place the order, read the order number and arrival date from the confirmation page, then call "
            f"record_order with purchase_id {purchase_id}, the order number and the final total.",
        ]
        return {"status": "approved", "purchase_id": purchase_id, "policy": verdict, "max_total": limit,
                "cart_link": link, "steps": steps,
                "message": "The owner said yes and the spending policy allows it. Place the order now in "
                           "your browser by following the steps, then call record_order."}

    def record_order(self, purchase_id: int, status: str, order_id: Optional[str] = None,
                     final_total: Optional[float] = None) -> dict:
        """After a checkout: status 'placed' (with the order number) or 'failed'."""
        if status not in ("placed", "failed"):
            return {"error": "status must be 'placed' or 'failed'"}
        row = self.ledger.db.execute("SELECT merchant, total FROM purchase WHERE id = ?", (purchase_id,)).fetchone()
        if row is None:
            return {"error": f"no purchase {purchase_id}"}
        led = self.ledger
        led.update(purchase_id, status, order_id)
        if final_total is not None:
            led.db.execute("UPDATE purchase SET total = ? WHERE id = ?", (round(final_total, 2), purchase_id))
            led.db.commit()
        now, _ = self._local_now()
        return {"purchase_id": purchase_id, "status": status, "order_id": order_id,
                "budget": self.policy.budget(led, now, row["merchant"])}

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
            url = os.environ.get("COGNEE_SERVICE_URL", "").strip()
            if url:   # the shared Cognee server, over HTTP: no Cognee install needed here
                from .cognee_client import CogneeClient
                self._memory = CogneeClient(url, user=self.memory_user or "anna_susan")
            else:     # local Cognee (needs the cognee package)
                from .memory import Memory
                self._memory = Memory(self.memory_user) if self.memory_user else Memory()
        return self._memory

    def remember(self, note: str) -> dict:
        self._mem().remember(note)
        return {"saved": note}

    def recall(self, question: str) -> dict:
        return {"answer": self._mem().ask(question) or "nothing known"}
