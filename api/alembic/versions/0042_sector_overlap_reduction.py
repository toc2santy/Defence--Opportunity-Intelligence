"""Reduce two real sources of confusing sector-count overlap

Revision ID: 0042_sector_overlap_reduction
Revises: 0041_narrow_naics_334511
Create Date: 2026-09-21

"""
from pathlib import Path

from alembic import op

revision = "0042_sector_overlap_reduction"
down_revision = "0041_narrow_naics_334511"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "042_sector_overlap_reduction.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        insert into taxonomy_naics_mapping (capability_id, naics_code)
        select id, '336412' from capability_taxonomy where code = 'AVIATION.MILITARY'
        on conflict do nothing;

        delete from taxonomy_unspsc_mapping
        where unspsc_prefix = '25132102'
          and capability_id in (
            select id from capability_taxonomy where code = 'UAV.INTEGRATION'
          );
    """)
