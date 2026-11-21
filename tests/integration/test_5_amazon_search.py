"""Searching Amazon through Pattie's tools (what the agent calls), via the MCP server.

Offline: saved results go through the real search-and-rank path over MCP.
Live: a real Amazon search through Tilion (sold by Amazon.com only).
"""

import re

import pytest

ASIN = re.compile(r"^[A-Z0-9]{10}$")


def test_find_offers_one_best_pick(client_offline):
    offer = client_offline.call("find_offers", item="power bank", search_query="Anker power bank",
                                category="accessories", specific=True, days_until=1)
    assert offer["mode"] == "one", offer
    pick = offer["picks"][0]
    assert pick["asin"] == "TESTPB0001" and "Anker" in pick["why"]
    assert pick["action"] == "order"


def test_find_offers_three_with_labels_and_sponsored(client_offline):
    offer = client_offline.call("find_offers", item="travel adapter", search_query="universal travel adapter",
                                category="travel", specific=False, days_until=6)
    assert offer["mode"] == "three" and len(offer["picks"]) == 3
    labels = " ".join(p["label"] for p in offer["picks"])
    assert "cheapest" in labels and "best rated" in labels
    sponsored = [p for p in offer["picks"] if p["sponsored"]]
    for p in sponsored:                       # labeled, never ranked first for being sponsored
        assert p["score"] <= offer["picks"][0]["score"]


def test_over_budget_products_are_filtered(client_offline):
    offer = client_offline.call("find_offers", item="travel adapter", search_query="universal travel adapter",
                                category="travel", specific=False, days_until=6)
    assert all(p["price"] <= 30 for p in offer["picks"])          # travel budget in the demo prefs
    assert any("budget" in s for s in offer["skipped"])           # the $79.99 adapter was skipped


def test_no_results_is_reported(client_offline):
    offer = client_offline.call("find_offers", item="unicorn", search_query="nothing matches this",
                                category="other")
    assert offer["mode"] == "none" and offer["picks"] == []


@pytest.mark.live
def test_live_amazon_search(client_live):
    offer = client_live.call("find_offers", item="USB-C cable", search_query="usb c cable",
                             category="accessories", specific=False, days_until=3)
    assert offer["mode"] in ("one", "three"), offer.get("note")
    for p in offer["picks"]:
        assert ASIN.match(p["asin"]), p["asin"]
        assert isinstance(p["price"], float) and p["price"] > 0
        assert p["url"].startswith("https://www.amazon.com/dp/")
