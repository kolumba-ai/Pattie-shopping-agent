"""Tests for the spending policy, location and the MCP tools (made-up TEST ASINs, no network).

    .venv/bin/python -m pytest tests/  or  .venv/bin/python tests/test_tools.py
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pattie.location import Locator  # noqa: E402
from pattie.policy import Ledger, Policy  # noqa: E402
from pattie.store import Store  # noqa: E402
from pattie.tools import PattieTools  # noqa: E402

LA = ZoneInfo("America/Los_Angeles")
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=LA)
PREFS = json.loads((ROOT / "demo/anna_susan_prefs.json").read_text())
POLICY = Policy.load()


def tmpdb() -> str:
    return tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False).name


def product(asin, title, price, rating=4.6, reviews="5,000", sponsored=False):
    return {"asin": asin, "title": title, "price": price, "rating": str(rating), "reviews": reviews,
            "sponsored": sponsored, "url": f"https://www.amazon.com/dp/{asin}"}


FAKE_RESULTS = {
    "Anker power bank": [
        product("TESTPB0001", "Anker Power Bank 10000mAh PowerCore Slim", 29.99, 4.7, "52,310"),
        product("TESTPB0002", "Generic 20000mAh Power Bank", 24.99, 4.3, "3,105", sponsored=True),
        product("TESTPB0003", "Brand X Power Bank 10000mAh", 27.50, 4.4, "1,980"),
    ],
}


def make_tools(clock=NOON) -> PattieTools:
    return PattieTools(db_path=tmpdb(), prefs=PREFS, policy=POLICY, default_tz="America/Los_Angeles",
                       home_city="San Francisco",
                       search=lambda qs: {q: FAKE_RESULTS.get(q, []) for q in qs},
                       clock=lambda: clock, memory_user=None)


# --- policy -------------------------------------------------------------------

def test_policy_blocks():
    led = Ledger(tmpdb())
    assert POLICY.check(20, "ebay", ["x"], led, NOON)["result"] == "block"
    v = POLICY.check(25, "amazon", ["Amazon eGift Card"], led, NOON)
    assert v["result"] == "block" and v["rule"] == "blocked_keywords"
    v = POLICY.check(80, "amazon", ["Laptop power bank"], led, NOON, owner_approved=True)
    assert v["result"] == "block" and v["rule"] == "per_purchase_limit"


def test_policy_daily_and_monthly_limits():
    led = Ledger(tmpdb())
    led.record("amazon", 70, [], "placed", NOON - timedelta(hours=2))
    led.record("amazon", 70, [], "placed", NOON - timedelta(hours=1))
    v = POLICY.check(20, "amazon", ["snacks"], led, NOON, owner_approved=True)
    assert v["result"] == "block" and v["rule"] == "daily_limit", v      # 150 - 140 = 10 left
    led.record("amazon", 70, [], "failed", NOON)                         # failed doesn't count
    led.record("amazon", 70, [], "cart_link", NOON)                      # nor does a cart link
    assert POLICY.check(9, "amazon", ["snacks"], led, NOON, owner_approved=True)["result"] == "allow"
    month = Ledger(tmpdb())
    for d in range(1, 6):                                                # 5 x $75 earlier this month
        month.record("amazon", 75, [], "placed", NOON.replace(day=d))
    v = POLICY.check(30, "amazon", ["x"], month, NOON, owner_approved=True)
    assert v["result"] == "block" and v["rule"] == "monthly_limit", v    # 400 - 375 = 25 left


def test_policy_ask_allow_wait():
    led = Ledger(tmpdb())
    assert POLICY.check(29.99, "amazon", ["Anker"], led, NOON)["result"] == "ask"
    assert POLICY.check(29.99, "amazon", ["Anker"], led, NOON, owner_approved=True)["result"] == "allow"
    late = NOON.replace(hour=23, minute=30)
    v = POLICY.check(29.99, "amazon", ["Anker"], led, late, owner_approved=True)
    assert v["result"] == "wait" and v["retry_at"].startswith("2026-11-21T07:00"), v
    assert POLICY.check(29.99, "amazon", ["Anker"], led, late, owner_approved=True, urgent=True)["result"] == "allow"
    assert POLICY.in_quiet_hours(NOON.replace(hour=6, minute=59))
    assert not POLICY.in_quiet_hours(NOON.replace(hour=7))


def test_price_tolerance():
    assert POLICY.price_ok(30.00, 31.50)       # +5%
    assert not POLICY.price_ok(30.00, 31.51)


# --- location -----------------------------------------------------------------

def test_location_priority():
    db = tmpdb()
    loc = Locator(db, "America/Los_Angeles", "San Francisco")
    assert loc.where_am_i(NOON)["source"] == "default"

    store = Store(db)  # the watcher's event table, in the same database
    store.upsert({"id": "e1", "status": "confirmed", "fingerprint": "f", "title": "Hackathon SF",
                  "start": "2026-11-20T08:00:00-08:00", "end": "2026-11-20T21:00:00-08:00",
                  "all_day": False, "location": "Shack15, Ferry Building"})
    w = loc.where_am_i(NOON)
    assert w["source"] == "calendar_event" and w["delivery"]["food"] == "Shack15, Ferry Building"

    loc.set_from_chat("Austin", "America/Chicago", NOON, until=NOON + timedelta(days=2),
                      message="I'm in Austin until Sunday")
    w = loc.where_am_i(NOON)
    assert w["source"] == "chat" and w["timezone"] == "America/Chicago"
    assert w["local_time"].startswith("2026-11-20T14:00")
    assert loc.where_am_i(NOON + timedelta(days=3))["source"] == "default"  # chat fact expired


# --- tools --------------------------------------------------------------------

def test_find_offers_and_checkout_fallback():
    t = make_tools()
    offer = t.find_offers("power bank", "Anker power bank", "accessories", specific=True, days_until=1)
    assert offer["mode"] == "one" and offer["picks"][0]["asin"] == "TESTPB0001", offer
    pick = offer["picks"][0]
    out = t.checkout([pick["asin"]], pick["price"], [pick["title"]], trace_id="t-1")
    assert out["status"] == "cart_link" and "ASIN.1=TESTPB0001" in out["cart_link"]
    assert out["policy"]["result"] == "allow"
    status = t.card_status()
    assert status["recent_purchases"][0]["status"] == "cart_link"
    assert status["spent_today"] == 0           # nothing was charged
    blocked = t.checkout(["TESTXX0001"], 120.0, ["Big thing"])
    assert blocked["status"] == "blocked" and blocked["policy"]["rule"] == "per_purchase_limit"


def test_set_location_and_shopping_time():
    t = make_tools(clock=NOON.astimezone(ZoneInfo("UTC")))
    w = t.set_location("New York", "America/New_York", until="2026-11-25", message="NY trip")
    assert w["city"] == "New York" and w["valid_until"].startswith("2026-11-25T00:00:00-05:00")
    urgent = t.next_shopping_time(urgent=True)
    assert urgent["wait_minutes"] == 0 and urgent["timezone"] == "America/New_York"
    later = t.next_shopping_time()
    start = datetime.fromisoformat(later["start_at"])
    assert start.tzinfo is not None and (start.hour >= 7 or start.hour == 0), later  # never 1-7 AM


def test_mcp_server_lists_tools():
    try:
        import asyncio
        from pattie import mcp_server
    except ImportError as e:  # mcp not installed
        print(f"skipped: {e}")
        return
    names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
    expected = {"find_offers", "search_products", "build_cart_link", "check_policy", "checkout",
                "card_status", "where_am_i", "set_location", "next_shopping_time", "remember", "recall"}
    assert expected <= names, names


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
