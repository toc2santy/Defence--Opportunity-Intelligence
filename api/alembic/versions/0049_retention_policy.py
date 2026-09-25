"""Add retention_purge job_type and a generic details column to backup_jobs

Revision ID: 0049_retention_policy
Revises: 0048_email_verification
Create Date: 2026-09-25

"""
from pathlib import Path

from alembic import op

revision = "0049_retention_policy"
down_revision = "0048_email_verification"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "049_retention_policy.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table backup_jobs drop column details;")
    op.execute("""
        alter table backup_jobs
            drop constraint backup_jobs_job_type_check,
            add constraint backup_jobs_job_type_check
                check (job_type in ('backup', 'restore_drill'));
    """)
