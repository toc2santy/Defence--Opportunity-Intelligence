"""Password reset tokens — Forgot Password flow

Revision ID: 0023_password_reset_tokens
Revises: 0022_company_compliance_profile
Create Date: 2026-09-11

"""
from pathlib import Path

from alembic import op

revision = "0023_password_reset_tokens"
down_revision = "0022_company_compliance_profile"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "023_password_reset_tokens.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("drop table if exists password_reset_tokens")
