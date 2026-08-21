"""baseline: Phase 0 schema (roles + tables + RLS)

This revision represents everything that existed before Alembic was
adopted into this project. On an EXISTING database (one that
already has this schema applied by hand, as this project's did),
run `alembic stamp 0001_baseline` instead of `alembic upgrade` —
stamping records this revision as applied without re-running SQL
against a database that already has it. Only a genuinely fresh
environment should `alembic upgrade head` from empty.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-19

"""
from pathlib import Path

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

DB_DIR = Path(__file__).resolve().parents[3] / "db"


def upgrade() -> None:
    op.execute((DB_DIR / "roles.sql").read_text())
    op.execute((DB_DIR / "schema.sql").read_text())


def downgrade() -> None:
    # Deliberately not implemented. This is the bottom of migration
    # history — "downgrading" it means dropping the entire schema,
    # which is not something to make one command away from an
    # accidental run. If you genuinely need to tear everything down,
    # do it explicitly: `docker compose down -v`.
    raise NotImplementedError(
        "Baseline schema has no automated downgrade by design. "
        "Use `docker compose down -v` to reset the whole database instead."
    )
