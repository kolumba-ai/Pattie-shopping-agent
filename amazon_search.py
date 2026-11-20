"""Search Amazon (sold by Amazon.com only) through a Tilion residential session.

Writes scratch/results.json: {query: [ {asin, title, price, rating, reviews, image, url, sponsored}, ... ]}
"""

import html as htmllib
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent))
from tilion import Tilion  # noqa: E402

QUERIES = {
    "spatula": "silicone spatula",
    "bread knife": "bread knife",
    "butter holder": "butter dish with lid",
    "whisk": "whisk",
    "cutting boards": "cutting board set",
}
AMAZON_SELLER = "ATVPDKIKX0DER"  # merchant id for "Amazon.com"


def first(pat, text):
    m = re.search(pat, text, re.S)
    return htmllib.unescape(m.group(1)).strip() if m else None


def parse(page: str) -> list[dict]:
    cards = re.split(r'(?=<div[^>]*data-component-type="s-search-result")', page)[1:]
    out = []
    for c in cards:
        c = c[:40000]
        asin = first(r'data-asin="([A-Z0-9]{10})"', c)
        title = first(r'<h2[^>]*aria-label="([^"]+)"', c) or first(r"<h2.*?<span[^>]*>(.*?)</span>", c)
        price = first(r'a-offscreen">\$([\d,]+\.\d{2})<', c)
        if not (asin and title and price):
            continue
        reviews = first(r'aria-label="([\d,]+) ratings?"', c) or first(r's-underline-text">\(?([\d.,]+K?)\)?<', c)
        out.append({
            "asin": asin,
            "title": re.sub(r"^Sponsored Ad - ", "", title),
            "price": float(price.replace(",", "")),
            "rating": first(r'aria-label="([\d.]+) out of 5 stars', c),
            "reviews": reviews,
            "image": first(r'class="s-image"[^>]*src="([^"]+)"', c),
            "url": f"https://www.amazon.com/dp/{asin}",
            "sponsored": "Sponsored" in c[:6000],
            "amazon_basics": title.lower().startswith("amazon basics") or "amazon basics" in title.lower()[:40],
        })
    return out


def main():
    t = Tilion()
    results = {}
    with t.session(residential=True) as sid:
        t.fetch(sid, "https://www.amazon.com/")  # warm up cookies like a normal visitor
        for item, q in QUERIES.items():
            url = f"https://www.amazon.com/s?k={quote_plus(q)}&rh=p_6%3A{AMAZON_SELLER}"
            r = t.fetch(sid, url, include_html=True)
            rows = parse(r.get("html") or "") if r.get("status") == "ok" else []
            print(f"{item:15} status={r.get('status')} html_len={r.get('html_len')} products={len(rows)}")
            results[item] = rows
    Path("scratch/results.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
