"""Save your Claude API key into .env without typing it into a command.

    .venv/bin/python -m pattie.set_key

Paste the key when asked and press Enter. Nothing is shown while you paste.
"""

import getpass
import os
import sys

ENV = ".env"


def main() -> None:
    key = getpass.getpass("Paste your Claude API key (it won't show) and press Enter: ").strip()
    if not key.startswith("sk-ant-") or len(key) < 40:
        sys.exit("That doesn't look like a Claude API key (it should start with sk-ant- and be "
                 "about 100 characters). Nothing was saved.")
    lines = open(ENV, encoding="utf-8").read().splitlines() if os.path.exists(ENV) else []
    lines = [l for l in lines if not l.startswith("ANTHROPIC_API_KEY=")]
    lines.insert(0, f"ANTHROPIC_API_KEY={key}")
    with open(ENV, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(ENV, 0o600)
    print(f"Saved. Key ends in ...{key[-4:]}. You can start the watcher now.")


if __name__ == "__main__":
    main()
