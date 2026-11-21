# Pattie tools MCP server, with the 1Password CLI for secrets (ARCHITECTURE.md, "1Password").
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -sS https://downloads.1password.com/linux/keys/1password.asc \
    | gpg --dearmor -o /usr/share/keyrings/1password-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/$(dpkg --print-architecture) stable main" \
    > /etc/apt/sources.list.d/1password.list \
 && apt-get update && apt-get install -y --no-install-recommends 1password-cli \
 && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY infra/pattie-tools/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY pattie/ /app/pattie/
COPY config/ /app/config/
COPY demo/anna_susan_prefs.json /app/demo/anna_susan_prefs.json
COPY demo/anna_susan_profile.md /app/demo/anna_susan_profile.md
COPY amazon_search.py tilion.py shopping_hours.py /app/
COPY infra/pattie-tools/secrets.env /app/secrets.env

RUN useradd --system --uid 10001 --home /data pattie && mkdir -p /data && chown pattie /data
USER pattie
ENV PATTIE_DB=/data/pattie.sqlite3 PYTHONUNBUFFERED=1

# `op run` resolves the op:// references in secrets.env into environment variables for this
# process only. Nothing is written to disk. OP_SERVICE_ACCOUNT_TOKEN comes from the host's
# root-only token file (compose.yaml).
CMD ["op", "run", "--env-file=/app/secrets.env", "--", \
     "python", "-m", "pattie.mcp_server", "--http", "--host", "0.0.0.0", "--port", "8765", \
     "--allowed-host", "pattie-tools"]
