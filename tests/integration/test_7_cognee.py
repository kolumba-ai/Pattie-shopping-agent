"""Pattie -> Cognee memory (the pattie-cognee server, over HTTP).

Offline: the client builds the right requests (checked against a tiny local HTTP server).
Live: needs COGNEE_SERVICE_URL (on the VPS: http://pattie-cognee:8000). Saves a unique note and
asks memory a question; Cognee builds its graph in the background, so the answer may lag.
"""

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class FakeCognee(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        FakeCognee.seen.append((self.path, self.headers["Content-Type"], body))
        reply = [{"search_result": "Pattie found an Anker power bank for $29.99."}] if self.path.endswith("recall") else {}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_empty_memory_reads_as_nothing_known():
    from pattie.cognee_client import CogneeClient, CogneeError
    mem = CogneeClient("http://127.0.0.1:1")
    mem._post = lambda *a: (_ for _ in ()).throw(CogneeError(
        "Cognee 404: No searchable memory in dataset 'x': The knowledge graph is empty. [NoDataError]"))
    assert mem.ask("anything?") == ""


def test_client_sends_remember_and_recall():
    from pattie.cognee_client import CogneeClient
    server = HTTPServer(("127.0.0.1", 0), FakeCognee)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        mem = CogneeClient(f"http://127.0.0.1:{server.server_port}", user="anna_susan")
        mem.remember("Pattie found an Anker power bank for $29.99.")
        answer = mem.ask("What power bank did Pattie find?")
    finally:
        server.shutdown()
    (p1, ct1, b1), (p2, ct2, b2) = FakeCognee.seen[-2:]
    assert p1 == "/api/v1/remember" and ct1.startswith("multipart/form-data")
    assert b"Anker power bank" in b1 and b"pattie_anna_susan" in b1
    assert p2 == "/api/v1/recall" and json.loads(b2) == {"query": "What power bank did Pattie find?",
                                                         "datasets": ["pattie_anna_susan"]}
    assert "Anker" in answer


@pytest.mark.live
def test_live_cognee_remember_and_recall():
    url = os.environ.get("COGNEE_SERVICE_URL")
    if not url:
        pytest.skip("needs COGNEE_SERVICE_URL (run on the VPS, on the pattie network)")
    from pattie.cognee_client import CogneeClient
    mem = CogneeClient(url, user="integration_test")
    marker = uuid.uuid4().hex[:8]
    mem.remember(f"Integration test {marker}: the owner prefers USB-C cables in white.")
    import time
    answer, deadline = "", time.time() + 180      # Cognee builds the graph in the background
    while time.time() < deadline:
        answer = mem.ask("What color of USB-C cable does the owner prefer?")
        if answer:
            break
        time.sleep(10)
    assert "white" in answer.lower(), f"memory didn't answer within 3 minutes: {answer!r}"
