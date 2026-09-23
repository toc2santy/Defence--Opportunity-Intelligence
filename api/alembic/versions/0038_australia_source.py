"""Seed AusTender (Australia) source

Revision ID: 0038_australia_source
Revises: 0037_cppp_page_rotation
Create Date: 2026-09-17

"""
from pathlib import Path

from alembic import op

revision = "0038_australia_source"
down_revision = "0037_cppp_page_rotation"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "038_australia_source.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("delete from sources where name = 'AusTender (Australian Government)'")
