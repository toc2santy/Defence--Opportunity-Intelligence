"""eTenders South Africa — sixth ingestion source

Revision ID: 0020_south_africa_source
Revises: 0019_opportunity_checklist_state
Create Date: 2026-08-24

"""
from pathlib import Path

from alembic import op

revision = "0020_south_africa_source"
down_revision = "0019_opportunity_checklist_state"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "020_south_africa_source.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("delete from sources where name = 'eTenders South Africa (National Treasury)'")
