"""A small HTTP client for Pattie's Cognee server (pattie/cognee_server.py, container pattie-cognee).

Same interface as pattie.memory.Memory for what the agent needs, without installing Cognee:

    mem = CogneeClient("http://pattie-cognee:8000", user="anna_susan")
    mem.remember("For Hackathon SF, Pattie found an INIU power bank for $20.39.")
    mem.ask("What power banks has Pattie found?")

Data goes to the dataset "pattie_<user>", the same one pattie.memory uses.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid


class CogneeError(Exception):
    pass


class CogneeClient:
    def __init__(self, base_url: str, user: str = "anna_susan", timeout: float = 120):
        self.base, self.user, self.timeout = base_url.rstrip("/"), user, timeout
        self.dataset = f"pattie_{user}"
        self.where = self.base

    def _post(self, path: str, data: bytes, content_type: str):
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": content_type, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise CogneeError(f"Cognee {e.code}: {e.read().decode(errors='replace')[:200]}") from None
        except urllib.error.URLError as e:
            raise CogneeError(f"Cognee unreachable: {e.reason}") from None

    def remember(self, text: str) -> None:
        """Add text to memory; Cognee builds the graph in the background."""
        boundary = uuid.uuid4().hex
        parts = [("raw_data", text), ("datasetName", self.dataset), ("run_in_background", "true")]
        body = b"".join(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
            for k, v in parts) + f"--{boundary}--\r\n".encode()
        self._post("/api/v1/remember", body, f"multipart/form-data; boundary={boundary}")

    def ask(self, question: str) -> str:
        try:
            result = self._post("/api/v1/recall", json.dumps(
                {"query": question, "datasets": [self.dataset]}).encode(), "application/json")
        except CogneeError as e:
            if "not found" in str(e).lower() or "no data" in str(e).lower():
                return ""
            raise
        return _text(result).strip()


def _text(result) -> str:
    """Recall answers come back in a few shapes; pull out the readable text."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(t for t in (_text(r) for r in result) if t)
    if isinstance(result, dict):
        for key in ("text", "answer", "search_result", "result", "content"):
            if key in result:
                return _text(result[key])
        return json.dumps(result)[:500]
    return str(result)
