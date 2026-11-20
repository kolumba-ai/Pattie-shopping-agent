"""Pick what to offer from live Amazon search results (PRD flow 4: one option or three).

The flow:
  1. The classifier suggests item types, each with an Amazon `search_query` and a `category`.
  2. amazon_search.py (Tilion) runs the query on Amazon and returns products shaped
     {asin, title, price, rating, reviews, image, url, sponsored}.
  3. rank() decides what Pattie shows:

    offer = rank(item, results, prefs, days_until=1)
    offer["mode"]  -> "one" (single best pick, yes/no), "three" (cheapest / best rated /
                      arrives soonest, or most reviewed when there are no delivery
                      estimates), or "none" if nothing fits
    offer["picks"] -> products with label, reason, arrival, Sponsored flag and action

Rules (PRD flow 4 and the spending policy in ARCHITECTURE.md):
- Drop products that miss the owner's must-haves for the category (e.g. "unscented" for
  toiletries), contain something they avoid (e.g. energy drinks), cost more than their budget
  for the category, or (when the result has a `delivery_days` field) arrive too late.
- Score = preferred brand + rating + review count + price + delivery. Sponsored results get
  no boost: they're labeled and ranked like everything else.
- One pick when the budget is known, the item is specific, and the top product scores clearly
  higher than the next. Otherwise three options, each with its trade-off.
- Over the per-purchase limit: offered as a cart link instead of an order.
"""

import math
from typing import Optional
from urllib.parse import urlencode

CLEAR_WIN = 0.12         # score gap that makes the top product "clearly" best
PER_PURCHASE_LIMIT = 75  # ARCHITECTURE.md spending policy, example value

# Words in a product title that count as having a tag (Amazon titles vary).
TAG_SYNONYMS = {
    "unscented": ["unscented", "fragrance free", "fragrance-free", "scent free", "scent-free"],
    "vegetarian": ["vegetarian", "vegan", "plant based", "plant-based"],
    "energy drink": ["energy drink", "red bull", "monster energy", "celsius", "bang energy"],
    "meat": ["jerky", "beef", "chicken", "pork", "turkey", "salami", "bacon"],
}


def _has(title: str, tag: str) -> bool:
    t = title.lower()
    return any(w in t for w in TAG_SYNONYMS.get(tag.lower(), [tag.lower()]))


def _budget(prefs: dict, category: str) -> Optional[float]:
    b = prefs.get("budgets", {}).get(category)
    return b.get("max") if isinstance(b, dict) else b


def _brand(p: dict, prefs: dict) -> Optional[str]:
    title = p["title"].lower()
    for b in prefs.get("preferred_brands", []):
        if b.lower() in title or (p.get("brand") or "").lower() == b.lower():
            return b
    return None


