#!/usr/bin/env bash
# Load an existing database (e.g. the dev one) into the production DB.
#
#   on the dev machine:
#     docker exec defence-oi-db-1 pg_dump -U postgres -Fc doi > doi.dump
#     scp doi.dump deploy@<server>:/opt/doi/
#   on the server:
#     deploy/restore-dump.sh doi.dump        # asks before replacing anything
#
# REPLACES everything currently in the production database. The dump
# carries tenant data, password hashes and MFA/PII ciphertext, so the
# server's MFA_ENCRYPTION_KEY and PII_ENCRYPTION_KEY must equal the
# source environment's, and the file should be deleted afterwards.
. "$(dirname "$0")/lib.sh"
need_env_file
DUMP="${1:?usage: deploy/restore-dump.sh <file.dump> [--yes]}"
[ -f "$DUMP" ] || { echo "No such file: $DUMP"; exit 1; }

if [ "${2:-}" != "--yes" ]; then
  echo "This will REPLACE all data in the production database with the contents of $DUMP."
  read -r -p "Type 'replace' to continue: " ans
  [ "$ans" = "replace" ] || { echo "Aborted."; exit 1; }
fi

"${COMPOSE[@]}" stop api caddy >/dev/null 2>&1 || true
echo "Restoring..."
"${COMPOSE[@]}" exec -T db pg_restore -U postgres -d doi --clean --if-exists --no-owner < "$DUMP" 2>&1 | tail -5 || true
deploy/migrate.sh
"${COMPOSE[@]}" up -d
echo "Restore complete. Now: shred -u $DUMP"
