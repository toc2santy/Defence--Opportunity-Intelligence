#!/usr/bin/env bash
# Brings the production database to the current schema. Idempotent;
# deploy.sh runs it on every deploy.
#
# This project applies db/migrations/*.sql directly with psql and then
# `alembic stamp head` — `alembic upgrade head` cannot run migrations
# with more than two statements on asyncpg (see CLAUDE.md). The current
# revision lives in alembic_version; a file is applied only if its
# numeric prefix is greater than that revision's. On an empty database
# the baseline (db/schema.sql, migration 001) was already applied by
# Postgres' first-boot init, so everything from 002 on is applied.
. "$(dirname "$0")/lib.sh"
need_env_file

psqlq() { "${COMPOSE[@]}" exec -T db psql -U postgres -d doi -v ON_ERROR_STOP=1 "$@"; }

CUR="$(psqlq -tAc "select version_num from alembic_version" 2>/dev/null || true)"
CUR_N=1
if [ -n "$CUR" ]; then CUR_N="$(echo "$CUR" | sed -E 's/^0*([0-9]+)_.*/\1/')"; fi
echo "Database at migration ${CUR_N} (${CUR:-fresh})"

APPLIED=0
for f in $(ls db/migrations/*.sql | sort -V); do
  n="$(basename "$f" | sed -E 's/^0*([0-9]+)_.*/\1/')"
  if [ "$n" -gt "$CUR_N" ]; then
    echo "  applying $(basename "$f")"
    psqlq < "$f" >/dev/null
    APPLIED=$((APPLIED+1))
  fi
done

if [ "$APPLIED" -gt 0 ] || [ -z "$CUR" ]; then
  "${COMPOSE[@]}" run --rm --no-deps api alembic stamp head 2>&1 | tail -1
fi

# roles.sql creates doi_app with a placeholder password at first boot;
# replace it with the real one every time (cheap, and self-heals if the
# secret was rotated).
APP_PW="$(envval DOI_APP_PASSWORD)"
printf "alter role doi_app with password :'pw';\n" | psqlq -v pw="$APP_PW" >/dev/null
echo "Migrations done ($APPLIED applied); doi_app password set."
