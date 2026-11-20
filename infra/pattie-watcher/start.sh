#!/bin/sh
# Fetch the Google service-account key from 1Password into memory-backed /tmp, then run the
# watcher and pipe its decisions into the bridge, with secrets injected by `op run`.
set -eu
op document get "${GOOGLE_SA_DOCUMENT:-Google Calendar SA}" --vault "${OP_VAULT:-Engineering}" \
   --out-file /tmp/google-sa.json --force >/dev/null
chmod 600 /tmp/google-sa.json
exec op run --env-file=/app/secrets.env -- sh -c \
  "python -m pattie.watcher --json --no-memory --credentials /tmp/google-sa.json \
   --calendar-id \"\$PATTIE_CALENDAR_ID\" --interval \"\${PATTIE_INTERVAL:-60}\" \
   | python -m pattie.openbot_bridge"
