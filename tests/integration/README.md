# Integration tests

One file per connection in [ARCHITECTURE.md](../../docs/ARCHITECTURE.md). Run them one at a time.

| # | File | What it checks |
|---|---|---|
| 1 | `test_1_calendar_to_pattie.py` | Google Calendar → watcher → bridge → agent → Telegram (calendar events and food nudges) |
| 2 | `test_2_1password.py` | Secrets are 1Password references only, and never appear in tool results |
| 3 | `test_3_telegram.py` | Pattie's Telegram bot: token works, messages reach the owner (bot, pairing and delivery are also in `tests/test_agent.py`) |
| 4 | `test_4_tilion.py` | Tilion works; the optional MCP server offers every tool |
| 5 | `test_5_amazon_search.py` | Amazon search and ranking: one pick or three, sponsored labels, budgets |
| 6 | `test_6_cart.py` | Amazon add-to-cart link and policy-checked checkout. Reading and deleting cart items are skipped tests (they'd need signed-in agent access, which Amazon flagged) |

Every file has **offline** tests (fakes and saved results, no network, no keys) and **live** tests (real services), which run only with `PATTIE_LIVE=1`.

## Setup (once)

Needs Python 3.10 or newer.

```bash
git clone https://github.com/kolumba-ai/Pattie-shopping-agent.git
cd Pattie-shopping-agent
python3 -m pip install --user uv
python3 -m uv venv --python 3.12 .venv
python3 -m uv pip install --python .venv/bin/python pytest "mcp>=1.12,<2" anthropic google-api-python-client google-auth amplitude-analytics singlestoredb
```

Cognee (in `requirements.txt`) isn't needed for these tests.

## Run offline tests, one file at a time

```bash
.venv/bin/python -m pytest tests/integration/test_1_calendar_to_pattie.py -v
.venv/bin/python -m pytest tests/integration/test_2_1password.py -v
.venv/bin/python -m pytest tests/integration/test_3_telegram.py -v
.venv/bin/python -m pytest tests/integration/test_4_tilion.py -v
.venv/bin/python -m pytest tests/integration/test_5_amazon_search.py -v
.venv/bin/python -m pytest tests/integration/test_6_cart.py -v
```

Live tests show as **skipped** unless you set `PATTIE_LIVE=1`.

## Run live tests

Live tests use real accounts. Settings come from your environment or a `.env` file in the repo root (gitignored; see `.env.example`). Never commit keys.

| Test | Needs |
|---|---|
| 1, Google Calendar | `GOOGLE_APPLICATION_CREDENTIALS` (service-account JSON path), `PATTIE_CALENDAR_ID` |
| 2, 1Password | The `op` CLI and `OP_SERVICE_ACCOUNT_TOKEN`; run inside the `pattie` container |
| 3, Telegram | `TELEGRAM_BOT_TOKEN`; optionally `PATTIE_TELEGRAM_CHAT_ID` to send one test message |
| 4, 5, 6, Tilion and Amazon search | `TILION_API_KEY` |

```bash
PATTIE_LIVE=1 .venv/bin/python -m pytest tests/integration/test_5_amazon_search.py -v -m live
```

## Running them with Claude Code

Open the cloned repo in Claude Code and ask, for example: "Run tests/integration/test_1_calendar_to_pattie.py and explain any failures." Each of you can run them on your own machine and account; the offline tests need no keys.
