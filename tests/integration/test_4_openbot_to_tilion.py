"""OpenBot -> Pattie tools (MCP over HTTP) -> Tilion.

Offline: starts the tools server over streamable HTTP, the way OpenBot connects, and checks every
tool OpenBot relies on is offered.
Live: Tilion answers with the API key, and a residential session reaches Amazon's homepage.
"""

import os

import pytest

EXPECTED_TOOLS = {"find_offers", "search_products", "build_cart_link", "check_policy", "checkout",
                  "card_status", "freeze_card", "unfreeze_card", "record_order", "where_am_i",
                  "set_location", "next_shopping_time", "remember", "recall"}


def test_openbot_sees_all_pattie_tools(client_offline):
    tools = client_offline.tools()
    assert EXPECTED_TOOLS <= tools, f"missing: {EXPECTED_TOOLS - tools}"


def test_tools_answer_over_http(client_offline):
    loc = client_offline.call("where_am_i")
    assert loc["timezone"] and loc["city"]
    nxt = client_offline.call("next_shopping_time", urgent=True)
    assert nxt["wait_minutes"] == 0


def test_server_rejects_unknown_host_names():
    """DNS-rebinding protection: only allowed Host names are served."""
    import socket
    import subprocess
    import sys
    import time
    from conftest import ROOT, _free_port
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "pattie.mcp_server", "--http", "--port", str(port)],
                            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", method="POST", data=b"{}",
                                     headers={"Host": "evil.example.com", "Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 421
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.live
def test_live_tilion_key_works():
    if not os.environ.get("TILION_API_KEY"):
        from pattie.tools import load_env_file
        load_env_file()
    if not os.environ.get("TILION_API_KEY"):
        pytest.skip("needs TILION_API_KEY")
    from tilion import Tilion
    page = Tilion().scrape("https://example.com", formats=["markdown"])
    assert page["status"] == "ok" and "Example Domain" in (page.get("title") or "")


@pytest.mark.live
def test_live_tilion_residential_reaches_amazon():
    from pattie.tools import load_env_file
    load_env_file()
    if not os.environ.get("TILION_API_KEY"):
        pytest.skip("needs TILION_API_KEY")
    from tilion import Tilion
    t = Tilion()
    with t.session(residential=True) as sid:
        r = t.fetch(sid, "https://www.amazon.com/")
    assert r["status"] == "ok", r.get("status")
    assert "amazon" in (r.get("title") or "").lower()
