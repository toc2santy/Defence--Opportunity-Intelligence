"""Seed DNCP Paraguay source

Revision ID: 0039_paraguay_source
Revises: 0038_australia_source
Create Date: 2026-09-18

"""
from pathlib import Path

from alembic import op

revision = "0039_paraguay_source"
down_revision = "0038_australia_source"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "039_paraguay_source.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("delete from sources where name = 'DNCP Paraguay (Dirección Nacional de Contrataciones Públicas)'")
