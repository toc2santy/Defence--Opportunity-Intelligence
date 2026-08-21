"""Phase 2: SAM.gov ingestion support (external_ref, source seed)

Revision ID: 0003_ingestion_sam_gov
Revises: 0002_capability_keywords
Create Date: 2026-08-19

"""
from pathlib import Path

from alembic import op

revision = "0003_ingestion_sam_gov"
down_revision = "0002_capability_keywords"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "003_ingestion_sam_gov.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # external_ref is deliberately left in place on downgrade: by
    # the time this would run, real ingested programmes almost
    # certainly depend on it for dedup, and dropping it would strip
    # that safety net from existing data rather than just undo a
    # structural change. The seeded SAM.gov source row is also left
    # in place — real evidence/programme rows reference it by
    # source_id, and those foreign keys would break otherwise.
    pass
