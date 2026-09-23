"""Contract awards — OEM Intelligence

Revision ID: 0016_contract_awards
Revises: 0015_canada_buys_source
Create Date: 2026-08-22

"""
from pathlib import Path

from alembic import op

revision = "0016_contract_awards"
down_revision = "0015_canada_buys_source"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "016_contract_awards.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("drop table if exists contract_awards")
