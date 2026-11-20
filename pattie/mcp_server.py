"""Pattie tools MCP server: what the OpenBot agent calls (ARCHITECTURE.md, "Tools").

    .venv/bin/python -m pattie.mcp_server            # stdio (OpenBot starts it as a command)
    .venv/bin/python -m pattie.mcp_server --http     # streamable HTTP on 127.0.0.1:8765

Every tool returns JSON. No tool ever returns a key, cookie or card number.
"""

from __future__ import annotations

import argparse
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .tools import PattieTools

mcp = FastMCP(
    "pattie-tools",
    instructions=(
        "Pattie's shopping tools. Flow: find_offers -> show the owner the picks (photo, price, "
        "rating, why, 'Sponsored' label) -> on a yes, check_policy -> checkout. If a tool returns "
        "a cart_link instead of an order, send the owner that link. Never buy without the "
        "owner's yes. Use next_shopping_time before browsing for anything that isn't urgent."
    ),
)
_tools: Optional[PattieTools] = None


def tools() -> PattieTools:
    global _tools
    if _tools is None:
        _tools = PattieTools.from_env()
    return _tools


@mcp.tool()
def find_offers(item: str, search_query: str, category: str = "other", specific: bool = True,
                days_until: Optional[int] = None, owner_asked: bool = False) -> dict:
    """Search Amazon (sold by Amazon.com) for one item and rank the results.

    Returns mode "one" (a single best pick to offer as yes/no), "three" (cheapest, best rated,
    and one more, each with its trade-off), or "none". Each pick has asin, title, price, rating,
    reviews, image URL, sponsored flag, why it was picked, and action "order" or "cart_link"
    (over the per-purchase limit). category is one of: accessories, travel, toiletries, gifts,
    gift extras, food and drinks, clothing, other. Set owner_asked when the owner named the
    product themselves, so their usual budget doesn't filter it out."""
    return tools().find_offers(item, search_query, category, specific, days_until, owner_asked)


@mcp.tool()
def search_products(queries: list[str]) -> dict:
    """Raw Amazon search results for several queries at once, unranked. Prefer find_offers."""
    return tools().search_products(queries)


@mcp.tool()
def build_cart_link(asins: list[str]) -> dict:
    """An Amazon link that adds all these products to the owner's own cart in one tap."""
    return tools().build_cart_link(asins)


@mcp.tool()
def check_policy(total: float, titles: list[str], merchant: str = "amazon",
                 owner_approved: bool = False, urgent: bool = False) -> dict:
    """Check a purchase against the spending policy before buying.

    result is "allow", "ask" (get the owner's yes first), "wait" (quiet hours; retry_at says
    when) or "block" (send a cart link instead). merchant is "amazon" or "doordash": each has
    its own Privacy.com card and limits. Includes what's left of that card's budget."""
    return tools().check_policy(total, titles, merchant, owner_approved, urgent)


@mcp.tool()
def checkout(asins: list[str], approved_total: float, titles: list[str], merchant: str = "amazon",
             trace_id: Optional[str] = None) -> dict:
    """Buy what the owner approved, with that vendor's Privacy.com card. Only call after a yes.

    merchant is "amazon" (asins are ASINs) or "doordash". Until agent checkout is connected,
    this returns a fallback: an Amazon cart link, or for DoorDash an instruction to send the
    restaurant link so the owner orders in the app with Pattie's DoorDash card."""
    return tools().checkout(asins, approved_total, titles, merchant, trace_id)


@mcp.tool()
def card_status(merchant: Optional[str] = None) -> dict:
    """Each vendor card (amazon, doordash): spend today and this month, what's left, recent
    purchases, and the card's state and limit at Privacy.com. Omit merchant for all cards."""
    return tools().card_status(merchant)


@mcp.tool()
def freeze_card(merchant: str) -> dict:
    """Pause a vendor's Privacy.com card (amazon or doordash) so every charge is declined.
    Use when the owner says "freeze the card"; ask which one if they didn't say."""
    return tools().freeze_card(merchant)


@mcp.tool()
def unfreeze_card(merchant: str) -> dict:
    """Reopen a paused vendor card (amazon or doordash). Only when the owner asks."""
    return tools().unfreeze_card(merchant)


@mcp.tool()
def where_am_i() -> dict:
    """The owner's current city, time zone, local time, and where packages and food should go."""
    return tools().where_am_i()


@mcp.tool()
def set_location(city: str, timezone: str, until: Optional[str] = None, message: str = "") -> dict:
    """Save where the owner says they are, e.g. "I'm in Austin until Friday".

    timezone is an IANA name such as America/Chicago. until is an ISO date or time (local to
    that time zone if no offset). message is the owner's words, kept as the source."""
    return tools().set_location(city, timezone, until, message)


@mcp.tool()
def next_shopping_time(urgent: bool = False) -> dict:
    """When to browse Amazon next, drawn at random from the shopping-hours curve in the owner's
    local time. urgent=True means now (ASAP items, or the owner is asking right now)."""
    return tools().next_shopping_time(urgent)


@mcp.tool()
def remember(note: str) -> dict:
    """Save a fact about the owner to long-term memory (Cognee): a preference, something they
    own, a decision and why."""
    return tools().remember(note)


@mcp.tool()
def recall(question: str) -> dict:
    """Ask long-term memory (Cognee) about the owner."""
    return tools().recall(question)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Pattie tools MCP server")
    p.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)
    if args.http:
        mcp.settings.host, mcp.settings.port = "127.0.0.1", args.port  # never on a public interface
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
