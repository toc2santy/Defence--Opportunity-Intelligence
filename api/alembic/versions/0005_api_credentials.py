"""Credential expiry tracking (SAM.gov key reminder)

Revision ID: 0005_api_credentials
Revises: 0004_programme_matching
Create Date: 2026-08-19

"""
from pathlib import Path

from alembic import op

revision = "0005_api_credentials"
down_revision = "0004_programme_matching"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "005_api_credentials.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("drop table if exists api_credentials;")
