"""New capability: Personal Protective & Tactical Equipment

Revision ID: 0033_tactical_ppe
Revises: 0032_unspsc_mapping_expansion
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0033_tactical_ppe"
down_revision = "0032_unspsc_mapping_expansion"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "033_tactical_protective_equipment.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # ON DELETE CASCADE on taxonomy_cpv_mapping/taxonomy_naics_mapping/
    # capability_taxonomy_keywords means deleting the capability row
    # removes all three in one statement.
    op.execute("delete from capability_taxonomy where code = 'TACTICAL.PROTECTIVE_EQUIPMENT'")
