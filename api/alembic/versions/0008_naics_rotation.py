"""NAICS rotation state — automatic coverage widening

Revision ID: 0008_naics_rotation
Revises: 0007_platform_admin
Create Date: 2026-08-19

"""
from pathlib import Path

from alembic import op

revision = "0008_naics_rotation"
down_revision = "0007_platform_admin"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "008_naics_rotation.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("drop table if exists ingestion_rotation_state;")
