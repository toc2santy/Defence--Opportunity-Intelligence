"""EU TED (Tenders Electronic Daily) — third ingestion source

Revision ID: 0013_ted_eu_source
Revises: 0012_eligibility_fields
Create Date: 2026-08-21

"""
from pathlib import Path

from alembic import op

revision = "0013_ted_eu_source"
down_revision = "0012_eligibility_fields"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "013_ted_eu_source.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    pass
