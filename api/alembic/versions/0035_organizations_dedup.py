"""Organizations dedup + unique(name, org_type) to make the race impossible

Revision ID: 0035_org_dedup
Revises: 0033_tactical_ppe
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0035_org_dedup"
down_revision = "0034_ppe_plural_kw"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "035_organizations_dedup.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table organizations drop constraint if exists organizations_name_org_type_key")
