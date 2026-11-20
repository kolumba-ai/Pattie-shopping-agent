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
