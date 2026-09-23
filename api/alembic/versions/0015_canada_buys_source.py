"""CanadaBuys (Government of Canada) — fifth ingestion source, plus UNSPSC mapping

Revision ID: 0015_canada_buys_source
Revises: 0014_cppp_india_source
Create Date: 2026-08-22

"""
from pathlib import Path

from alembic import op

revision = "0015_canada_buys_source"
down_revision = "0014_cppp_india_source"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "015_canada_buys_source.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("drop table if exists taxonomy_unspsc_mapping")
