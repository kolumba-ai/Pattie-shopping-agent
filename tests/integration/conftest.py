"""Shared setup for the integration tests (one file per connection in ARCHITECTURE.md).

Offline tests use fakes and saved search results, so they run anywhere.
Live tests talk to the real services and run only with PATTIE_LIVE=1:

    .venv/bin/python -m pytest tests/integration/test_1_calendar_to_openbot.py -v
    PATTIE_LIVE=1 .venv/bin/python -m pytest tests/integration/test_4_openbot_to_tilion.py -v -m live
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LIVE = os.environ.get("PATTIE_LIVE") == "1"


def _load_dotenv() -> None:
    """Live tests read settings from the environment or the repo's git-ignored .env (README).
    Values already in the environment win. Relative file paths are resolved from the repo root."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not value or os.environ.get(key):
            continue
        if key == "GOOGLE_APPLICATION_CREDENTIALS" and not os.path.isabs(value):
            value = str(ROOT / value)
        os.environ[key] = value


if LIVE:
    _load_dotenv()


def pytest_configure(config):
    config.addinivalue_line("markers", "live: talks to real services; needs PATTIE_LIVE=1")


def pytest_collection_modifyitems(config, items):
    if LIVE:
        return
    skip = pytest.mark.skip(reason="live test: set PATTIE_LIVE=1 to run it")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def product(asin, title, price, rating=4.6, reviews="5,000", sponsored=False):
    return {"asin": asin, "title": title, "price": price, "rating": str(rating), "reviews": reviews,
            "sponsored": sponsored, "url": f"https://www.amazon.com/dp/{asin}",
            "image": f"https://m.media-amazon.com/images/I/{asin}.jpg"}


SAVED_RESULTS = {
    "Anker power bank": [
        product("TESTPB0001", "Anker Power Bank 10000mAh PowerCore Slim", 29.99, 4.7, "52,310"),
        product("TESTPB0002", "Generic 20000mAh Power Bank", 24.99, 4.3, "3,105", sponsored=True),
        product("TESTPB0003", "Brand X Power Bank 10000mAh", 27.50, 4.4, "1,980"),
    ],
    "universal travel adapter": [
        product("TESTTA0001", "Universal Travel Adapter, Type A/C/G/I", 19.99, 4.6, "12,400"),
        product("TESTTA0002", "All-in-One Travel Adapter with USB-C", 24.99, 4.7, "8,100", sponsored=True),
        product("TESTTA0003", "Premium Travel Adapter 65W GaN", 79.99, 4.8, "2,300"),
        product("TESTTA0004", "Basic Travel Plug Adapter 3-Pack", 9.79, 4.4, "20,500"),
    ],
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_mcp_server(tmp_path, saved: bool):
    """Start pattie.mcp_server over streamable HTTP, the way OpenBot connects to it."""
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PATTIE_DB": str(tmp_path / "pattie.sqlite3")}
    if saved:
        results = tmp_path / "saved.json"
        results.write_text(json.dumps(SAVED_RESULTS))
        env["PATTIE_SEARCH_RESULTS"] = str(results)
    else:
        env.pop("PATTIE_SEARCH_RESULTS", None)
    proc = subprocess.Popen([sys.executable, "-m", "pattie.mcp_server", "--http", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 20
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return proc, f"http://127.0.0.1:{port}/mcp"
        if proc.poll() is not None:
            raise RuntimeError(f"MCP server exited: {proc.stderr.read().decode()[-500:]}")
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError("MCP server didn't start within 20 seconds")


@pytest.fixture
def mcp_offline(tmp_path):
    """Tools server with saved Amazon results: no network."""
    proc, url = _start_mcp_server(tmp_path, saved=True)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def mcp_live(tmp_path):
    """Tools server that searches Amazon through Tilion for real (needs TILION_API_KEY)."""
    proc, url = _start_mcp_server(tmp_path, saved=False)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


class MCPClient:
    """Calls tools on the MCP server over streamable HTTP, like OpenBot does."""

    def __init__(self, url):
        self.url = url

    async def _session(self, fn):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(self.url) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await fn(s)

    def tools(self) -> set:
        async def go(s):
            return {t.name for t in (await s.list_tools()).tools}
        return asyncio.run(self._session(go))

    def call(self, name: str, **args) -> dict:
        async def go(s):
            res = await s.call_tool(name, args)
            assert not res.isError, res.content[0].text if res.content else res
            return json.loads(res.content[0].text)
        return asyncio.run(self._session(go))


@pytest.fixture
def client_offline(mcp_offline):
    return MCPClient(mcp_offline)


@pytest.fixture
def client_live(mcp_live):
    return MCPClient(mcp_live)
