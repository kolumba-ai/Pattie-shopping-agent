"""Minimal Tilion client over plain HTTP (stdlib only, no installs).

Encodes the rules from the Tilion docs:
  - any 2xx is success at the transport level (session create is 201, release is 204)
  - /v1/scrape, /v1/crawl, /v1/extract can fail inside a 200: check body["status"] == "ok"
  - content_ok on /v1/fetch means "big page", not "success"; captcha is the string "none" when clean
  - retry only 429/502/504, 3 attempts, honour Retry-After; "concurrency cap" 429 is terminal
  - sessions bill until released: use `with client.session() as sid:` so release always happens

Usage:
    from tilion import Tilion
    t = Tilion()
    page = t.scrape("https://example.com", formats=["markdown"])
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

BASE_URL = "https://back.tilion.dev"
DEFAULT_TIMEOUT = 120  # slow sites can take 45s+; a 30s default looks like a transport error
RETRYABLE = {429, 502, 504}
MAX_ATTEMPTS = 3


class TilionError(Exception):
    """Transport-level failure (non-2xx after retries, or a terminal 4xx)."""

    def __init__(self, status: int, detail: Any):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class NotOk(Exception):
    """The call worked but the body says it failed (requires_login, hard_blocked, ...)."""

    def __init__(self, body: dict):
        super().__init__(f"status={body.get('status')!r} blocked_reason={body.get('blocked_reason')!r}")
        self.body = body
        self.status = body.get("status")


def _load_key() -> str:
    key = os.environ.get("TILION_API_KEY")
    if key:
        return key.strip()
    # Nothing loads .env for us, so parse it by hand.
    env = Path(__file__).with_name(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith("TILION_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("Set TILION_API_KEY in the environment or in .env next to tilion.py")


class Tilion:
    def __init__(self, key: str | None = None, base_url: str = BASE_URL, timeout: float = DEFAULT_TIMEOUT):
        self.key = key or _load_key()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ---- transport -------------------------------------------------------

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"

        delay = 0.5
        for attempt in range(1, MAX_ATTEMPTS + 1):
            req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if resp.status == 204 or not raw:
                        return None
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                raw = e.read()
                try:
                    detail = json.loads(raw).get("detail", raw.decode(errors="replace"))
                except Exception:
                    detail = raw.decode(errors="replace")
                terminal = (
                    e.code not in RETRYABLE
                    or attempt == MAX_ATTEMPTS
                    or (e.code == 429 and "concurrency cap" in str(detail))
                )
                if terminal:
                    raise TilionError(e.code, detail) from None
                retry_after = e.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else delay
                except ValueError:
                    wait = delay
                time.sleep(wait)
                delay *= 2

    def _ok(self, body: dict) -> dict:
        if body.get("status") != "ok":
            raise NotOk(body)
        return body

    # ---- stateless reads -------------------------------------------------

    def scrape(self, url: str, formats: list[str] | None = None, schema: dict | None = None,
               prompt: str | None = None) -> dict:
        payload: dict[str, Any] = {"url": url, "formats": formats or ["markdown"]}
        if schema is not None:
            payload["schema"] = schema
        if prompt is not None:
            payload["prompt"] = prompt
        return self._ok(self.request("POST", "/v1/scrape", payload))

    def crawl(self, url: str, max_pages: int = 10, depth: int = 2) -> dict:
        return self._ok(self.request("POST", "/v1/crawl", {"url": url, "max_pages": max_pages, "depth": depth}))

    # ---- sessions --------------------------------------------------------

    def create_session(self, **opts: Any) -> dict:
        return self.request("POST", "/v1/session", opts)

    def release(self, session_id: str) -> None:
        try:
            self.request("DELETE", f"/v1/session/{session_id}")
        except TilionError as e:
            if e.status != 404:  # 404 = already gone, which is fine
                raise

    @contextmanager
    def session(self, **opts: Any) -> Iterator[str]:
        """`with t.session() as sid:` — always released, even on error."""
        sid = self.create_session(**opts)["session_id"]
        try:
            yield sid
        finally:
            self.release(sid)

    def fetch(self, session_id: str, url: str, render: bool = True, include_html: bool = False,
              wait_ms: int | None = None) -> dict:
        payload: dict[str, Any] = {"session_id": session_id, "url": url, "render": render,
                                   "include_html": include_html}
        if wait_ms is not None:
            payload["wait_ms"] = wait_ms
        body = self.request("POST", "/v1/fetch", payload)
        body["captcha_clean"] = body.get("captcha") == "none"
        return body  # judge by body["status"]; content_ok only means "big page"

    def extract(self, session_id: str, url: str, schema: dict | None = None, prompt: str | None = None,
                wait_ms: int | None = None) -> dict:
        payload: dict[str, Any] = {"session_id": session_id, "url": url}
        for k, v in (("schema", schema), ("prompt", prompt), ("wait_ms", wait_ms)):
            if v is not None:
                payload[k] = v
        return self._ok(self.request("POST", "/v1/extract", payload))

    # ---- driving a page --------------------------------------------------

    def navigate(self, session_id: str, url: str) -> dict:
        return self.request("POST", f"/v1/session/{session_id}/navigate", {"url": url})

    def elements(self, session_id: str, text: str | None = None, selector: str | None = None,
                 limit: int = 60) -> list[dict]:
        payload: dict[str, Any] = {"limit": limit}
        if text is not None:
            payload["text"] = text
        if selector is not None:
            payload["selector"] = selector
        body = self.request("POST", f"/v1/session/{session_id}/elements", payload)
        return body.get("elements", [])  # keys are sparse: use el.get("name"), never el["name"]

    def act(self, session_id: str, action: str, **kw: Any) -> dict:
        return self.request("POST", f"/v1/session/{session_id}/act", {"action": action, **kw})

    def screenshot(self, session_id: str, full_page: bool = False, fmt: str = "jpeg") -> dict:
        return self.request("POST", f"/v1/session/{session_id}/screenshot",
                            {"full_page": full_page, "format": fmt})


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    t = Tilion()
    try:
        page = t.scrape(target, formats=["markdown"])
    except NotOk as e:
        print(f"Not ok: {e}")
        sys.exit(1)
    print(f"title: {page.get('title')}")
    print(f"final_url: {page.get('final_url')}")
    print((page.get("markdown") or "")[:800])
