FROM postgres:16

# wal-g (2026-09, Backup & DR Phase 2) — continuous WAL archiving to
# R2 for point-in-time recovery, on top of Phase 1's daily pg_dump
# (api/Dockerfile's own postgresql-client, a separate mechanism).
# Installed HERE, not in api/Dockerfile, because archive_command runs
# INSIDE the running postgres server process itself — it needs the
# wal-g binary in THIS container's filesystem, not the API's.
#
# Version pinned to v3.0.9 (the real latest release, checked live via
# the GitHub API rather than guessed) and the pg-24.04-amd64 build
# specifically, verified against this image's own base
# (`cat /etc/os-release` on postgres:16 -> Debian 13 "trixie", glibc
# new enough for the 24.04 Ubuntu build's own glibc baseline).
ARG WALG_VERSION=v3.0.9
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates cron gosu \
    && curl -fL -o /tmp/wal-g.tar.gz \
       "https://github.com/wal-g/wal-g/releases/download/${WALG_VERSION}/wal-g-pg-24.04-amd64.tar.gz" \
    && tar -xzf /tmp/wal-g.tar.gz -C /usr/local/bin \
    && mv /usr/local/bin/wal-g-pg-24.04-amd64 /usr/local/bin/wal-g \
    && chmod +x /usr/local/bin/wal-g \
    && rm /tmp/wal-g.tar.gz \
    && rm -rf /var/lib/apt/lists/*

# The daily base-backup job — cron.d format (min hour day month
# weekday USER command), so it runs as `postgres`, not root, matching
# the OS user this container's own postgres process already runs as.
# 03:30 UTC, 30 minutes after Phase 1's pg_dump backup (03:00 UTC in
# app/main.py's scheduler) — staggered so both backups don't compete
# for the same disk/network I/O at once. Sources the env dump this
# image's own entrypoint writes at container start (see
# entrypoint-with-cron.sh) — cron itself runs jobs with a bare
# environment, not this container's real one, a well-known cron
# gotcha that would otherwise leave wal-g with no R2 credentials at
# all when it actually runs.
COPY db/wal-g-base-backup.cron /etc/cron.d/wal-g-base-backup
RUN chmod 0644 /etc/cron.d/wal-g-base-backup

COPY db/entrypoint-with-cron.sh /usr/local/bin/entrypoint-with-cron.sh
RUN chmod +x /usr/local/bin/entrypoint-with-cron.sh

# Point-in-Time Recovery — an on-demand incident-response tool, run
# via `docker compose run --rm db /pitr_restore.sh` (see that script's
# own header for the full runbook), never as this container's normal
# CMD. Placed at the filesystem root, not /usr/local/bin, so it's
# never on PATH and can't be invoked by accident.
COPY db/pitr_restore.sh /pitr_restore.sh
RUN chmod +x /pitr_restore.sh

ENTRYPOINT ["/usr/local/bin/entrypoint-with-cron.sh"]
CMD ["postgres"]
