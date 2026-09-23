"""Add backup_jobs table (Backup & DR Phase 1)

Revision ID: 0043_backup_jobs
Revises: 0042_sector_overlap_reduction
Create Date: 2026-09-22

"""
from pathlib import Path

from alembic import op

revision = "0043_backup_jobs"
down_revision = "0042_sector_overlap_reduction"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "043_backup_jobs.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("drop table if exists backup_jobs;")
