"""Keyword-only matching: standalone keywords + opportunities.match_basis

Revision ID: 0054_keyword_only_matching
Revises: 0053_missing_capabilities
Create Date: 2026-10-06

"""
from pathlib import Path

from alembic import op

revision = "0054_keyword_only_matching"
down_revision = "0053_missing_capabilities"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "054_keyword_only_matching.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        alter table opportunities drop column match_basis;
        alter table capability_taxonomy_keywords drop column standalone;
    """)
