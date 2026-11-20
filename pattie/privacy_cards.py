"""Privacy.com cards: one merchant-locked card per vendor (Amazon, DoorDash).

    cards = PrivacyCards.from_env()        # PRIVACY_API_KEY, PATTIE_CARD_TOKEN_AMAZON, ...
    cards.status("amazon")                 # state, monthly limit, spent this month, last four
    cards.pause("amazon")                  # "freeze the card"
    cards.resume("amazon")

API: https://developers.privacy.com (Authorization: api-key <key>). Amounts are in cents.
Each card is MERCHANT_LOCKED (it locks to the first merchant that charges it) with a MONTHLY
spend limit, so Privacy declines anything over the limit no matter what Pattie does.

This module reads card state, limits and transactions only. It never asks for or returns the
card number or CVV; those live in 1Password and in each vendor's saved payment methods.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

LIVE = "https://api.privacy.com/v1"
SANDBOX = "https://sandbox.privacy.com/v1"
SAFE_CARD_FIELDS = ("token", "memo", "state", "type", "last_four", "spend_limit",
                    "spend_limit_duration", "created")


class PrivacyError(Exception):
    pass


def _http(method: str, url: str, api_key: str, body: Optional[dict] = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"api-key {api_key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise PrivacyError(f"Privacy API {e.code}: {e.read().decode(errors='replace')[:200]}") from None


class PrivacyCards:
    def __init__(self, api_key: str, card_tokens: dict, base_url: str = LIVE,
                 http: Callable = _http):
        self.api_key, self.card_tokens, self.base, self.http = api_key, card_tokens, base_url, http

    @classmethod
    def from_env(cls) -> Optional["PrivacyCards"]:
        """None when no API key is set (cards not connected yet)."""
        key = os.environ.get("PRIVACY_API_KEY")
        if not key:
            return None
        tokens = {k[len("PATTIE_CARD_TOKEN_"):].lower(): v for k, v in os.environ.items()
                  if k.startswith("PATTIE_CARD_TOKEN_") and v}
        base = SANDBOX if os.environ.get("PRIVACY_SANDBOX") == "1" else LIVE
        return cls(key, tokens, base)

    def _token(self, merchant: str) -> str:
        token = self.card_tokens.get(merchant.lower())
        if not token:
            raise PrivacyError(f"no Privacy card configured for {merchant} "
                               f"(set PATTIE_CARD_TOKEN_{merchant.upper()})")
        return token

    def card(self, merchant: str) -> dict:
        raw = self.http("GET", f"{self.base}/cards/{self._token(merchant)}", self.api_key)
        raw = raw.get("data", [raw])[0] if "data" in raw else raw
        return {k: raw.get(k) for k in SAFE_CARD_FIELDS}  # never pan, cvv or exp

    def spent_this_period(self, merchant: str, now: Optional[datetime] = None) -> float:
        """Dollars settled or pending on the card in the trailing month (Privacy's MONTHLY window)."""
        now = now or datetime.now(timezone.utc)
        begin = (now - timedelta(days=31)).strftime("%Y-%m-%d")
        data = self.http("GET", f"{self.base}/transactions?card_token={self._token(merchant)}"
                                f"&begin={begin}&page_size=100", self.api_key)
        cents = sum(t.get("amount", 0) for t in data.get("data", [])
                    if t.get("status") in ("PENDING", "SETTLED", "SETTLING", "APPROVED"))
        return round(cents / 100, 2)

    def status(self, merchant: str) -> dict:
        c = self.card(merchant)
        limit = (c.get("spend_limit") or 0) / 100
        spent = self.spent_this_period(merchant)
        return {"merchant": merchant.lower(), "state": c["state"], "last_four": c["last_four"],
                "memo": c["memo"], "limit": limit, "limit_period": c["spend_limit_duration"],
                "spent_this_period": spent,
                "left_this_period": round(max(limit - spent, 0), 2) if limit else None}

    def _set_state(self, merchant: str, state: str) -> dict:
        self.http("PATCH", f"{self.base}/cards/{self._token(merchant)}", self.api_key, {"state": state})
        return self.card(merchant)

    def pause(self, merchant: str) -> dict:
        return self._set_state(merchant, "PAUSED")

    def resume(self, merchant: str) -> dict:
        return self._set_state(merchant, "OPEN")
