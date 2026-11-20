"""Generate the Pattie demo calendar as an .ics file to import into Google Calendar.

Event dates are relative to the demo day, so regenerate it right before the
demo (or pass --demo-date) and the "tomorrow" / "in 6 days" framing stays true.

    python3 demo/gen_seed_calendar.py                      # demo day = today
    python3 demo/gen_seed_calendar.py --demo-date 2026-11-22
    python3 demo/gen_seed_calendar.py --out my.ics

Standard library only.
"""

import argparse
import uuid
from datetime import date, datetime, timedelta, timezone

TZID = "America/Los_Angeles"

# (days after demo day, start "HH:MM", end "HH:MM", title, location, description)
# The first four are the PRD demo events. The last two are distractors that
# Pattie should skip silently (no shopping need, no food nudge).
EVENTS = [
    (1, "08:00", "21:00", "Hackathon SF",
     "San Francisco, CA",
     "Personal Agent Hackathon. Full day of building, demos at 6pm."),
    (5, "17:30", "19:00", "AI Meetup",
     "SoMa, San Francisco, CA",
     "Talks on agent memory + networking."),
    (6, "09:15", "14:45", "Flight to NY",
     "SFO Terminal 2",
     "AA 2 SFO -> JFK. Staying 5 nights in Brooklyn for work."),
    (21, "19:00", "23:00", "Lisa's birthday",
     "Lisa's place, Oakland, CA",
     "Turning 30! Dinner and drinks, bring something nice."),
    (2, "10:00", "10:45", "Dentist",
     "Mission Dental, San Francisco, CA", ""),
    (3, "14:00", "14:30", "1:1 with Mauricio", "Google Meet", ""),
]


def ics_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def local(d: date, hhmm: str) -> str:
    h, m = hhmm.split(":")
    return f"{d:%Y%m%d}T{int(h):02d}{int(m):02d}00"


def build(demo_day: date) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Pattie//Demo Seed Calendar//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Pattie Demo",
        f"X-WR-TIMEZONE:{TZID}",
    ]
    for offset, start, end, title, location, desc in EVENTS:
        day = demo_day + timedelta(days=offset)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uuid.uuid4()}@pattie-demo",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID={TZID}:{local(day, start)}",
            f"DTEND;TZID={TZID}:{local(day, end)}",
            f"SUMMARY:{ics_escape(title)}",
            f"LOCATION:{ics_escape(location)}",
        ]
        if desc:
            lines.append(f"DESCRIPTION:{ics_escape(desc)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--demo-date", type=date.fromisoformat, default=date.today(),
                   help="day the demo runs, YYYY-MM-DD (default: today)")
    p.add_argument("--out", default="demo/pattie-demo-calendar.ics")
    args = p.parse_args()
    with open(args.out, "w", newline="") as f:
        f.write(build(args.demo_date))
    print(f"Wrote {args.out} for demo day {args.demo_date}:")
    for offset, start, end, title, *_ in sorted(EVENTS):
        d = args.demo_date + timedelta(days=offset)
        print(f"  {d:%a %b %d}  {start}-{end}  {title}")


if __name__ == "__main__":
    main()
