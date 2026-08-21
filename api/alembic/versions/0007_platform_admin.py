"""Platform admin flag (separate from tenant-scoped admin role)

Revision ID: 0007_platform_admin
Revises: 0006_scheduled_ingestion
Create Date: 2026-08-19

"""
from pathlib import Path

from alembic import op

revision = "0007_platform_admin"
down_revision = "0006_scheduled_ingestion"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "007_platform_admin.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table users drop column if exists is_platform_admin;")
