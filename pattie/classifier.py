"""Decide whether a calendar event needs anything bought for it.

Products: Claude reads the event and returns a structured decision.
Food nudges and urgency tiers: plain rules from the PRD, so they're predictable on stage.
"""

import json
import os
from datetime import datetime, timedelta

import anthropic

from .sources import parse_time

MODEL = os.environ.get("PATTIE_MODEL", "claude-sonnet-5-5")  # PRD: "latest Sonnet"

EVENT_TYPES = [
    "flight_or_long_trip", "road_trip", "short_trip", "social_event",
    "full_day_event", "meetup_or_day_event", "routine", "other",
]

ITEM_CATEGORIES = [
    "accessories", "travel", "toiletries", "gifts", "gift extras",
    "food and drinks", "clothing", "other",
]

SYSTEM = """You are Pattie's event classifier. Pattie is a shopping assistant that reads a \
person's calendar and proactively offers to buy things they may need for upcoming events. \
It always asks before buying, so a wrong "yes" costs the user an annoying message. \
Only say an event needs products when a typical person would plausibly need to buy \
something specific for it. Routine events (appointments, 1:1s, standups, calls, gym, \
regular dinners) need nothing. When in doubt, say no.

Event types and typical needs:
- flight_or_long_trip (e.g. "Flight to NY"): travel adapter, packing cubes, toiletries, neck pillow
- road_trip (e.g. "Roadtrip Big Sur"): car phone mount, snacks, cooler, charger
- short_trip (e.g. "Weekend Tahoe"): sunscreen, weather-specific gear
- social_event (e.g. "Lisa's birthday"): gift, card, wrapping, party supplies
- full_day_event (e.g. "Hackathon SF"): power bank, snacks, energy drinks
- meetup_or_day_event (e.g. "AI Meetup"): usually nothing
- routine: never needs anything
- other: anything else

You may also get what Pattie remembers about the person (budgets, brands, things they \
already own, dislikes, people involved, past accepted or declined suggestions). Treat it \
as true and let it shape the decision: don't suggest things they already own or have \
declined before, prefer what they like, and mention the memory in the reason when it \
changed your pick (e.g. "you skipped energy drinks last time, so cold brew instead").

When products are needed, suggest at most 5 item types (generic types like "travel \
adapter", not brands or listings), most useful first, each with a short reason tied to \
this specific event (destination, length, who it's for). List only items you actually \
recommend buying; never list an item just to say it isn't needed.

For each item also give:
- search_query: what Pattie should type into Amazon search, 2-6 words. Put a preferred \
brand or a must-have from memory in it ("Anker power bank", "unscented travel toiletries \
kit", "vegetarian protein bars").
- category: accessories (chargers, cables, gadgets), travel, toiletries, gifts (the main \
present), gift extras (card, wrapping), food and drinks, clothing, or other.
- specific: true if the event and memory make clear exactly which kind of product fits \
(Pattie can offer one best pick); false if it's a matter of taste the owner should choose \
(Pattie offers three options).

Use the title, location, duration and description. Don't invent facts the event doesn't \
support."""

SCHEMA = {
    "type": "object",
    "properties": {
        "event_type": {"type": "string", "enum": EVENT_TYPES},
        "needs_products": {"type": "boolean"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "reason": {"type": "string"},
                    "search_query": {
                        "type": "string",
                        "description": "What to type into Amazon search to find this, 2-6 words.",
                    },
                    "category": {"type": "string", "enum": ITEM_CATEGORIES},
                    "specific": {
                        "type": "boolean",
                        "description": "True if the event makes clear exactly what kind of "
                                       "product fits; false if the owner should choose.",
                    },
                },
                "required": ["item", "reason", "search_query", "category", "specific"],
                "additionalProperties": False,
            },
        },
        "summary": {
            "type": "string",
            "description": "One sentence on why Pattie should or shouldn't offer anything.",
        },
    },
    "required": ["event_type", "needs_products", "items", "summary"],
    "additionalProperties": False,
}


class Classifier:
    def __init__(self, tz: str, memory=None):
        self.client = anthropic.Anthropic()
        self.tz = tz
        self.memory = memory  # pattie.memory.Memory, or None to run without memory

    def decide(self, event: dict, now: datetime) -> dict:
        start = parse_time(event["start"], self.tz)
        end = parse_time(event["end"], self.tz)
        days_until = (start.date() - now.date()).days
        remembered = self._remembered(event)
        products = self._classify_products(event, start, end, now, remembered)
        if not products["needs_products"]:
            products["items"] = []
        return {
            **products,
            "days_until": days_until,
            "urgency_tier": urgency_tier(days_until) if products["needs_products"] else None,
            "food_nudges": food_nudges(event, start, end),
            "memory": remembered,
            "model": MODEL,
        }

    def _remembered(self, event) -> str:
        if self.memory is None:
            return ""
        try:
            return self.memory.context_for_event(event)
        except Exception as e:  # memory is a bonus; never block a decision on it
            print(f"  ! memory lookup failed, deciding without it: {e}")
            return ""

    def _classify_products(self, event, start, end, now, remembered="") -> dict:
        hours = round((end - start).total_seconds() / 3600, 1)
        prompt = (
            f"Today is {now:%A %Y-%m-%d %H:%M} ({self.tz}).\n\n"
            f"Event:\n"
            f"- Title: {event['title']}\n"
            f"- Starts: {start:%A %Y-%m-%d %H:%M}"
            f"{' (all day)' if event['all_day'] else ''}\n"
            f"- Duration: {hours} hours\n"
            f"- Location: {event['location'] or '(none)'}\n"
            f"- Attendees: {event['attendees']}\n"
            f"- Description: {event['description'] or '(none)'}\n\n"
            + (f"What Pattie remembers about this person:\n{remembered}\n\n" if remembered else "")
            + "Classify this event and decide whether Pattie should offer to buy anything for it."
        )
        response = self.client.beta.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise ClassifierError(f"model declined: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise ClassifierError("ran out of tokens before answering")
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)


class ClassifierError(Exception):
    pass


def urgency_tier(days_until: int) -> str:
    """PRD flow 3."""
    if days_until <= 2:
        return "asap"
    if days_until <= 7:
        return "this_week"
    return "long_term"


def food_nudges(event: dict, start: datetime, end: datetime) -> list:
    """PRD flow 6. Back-to-back events over a mealtime need the whole day, so not here."""
    if event["all_day"]:
        return []
    nudges = []
    hours = (end - start).total_seconds() / 3600
    if hours >= 6 and start.hour < 9:
        ask_at = (start - timedelta(days=1)).replace(hour=20, minute=0, second=0)
        nudges.append({
            "meal": "breakfast",
            "deliver_to": event["location"] or "event venue",
            "deliver_at": (start + timedelta(minutes=15)).isoformat(),
            "ask_at": ask_at.isoformat(),
            "reason": f"Full-day event starts at {start:%-I:%M%p}.",
        })
    # Busy through dinnertime (started before 6pm, ends 7pm or later). An event that
    # starts in the evening, like a birthday dinner, is the meal itself.
    if start.hour < 18 and end.hour >= 19 and end.date() == start.date():
        nudges.append({
            "meal": "dinner",
            "deliver_to": "home",
            "deliver_at": (end + timedelta(minutes=45)).isoformat(),
            "ask_at": (end - timedelta(hours=1)).isoformat(),
            "reason": f"Event ends at {end:%-I:%M%p}.",
        })
    return nudges
