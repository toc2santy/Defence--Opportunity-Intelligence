"""
Rotate MFA_ENCRYPTION_KEY or PII_ENCRYPTION_KEY (2026-09) — re-encrypts
every stored value with a NEW Fernet key while the OLD key is still
supplied, so nothing is ever left undecryptable mid-rotation.

Both keys are passed in explicitly on the command line — this script
never reads "the current key" out of the live environment, so there is
no ambiguity about which key is old vs. new. BACKUP_ENCRYPTION_KEY is
deliberately NOT handled here: existing backups in R2 were encrypted
at rest with whatever key was active the day they were taken, and
"rotating" that key can only mean download-decrypt-reencrypt-reupload
of every historical dump, a much heavier and riskier operation than
this. The safe manual process for BACKUP_ENCRYPTION_KEY: generate a
new key, add it as BACKUP_ENCRYPTION_KEY (new backups use it going
forward), and keep the OLD value available under a separate env var
(e.g. BACKUP_ENCRYPTION_KEY_PREVIOUS) for as long as any backup
encrypted with it might still need restoring — db/pitr_restore.sh and
any manual restore must be able to reach whichever key actually
encrypted the dump being restored.

Usage (run inside the api container, where the DB is reachable):

  python scripts/rotate_encryption_key.py mfa \
      --old-key "<current MFA_ENCRYPTION_KEY>" \
      --new-key "<freshly generated key>"

  python scripts/rotate_encryption_key.py pii \
      --old-key "<current PII_ENCRYPTION_KEY>" \
      --new-key "<freshly generated key>"

Generate a fresh key with:
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

DRY RUN BY DEFAULT — decrypts every affected row with --old-key,
re-encrypts in memory with --new-key, and reports how many rows WOULD
be rewritten. Writes nothing until --execute is passed. This mirrors
this project's own standing bulk-op precaution (preview scope before
any destructive/bulk write against real data — see CLAUDE.md and
memory/feedback_bulk_delete_precaution.md): a rotation that touches
every tenant's MFA secret or every company's GST/PAN/TAN/IEC number is
exactly the kind of operation that precaution exists for, even though
nothing is being deleted.

--execute also refuses to run unless a backup_jobs row shows a
succeeded backup within the last hour, same "fresh backup before a
bulk write" rule — pass --skip-backup-check only against a disposable
stack (e.g. the isolated test stack) where that check doesn't apply.

Fully transactional: every row is decrypted+re-encrypted in memory
FIRST; only if every single row succeeds does the script open one
transaction and issue every UPDATE, then commit. A single bad row
(wrong --old-key, corrupted ciphertext) aborts the whole run before
anything is written — never a partial rotation with some rows on the
old key and some on the new one.

After a successful --execute run:
  1. Update MFA_ENCRYPTION_KEY (or PII_ENCRYPTION_KEY) in .env to the
     NEW key.
  2. docker compose up -d --build api (or api-test) to pick it up.
  3. Only THEN is the old key safe to discard — the running app still
     needs the OLD key right up until the container has actually
     picked up the new one.
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

# Run as `python scripts/rotate_encryption_key.py`, which puts this
# file's own directory (scripts/) at sys.path[0], not the api/ dir the
# `app` package lives in — add it explicitly rather than requiring
# `python -m` or a PYTHONPATH the caller has to remember.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from app.main import SessionLocal

FIELD_CONFIG = {
    "mfa": {
        "table": "users",
        "id_column": "id",
        "columns": ["mfa_secret"],
        "where": "mfa_secret is not null",
    },
    "pii": {
        "table": "tenants",
        "id_column": "id",
        "columns": ["gst_number", "pan_number", "tan_number", "iec_license"],
        "where": "gst_number is not null or pan_number is not null or tan_number is not null or iec_license is not null",
    },
}


async def _recent_backup_exists(session) -> bool:
    result = await session.execute(
        text("""
            select 1 from backup_jobs
            where job_type = 'backup' and status = 'succeeded'
              and finished_at > :cutoff
            limit 1
        """),
        {"cutoff": datetime.now(timezone.utc) - timedelta(hours=1)},
    )
    return result.first() is not None


async def rotate(field: str, old_key: str, new_key: str, execute: bool, skip_backup_check: bool) -> int:
    config = FIELD_CONFIG[field]
    old_fernet = Fernet(old_key.encode())
    new_fernet = Fernet(new_key.encode())

    columns_sql = ", ".join(config["columns"])
    async with SessionLocal() as session:
        if execute and not skip_backup_check:
            if not await _recent_backup_exists(session):
                print(
                    "REFUSING to --execute: no succeeded backup in the last hour. "
                    "Trigger one first (POST /admin/backup/run as a platform admin), "
                    "or pass --skip-backup-check if this is a disposable stack.",
                    file=sys.stderr,
                )
                return 1

        result = await session.execute(
            text(f"select {config['id_column']} as id, {columns_sql} from {config['table']} where {config['where']}")
        )
        rows = result.mappings().all()

        print(f"[{field}] {len(rows)} row(s) in {config['table']} match: {config['where']}")
        if not rows:
            print("Nothing to rotate.")
            return 0

        rewritten = []
        for row in rows:
            new_values = {}
            for col in config["columns"]:
                encrypted = row[col]
                if encrypted is None:
                    new_values[col] = None
                    continue
                try:
                    plain = old_fernet.decrypt(encrypted.encode()).decode()
                except InvalidToken:
                    print(
                        f"ABORTING: row {row['id']}, column {col} could not be decrypted with --old-key. "
                        "Nothing has been written. Check that --old-key is really the CURRENT key.",
                        file=sys.stderr,
                    )
                    return 1
                new_values[col] = new_fernet.encrypt(plain.encode()).decode()
            rewritten.append((row["id"], new_values))

        print(f"[{field}] All {len(rewritten)} row(s) decrypted with --old-key and re-encrypted with --new-key in memory.")

        if not execute:
            print("Dry run only — nothing written. Re-run with --execute to apply.")
            return 0

        set_clause = ", ".join(f"{col} = :{col}" for col in config["columns"])
        for row_id, new_values in rewritten:
            await session.execute(
                text(f"update {config['table']} set {set_clause} where {config['id_column']} = :row_id"),
                {**new_values, "row_id": row_id},
            )
        await session.commit()
        print(f"[{field}] {len(rewritten)} row(s) rewritten with the new key. Commit successful.")
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("field", choices=sorted(FIELD_CONFIG.keys()))
    parser.add_argument("--old-key", required=True, help="The CURRENT Fernet key currently protecting this field")
    parser.add_argument("--new-key", required=True, help="The NEW Fernet key to re-encrypt with")
    parser.add_argument("--execute", action="store_true", help="Actually write. Without this, dry-run only.")
    parser.add_argument("--skip-backup-check", action="store_true", help="Skip the 'backup within the last hour' guard — only for disposable/test stacks")
    args = parser.parse_args()

    if args.old_key == args.new_key:
        print("--old-key and --new-key are identical — nothing to rotate.", file=sys.stderr)
        sys.exit(1)

    exit_code = asyncio.run(rotate(args.field, args.old_key, args.new_key, args.execute, args.skip_backup_check))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
