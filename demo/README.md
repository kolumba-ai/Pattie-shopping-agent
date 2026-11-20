# Demo Google Calendar

Pattie reads one test calendar during the demo. This sets it up.

## 1. Create the demo Google account

Make a new Gmail account just for the demo (for example `pattie.demo.<team>@gmail.com`).
Keep the login in your team's password manager, not in this repo.

## 2. Generate and import the seed events

```bash
python3 demo/gen_seed_calendar.py --demo-date YYYY-MM-DD
```

Dates are relative to the demo day, so run it again if the demo moves.

| Day | Event | What Pattie should do |
| --- | --- | --- |
| +1 | Hackathon SF, 8am–9pm | Power bank and snacks (ASAP tier), breakfast nudge the night before |
| +2 | Dentist | Skip |
| +3 | 1:1 with Mauricio | Skip |
| +5 | AI Meetup, ends 7pm | Dinner nudge at 6pm |
| +6 | Flight to NY | Three travel adapter options (this-week tier) |
| +21 | Lisa's birthday | Gift goes to the long-term basket |

In Google Calendar, signed in as the demo account:

1. Create a new calendar named **Pattie Demo** (Settings → Add calendar → Create new calendar), time zone Pacific.
2. Settings → Import & export → Import → choose `demo/pattie-demo-calendar.ics` → add to **Pattie Demo**.
3. Under the calendar's settings → Integrate calendar, copy the **Calendar ID**. The backend needs it.
   (If you put events straight into the account's main calendar instead, its Calendar ID is the Gmail address.)

To reset: delete the Pattie Demo calendar, create it again, and import a fresh file.

## 3. Give the backend read access (service account)

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project (e.g. `pattie-demo`).
2. APIs & Services → Library → enable **Google Calendar API**.
3. IAM & Admin → Service accounts → create one (e.g. `pattie-calendar`). It needs no roles.
4. Open it → Keys → Add key → JSON. Save it as `secrets/service-account.json` (git ignores `secrets/`) and share it with the backend owner privately, never in the repo or a public channel.
5. Back in Google Calendar → Pattie Demo → Share with specific people → add the service account's email
   (`pattie-calendar@<project>.iam.gserviceaccount.com`) with **See all event details**.

Backend config:

```
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
PATTIE_CALENDAR_ID=<calendar id from step 2.3>
```

Read with the `https://www.googleapis.com/auth/calendar.readonly` scope.

## 4. Run the calendar watcher

Needs Python 3.10 or newer (Cognee requires it). If your Mac only has Python 3.9, `uv` installs
a newer one without Homebrew:

```bash
python3 -m pip install --user uv
python3 -m uv python install 3.12
python3 -m uv venv --python 3.12 .venv
python3 -m uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m pattie.set_key     # paste your Claude API key; saved to .env (git-ignored)
.venv/bin/python -m pattie.watcher \
    --calendar-id <calendar id> \
    --credentials secrets/service-account.json
```

Settings can also live in `.env` (`ANTHROPIC_API_KEY`, `PATTIE_CALENDAR_ID`,
`GOOGLE_APPLICATION_CREDENTIALS`); then `.venv/bin/python -m pattie.watcher` needs no flags.

It checks the calendar every 60 seconds (`--interval`). The first run reads every event; after
that Google's sync token means each check only returns what was added, edited or deleted.
Each new or edited event in the next 30 days gets a decision:

```
[      new] Hackathon SF  2026-11-21T08:00:00-08:00
  -> Hackathon SF (full_day_event, in 1 days)
     NEEDS STUFF [asap]: Long day of building, needs power and snacks.
       - power bank: ...
     food: ask about breakfast at 2026-11-20T20:00:00-08:00 (Full-day event starts at 8:00AM.)
```

- Claude (`claude-sonnet-5-5`, change with `PATTIE_MODEL`) decides whether the event needs
  products and which item types.
- Urgency tier and food nudges follow the PRD rules in code, so they don't vary on stage.
- Decisions are saved in `pattie.sqlite3`; delete it to start fresh.
- `--now 2026-11-20T20:00` sets the demo clock. `--json` prints one JSON line per decision for the
  next step (WhatsApp proposal) to read. `--once` polls once and exits.

Test without Google, using the seed file:

```bash
.venv/bin/python -m pattie.watcher --ics demo/pattie-demo-calendar.ics --once --db /tmp/pattie-test.sqlite3
```

## 5. Memory (Cognee)

Pattie remembers its owner with [Cognee](https://github.com/topoteretes/cognee): budgets, brands,
things they already own, dislikes, people in their life, and which suggestions they accepted or
declined. The watcher asks Cognee what matters for each event before Claude decides, so the
picks get personal: cold brew instead of energy drinks, no neck pillow if she owns one, a
gardening gift for Lisa instead of another cookbook.

```bash
.venv/bin/python -m pattie.memory seed demo/anna_susan_profile.md     # load the demo persona
.venv/bin/python -m pattie.memory ask "What's Anna Susan's gift budget?"
.venv/bin/python -m pattie.memory note "Anna Susan declined the cold brew for Hackathon SF"
.venv/bin/python -m pattie.memory reset                                # forget everything
```

- Everything stays on this machine, in `.cognee/` (git-ignored): Turso graph, LanceDB vectors,
  SQLite. Embeddings come from a small local model, Claude is the LLM, and Cognee telemetry is off.
  That fits our "Ownership" tenet.
- The watcher uses memory automatically; `--no-memory` turns it off, `--user` picks whose memory.
- Each event costs one extra Claude call for the memory lookup (about 5 to 10 seconds).
- Why Turso and not Cognee's default graph DB: the default (Ladybug) needs Homebrew's OpenSSL.
  `pattie/memory.py` explains the small workaround that makes Turso start cleanly.

## 6. Products from Amazon (search and ranking)

For each item Pattie suggests, Claude also writes an Amazon search term that uses memory
("Anker power bank", "unscented travel toiletries kit TSA"), a category and whether the choice
is obvious. Then:

1. `pattie/offers.py` searches Amazon live through Tilion, reusing `amazon_search.py`
   (sold by Amazon.com only). Needs `TILION_API_KEY` in the environment or `.env`.
2. `pattie/ranking.py` picks what to show (PRD flow 4):
   - drops products over the owner's budget for the category, missing a must-have
     (unscented toiletries), or containing something they avoid (energy drinks, meat);
   - scores preferred brand, rating, reviews, price and delivery; **Sponsored gets no boost**;
   - **one best pick** when the budget is known, the item is specific, and one product is
     clearly ahead; otherwise **three options**, each labeled with its real trade-off
     (cheapest, best rated, arrives soonest);
   - over the $75 per-purchase limit: a cart link instead of an order.
3. Exact values for ranking (budgets, brands, must-haves) are in `demo/anna_susan_prefs.json`
   (Postgres later). Cognee has the same facts as text for Claude.

```bash
.venv/bin/python -m pattie.watcher                                  # live Amazon search
.venv/bin/python -m pattie.watcher --search-results saved.json      # rank saved results
.venv/bin/python tests/test_ranking.py                              # ranking tests
```

Note for `amazon_search.py`: it doesn't read delivery dates yet. Until it does, Pattie can't
filter out items that arrive after the event, and it never claims "arrives soonest" (the third
option becomes "most reviewed" or "good all-rounder").

## 7. Pattie tools MCP server (for OpenBot)

`pattie/mcp_server.py` exposes Pattie's tools to the OpenBot agent over MCP
(ARCHITECTURE.md, "Tools"). It wraps the search and ranking above and adds the spending policy,
a purchase ledger, location and shopping-hours timing.

```bash
.venv/bin/python -m pattie.mcp_server            # stdio: OpenBot starts it as a command
.venv/bin/python -m pattie.mcp_server --http     # or streamable HTTP on 127.0.0.1:8765
.venv/bin/python -m pytest tests/                # all tests, no network
```

| Tool | What it does |
| --- | --- |
| `find_offers` | Search Amazon for one item and rank: one best pick or three options |
| `search_products` | Raw results for several queries (prefer `find_offers`) |
| `build_cart_link` | One link that adds products to the owner's own cart |
| `check_policy` | allow / ask / wait / block against that vendor's limits in `config/policy.json` |
| `checkout` | Buys after a yes, with that vendor's Privacy card. **Not connected yet**: returns a fallback (Amazon cart link, or the DoorDash restaurant link) |
| `card_status` | Per vendor card (amazon, doordash): spend, what's left, recent purchases, state at Privacy |
| `freeze_card` / `unfreeze_card` | Pause or reopen one vendor's Privacy card |
| `where_am_i` / `set_location` | Current city and time zone (chat > calendar event in progress > default), where deliveries go |
| `next_shopping_time` | When to browse next, drawn from the shopping-hours curve in local time |
| `remember` / `recall` | Cognee memory through `pattie/memory.py` |

- **Cards:** one Privacy.com card per vendor, merchant-locked with a monthly limit (example
  values: Amazon $300, DoorDash $150). `pattie/privacy_cards.py` reads card state and spend and can
  pause a card; it never handles card numbers. Settings: `PRIVACY_API_KEY`,
  `PATTIE_CARD_TOKEN_AMAZON`, `PATTIE_CARD_TOKEN_DOORDASH` (from 1Password), `PRIVACY_SANDBOX=1`
  for Privacy's sandbox. Without a key, `card_status` shows Pattie's own limits only.
- **Spending policy:** `config/policy.json`, per vendor (Amazon: $75 per purchase, $150 a day,
  $300 a month; DoorDash: $40, $80, $150) plus $400 a month overall, quiet hours 10pm–7am, and
  auto-approve at $0 so every purchase needs a yes. `ranking.py` still has its own
  `PER_PURCHASE_LIMIT = 75`; keep it in sync with the Amazon limit until ranking reads the policy.
- The ledger, location facts and the watcher's events share one SQLite file (`PATTIE_DB`,
  default `pattie.sqlite3`), so `where_am_i` sees calendar events in progress.
- Settings: `PATTIE_TZ`, `PATTIE_HOME_CITY`, `PATTIE_PREFS`, `PATTIE_POLICY`, `PATTIE_USER`, and
  `PATTIE_SEARCH_RESULTS` (a saved-results JSON file instead of live Amazon, as an on-stage fallback).
