"""contact_email_secondary on programmes + linkedin_url on tenants

Revision ID: 0036_contact2_linkedin
Revises: 0035_org_dedup
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0036_contact2_linkedin"
down_revision = "0035_org_dedup"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "036_contact_secondary_and_linkedin.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table programmes drop column if exists contact_email_secondary")
    op.execute("alter table tenants drop column if exists linkedin_url")
