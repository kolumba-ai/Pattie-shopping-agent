"""Pattie: one service with the Telegram bot, the agent, and the calendar bridge.

    python -m pattie.watcher --json ... | python -m pattie.app

- Telegram (main thread): owner messages go to the agent; replies and photos go back.
- Calendar (second thread): the watcher's JSON decisions go through the bridge, which queues them
  and hands each to the agent ("[Calendar event]" now, "[Food nudge]" at its time); the agent's
  message goes to the owner. Nothing is lost while the owner hasn't paired yet: it stays queued.

Settings: TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, TILION_API_KEY, PATTIE_DB, PATTIE_PAIRING_CODE,
and the PattieTools settings (PATTIE_TZ, PATTIE_PREFS, ...).
"""

from __future__ import annotations

import json
import logging
import os
import select
import sys
import threading
import time

from .agent import Agent
from .bridge import Bridge, DeliveryError, compact
from .telegram import Owner, TelegramBot, TelegramError
from .tools import PattieTools, load_env_file

log = logging.getLogger("pattie.app")


class AgentDelivery:
    """The bridge's delivery: run the agent on the event and message the owner."""

    def __init__(self, agent: Agent, bot: TelegramBot, owner: Owner):
        self.agent, self.bot, self.owner = agent, bot, owner
        self._ids = {}   # the bridge clears this after a failure; nothing to cache here

    def run(self, _agent_name: str, routine: str, payload: dict) -> dict:
        chat_id = self.owner.chat_id
        if chat_id is None:
            raise DeliveryError("the owner hasn't paired with the bot yet (send /start <code>)")
        kind = "food_nudge" if routine.lower().startswith("food") else "calendar_event"
        reply = self.agent.event(chat_id, kind, json.loads(compact(payload)))
        try:
            self.bot.text(chat_id, reply)
        except TelegramError as e:
            raise DeliveryError(f"Telegram: {e}") from None
        return {"sent": True}


def calendar_loop(bridge: Bridge, stop: threading.Event) -> None:
    """Read the watcher's JSON lines from stdin; deliver due entries at least every 30 seconds."""
    stdin_open = not sys.stdin.isatty()
    while not stop.is_set():
        if stdin_open:
            ready, _, _ = select.select([sys.stdin], [], [], 30)
            if ready:
                line = sys.stdin.readline()
                if not line:
                    log.warning("calendar watcher stopped writing; still delivering queued items")
                    stdin_open = False
                elif line.strip():
                    bridge.handle(line)
        else:
            stop.wait(30)
        bridge.send_due()


def handle_message(update: dict, agent: Agent, bot: TelegramBot, owner: Owner) -> None:
    msg = update.get("message") or {}
    chat_id, text = (msg.get("chat") or {}).get("id"), (msg.get("text") or "").strip()
    if chat_id is None or not text:
        return
    if owner.try_pair(chat_id, text):
        log.info("paired with the owner's chat")
        bot.text(chat_id, "Hi, I'm Pattie. I'm watching your calendar and I'll message you here when "
                          "something needs shopping. You can also ask me for anything you need.")
        return
    if not owner.is_owner(chat_id):
        log.info("ignored a message from a chat that isn't the owner's")
        return
    if text == "/reset":
        agent.reset(chat_id)
        bot.text(chat_id, "Started a fresh conversation.")
        return
    bot.typing(chat_id)
    try:
        reply = agent.chat(chat_id, text)
    except Exception as e:
        log.exception("agent failed")
        reply = f"Something went wrong on my side ({type(e).__name__}). Please try again."
    bot.text(chat_id, reply)


def main() -> None:
    logging.basicConfig(level=os.environ.get("PATTIE_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    load_env_file()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set")
    db = os.environ.get("PATTIE_DB", "pattie.sqlite3")

    bot = TelegramBot(token)
    me = bot.me()
    owner = Owner(db, os.environ.get("PATTIE_PAIRING_CODE"))
    tools = PattieTools.from_env()
    agent = Agent(tools, db, send=bot)
    if owner.chat_id:
        log.info("bot @%s ready; owner paired", me.get("username"))
    else:
        log.info("bot @%s ready; to pair, send this to the bot in Telegram: /start %s", me.get("username"), owner.code)

    bridge = Bridge(AgentDelivery(agent, bot, owner), db,
                    log=lambda m: logging.getLogger("pattie.bridge").info(m))
    stop = threading.Event()
    threading.Thread(target=calendar_loop, args=(bridge, stop), daemon=True).start()

    while True:
        try:
            for update in bot.updates():
                handle_message(update, agent, bot, owner)
        except TelegramError as e:
            log.warning("Telegram polling error: %s", e)
            time.sleep(5)
        except Exception:
            log.exception("polling loop error")
            time.sleep(5)


if __name__ == "__main__":
    main()
