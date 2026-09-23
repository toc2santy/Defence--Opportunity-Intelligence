"""Narrow over-broad NAICS 334511 mapping (11 -> 3 capabilities)

Revision ID: 0041_narrow_naics_334511
Revises: 0040_contract_award_value
Create Date: 2026-09-18

"""
from pathlib import Path

from alembic import op

revision = "0041_narrow_naics_334511"
down_revision = "0040_contract_award_value"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "041_narrow_naics_334511.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        insert into taxonomy_naics_mapping (capability_id, naics_code)
        select ct.id, '334511' from capability_taxonomy ct
        where ct.code in (
            'UAV.INTEGRATION', 'EW.GENERAL', 'C4ISR.INTEGRATION',
            'SENSOR.ELECTRO_OPTIC', 'AUTONOMY.GENERAL', 'CUAS.GENERAL',
            'ISR.GENERAL', 'AUTONOMY.ROBOTICS'
        )
        on conflict do nothing
    """)
