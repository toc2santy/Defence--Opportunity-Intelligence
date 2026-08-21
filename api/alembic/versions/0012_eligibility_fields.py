"""Eligibility fields (set-aside, response deadline) + naics_code regression fix

Revision ID: 0012_eligibility_fields
Revises: 0011_product_deletion_cascade
Create Date: 2026-08-20

"""
from pathlib import Path

from alembic import op

revision = "0012_eligibility_fields"
down_revision = "0011_product_deletion_cascade"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "012_eligibility_fields.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        alter table programmes drop column if exists set_aside_code;
        alter table programmes drop column if exists set_aside_description;
        alter table programmes drop column if exists response_deadline;
    """)
