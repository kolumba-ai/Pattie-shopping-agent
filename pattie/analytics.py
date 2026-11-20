"""Amplitude client for Pattie (ARCHITECTURE.md, "Amplitude and Statsig").

One shared client per process. The project API key is public and ingestion-scoped; it lives
in the git-ignored .env as AMPLITUDE_API_KEY. Without it, Pattie runs and sends nothing.

Import after .env is loaded (the watcher and tools load it at startup).
"""

import os

from amplitude import Amplitude

_key = os.environ.get("AMPLITUDE_API_KEY")
if not _key:
    print("[amplitude] AMPLITUDE_API_KEY is not set — no events will be sent")
    client = None
else:
    client = Amplitude(_key)
    # server_zone defaults to "US"; set "EU" only for EU-residency projects.
    client.configuration.server_zone = "US"


def flush_and_wait() -> None:
    """For short runs (--once): make sure queued events are sent before the process exits."""
    if client is None:
        return
    for future in client.flush() or []:
        if future is not None:
            future.result()
