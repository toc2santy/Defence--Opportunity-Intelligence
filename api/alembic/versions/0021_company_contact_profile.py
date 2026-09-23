"""Company + contact profile fields at signup

Revision ID: 0021_company_contact_profile
Revises: 0020_south_africa_source
Create Date: 2026-08-25

"""
from pathlib import Path

from alembic import op

revision = "0021_company_contact_profile"
down_revision = "0020_south_africa_source"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "021_company_contact_profile.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table users drop column if exists full_name")
    op.execute("alter table users drop column if exists title")
    op.execute("alter table users drop column if exists phone")
    op.execute("alter table tenants drop column if exists website")
    op.execute("alter table tenants drop column if exists country")
    op.execute("alter table tenants drop column if exists phone")
