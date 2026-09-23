"""contact_address on programmes — the address half of the tender contact

Revision ID: 0029_contact_address
Revises: 0028_spanish_keywords
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0029_contact_address"
down_revision = "0028_spanish_keywords"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "029_contact_address.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table programmes drop column if exists contact_address")
