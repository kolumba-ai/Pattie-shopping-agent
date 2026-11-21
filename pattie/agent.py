"""Pattie's agent loop: Claude (Messages API, tool use) over Pattie's tools.

    agent = Agent(tools=PattieTools.from_env(), db_path="pattie.sqlite3", send=telegram_sender)
    reply = agent.chat(chat_id, "Find me a power bank")         # owner messages
    agent.event(chat_id, "[Calendar event] ...")                 # calendar watcher events

Tracing: every turn gets a trace_id; agent_turn_started, agent_tool_called and
agent_turn_finished go to Amplitude (when AMPLITUDE_API_KEY is set).
Memory: after a search or an approved purchase, a short note of what was found goes to Cognee
in the background.

One conversation per Telegram chat, kept in SQLite. The full assistant content (including thinking
blocks) is stored and sent back unchanged, as the API requires. Long conversations aren't edited:
after MAX_MESSAGES the chat starts a fresh conversation (memory lives in Cognee and the ledger).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import anthropic

log = logging.getLogger("pattie.agent")

MODEL = os.environ.get("PATTIE_AGENT_MODEL", "claude-sonnet-5-5")
EFFORT = os.environ.get("PATTIE_AGENT_EFFORT", "medium")   # multistep tool use; see the Claude API guidance
MAX_TOOL_ROUNDS = 12
MAX_MESSAGES = 80
INSTRUCTIONS = (Path(__file__).with_name("instructions.md")).read_text(encoding="utf-8")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation (
    chat_id  TEXT PRIMARY KEY,
    messages TEXT NOT NULL
);
"""

