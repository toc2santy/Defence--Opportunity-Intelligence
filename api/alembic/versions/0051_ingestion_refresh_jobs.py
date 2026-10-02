"""Add job_type to ingestion_jobs for periodic contact/amendment refresh

Revision ID: 0051_ingestion_refresh_jobs
Revises: 0050_oidc_sso
Create Date: 2026-09-25

"""
from pathlib import Path

from alembic import op

revision = "0051_ingestion_refresh_jobs"
down_revision = "0050_oidc_sso"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "051_ingestion_refresh_jobs.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table ingestion_jobs drop column job_type;")
