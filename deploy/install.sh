#!/bin/sh
# One-time VPS setup for dejure-agent: the things that are easy to forget and only
# noticed when they are already needed — the nightly backup and a restore rehearsal.
#
#   sudo sh deploy/install.sh
#
# Everything here is idempotent; re-running it is safe.

set -eu

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
CRON_FILE=/etc/cron.d/dejure-agent

echo "dejure-agent setup — repo at $REPO_DIR"

# --- Nightly database backup ------------------------------------------------
# The backup script has always existed; nothing ever installed it, so a VPS could run
# for months with no backup at all.
cat > "$CRON_FILE" <<EOF
# dejure-agent — installed by deploy/install.sh
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 2 * * * root $REPO_DIR/deploy/backup-db.sh >> /var/log/dejure-backup.log 2>&1
EOF
chmod 0644 "$CRON_FILE"
echo "✓ nightly backup installed ($CRON_FILE, 02:30 daily)"

# --- Log rotation for the backup log ----------------------------------------
# Container logs are capped in docker-compose.yml; this is the one log outside Docker.
cat > /etc/logrotate.d/dejure-agent <<EOF
/var/log/dejure-backup.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
EOF
echo "✓ backup log rotation installed"

# --- Prove the backup actually works ----------------------------------------
echo
echo "Running one backup now to prove it works..."
sh "$REPO_DIR/deploy/backup-db.sh"

cat <<'EOF'

Remaining manual steps — the bot cannot do these for you:

  1. EXTERNAL UPTIME MONITOR (the most important one).
     Point a monitor at https://bot.dejureacademy.net/health every 1-5 minutes,
     alerting on any non-200. /health returns 503 when lead delivery is stuck,
     when a lead/payment/escalation write has FAILED (data lost), when the
     knowledge base is empty, when replies are not reaching Facebook, when the
     AI model is failing, or when lead capture is switched off.
     Without this, the bot can be broken for days and nobody will know — it
     served an empty knowledge base for a day while returning a clean 200.

     Do NOT point the monitor at /health/live. That one is the container's own
     healthcheck (autoheal restarts on it) and reports liveness only, so it
     stays 200 through every problem a restart cannot fix.
     Free options: UptimeRobot, BetterStack, Healthchecks.io.

  2. VERIFY A RESTORE, once, before you need it:
       gunzip -c /srv/backups/dejure-agent/dejure_<stamp>.sql.gz | \
         docker compose exec -T db psql -U dejure -d dejure_restore_test
     A backup nobody has restored is not yet a backup.

  3. CHECK THE STARTUP MESSAGE in Telegram. The bot posts one on every boot with
     its lead-capture and payment-alert status. If it did not arrive, the team's
     alerting path is broken — fix that before taking real traffic.

EOF