# Tool definitions: name -> (description, input schema). Each maps to a PattieTools method,
# except send_photo, which goes to Telegram.
TOOLS = [
    {"name": "find_offers",
     "description": "Search Amazon (sold by Amazon.com, through Tilion) for one item and rank the results. "
                    "Returns mode 'one' (single best pick), 'three' (cheapest, best rated, one more) or 'none'. "
                    "Each pick has asin, title, price, rating, reviews, image URL, sponsored flag, why it was "
                    "picked, and action 'order' or 'cart_link' (over the per-purchase limit).",
     "input_schema": {"type": "object", "properties": {
         "item": {"type": "string"},
         "search_query": {"type": "string", "description": "Amazon search words, e.g. 'Anker power bank'"},
         "category": {"type": "string", "enum": ["accessories", "travel", "toiletries", "gifts", "gift extras",
                                                  "food and drinks", "clothing", "other"]},
         "specific": {"type": "boolean", "description": "true if the use case is clear"},
         "days_until": {"type": "integer", "description": "days until the event, if any"},
         "owner_asked": {"type": "boolean", "description": "true if the owner named the product themselves"}},
         "required": ["item", "search_query", "category"]}},
    {"name": "build_cart_link",
     "description": "An Amazon link that adds these products (ASINs) to the owner's own cart in one tap.",
     "input_schema": {"type": "object", "properties": {"asins": {"type": "array", "items": {"type": "string"}}},
                      "required": ["asins"]}},
    {"name": "check_policy",
     "description": "Check a purchase against the spending policy. result: allow, ask, wait or block.",
     "input_schema": {"type": "object", "properties": {
         "total": {"type": "number"}, "titles": {"type": "array", "items": {"type": "string"}},
         "merchant": {"type": "string", "enum": ["amazon", "doordash"]},
         "owner_approved": {"type": "boolean"}, "urgent": {"type": "boolean"}},
         "required": ["total", "titles"]}},
    {"name": "checkout",
     "description": "After the owner's explicit yes: runs the spending policy and returns a cart link "
                    "(status 'cart_link') or a block with the reason (status 'blocked').",
     "input_schema": {"type": "object", "properties": {
         "asins": {"type": "array", "items": {"type": "string"}}, "approved_total": {"type": "number"},
         "titles": {"type": "array", "items": {"type": "string"}},
         "merchant": {"type": "string", "enum": ["amazon", "doordash"]}},
         "required": ["asins", "approved_total", "titles"]}},
    {"name": "card_status",
     "description": "Each vendor card (amazon, doordash): spend today and this month, what's left, recent purchases.",
     "input_schema": {"type": "object", "properties": {"merchant": {"type": "string", "enum": ["amazon", "doordash"]}}}},
    {"name": "freeze_card", "description": "Pause one vendor's Privacy.com card.",
     "input_schema": {"type": "object", "properties": {"merchant": {"type": "string", "enum": ["amazon", "doordash"]}},
                      "required": ["merchant"]}},
    {"name": "unfreeze_card", "description": "Reopen a paused vendor card. Only when the owner asks.",
     "input_schema": {"type": "object", "properties": {"merchant": {"type": "string", "enum": ["amazon", "doordash"]}},
                      "required": ["merchant"]}},
    {"name": "where_am_i", "description": "The owner's city, time zone, local time, and where deliveries go.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "set_location",
     "description": "Save where the owner says they are. timezone is an IANA name like America/Chicago; "
                    "until is an ISO date or time.",
     "input_schema": {"type": "object", "properties": {
         "city": {"type": "string"}, "timezone_name": {"type": "string"}, "until": {"type": "string"},
         "message": {"type": "string"}}, "required": ["city", "timezone_name"]}},
    {"name": "next_shopping_time",
     "description": "When to browse Amazon next, from the shopping-hours curve. urgent=true means now.",
     "input_schema": {"type": "object", "properties": {"urgent": {"type": "boolean"}}}},
    {"name": "remember", "description": "Save a fact about the owner to long-term memory.",
     "input_schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}},
    {"name": "recall", "description": "Ask long-term memory about the owner.",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "send_photo",
     "description": "Send a product photo to the owner in Telegram, with a short caption.",
     "input_schema": {"type": "object", "properties": {
         "url": {"type": "string"}, "caption": {"type": "string"}}, "required": ["url", "caption"]}},
]
TOOL_NAMES = {t["name"] for t in TOOLS}


class Agent:
    def __init__(self, tools, db_path: str, send: Optional[Callable] = None, client=None,
                 model: str = MODEL):
        """tools: PattieTools. send: object with .photo(chat_id, url, caption) (Telegram)."""
        self.tools, self.db_path, self.send = tools, db_path, send
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.user_id = os.environ.get("PATTIE_USER", "pattie_owner")
        self.amplitude = _amplitude_client()
        self.lock = threading.Lock()   # Telegram and the calendar bridge share one agent
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _load(self, chat_id: str) -> list:
        with self._db() as db:
            row = db.execute("SELECT messages FROM conversation WHERE chat_id = ?", (str(chat_id),)).fetchone()
        return json.loads(row["messages"]) if row else []

    def _save(self, chat_id: str, messages: list) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO conversation (chat_id, messages) VALUES (?, ?)",
                       (str(chat_id), json.dumps(messages)))

    def reset(self, chat_id: str) -> None:
        self._save(chat_id, [])

    # --- tools -----------------------------------------------------------------

    def _run_tool(self, chat_id: str, name: str, args: dict) -> tuple[str, bool]:
        """Returns (result JSON, is_error). Tool errors go back to Claude, not up the stack."""
        if name not in TOOL_NAMES:
            return json.dumps({"error": f"unknown tool {name}"}), True
        try:
            if name == "send_photo":
                if self.send is None:
                    return json.dumps({"error": "no chat to send to"}), True
                self.send.photo(chat_id, args["url"], args.get("caption", ""))
                return json.dumps({"sent": True}), False
            result = getattr(self.tools, name)(**args)
            return json.dumps(result, default=str), "error" in result if isinstance(result, dict) else False
        except Exception as e:   # a failing tool shouldn't end the conversation
            log.exception("tool %s failed", name)
            return json.dumps({"error": f"{type(e).__name__}: {e}"}), True

    # --- traces and memory ----------------------------------------------------

    def track(self, event_type: str, props: dict) -> None:
        """Send one event to Amplitude; never let tracing break a reply."""
        if self.amplitude is None:
            return
        try:
            from amplitude import BaseEvent
            self.amplitude.track(BaseEvent(event_type=event_type, user_id=self.user_id, event_properties=props))
        except Exception:
            log.exception("Amplitude tracking failed")

    def _note_for_memory(self, name: str, args: dict, result: dict) -> Optional[str]:
        today = datetime.now(timezone.utc).date().isoformat()
        if name == "find_offers" and result.get("picks"):
            picks = "; ".join(f"{p.get('label', 'pick')}: {(p.get('title') or '')[:70]} at ${p.get('price')}"
                              f"{' (Sponsored)' if p.get('sponsored') else ''}" for p in result["picks"])
            return (f"On {today}, Pattie searched Amazon for {args.get('item')} "
                    f"(search '{args.get('search_query')}') and offered: {picks}.")
        if name == "checkout" and result.get("status") in ("cart_link", "blocked"):
            titles = ", ".join(args.get("titles") or [])
            verb = "approved" if result["status"] == "cart_link" else "asked for, but the spending policy blocked,"
            return f"On {today}, the owner {verb} {titles} for ${args.get('approved_total')}."
        return None

    def remember_in_background(self, note: str, trace_id: str) -> None:
        def save():
            try:
                self.tools.remember(note)
                self.track("memory_saved", {"trace_id": trace_id, "chars": len(note)})
            except Exception as e:
                log.warning("couldn't save to memory: %s", e)
        threading.Thread(target=save, daemon=True).start()

    # --- the loop -------------------------------------------------------------

    def _respond(self, chat_id: str, user_content: str, source: str = "chat") -> str:
        trace_id, started = str(uuid.uuid4()), time.monotonic()
        self.track("agent_turn_started", {"trace_id": trace_id, "source": source, "model": self.model})
        reply, stop = self._loop(chat_id, user_content, trace_id)
        self.track("agent_turn_finished", {"trace_id": trace_id, "source": source, "stop_reason": stop,
                                            "seconds": round(time.monotonic() - started, 2)})
        return reply

    def _loop(self, chat_id: str, user_content: str, trace_id: str) -> tuple[str, str]:
        messages = self._load(chat_id)
        if len(messages) > MAX_MESSAGES:
            log.info("chat %s: starting a fresh conversation after %d messages", chat_id, len(messages))
            messages = []
        messages.append({"role": "user", "content": user_content})
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": INSTRUCTIONS, "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS,
                messages=messages,
                output_config={"effort": EFFORT},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            # Keep the full content (thinking blocks included) so the next request is valid
            messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in response.content]})
            if response.stop_reason == "refusal":
                self._save(chat_id, messages)
                return "Sorry, I can't help with that one.", "refusal"
            if response.stop_reason != "tool_use":
                self._save(chat_id, messages)
                return "\n".join(b.text for b in response.content if b.type == "text").strip(), response.stop_reason
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    log.info("chat %s: tool %s %s", chat_id, block.name, json.dumps(block.input)[:200])
                    t0 = time.monotonic()
                    out, is_error = self._run_tool(chat_id, block.name, block.input)
                    self.track("agent_tool_called", {"trace_id": trace_id, "tool": block.name, "ok": not is_error,
                                                     "seconds": round(time.monotonic() - t0, 2)})
                    if not is_error:
                        note = self._note_for_memory(block.name, block.input, json.loads(out))
                        if note:
                            self.remember_in_background(note, trace_id)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out,
                                    "is_error": is_error})
            messages.append({"role": "user", "content": results})   # all results in one message
        self._save(chat_id, messages)
        return "I got stuck working on that. Could you say it another way?", "max_rounds"

    def chat(self, chat_id: str, text: str) -> str:
        with self.lock:
            return self._respond(chat_id, text)

    def event(self, chat_id: str, kind: str, payload: dict) -> str:
        label = "[Food nudge]" if kind == "food_nudge" else "[Calendar event]"
        with self.lock:
            return self._respond(chat_id, f"{label} {json.dumps(payload, ensure_ascii=False)}", source=kind)


def _amplitude_client():
    """The shared Amplitude client (pattie/analytics.py), or None without AMPLITUDE_API_KEY."""
    if not os.environ.get("AMPLITUDE_API_KEY"):
        return None
    try:
        from .analytics import client
        return client
    except Exception:
        log.exception("Amplitude unavailable")
        return None
