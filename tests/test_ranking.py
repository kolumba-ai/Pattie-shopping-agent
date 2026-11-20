"""Ranking tests with made-up search results (TEST ASINs, not real products).

    .venv/bin/python -m pytest tests/  or  .venv/bin/python tests/test_ranking.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pattie.ranking import format_offer, rank  # noqa: E402

PREFS = json.loads((Path(__file__).resolve().parent.parent / "demo/anna_susan_prefs.json").read_text())


def r(asin, title, price, rating, reviews, sponsored=False, **kw):
    return {"asin": asin, "title": title, "price": price, "rating": str(rating),
            "reviews": reviews, "sponsored": sponsored, "url": f"https://www.amazon.com/dp/{asin}", **kw}


POWER_BANKS = [
    r("TESTPB0001", "Anker Power Bank 10000mAh, PowerCore Slim Portable Charger", 29.99, 4.7, "52,310"),
    r("TESTPB0002", "Generic 20000mAh Portable Charger Power Bank", 24.99, 4.3, "3,105", sponsored=True),
    r("TESTPB0003", "Brand X Power Bank 10000mAh USB-C", 27.50, 4.4, "1,980"),
    r("TESTPB0004", "Premium 26800mAh Laptop Power Bank 140W", 79.99, 4.6, "8,200"),
]
TOILETRIES = [
    r("TESTTO0001", "Unscented Travel Size Toiletries Kit, TSA Approved, 12 pcs", 18.99, 4.5, "2,410"),
    r("TESTTO0002", "Fragrance-Free Travel Toiletry Set for Sensitive Skin", 24.50, 4.7, "960", sponsored=True),
    r("TESTTO0003", "Unscented TSA Travel Bottles and Toiletries Bundle", 14.99, 4.2, "5,300", delivery_days=0),
    r("TESTTO0004", "Lavender Scented Travel Toiletries Gift Set", 16.99, 4.8, "7,000"),
]
DRINKS = [
    r("TESTDR0001", "Red Bull Energy Drink, 12 Pack", 22.99, 4.8, "40,000"),
    r("TESTDR0002", "La Colombe Draft Latte Cold Brew Coffee, Oat Milk, 9 oz cans, 12 pack", 23.99, 4.5, "12,000"),
]
GARDEN = [
    r("TESTGA0001", "Premium Stainless Steel Garden Tool Set, 9 Piece with Tote", 89.00, 4.7, "4,100"),
]


def test_known_budget_and_brand_gives_one_pick():
    o = rank({"item": "power bank", "search_query": "Anker power bank", "category": "accessories",
              "specific": True}, POWER_BANKS, PREFS, days_until=1)
    assert o["mode"] == "one", o
    assert o["picks"][0]["asin"] == "TESTPB0001"          # Anker, within $40
    assert any("over the $40 budget" in s for s in o["skipped"])  # $79.99 one dropped


def test_matter_of_taste_gives_three_options_with_sponsored_label():
    o = rank({"item": "travel toiletries", "search_query": "unscented travel toiletries kit",
              "category": "toiletries", "specific": False}, TOILETRIES, PREFS, days_until=6)
    assert o["mode"] == "three", o
    labels = {p["asin"]: p["label"] for p in o["picks"]}
    assert len(o["picks"]) == 3
    assert labels["TESTTO0003"] == "cheapest and arrives soonest"   # truly both
    assert labels["TESTTO0002"] == "best rated"
    assert labels["TESTTO0001"] == "good all-rounder"   # not "most reviewed": TESTTO0003 has more
    assert "TESTTO0004" not in [p["asin"] for p in o["picks"]]  # scented: filtered
    assert any(p["sponsored"] for p in o["picks"])            # sponsored shown, labeled


def test_no_delivery_dates_never_claims_arrives_soonest():
    undated = [dict(p, delivery_days=None) for p in TOILETRIES]
    o = rank({"item": "travel toiletries", "search_query": "unscented travel toiletries kit",
              "category": "toiletries", "specific": False}, undated, PREFS, days_until=6)
    assert not any("arrives soonest" in p["label"] for p in o["picks"])
    assert len(o["picks"]) == 3


def test_avoided_items_are_filtered():
    o = rank({"item": "cold brew", "search_query": "cold brew coffee cans",
              "category": "food and drinks", "specific": True}, DRINKS, PREFS, days_until=1)
    assert [p["asin"] for p in o["picks"]] == ["TESTDR0002"]   # no Red Bull


def test_owner_request_over_limit_becomes_cart_link():
    item = {"item": "gardening tool set", "search_query": "garden tool set gift",
            "category": "gifts", "specific": True}
    assert rank(item, GARDEN, PREFS, days_until=21)["mode"] == "none"     # over $60 gift budget
    o = rank(item, GARDEN, PREFS, days_until=21, owner_asked=True)       # she asked for it
    assert o["picks"][0]["action"] == "cart_link"                         # over $75 limit
    assert o["picks"][0]["cart_link"].startswith("https://www.amazon.com/gp/aws/cart/add.html?ASIN.1=TESTGA0001")


def test_decision_to_offers():
    from pattie.offers import make_offers
    decision = {"needs_products": True, "days_until": 1, "items": [
        {"item": "power bank", "search_query": "Anker power bank", "category": "accessories", "specific": True},
        {"item": "cold brew", "search_query": "cold brew coffee cans", "category": "food and drinks", "specific": False},
    ]}
    fake_amazon = lambda qs: {"Anker power bank": POWER_BANKS, "cold brew coffee cans": DRINKS}
    offers = make_offers(decision, PREFS, fake_amazon)
    assert [o["mode"] for o in offers] == ["one", "three"]
    assert offers[1]["picks"][0]["asin"] == "TESTDR0002"   # Red Bull filtered out


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
    print(format_offer(rank({"item": "travel toiletries", "search_query": "unscented travel toiletries kit",
                             "category": "toiletries", "specific": False}, TOILETRIES, PREFS, 6)))
    print(format_offer(rank({"item": "power bank", "search_query": "Anker power bank",
                             "category": "accessories", "specific": True}, POWER_BANKS, PREFS, 1)))
