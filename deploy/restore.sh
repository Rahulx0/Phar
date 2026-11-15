#!/usr/bin/env bash
# Restore the database from a dump produced by deploy/backup.sh.
# Usage: deploy/restore.sh backups/pharmacy-20260101T120000Z.dump
#
# Procedure: stop the app (it holds pooled connections that would block the
# drop) -> drop/recreate the database inside the running Postgres container
# (the pgdata volume itself is untouched) -> pg_restore -> restart the app
# -> verify the restored data is being served.
set -euo pipefail

FILE="${1:?usage: restore.sh <backup-file.dump>}"
[ -f "$FILE" ] || { echo "ERROR: no such file: $FILE"; exit 1; }
FILE="$(cd "$(dirname "$FILE")" && pwd)/$(basename "$FILE")"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
DB_USER="${POSTGRES_USER:-pharmacy}"
DB_NAME="${POSTGRES_DB:-pharmacy}"

echo "Restoring $FILE into database '$DB_NAME' (current data will be replaced)..."

echo "Stopping app (it holds pooled DB connections)..."
docker compose stop app

docker compose exec -T db dropdb -U "$DB_USER" --if-exists "$DB_NAME"
docker compose exec -T db createdb -U "$DB_USER" "$DB_NAME"
docker compose exec -T db pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner < "$FILE"

echo "Starting app..."
docker compose start app

# Wait for the app to report ready (max ~30s). Base URL honours the
# NGINX_HTTPS_PORT override from .env (default 443).
BASE_URL="https://localhost:${NGINX_HTTPS_PORT:-443}"
for _ in $(seq 1 15); do
  if curl -ksS -f "$BASE_URL/ready" >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "Verifying restored service:"
curl -ksS "$BASE_URL/health"
echo
if curl -ksS "$BASE_URL/report/stock" | head -c 400; then
  echo
  echo "Restore complete."
else
  echo "WARNING: could not verify restored data via the API" >&2
  exit 1
fi
