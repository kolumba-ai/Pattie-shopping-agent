"""The Amazon cart: adding to it, reading it, deleting from it.

The cart is the owner's Amazon cart. Pattie doesn't keep one.

- Add: Pattie builds Amazon's official add-to-cart link (build_cart_link, or checkout after a yes).
  One tap by the owner puts the items in their own Amazon cart. Tested here: the link holds exactly
  the approved items and quantities, and checkout only hands it over within the spending policy.
- Read and delete: these need Pattie signed in to the owner's Amazon account. Amazon flagged
  signed-in agent access ("Continued access by an unauthorized AI agent violates Amazon's
  Conditions of Use"), so they're not built. The tests document that and are skipped until Amazon
  allows agent access (for example an official API).
"""

import os
from urllib.parse import parse_qs, urlparse

import pytest

NOT_ALLOWED = ("Reading or deleting items in the owner's Amazon cart needs signed-in access, which "
               "Amazon flagged as unauthorized AI-agent access. Not built until Amazon allows it.")


def _link_items(link):
    q = parse_qs(urlparse(link).query)
    n = len([k for k in q if k.startswith("ASIN.")])
    return {q[f"ASIN.{i}"][0]: int(q[f"Quantity.{i}"][0]) for i in range(1, n + 1)}


def test_add_to_amazon_cart_link(client_offline):
    out = client_offline.call("build_cart_link", asins=["TESTPB0001", "TESTTA0001"])
    assert out["items"] == 2
    assert urlparse(out["cart_link"]).netloc == "www.amazon.com"
    assert urlparse(out["cart_link"]).path == "/gp/aws/cart/add.html"
    assert _link_items(out["cart_link"]) == {"TESTPB0001": 1, "TESTTA0001": 1}


def test_checkout_hands_over_add_to_cart_link_within_policy(client_offline):
    out = client_offline.call("checkout", asins=["TESTPB0001"], approved_total=29.99,
                              titles=["Anker Power Bank"])
    assert out["status"] == "cart_link" and out["policy"]["result"] == "allow"
    assert _link_items(out["cart_link"]) == {"TESTPB0001": 1}
    status = client_offline.call("card_status", merchant="amazon")["amazon"]
    assert status["spent_today"] == 0                      # nothing charged: the owner places the order


def test_checkout_over_policy_still_gives_link_but_blocks(client_offline):
    out = client_offline.call("checkout", asins=["TESTTA0003"], approved_total=79.99,
                              titles=["Premium Travel Adapter"])
    assert out["status"] == "blocked" and out["policy"]["rule"] == "per_purchase_limit"
    assert _link_items(out["cart_link"]) == {"TESTTA0003": 1}


def test_checkout_never_uses_the_agent_browser_by_default(client_offline):
    assert os.environ.get("PATTIE_CHECKOUT_MODE", "cart_link") == "cart_link"
    out = client_offline.call("checkout", asins=["TESTPB0001"], approved_total=29.99, titles=["Anker Power Bank"])
    assert "steps" not in out                              # no instructions to sign in or check out


@pytest.mark.skip(reason=NOT_ALLOWED)
def test_read_amazon_cart():
    """Would list the items in the owner's Amazon cart."""


@pytest.mark.skip(reason=NOT_ALLOWED)
def test_delete_from_amazon_cart():
    """Would remove an item from the owner's Amazon cart."""


@pytest.mark.live
def test_live_search_result_link(client_live):
    offer = client_live.call("find_offers", item="USB-C cable", search_query="usb c cable",
                             category="accessories", specific=False, days_until=3)
    assert offer["picks"], offer.get("note")
    p = offer["picks"][0]
    link = client_live.call("build_cart_link", asins=[p["asin"]])["cart_link"]
    assert _link_items(link) == {p["asin"]: 1}             # the link isn't opened: that's the owner's tap
