"""Scheduled ingestion + what's-new checkpoint

Revision ID: 0006_scheduled_ingestion
Revises: 0005_api_credentials
Create Date: 2026-08-19

"""
from pathlib import Path

from alembic import op

revision = "0006_scheduled_ingestion"
down_revision = "0005_api_credentials"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "006_scheduled_ingestion_and_notifications.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table tenants drop column if exists opportunities_last_viewed_at;")
