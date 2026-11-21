"""OpenBot -> Telegram.

Telegram is OpenBot's built-in connector; Pattie's code doesn't talk to Telegram directly. What we
can test is our side of the contract:
- the routines' messages fit what OpenBot accepts,
- Pattie's instructions tell routine runs to post with openbot.messaging_post (the scheduler
  doesn't post a run's answer by itself),
- nothing Pattie sends to Telegram carries a secret.

Live: there's no API to check the connection; the live test is a manual checklist that fails
until PATTIE_TELEGRAM_CONNECTED=1 is set after checking it in the app.
"""

import json
import os

import pytest

from conftest import ROOT

INSTRUCTIONS = (ROOT / "infra/openbot/pattie-instructions.md").read_text()


def test_instructions_post_routine_results_to_telegram():
    assert "openbot.messaging_post" in INSTRUCTIONS
    assert "Telegram" in INSTRUCTIONS


def test_instructions_forbid_secrets_in_chat():
    lowered = INSTRUCTIONS.lower()
    assert "never show" in lowered and "card numbers" in lowered


def test_routine_payloads_fit_openbot_limit():
    from pattie.openbot_bridge import MAX_PAYLOAD, compact
    big = {"kind": "calendar_event",
           "event": {"id": "e1", "title": "Flight to NY", "start": "2026-11-26T09:15:00-08:00"},
           "decision": {"needs_products": True, "summary": "Trip " * 200,
                        "items": [{"item": f"item {n}", "reason": "r " * 100} for n in range(5)],
                        "offers": [{"item": "x", "mode": "three",
                                    "picks": [{"asin": "TEST000001", "title": "t " * 200}] * 3}] * 5}}
    text = compact(big)
    assert len(text) <= MAX_PAYLOAD
    assert json.loads(text)["event"]["title"] == "Flight to NY"


@pytest.mark.live
def test_live_telegram_connected():
    if os.environ.get("PATTIE_TELEGRAM_CONNECTED") != "1":
        pytest.fail("Check by hand, then set PATTIE_TELEGRAM_CONNECTED=1: OpenBot -> Marketplace -> Apps -> "
                    "Telegram shows Connected, Pattie is the Telegram Orchestrator, and a message to "
                    "@meetOpenBot gets an answer from Pattie.")
