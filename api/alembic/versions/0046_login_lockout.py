"""Add per-account failed-login lockout columns to users

Revision ID: 0046_login_lockout
Revises: 0045_mfa
Create Date: 2026-09-23

"""
from pathlib import Path

from alembic import op

revision = "0046_login_lockout"
down_revision = "0045_mfa"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "046_login_lockout.sql"


def upgrade() -> None:
    # A single ALTER TABLE statement (see the .sql file's own header)
    # — safe for op.execute() the normal way, unlike 0045_mfa.
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        alter table users
            drop column failed_login_attempts,
            drop column locked_until;
    """)
