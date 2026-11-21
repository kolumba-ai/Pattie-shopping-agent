"""Run one shared Cognee server for the whole team (the server end of pattie/memory.py).

Everyone's Pattie (the tools server, the calendar watcher, a teammate's laptop over Tailscale)
sets COGNEE_SERVICE_URL to this server, so all of them read and write the same memory.

Same settings as local memory: Claude as Cognee's LLM, local fastembed embeddings, Turso graph,
telemetry off. Data lives in PATTIE_COGNEE_DIR (default .cognee/). Access control is off, so only
expose it on a private network (the `pattie` Docker network, Tailscale), never publicly.

    python -m pattie.cognee_server --host 0.0.0.0 --port 8000
"""

import argparse
import os


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Shared Cognee server for Pattie")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

    from .tools import load_env_file  # no Amplitude dependency (the watcher's needs amplitude)

    load_env_file()
    os.environ.pop("COGNEE_SERVICE_URL", None)  # this process IS the server
    os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
    from . import memory

    memory._configure()
    from cognee.api.client import start_api_server

    start_api_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
