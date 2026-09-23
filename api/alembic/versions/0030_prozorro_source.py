"""ProZorro (Ukraine) — eighth ingestion source

Revision ID: 0030_prozorro_source
Revises: 0029_contact_address
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0030_prozorro_source"
down_revision = "0029_contact_address"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "030_prozorro_source.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("delete from sources where name = 'ProZorro (Ukraine Public Procurement)'")
