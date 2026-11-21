"""Pattie <-> Telegram (Pattie's own bot, Bot API over HTTPS).

Offline coverage of the bot, pairing and delivery is in tests/test_agent.py; this file checks the
contract with the agent and, live, the real bot token.

Live: getMe with TELEGRAM_BOT_TOKEN; with PATTIE_TELEGRAM_CHAT_ID set, also sends one test message.
"""

import os

import pytest

from conftest import ROOT

INSTRUCTIONS = (ROOT / "pattie/instructions.md").read_text()


def test_instructions_send_photos_and_forbid_secrets():
    assert "send_photo" in INSTRUCTIONS
    lowered = INSTRUCTIONS.lower()
    assert "never show" in lowered and "card numbers" in lowered


def test_agent_offers_send_photo_tool():
    from pattie.agent import TOOL_NAMES
    assert "send_photo" in TOOL_NAMES


def _token():
    from pattie.tools import load_env_file
    load_env_file()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        pytest.skip("needs TELEGRAM_BOT_TOKEN (from BotFather, kept in 1Password)")
    return token


@pytest.mark.live
def test_live_bot_token_works():
    from pattie.telegram import TelegramBot
    me = TelegramBot(_token()).me()
    assert me.get("is_bot") and me.get("username")


@pytest.mark.live
def test_live_send_message_to_owner():
    chat = os.environ.get("PATTIE_TELEGRAM_CHAT_ID")
    if not chat:
        pytest.skip("set PATTIE_TELEGRAM_CHAT_ID (the paired owner chat) to send a test message")
    from pattie.telegram import TelegramBot
    TelegramBot(_token()).text(chat, "Pattie integration test: Telegram works.")
