"""Seed CPPP page-offset rotation state row

Revision ID: 0037_cppp_page_rotation
Revises: 0036_contact2_linkedin
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0037_cppp_page_rotation"
down_revision = "0036_contact2_linkedin"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "037_cppp_page_rotation.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("delete from ingestion_rotation_state where source_name = 'cppp_india_page_offset'")
