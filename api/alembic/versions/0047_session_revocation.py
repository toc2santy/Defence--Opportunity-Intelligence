"""Add session_version column to users for token revocation

Revision ID: 0047_session_revocation
Revises: 0046_login_lockout
Create Date: 2026-09-23

"""
from pathlib import Path

from alembic import op

revision = "0047_session_revocation"
down_revision = "0046_login_lockout"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "047_session_revocation.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table users drop column session_version;")
