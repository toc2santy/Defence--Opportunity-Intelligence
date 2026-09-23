"""Add email_verified column and email_verification_tokens table

Revision ID: 0048_email_verification
Revises: 0047_session_revocation
Create Date: 2026-09-23

"""
from pathlib import Path

from alembic import op

revision = "0048_email_verification"
down_revision = "0047_session_revocation"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "048_email_verification.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("drop table email_verification_tokens;")
    op.execute("alter table users drop column email_verified;")
