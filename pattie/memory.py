"""Pattie's long-term memory of its owner, stored in Cognee.

Everything runs locally: Cognee's knowledge graph lives in .cognee/ in this project
(Turso graph + LanceDB vectors + SQLite), embeddings come from a small local model
(fastembed), and Claude is the LLM that builds the graph and answers questions.
No data goes to Cognee's servers, and Cognee telemetry is off.

    python -m pattie.memory seed demo/anna_susan_profile.md   # load a profile
    python -m pattie.memory ask "What's Anna Susan's gift budget?"
    python -m pattie.memory note "Anna Susan declined energy drinks for Hackathon SF"
    python -m pattie.memory reset                              # forget everything

In code:
    mem = Memory("anna_susan")
    mem.remember("She already owns a neck pillow.")
    mem.context_for_event(event)   # what matters for this event, or ""
"""

import argparse
import asyncio
import contextlib
import io
import os
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORE = Path(os.environ.get("PATTIE_COGNEE_DIR") or PROJECT_ROOT / ".cognee")
DEFAULT_USER = os.environ.get("PATTIE_USER", "anna_susan")
MODEL = os.environ.get("PATTIE_MODEL", "claude-sonnet-5-5")


def _configure() -> None:
    """Set Cognee's settings before it's imported. Explicit env vars still win."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    defaults = {
        "LLM_PROVIDER": "anthropic",
        "LLM_MODEL": f"anthropic/{MODEL}",
        "LLM_API_KEY": key,
        # Anthropic has no embeddings; use a small free local model instead of OpenAI.
        "EMBEDDING_PROVIDER": "fastembed",
        "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
        "EMBEDDING_DIMENSIONS": "384",
        # Cognee's default graph DB (Ladybug) needs Homebrew's OpenSSL; Turso doesn't.
        "GRAPH_DATABASE_PROVIDER": "turso",
        "GRAPH_DATASET_DATABASE_HANDLER": "turso",
        "SYSTEM_ROOT_DIRECTORY": str(STORE / "system"),
        "DATA_ROOT_DIRECTORY": str(STORE / "data"),
        "TELEMETRY_DISABLED": "1",
        # Session memory (per-conversation turn analysis) isn't used by Pattie; it costs
        # an extra Claude call per question and its schema trips Claude's validator.
        "CACHING": "false",
        "LOG_LEVEL": os.environ.get("PATTIE_MEMORY_LOG_LEVEL", "ERROR"),
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, v)
    if os.environ["GRAPH_DATABASE_PROVIDER"] == "turso" and "kuzu" not in sys.modules:
        # One of Cognee's startup migrations does `import kuzu` just to read its version,
        # and kuzu pulls in Ladybug's native library. We don't use Kuzu, so a stand-in
        # module is enough for that migration to skip cleanly.
        stub = types.ModuleType("kuzu")
        stub.__version__ = "0.0.0-not-used"
        sys.modules["kuzu"] = stub


def _text(result) -> str:
    """Answer text from a recall result: an object locally, possibly a dict from a server."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("text") or (result.get("raw") or {}).get("value") or ""
    return getattr(result, "text", None) or ""


class Memory:
    def __init__(self, user: str = DEFAULT_USER):
        _configure()
        import cognee  # noqa: E402  (must come after _configure)
        import litellm

        litellm.suppress_debug_info = True

        self.cognee = cognee
        self.user = user
        self.dataset = f"pattie_{user}"
        self.loop = asyncio.new_event_loop()  # Cognee keeps engines bound to one loop

        # Shared memory: with COGNEE_SERVICE_URL (+ COGNEE_API_KEY for Cognee Cloud), every
        # remember/recall goes to that one Cognee instance, so the team's laptops and the
        # server all see the same memory. Without it, memory stays in .cognee/ on this machine.
        url = os.environ.get("COGNEE_SERVICE_URL", "").strip()
        if url:
            self._run(cognee.serve(url=url, api_key=os.environ.get("COGNEE_API_KEY") or None))
            self.where = url
        else:
            self.where = f"local ({STORE})"

    def _run(self, coro):
        # Cognee prints setup chatter ("table already exists") to stdout; keep it quiet.
        with contextlib.redirect_stdout(io.StringIO()):
            return self.loop.run_until_complete(coro)

    def remember(self, text: str) -> None:
        self._run(self.cognee.remember(text, dataset_name=self.dataset))

    def ask(self, question: str) -> str:
        try:
            results = self._run(self.cognee.recall(question, datasets=[self.dataset]))
        except Exception as e:  # empty memory raises; treat as "nothing known"
            if "not found" in str(e).lower() or "no data" in str(e).lower():
                return ""
            raise
        return "\n".join(t for t in (_text(r) for r in results or []) if t).strip()

    def context_for_event(self, event: dict) -> str:
        """What Pattie remembers that matters for buying things for this event."""
        question = (
            f"Pattie is deciding what to offer to buy for the calendar event "
            f"'{event['title']}' ({event.get('location') or 'no location'}; "
            f"{event.get('description') or 'no description'}). "
            "List only the facts from memory that should change that decision: budgets, "
            "favorite or avoided brands, things already owned, dislikes, dietary needs, "
            "facts about people involved, and past accepted or declined suggestions. "
            "If nothing relevant is in memory, answer exactly: nothing relevant."
        )
        answer = self.ask(question)
        return "" if not answer or "nothing relevant" in answer.lower() else answer

    def remember_feedback(self, event_title: str, item: str, accepted: bool,
                          note: str = "") -> None:
        verb = "accepted" if accepted else "declined"
        text = f"{self.user.replace('_', ' ').title()} {verb} Pattie's suggestion of " \
               f"{item} for '{event_title}'."
        self.remember(f"{text} {note}".strip())

    def reset(self) -> None:
        self._run(self.cognee.forget(dataset=self.dataset))


def main(argv=None) -> None:
    from .watcher import load_env_file

    load_env_file(str(PROJECT_ROOT / ".env"))
    p = argparse.ArgumentParser(description="Pattie memory (Cognee)")
    p.add_argument("--user", default=DEFAULT_USER)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="remember the contents of a text/markdown file") \
        .add_argument("file")
    sub.add_parser("note", help="remember one fact").add_argument("text")
    sub.add_parser("ask", help="ask what Pattie remembers").add_argument("question")
    sub.add_parser("reset", help="forget everything about this user")
    sub.add_parser("where", help="show which Cognee this machine uses (local or shared)")
    args = p.parse_args(argv)

    mem = Memory(args.user)
    if args.cmd == "where":
        print(f"Memory for {args.user}: {mem.where}")
        return
    if args.cmd == "seed":
        mem.remember(Path(args.file).read_text(encoding="utf-8"))
        print(f"Remembered {args.file} for {args.user}.")
    elif args.cmd == "note":
        mem.remember(args.text)
        print("Remembered.")
    elif args.cmd == "ask":
        print(mem.ask(args.question) or "(nothing in memory yet)")
    elif args.cmd == "reset":
        mem.reset()
        print(f"Forgot everything about {args.user}.")


if __name__ == "__main__":
    main()
