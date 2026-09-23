"""Add job_type to backup_jobs for restore-drill tracking (Backup & DR Phase 4)

Revision ID: 0044_restore_drill_jobs
Revises: 0043_backup_jobs
Create Date: 2026-09-22

"""
from pathlib import Path

from alembic import op

revision = "0044_restore_drill_jobs"
down_revision = "0043_backup_jobs"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "044_restore_drill_jobs.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table backup_jobs drop column job_type;")
