"""UNSPSC mapping — one verified addition (ammunition family, Manufacturing.Defence)

Revision ID: 0032_unspsc_mapping_expansion
Revises: 0031_naics_mapping_expansion
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0032_unspsc_mapping_expansion"
down_revision = "0031_naics_mapping_expansion"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "032_unspsc_mapping_expansion.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        delete from taxonomy_unspsc_mapping m
        using capability_taxonomy ct
        where ct.id = m.capability_id and ct.code = 'MANUFACTURING.DEFENCE' and m.unspsc_prefix = '4610'
    """)
