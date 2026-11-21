# Pattie: Telegram bot + agent + calendar watcher in one container, with the 1Password CLI for secrets.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -sS https://downloads.1password.com/linux/keys/1password.asc \
    | gpg --dearmor -o /usr/share/keyrings/1password-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/$(dpkg --print-architecture) stable main" \
    > /etc/apt/sources.list.d/1password.list \
 && apt-get update && apt-get install -y --no-install-recommends 1password-cli \
 && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY infra/pattie/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY pattie/ /app/pattie/
COPY config/ /app/config/
COPY demo/anna_susan_prefs.json /app/demo/anna_susan_prefs.json
COPY amazon_search.py tilion.py shopping_hours.py /app/
COPY infra/pattie/secrets.env /app/secrets.env
COPY infra/pattie/start.sh /app/start.sh

RUN useradd --uid 10001 --home /data pattie && mkdir -p /data && chown pattie /data
USER pattie
ENV PATTIE_DB=/data/pattie.sqlite3 PYTHONUNBUFFERED=1
CMD ["/app/start.sh"]
