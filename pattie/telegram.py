"""Pattie's own Telegram bot (Bot API over HTTPS, long polling). No webhook, no inbound port.

    bot = TelegramBot(token)
    for update in bot.updates():     # blocks up to 30 s per poll
        ...
    bot.text(chat_id, "Hello")
    bot.photo(chat_id, "https://m.media-amazon.com/...jpg", "Option A, $25.60")

Pairing: only one chat (the owner) talks to Pattie. The first chat that sends
"/start <pairing code>" becomes the owner; the code is printed in the service log at startup and
can be fixed with PATTIE_PAIRING_CODE. Everyone else is ignored.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import urllib.error
import urllib.request
from typing import Callable, Iterator, Optional

log = logging.getLogger("pattie.telegram")
API = "https://api.telegram.org"
MAX_TEXT = 4000      # Telegram's limit is 4096 characters per message
MAX_CAPTION = 1000   # and 1024 per photo caption


class TelegramError(Exception):
    pass


def _http(method: str, url: str, body: Optional[dict] = None, timeout: float = 40) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        data = json.loads(e.read() or b"{}") or {"ok": False, "description": f"HTTP {e.code}"}
    if not data.get("ok"):
        raise TelegramError(data.get("description", "Telegram error"))
    return data["result"]


class TelegramBot:
    def __init__(self, token: str, http: Callable = _http):
        self.base, self.http, self.offset = f"{API}/bot{token}", http, 0

    def call(self, method: str, **params):
        return self.http("POST", f"{self.base}/{method}", params, timeout=params.get("timeout", 30) + 10)

    def me(self) -> dict:
        return self.call("getMe")

    def updates(self) -> Iterator[dict]:
        for u in self.call("getUpdates", offset=self.offset, timeout=30, allowed_updates=["message"]):
            self.offset = u["update_id"] + 1
            yield u

    def text(self, chat_id, text: str) -> None:
        text = text or "(no reply)"
        for start in range(0, len(text), MAX_TEXT):          # long replies go in several messages
            self.call("sendMessage", chat_id=chat_id, text=text[start:start + MAX_TEXT],
                      disable_web_page_preview=True)

    def photo(self, chat_id, url: str, caption: str = "") -> None:
        try:
            self.call("sendPhoto", chat_id=chat_id, photo=url, caption=caption[:MAX_CAPTION])
        except TelegramError as e:   # Telegram couldn't fetch the image: send the caption with the link
            log.warning("sendPhoto failed (%s); sending text instead", e)
            self.text(chat_id, f"{caption}\n{url}")

    def typing(self, chat_id) -> None:
        try:
            self.call("sendChatAction", chat_id=chat_id, action="typing")
        except TelegramError:
            pass


class Owner:
    """Which Telegram chat is the owner's, kept in SQLite after pairing."""

    def __init__(self, db_path: str, pairing_code: Optional[str] = None):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
        self.db.commit()
        self.code = pairing_code or secrets.token_hex(3).upper()

    @property
    def chat_id(self) -> Optional[str]:
        row = self.db.execute("SELECT value FROM kv WHERE key = 'owner_chat_id'").fetchone()
        return row[0] if row else None

    def try_pair(self, chat_id, text: str) -> bool:
        """True if this message paired the chat as the owner."""
        if self.chat_id is None and text.strip() == f"/start {self.code}":
            self.db.execute("INSERT OR REPLACE INTO kv VALUES ('owner_chat_id', ?)", (str(chat_id),))
            self.db.commit()
            return True
        return False

    def is_owner(self, chat_id) -> bool:
        return self.chat_id is not None and str(chat_id) == self.chat_id
