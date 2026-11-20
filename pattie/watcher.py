"""Watch the calendar, notice every change, and decide what each event needs.

    # real calendar (service account)
    python -m pattie.watcher --calendar-id <id> --credentials secrets/service-account.json

    # offline test against an .ics file
    python -m pattie.watcher --ics demo/pattie-demo-calendar.ics --once

    # demo clock: pretend it's 8pm the night before the hackathon
    python -m pattie.watcher ... --now 2026-11-20T20:00

Env vars work too: PATTIE_CALENDAR_ID, GOOGLE_APPLICATION_CREDENTIALS, PATTIE_TZ.
Claude credentials come from ANTHROPIC_API_KEY.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import anthropic

from .classifier import Classifier, ClassifierError
from .ranking import format_offer
from .sources import GoogleCalendarSource, IcsFileSource, ZoneInfo, parse_time
from .store import Store

LOOKAHEAD_DAYS = 30  # PRD flow 1


def poll(source, store, classifier, now: datetime, tz: str, out=print, offers_fn=None) -> list:
    """One pass: pull changes, record them, classify anything new in the window."""
    changes = source.fetch_changes()
    for ev in changes:
        kind = store.upsert(ev)
        if kind != "unchanged":
            out(f"[{kind:>9}] {ev['title'] or '(untitled)'}  {ev['start']}")

    decisions = []
    window_end = now + timedelta(days=LOOKAHEAD_DAYS)
    for ev in sorted(store.undecided(), key=lambda e: e["start"]):
        start = parse_time(ev["start"], tz)
        if not (now <= start <= window_end):
            continue  # past, or more than 30 days out: picked up once it's in range
        try:
            decision = classifier.decide(ev, now)
        except (ClassifierError, anthropic.APIConnectionError, anthropic.RateLimitError,
                anthropic.InternalServerError) as e:
            out(f"  ! couldn't classify '{ev['title']}', will retry next poll: {e}")
            continue
        except anthropic.APIStatusError as e:
            out(f"  ! Claude API error for '{ev['title']}': {e.status_code} {e.message}")
            continue
        if offers_fn and decision["needs_products"]:
            try:
                decision["offers"] = offers_fn(decision)
            except Exception as e:  # search is best effort; the decision still stands
                out(f"  ! Amazon search failed for '{ev['title']}': {e}")
        store.save_decision(ev["id"], decision, now.isoformat())
        decisions.append((ev, decision))
        out(format_decision(ev, decision))
    return decisions


def format_decision(ev: dict, d: dict) -> str:
    head = f"  -> {ev['title']} ({d['event_type']}, in {d['days_until']} days)"
    lines = [head]
    if d.get("memory"):
        first = d["memory"].replace("\n", " ")
        lines.append(f"     memory: {first[:220]}{'...' if len(first) > 220 else ''}")
    if d["needs_products"]:
        lines.append(f"     NEEDS STUFF [{d['urgency_tier']}]: {d['summary']}")
        lines += [f"       - {i['item']}: {i['reason']}" for i in d["items"]]
    else:
        lines.append(f"     skip: {d['summary']}")
    for o in d.get("offers", []):
        lines.append(format_offer(o))
    for n in d["food_nudges"]:
        lines.append(f"     food: ask about {n['meal']} at {n['ask_at']} ({n['reason']})")
    return "\n".join(lines)


def load_env_file(path: str = ".env") -> None:
    """Read KEY=value lines from .env into the environment. Filled-in .env values win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ[key.strip()] = value


def main(argv=None) -> None:
    load_env_file()
    p = argparse.ArgumentParser(description="Pattie calendar watcher")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--ics", help="read events from an .ics file instead of Google")
    p.add_argument("--calendar-id", default=os.environ.get("PATTIE_CALENDAR_ID"))
    p.add_argument("--credentials", default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
                   help="service account JSON key")
    p.add_argument("--tz", default=os.environ.get("PATTIE_TZ", "America/Los_Angeles"))
    p.add_argument("--db", default="pattie.sqlite3")
    p.add_argument("--interval", type=int, default=60, help="seconds between polls")
    p.add_argument("--once", action="store_true", help="poll once and exit")
    p.add_argument("--now", help="override the clock, e.g. 2026-11-20T20:00 (demo time travel)")
    p.add_argument("--json", action="store_true", help="print decisions as JSON lines")
    p.add_argument("--no-memory", action="store_true",
                   help="decide without Cognee memory (faster, no personalization)")
    p.add_argument("--user", default=os.environ.get("PATTIE_USER", "anna_susan"),
                   help="whose memory to use")
    p.add_argument("--prefs", default=os.environ.get("PATTIE_PREFS", "demo/anna_susan_prefs.json"),
                   help="exact preferences for ranking (budgets, brands, must-haves)")
    p.add_argument("--search-results",
                   help="rank saved Amazon results from this JSON file instead of searching live")
    p.add_argument("--no-search", action="store_true", help="don't search Amazon for products")
    args = p.parse_args(argv)

    store = Store(args.db)
    if args.ics:
        source = IcsFileSource(args.ics, args.tz)
    elif args.calendar_id and args.credentials:
        source = GoogleCalendarSource(args.calendar_id, args.credentials, store)
    else:
        p.error("give --ics FILE, or --calendar-id and --credentials (or their env vars)")
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key.startswith("sk-ant-") or len(key) < 40:
        p.error("ANTHROPIC_API_KEY is missing or looks like a placeholder. "
                "Paste your real key after ANTHROPIC_API_KEY= in the .env file.")
    memory = None
    if not args.no_memory:
        try:
            from .memory import Memory
            memory = Memory(args.user)
        except Exception as e:
            print(f"! Cognee memory unavailable, continuing without it: {e}", file=sys.stderr)
    classifier = Classifier(args.tz, memory)

    offers_fn = None
    if not args.no_search and os.path.exists(args.prefs):
        from .offers import amazon_live_search, make_offers, saved_search
        from .ranking import load_json

        prefs = load_json(args.prefs)
        search = None
        if args.search_results:
            search = saved_search(args.search_results)
        elif os.environ.get("TILION_API_KEY"):
            search = amazon_live_search
        else:
            print("! No TILION_API_KEY: skipping Amazon search (use --search-results FILE "
                  "to rank saved results)", file=sys.stderr)
        if search:
            offers_fn = lambda decision: make_offers(decision, prefs, search)  # noqa: E731

    tz = ZoneInfo(args.tz)
    fixed_now = datetime.fromisoformat(args.now).replace(tzinfo=tz) if args.now else None
    started = datetime.now(tz)

    def clock():
        # With --now, time still moves forward from that point.
        return fixed_now + (datetime.now(tz) - started) if fixed_now else datetime.now(tz)

    out = print
    if args.json:
        def out(_msg):  # keep stdout clean for JSON consumers
            print(_msg, file=sys.stderr)

    print(f"Watching {'file ' + args.ics if args.ics else args.calendar_id} "
          f"(every {args.interval}s, now = {clock():%Y-%m-%d %H:%M %Z}, "
          f"memory {'on for ' + args.user if memory else 'off'})", file=sys.stderr)
    while True:
        for ev, d in poll(source, store, classifier, clock(), args.tz, out, offers_fn):
            if args.json:
                print(json.dumps({"event": ev, "decision": d}), flush=True)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
