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

When products are needed, suggest at most 5 item types (generic types like "travel \
adapter", not brands or listings), most useful first, each with a short reason tied to \
this specific event (destination, length, who it's for). Use the title, location, \
duration and description. Don't invent facts the event doesn't support."""

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
                },
                "required": ["item", "reason"],
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
    def __init__(self, tz: str):
        self.client = anthropic.Anthropic()
        self.tz = tz

    def decide(self, event: dict, now: datetime) -> dict:
        start = parse_time(event["start"], self.tz)
        end = parse_time(event["end"], self.tz)
        days_until = (start.date() - now.date()).days
        products = self._classify_products(event, start, end, now)
        if not products["needs_products"]:
            products["items"] = []
        return {
            **products,
            "days_until": days_until,
            "urgency_tier": urgency_tier(days_until) if products["needs_products"] else None,
            "food_nudges": food_nudges(event, start, end),
            "model": MODEL,
        }

    def _classify_products(self, event, start, end, now) -> dict:
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
            "Classify this event and decide whether Pattie should offer to buy anything for it."
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
