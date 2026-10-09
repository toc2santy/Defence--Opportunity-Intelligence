"""Automatic data-health check results

Revision ID: 0055_data_health
Revises: 0054_keyword_only_matching
Create Date: 2026-10-09

"""
from pathlib import Path

from alembic import op

revision = "0055_data_health"
down_revision = "0054_keyword_only_matching"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "055_data_health.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("drop table data_health_results;")
