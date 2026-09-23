#!/bin/bash
# Wraps the official postgres image's own entrypoint (2026-09, Backup
# & DR Phase 2) — adds exactly two things on top of stock postgres:16
# behaviour, nothing else: a cron daemon for the daily wal-g base
# backup, and a snapshot of this container's real environment that
# cron job can actually read.
set -e

# cron runs every job with a near-empty environment by default (no
# AWS_*/WALG_*/PGDATA — none of what docker-compose injected into
# THIS process) — a well-documented cron gotcha, not a wal-g quirk.
# Dumping the real environment once at startup, into a file the cron
# job explicitly sources, is the standard fix.
printenv | sed 's/^\(.*\)$/export \1/' > /etc/wal-g-env.sh
# Owner-only read, but owned by `postgres` (not root) — the cron job
# itself runs AS postgres (see wal-g-base-backup.cron's own user
# field), and this file carries real secrets (POSTGRES_PASSWORD,
# AWS_SECRET_ACCESS_KEY), so 644/world-readable would be a real
# regression from the least-privilege this is trying to preserve.
# A real bug, caught live: this was first written as root-owned/600,
# which is unreadable to postgres — the exact permission error a
# manual dry-run of the cron job surfaced before ever waiting for the
# real 03:30 UTC schedule to fail silently the same way.
chown postgres:postgres /etc/wal-g-env.sh
chmod 600 /etc/wal-g-env.sh

# Started in the background as root (cron itself needs root to
# launch), the job entry in wal-g-base-backup.cron switches to the
# `postgres` OS user before running wal-g — matching the user this
# container's postgres server process already runs as, and the user
# pg_hba.conf's default local-peer trust rule expects.
cron

# Hand off to the real, unmodified postgres entrypoint as PID 1 — WAL
# archiving itself (archive_mode/archive_command) is configured via
# docker-compose.yml's `command:` overrides, not by touching this
# script or the base image's own startup logic.
exec docker-entrypoint.sh "$@"
