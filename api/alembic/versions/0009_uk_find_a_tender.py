"""UK Find a Tender Service — second ingestion source

Revision ID: 0009_uk_find_a_tender
Revises: 0008_naics_rotation
Create Date: 2026-08-20

"""
from pathlib import Path

from alembic import op

revision = "0009_uk_find_a_tender"
down_revision = "0008_naics_rotation"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "009_uk_find_a_tender.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Leave the source row in place — real ingested UK programme
    # data would reference it via source_id, and removing it would
    # break those foreign keys rather than just undo a seed insert.
    pass
