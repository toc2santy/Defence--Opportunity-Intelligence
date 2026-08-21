"""Phase 3: NAICS mapping, programme matching support

Revision ID: 0004_programme_matching
Revises: 0003_ingestion_sam_gov
Create Date: 2026-08-19

"""
from pathlib import Path

from alembic import op

revision = "0004_programme_matching"
down_revision = "0003_ingestion_sam_gov"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "004_programme_matching.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # taxonomy_naics_mapping and the opportunities dedup constraint
    # are safe to remove structurally. programmes.naics_code is
    # deliberately NOT dropped — by the time a downgrade would run,
    # real ingested programme data depends on it for matching, and
    # the original backfill (parsed from evidence.claim text) is not
    # cheaply reconstructible if lost.
    op.execute("""
        alter table opportunities drop constraint if exists uq_opportunities_product_programme;
        drop table if exists taxonomy_naics_mapping;
    """)
