"""From a decision to product offers: search Amazon for each item, then rank.

    offers = make_offers(decision, prefs, search)     # one offer per suggested item

`search(queries) -> {query: [products]}` can be:
- amazon_live_search: live Amazon through Tilion, reusing amazon_search.py (needs TILION_API_KEY)
- saved_search(path): a JSON file of earlier results, {query: [products]} like
  amazon_search.py writes. Useful for tests and as an on-stage fallback.
"""

import json
from pathlib import Path
from urllib.parse import quote_plus

from .ranking import rank

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def amazon_live_search(queries: list) -> dict:
    """One residential Tilion session for all queries, filtered to "sold by Amazon.com"."""
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))
    from amazon_search import AMAZON_SELLER, parse  # Mauricio's parser
    from tilion import Tilion

    t, out = Tilion(), {}
    with t.session(residential=True) as sid:
        t.fetch(sid, "https://www.amazon.com/")  # warm up cookies like a normal visitor
        for q in queries:
            url = f"https://www.amazon.com/s?k={quote_plus(q)}&rh=p_6%3A{AMAZON_SELLER}"
            r = t.fetch(sid, url, include_html=True)
            out[q] = parse(r.get("html") or "") if r.get("status") == "ok" else []
    return out


def saved_search(path: str):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return lambda queries: {q: data.get(q, []) for q in queries}


def make_offers(decision: dict, prefs: dict, search) -> list:
    if not decision.get("needs_products") or not decision.get("items"):
        return []
    queries = [i["search_query"] for i in decision["items"] if i.get("search_query")]
    results = search(queries)
    return [rank(i, results.get(i.get("search_query"), []), prefs, decision.get("days_until"))
            for i in decision["items"]]
