"""Self-expanding taxonomy: learned keywords + code suggestions

Revision ID: 0052_taxonomy_learning
Revises: 0051_ingestion_refresh_jobs
Create Date: 2026-10-06

"""
from pathlib import Path

from alembic import op

revision = "0052_taxonomy_learning"
down_revision = "0051_ingestion_refresh_jobs"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "052_taxonomy_learning.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        drop table taxonomy_learning_suggestions;
        alter table capability_taxonomy_keywords drop column origin;
        alter table backup_jobs
            drop constraint backup_jobs_job_type_check,
            add constraint backup_jobs_job_type_check
                check (job_type in ('backup', 'restore_drill', 'retention_purge'));
    """)
