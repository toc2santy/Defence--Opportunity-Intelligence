"""Tender-open counter — repeat opens increment, only the first is logged

Revision ID: 0024_tender_opened_counter
Revises: 0023_password_reset_tokens
Create Date: 2026-09-15

"""
from pathlib import Path

from alembic import op

revision = "0024_tender_opened_counter"
down_revision = "0023_password_reset_tokens"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "024_tender_opened_counter.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table opportunities drop column if exists tender_opened_count")
    op.execute("alter table opportunities drop column if exists tender_last_opened_at")
