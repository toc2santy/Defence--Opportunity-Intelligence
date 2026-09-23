"""Add TOTP MFA columns to users

Revision ID: 0045_mfa
Revises: 0044_restore_drill_jobs
Create Date: 2026-09-23

"""
from pathlib import Path

from alembic import op

revision = "0045_mfa"
down_revision = "0044_restore_drill_jobs"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "045_mfa.sql"


def upgrade() -> None:
    # DO NOT run this migration via `alembic upgrade head` — confirmed
    # live (2026-09-23) that this project's async alembic env.py
    # (SQLAlchemy's asyncpg dialect, prepared-statement path) cannot
    # execute this file: both a single op.execute() of the whole file
    # ("cannot insert multiple commands into a prepared statement")
    # and a split into one op.execute()/exec_driver_sql() call per
    # statement (a separate crash inside SQLAlchemy's own asyncpg
    # error-translation code, `TypeError: expected string or
    # bytes-like object` deep in dialects/postgresql/asyncpg.py) both
    # failed. The SQL itself is fine — confirmed by applying the exact
    # same file cleanly via `psql -f` — this is a real, reproducible
    # gap in this project's own migration tooling for any file with
    # more than 2 top-level statements, not a bug in this migration.
    #
    # Apply instead with:
    #   docker compose exec -T db psql -U postgres -d doi < db/migrations/045_mfa.sql
    #   docker compose exec api alembic stamp 0045_mfa
    #
    # op.execute() is kept here (rather than left empty) so
    # `alembic upgrade head` at least fails LOUDLY with the real
    # traceback above instead of silently skipping the schema change.
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        alter table users
            drop column mfa_secret,
            drop column mfa_enabled,
            drop column mfa_backup_codes,
            drop column mfa_enrolled_at;
    """)
