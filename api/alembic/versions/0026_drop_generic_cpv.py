"""Remove generic CPV 35120000 from seven capabilities

Revision ID: 0026_drop_generic_cpv
Revises: 0025_cpv_mapping_expansion
Create Date: 2026-09-15

"""
from pathlib import Path

from alembic import op

revision = "0026_drop_generic_cpv"
down_revision = "0025_cpv_mapping_expansion"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "026_remove_generic_surveillance_cpv.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Restores the v1 rows — knowingly reintroducing the false
    # positive documented in the migration.
    op.execute("""
        insert into taxonomy_cpv_mapping (capability_id, cpv_code)
        select ct.id, '35120000'
        from capability_taxonomy ct
        where ct.code in (
            'CUAS.GENERAL', 'CYBER.DEFENCE', 'EW.GENERAL', 'ISR.GENERAL',
            'SENSING.RADAR', 'SENSOR.ELECTRO_OPTIC', 'SENSORS.GENERAL'
        )
        on conflict do nothing
    """)
