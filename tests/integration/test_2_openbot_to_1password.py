"""1Password: secrets reach Pattie's services by reference, and never leak to the agent.

Offline: the container secret files hold only op:// references, and no tool result ever contains
a secret value.
Live: resolves every reference with the 1Password CLI and the service-account token (values are
checked, never printed).

Not testable from here: OpenBot's own 1Password app (Marketplace -> Apps -> 1Password). OpenBot
has no API to query it; check it shows "Connected" in the app.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

from conftest import ROOT

REF = re.compile(r"^op://[^/]+/[^/]+/[^/]+$")
SECRET_FILES = [ROOT / "infra/pattie-tools/secrets.env", ROOT / "infra/pattie-watcher/secrets.env"]


def _references():
    refs = {}
    for f in SECRET_FILES:
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                refs[f"{f.parent.name}:{key}"] = value
    return refs


def test_secret_files_hold_only_references():
    refs = _references()
    assert refs, "no secret references found"
    for name, value in refs.items():
        assert REF.match(value), f"{name} isn't an op:// reference"


def test_no_token_or_key_committed():
    for f in [*SECRET_FILES, ROOT / "infra/pattie-tools/compose.yaml", ROOT / "infra/pattie-watcher/compose.yaml"]:
        text = f.read_text()
        assert "ops_" not in text, f"{f} looks like it contains a 1Password token"
        assert not re.search(r"sk-ant-[A-Za-z0-9]", text), f"{f} looks like it contains an Anthropic key"


def test_tool_results_never_contain_secrets(client_offline, monkeypatch):
    # The server loads the environment and .env; these values must never come back.
    from pattie.tools import load_env_file
    load_env_file()
    secrets = [v for k, v in os.environ.items()
               if k in ("TILION_API_KEY", "ANTHROPIC_API_KEY", "PRIVACY_API_KEY", "OP_SERVICE_ACCOUNT_TOKEN") and v]
    if not secrets:
        pytest.skip("no secrets in the environment or .env to check for (set TILION_API_KEY to run this)")
    results = [client_offline.call("card_status"), client_offline.call("where_am_i"),
               client_offline.call("check_policy", total=20.0, titles=["cable"]),
               client_offline.call("find_offers", item="power bank", search_query="Anker power bank",
                                   category="accessories", days_until=1)]
    blob = json.dumps(results)
    for s in secrets:
        assert s not in blob, "a secret value appeared in a tool result"


@pytest.mark.live
def test_live_references_resolve_in_1password():
    if not shutil.which("op"):
        pytest.skip("1Password CLI (op) not installed here; run inside the pattie-tools container")
    if not os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
        pytest.skip("needs OP_SERVICE_ACCOUNT_TOKEN (the server's token file)")
    for name, ref in _references().items():
        out = subprocess.run(["op", "read", ref], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, f"{name}: 1Password couldn't resolve it ({out.stderr.strip()[:120]})"
        assert out.stdout.strip(), f"{name}: resolved to an empty value"
