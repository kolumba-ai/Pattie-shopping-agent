"""How likely the agent is to shop at a given local time of day.

shopping_weight(hour) returns a value from 0 to 1:
  - 1.0 inside the peak windows, 10:00-13:00 and 19:00-22:00 local time
  - outside them it fades smoothly (a Gaussian falloff from the nearest window edge)
  - the fade is slow in the afternoon gap, quicker on a weekday morning,
    and quickest after 22:00, so late night and early morning sit near zero

pick_start_time() samples a start time in proportion to that weight, so sessions
cluster in the peaks, spill into the shoulders and never land on a fixed schedule.

Run it to print the curve:  python3 shopping_hours.py [Area/City]
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# (start, end) in local hours
PEAKS = [(10.0, 13.0), (19.0, 22.0)]

# How fast the weight fades outside a window, in hours (one standard deviation).
FADE_MORNING = 1.2     # before 10:00: 9:00 is still fairly likely, 7:00 is rare
FADE_AFTERNOON = 2.0   # 13:00-19:00: a moderate dip, lowest around 16:00
FADE_NIGHT = 0.8       # after 22:00: 23:00 is possible, past midnight almost never

MIN_WEIGHT = 0.05      # below this the agent doesn't start a session unless the task is urgent


def _fade(distance_h: float, sigma: float) -> float:
    return math.exp(-0.5 * (distance_h / sigma) ** 2)


def shopping_weight(hour: float) -> float:
    """Weight from 0 to 1 for a local time given as hours since midnight (e.g. 14.5 = 2:30 PM)."""
    h = hour % 24
    for start, end in PEAKS:
        if start <= h <= end:
            return 1.0
    (m_start, m_end), (e_start, e_end) = PEAKS
    candidates = [
        _fade((m_start - h) % 24, FADE_MORNING),   # approaching the midday peak
        _fade(h - m_end, FADE_AFTERNOON) if m_end < h < e_start else 0.0,  # leaving midday
        _fade(e_start - h, FADE_AFTERNOON) if m_end < h < e_start else 0.0,  # approaching evening
        _fade((h - e_end) % 24, FADE_NIGHT),       # after the evening peak, wrapping past midnight
    ]
    return max(candidates)


def can_start_now(tz: str, urgent: bool = False, now: datetime | None = None) -> bool:
    """True if the agent may start a session right now in the owner's time zone."""
    local = (now or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    w = shopping_weight(local.hour + local.minute / 60)
    return urgent or w >= MIN_WEIGHT


def pick_start_time(tz: str, after: datetime | None = None, within_h: float = 24.0,
                    rng: random.Random | None = None) -> datetime:
    """Random start time in the next `within_h` hours, drawn in proportion to shopping_weight.

    Rejection sampling at minute resolution, so two calls almost never return the same minute.
    """
    rng = rng or random.Random()
    zone = ZoneInfo(tz)
    base = (after or datetime.now(zone)).astimezone(zone)
    for _ in range(100_000):
        candidate = base + timedelta(minutes=rng.uniform(0, within_h * 60))
        w = shopping_weight(candidate.hour + candidate.minute / 60)
        if w >= MIN_WEIGHT and rng.random() < w:
            return candidate.replace(second=rng.randint(0, 59), microsecond=0)
    raise RuntimeError("No acceptable time found in the window; widen within_h")


if __name__ == "__main__":
    import sys

    tz = sys.argv[1] if len(sys.argv) > 1 else "America/Los_Angeles"
    print(f"Shopping weight by local hour ({tz})\n")
    for half in range(48):
        h = half / 2
        w = shopping_weight(h)
        label = f"{int(h):02d}:{'30' if h % 1 else '00'}"
        flag = "" if w >= MIN_WEIGHT else "  (off)"
        print(f"{label}  {w:4.2f}  {'#' * round(w * 40)}{flag}")
    print("\nNext five sampled start times:")
    rng = random.Random()
    t = datetime.now(ZoneInfo(tz))
    for _ in range(5):
        t = pick_start_time(tz, after=t + timedelta(hours=2), rng=rng)
        print("  ", t.strftime("%a %H:%M:%S %Z"))