def _reviews(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    v = str(value).replace(",", "").strip().upper()
    mult = 1000 if v.endswith("K") else 1
    try:
        return int(float(v.rstrip("K")) * mult)
    except ValueError:
        return 0


def _price(p) -> Optional[float]:
    try:
        return float(p.get("price"))
    except (TypeError, ValueError):
        return None


def _excluded(p: dict, prefs: dict, category: str, budget, days_until,
              owner_asked: bool = False) -> Optional[str]:
    if _price(p) is None:
        return "no price"
    for needed in prefs.get("require_tags", {}).get(category, []):
        if not _has(p["title"], needed):
            return f"not {needed}"
    for bad in prefs.get("avoid_tags", []):
        if _has(p["title"], bad):
            return f"owner avoids {bad}"
    if budget and _price(p) > budget and not owner_asked:
        return f"over the ${budget:.0f} budget"
    if days_until is not None and p.get("delivery_days") is not None:
        if p["delivery_days"] > max(days_until - 1, 0):  # must arrive the day before
            return "arrives too late"
    return None


def _score(p: dict, prefs: dict, budget: Optional[float], cap: float) -> float:
    rating_s = max(0.0, min(1.0, (float(p.get("rating") or 0) - 3.5) / 1.5))
    reviews_s = min(1.0, math.log10(_reviews(p.get("reviews")) + 1) / 5)  # 100k = 1.0
    price_s = max(0.0, 1 - _price(p) / (budget or cap)) if (budget or cap) else 0.0
    days = p.get("delivery_days")
    delivery_s = 1 - min(3 if days is None else days, 7) / 7
    brand_s = 1.0 if _brand(p, prefs) else 0.0
    return round(0.30 * rating_s + 0.15 * reviews_s + 0.20 * price_s
                 + 0.10 * delivery_s + 0.25 * brand_s, 3)


def _arrives(days) -> str:
    if days is None:
        return "check at checkout"
    return {0: "today", 1: "tomorrow"}.get(days, f"in {days} days")


def _pick(p: dict, label: str, why: str) -> dict:
    over = _price(p) > PER_PURCHASE_LIMIT
    return {
        "asin": p["asin"], "title": p["title"], "price": _price(p),
        "rating": p.get("rating"), "reviews": p.get("reviews"),
        "arrives": _arrives(p.get("delivery_days")), "sponsored": bool(p.get("sponsored")),
        "image": p.get("image"), "url": p.get("url") or f"https://www.amazon.com/dp/{p['asin']}",
        "label": label, "why": why, "score": p["_score"],
        "action": "cart_link" if over else "order",
        "cart_link": cart_link([p["asin"]]) if over else None,
    }


def rank(item: dict, results: list, prefs: dict, days_until: Optional[int] = None,
         owner_asked: bool = False) -> dict:
    """item: one suggestion from the classifier, {item, reason, search_query, category,
    specific}. results: Amazon search results for item["search_query"].
    owner_asked: the owner asked for this product themselves, so their budget doesn't
    filter it out (the per-purchase limit still applies: over it, it becomes a cart link)."""
    name, category = item["item"], item.get("category", "other")
    budget = _budget(prefs, category)
    skipped, ok = [], []
    for p in results:
        why_not = _excluded(p, prefs, category, budget, days_until, owner_asked)
        if why_not:
            skipped.append(f"{p['title'][:40]}: {why_not}")
        else:
            ok.append(dict(p))
    base = {"item": name, "category": category, "search_query": item.get("search_query"),
            "skipped": skipped}
    if not ok:
        return {**base, "mode": "none", "picks": [],
                "note": "nothing on Amazon fits" if results else "no search results"}

    cap = max(_price(p) for p in ok)
    for p in ok:
        p["_score"] = _score(p, prefs, budget, cap)
    ok.sort(key=lambda p: p["_score"], reverse=True)

    top = ok[0]
    gap = top["_score"] - (ok[1]["_score"] if len(ok) > 1 else 0)
    specific = item.get("specific", True)
    if budget and specific and (len(ok) == 1 or gap >= CLEAR_WIN):
        reasons = []
        brand = _brand(top, prefs)
        if brand:
            reasons.append(f"{brand}, a brand you like")
        reasons.append(f"${_price(top):.2f}, within your ${budget:.0f} budget")
        reasons.append(f"{top.get('rating')} stars from {top.get('reviews')} reviews")
        return {**base, "mode": "one", "picks": [_pick(top, "best pick", "; ".join(reasons))]}

    # Each trade-off label goes to the product that is truly best at it; one product can
    # carry two labels. Remaining slots are filled by fit, labeled for what they are.
    winners = [
        ("cheapest", min(ok, key=lambda p: (_price(p), -p["_score"])),
         lambda p: f"${_price(p):.2f}, the lowest price"),
        ("best rated", min(ok, key=lambda p: (-float(p.get("rating") or 0), -_reviews(p.get("reviews")))),
         lambda p: f"{p.get('rating')} stars from {p.get('reviews')} reviews"),
    ]
    dated = [p for p in ok if p.get("delivery_days") is not None]
    if dated:  # only claim a delivery advantage we can back up
        winners.append(("arrives soonest", min(dated, key=lambda p: (p["delivery_days"], -p["_score"])),
                        lambda p: f"arrives {_arrives(p.get('delivery_days'))}"))
    by_asin = {}
    for label, p, why in winners:
        entry = by_asin.setdefault(p["asin"], {"p": p, "labels": [], "whys": []})
        entry["labels"].append(label)
        entry["whys"].append(why(p))
    for p in ok:  # fill up to three by fit
        if len(by_asin) >= 3:
            break
        if p["asin"] not in by_asin:
            most = max(_reviews(q.get("reviews")) for q in ok)  # across all results
            label = "most reviewed" if _reviews(p.get("reviews")) == most else "good all-rounder"
            by_asin[p["asin"]] = {"p": p, "labels": [label],
                                  "whys": [f"{p.get('rating')} stars, {p.get('reviews')} reviews, ${_price(p):.2f}"]}
    picks = [_pick(e["p"], " and ".join(e["labels"]), "; ".join(e["whys"])) for e in by_asin.values()][:3]
    picks.sort(key=lambda x: x["score"], reverse=True)  # fit order, not ad placement
    reason = ("no budget known for this" if not budget else
              "the use case isn't clear" if not specific else "no product is clearly best")
    return {**base, "mode": "three", "picks": picks, "why_three": reason}


def cart_link(asins: list) -> str:
    """Amazon cart handoff: one tap adds every item to the owner's own cart."""
    params = {}
    for i, a in enumerate(asins, 1):
        params[f"ASIN.{i}"] = a
        params[f"Quantity.{i}"] = 1
    return "https://www.amazon.com/gp/aws/cart/add.html?" + urlencode(params)


def format_offer(offer: dict) -> str:
    if offer["mode"] == "none":
        return f"       {offer['item']}: nothing to offer ({offer.get('note')})"
    head = "one pick" if offer["mode"] == "one" else f"three options ({offer['why_three']})"
    lines = [f"       {offer['item']} [search: {offer.get('search_query')}] -> {head}"]
    for p in offer["picks"]:
        tag = " [Sponsored]" if p["sponsored"] else ""
        act = " -> cart link (over limit)" if p["action"] == "cart_link" else ""
        lines.append(f"         * {p['label']}: {p['title'][:60]} ${p['price']:.2f}, "
                     f"{p['rating']} stars{tag}{act}")
        lines.append(f"           {p['why']}")
    return "\n".join(lines)
