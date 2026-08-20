#!/bin/sh
# Pull the LIVE knowledge base + prompts + data off the production VPS.
#
# The bot's admin panel writes to host-side bind mounts (see the old repo's
# docker-compose.yml), so everything the team has edited lives in two places on the
# server — NOT in git:
#
#   <repo>/knowledge_base.json   the course catalog (panel edits land here)
#   <repo>/data/                 system_prompt.txt, answering_rules.txt,
#                                prompt_sections.json, lead_capture.json,
#                                bot.db.json, leads.jsonl, payments.jsonl,
#                                lead_outbox.json, blocklist.json, deletions.json
#
# Usage:
#   ./scripts/fetch_server_data.sh user@your-vps /srv/dejure-fb-bot [dest]
#
# Then migrate it into Postgres:
#   uv run python scripts/migrate_json.py ./server-snapshot
#
# Stop the bot first if you want a clean, non-moving copy of bot.db.json:
#   ssh user@your-vps 'cd /srv/dejure-fb-bot && docker compose stop'
# (leads.jsonl and the outbox are append-only, so a live copy is safe for those.)

set -eu

if [ $# -lt 2 ]; then
  sed -n '2,26p' "$0"
  exit 1
fi

REMOTE="$1"
REMOTE_DIR="$2"
DEST="${3:-./server-snapshot}"

mkdir -p "$DEST"

echo "Pulling from $REMOTE:$REMOTE_DIR ..."
# One tar over ssh: fewer round trips, preserves the data/ layout migrate_json.py expects.
ssh "$REMOTE" "cd '$REMOTE_DIR' && tar cf - knowledge_base.json knowledge_base.md data" \
  | tar xf - -C "$DEST"

echo
echo "Fetched into $DEST:"
find "$DEST" -maxdepth 2 -type f | sed 's|^|  |'
echo
echo "Next:  uv run python scripts/migrate_json.py $DEST"
