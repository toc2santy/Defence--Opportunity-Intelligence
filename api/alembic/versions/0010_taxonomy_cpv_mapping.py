"""CPV-to-taxonomy mapping — connects UK data to Phase 3 matching

Revision ID: 0010_taxonomy_cpv_mapping
Revises: 0009_uk_find_a_tender
Create Date: 2026-08-20

"""
from pathlib import Path

from alembic import op

revision = "0010_taxonomy_cpv_mapping"
down_revision = "0009_uk_find_a_tender"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "010_taxonomy_cpv_mapping.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("drop table if exists taxonomy_cpv_mapping;")
