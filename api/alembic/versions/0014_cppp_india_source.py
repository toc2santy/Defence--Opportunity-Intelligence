"""CPPP (Central Public Procurement Portal, India) — fourth ingestion source

Revision ID: 0014_cppp_india_source
Revises: 0013_ted_eu_source
Create Date: 2026-08-22

"""
from pathlib import Path

from alembic import op

revision = "0014_cppp_india_source"
down_revision = "0013_ted_eu_source"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "014_cppp_india_source.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    pass
