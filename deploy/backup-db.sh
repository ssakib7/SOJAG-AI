#!/bin/sh
# Nightly Postgres backup for dejure-agent (successor to dejure-fb-bot's backup-data.sh).
#
# Cron example (2:30 AM daily):
#   30 2 * * * /srv/dejure-agent/deploy/backup-db.sh >> /var/log/dejure-backup.log 2>&1
#
# Env:
#   BACKUP_DIR    where dumps go (default /srv/backups/dejure-agent)
#   KEEP_DAYS     local retention (default 14)
#   RCLONE_REMOTE optional rclone remote (e.g. "gdrive:dejure-backups") for an off-box copy
#   COMPOSE_DIR   directory holding docker-compose.yml (default: this repo root)

set -eu

COMPOSE_DIR="${COMPOSE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-/srv/backups/dejure-agent}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y-%m-%d_%H%M)"
OUT="$BACKUP_DIR/dejure_$STAMP.sql.gz"

mkdir -p "$BACKUP_DIR"

# pg_dump inside the db container; plain-format piped through gzip on the host.
cd "$COMPOSE_DIR"
docker compose exec -T db pg_dump -U dejure -d dejure | gzip > "$OUT"

echo "$(date -Is) backup written: $OUT ($(du -h "$OUT" | cut -f1))"

# Prune old local dumps.
find "$BACKUP_DIR" -name 'dejure_*.sql.gz' -mtime "+$KEEP_DAYS" -delete

# Optional off-box copy.
if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copy "$OUT" "$RCLONE_REMOTE/" && echo "$(date -Is) copied to $RCLONE_REMOTE"
fi
