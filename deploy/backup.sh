#!/usr/bin/env bash
# Backup the PostgreSQL database into a timestamped, compressed custom-format
# dump (pg_dump -Fc) under ./backups/. Keeps the 7 most recent dumps.
# Usage: deploy/backup.sh   (or: make backup)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
cd "$ROOT"

# Source .env for POSTGRES_USER / POSTGRES_DB if present.
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
DB_USER="${POSTGRES_USER:-pharmacy}"
DB_NAME="${POSTGRES_DB:-pharmacy}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$BACKUP_DIR/pharmacy-$STAMP.dump"

docker compose exec -T db pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$FILE"

# Retention: keep the newest 7 dumps, delete older ones.
ls -1t "$BACKUP_DIR"/pharmacy-*.dump 2>/dev/null | tail -n +8 | xargs -r rm --

echo "Backup written: $FILE ($(du -h "$FILE" | cut -f1))"
