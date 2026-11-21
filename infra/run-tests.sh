#!/bin/sh
# Run every test (unit + integration, live included) on the VPS, inside a throwaway copy of the
# pattie container, with the real secrets from 1Password. Nothing is left behind.
#   bash /opt/pattie/infra/run-tests.sh                      # all tests
#   bash /opt/pattie/infra/run-tests.sh tests/integration/test_2_1password.py
# Set PATTIE_TELEGRAM_CHAT_ID to also send one test message to the owner's Telegram chat.
set -eu
TARGET="${*:-tests}"
docker run --rm --network pattie --user root --tmpfs /tmp \
  --env-file /etc/pattie/op-token.env --env-file /etc/pattie/watcher.env \
  -e PATTIE_LIVE=1 -e PYTHONDONTWRITEBYTECODE=1 -e COGNEE_SERVICE_URL=http://pattie-cognee:8000 \
  -e PATTIE_TELEGRAM_CHAT_ID="${PATTIE_TELEGRAM_CHAT_ID:-}" \
  -v /opt/pattie:/repo:ro -w /repo --entrypoint sh pattie-pattie -c "
    pip install -q pytest 'mcp>=1.12,<2' >/dev/null 2>&1
    op document get 'Google Calendar SA' --vault Engineering --out-file /tmp/sa.json --force >/dev/null
    GOOGLE_APPLICATION_CREDENTIALS=/tmp/sa.json op run --env-file=/repo/infra/pattie/secrets.env -- \
      python -m pytest $TARGET -v -p no:warnings -p no:cacheprovider"
