#!/bin/bash
# Point-in-Time Recovery (PITR) restore — an on-demand incident-
# response tool, not a continuously-running service (2026-09-23,
# Backup & DR Phase 2 follow-up). Built after a real incident (see
# CLAUDE.md's own postmortem) was recovered from the previous day's
# pg_dump instead of this — WAL-based recovery existed (continuous
# archiving, archive_timeout=300s) but no script existed to actually
# USE it, so the coarser, ~22-hour-stale daily backup got used
# instead purely because it was simpler to run correctly under
# pressure. This is what makes the precise path just as fast next
# time.
#
# Restores a wal-g base backup into a FRESH, EMPTY data directory
# (never the real running `db` service's own PGDATA — this never
# touches production), then replays archived WAL forward to a
# specific target timestamp and PAUSES there
# (recovery_target_action=pause) rather than auto-promoting — giving
# a live, read-only Postgres instance frozen at exactly that moment,
# on this container's own port 5432, for inspection and data
# extraction (the same `COPY ... TO STDOUT | COPY ... FROM STDIN`
# technique the real incident's manual recovery used).
#
# Usage (from the host, in this project's root):
#   docker compose run --rm -e PITR_TARGET_TIME="2026-09-23 07:44:00+00" \
#     -p 5434:5432 db /pitr_restore.sh
#
# Then, from a second terminal, once it logs "reached target time":
#   psql -h localhost -p 5434 -U postgres -d doi
#   -- select pg_is_wal_replay_paused();   should be true
#   -- inspect/COPY out whatever real data is needed, exactly the
#   -- way the 2026-09-23 incident's own recovery did
#
# Ctrl+C / `docker compose down` the run when done — this is a
# throwaway container with its own ephemeral data directory inside
# it, not a named volume, so nothing persists after it exits.
#
# PITR_TARGET_TIME is required. BASE_BACKUP_NAME is optional (default
# LATEST) — needed only if the target time is BEFORE the most recent
# base backup; find real backup names first with:
#   docker compose exec db bash -c '. /etc/wal-g-env.sh && wal-g backup-list'
set -euo pipefail

: "${PITR_TARGET_TIME:?Set PITR_TARGET_TIME, e.g. -e PITR_TARGET_TIME=\"2026-09-23 07:44:00+00\"}"
BASE_BACKUP_NAME="${BASE_BACKUP_NAME:-LATEST}"
PITR_DATA_DIR="/var/lib/postgresql/pitr_data"

# NOT sourcing /etc/wal-g-env.sh (entrypoint-with-cron.sh's own
# env-dump for cron, which strips the environment) — this script runs
# as this container's actual PID 1 command, so it already has the
# real environment (R2 credentials included) natively. A real bug
# caught live building this: that dump's own `export KEY=VALUE`
# format breaks on any value containing a space — exactly what
# PITR_TARGET_TIME ("2026-09-23 08:34:30+00" has one) is — so
# sourcing it here would have silently clobbered this script's own
# target time before wal-g ever saw it.
echo "[pitr] fetching base backup '$BASE_BACKUP_NAME' into $PITR_DATA_DIR ..."
rm -rf "$PITR_DATA_DIR"
mkdir -p "$PITR_DATA_DIR"
chown postgres:postgres "$PITR_DATA_DIR"
gosu postgres wal-g backup-fetch "$PITR_DATA_DIR" "$BASE_BACKUP_NAME"

echo "[pitr] configuring recovery target: $PITR_TARGET_TIME (pauses there, never auto-promotes)"
gosu postgres touch "$PITR_DATA_DIR/recovery.signal"
cat >> "$PITR_DATA_DIR/postgresql.auto.conf" <<EOF
restore_command = 'wal-g wal-fetch %f "%p"'
recovery_target_time = '$PITR_TARGET_TIME'
recovery_target_action = 'pause'
EOF
chown postgres:postgres "$PITR_DATA_DIR/postgresql.auto.conf"

echo "[pitr] starting Postgres in recovery mode on port 5432 — will PAUSE once $PITR_TARGET_TIME is reached."
echo "[pitr] connect from the host once paused (see this file's own usage comment for the exact commands)."
exec gosu postgres postgres -D "$PITR_DATA_DIR"
