"""NAICS mapping supplement — new codes only, rotation fix lives in code

Revision ID: 0031_naics_mapping_expansion
Revises: 0030_prozorro_source
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0031_naics_mapping_expansion"
down_revision = "0030_prozorro_source"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "031_naics_mapping_expansion.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        delete from taxonomy_naics_mapping m
        using capability_taxonomy ct
        where ct.id = m.capability_id
          and (ct.code, m.naics_code) in (
            ('SONAR.PASSIVE', '334511'), ('CYBER.DEFENCE', '541512'),
            ('C4ISR.INTEGRATION', '541512'), ('MRO.GENERAL', '811210'),
            ('AEROSPACE.COMPONENTS', '336412'), ('AVIATION.MILITARY', '336412')
          )
    """)
