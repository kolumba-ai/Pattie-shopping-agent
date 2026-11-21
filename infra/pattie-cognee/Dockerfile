# Shared Cognee memory server for Pattie, with the 1Password CLI for the Claude key.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -sS https://downloads.1password.com/linux/keys/1password.asc \
    | gpg --dearmor -o /usr/share/keyrings/1password-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/$(dpkg --print-architecture) stable main" \
    > /etc/apt/sources.list.d/1password.list \
 && apt-get update && apt-get install -y --no-install-recommends 1password-cli \
 && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir "cognee[anthropic,turso]==1.6.10" "anthropic>=0.125.0"
COPY pattie/ /app/pattie/
COPY infra/pattie-cognee/secrets.env /app/secrets.env

RUN useradd --uid 10002 --home /data pattie && mkdir -p /data && chown pattie /data
USER pattie
ENV PATTIE_COGNEE_DIR=/data/cognee HOME=/data FASTEMBED_CACHE_PATH=/data/fastembed \
    PYTHONUNBUFFERED=1
CMD ["op", "run", "--env-file=/app/secrets.env", "--", \
     "python", "-m", "pattie.cognee_server", "--host", "0.0.0.0", "--port", "8000"]
