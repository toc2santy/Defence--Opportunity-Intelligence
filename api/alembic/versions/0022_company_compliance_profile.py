"""Company compliance profile — GST/PAN/TAN/IEC, logo, public share card

Revision ID: 0022_company_compliance_profile
Revises: 0021_company_contact_profile
Create Date: 2026-08-25

"""
from pathlib import Path

from alembic import op

revision = "0022_company_compliance_profile"
down_revision = "0021_company_contact_profile"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "022_company_compliance_profile.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table tenants drop column if exists gst_number")
    op.execute("alter table tenants drop column if exists pan_number")
    op.execute("alter table tenants drop column if exists tan_number")
    op.execute("alter table tenants drop column if exists iec_license")
    op.execute("alter table tenants drop column if exists tagline")
    op.execute("alter table tenants drop column if exists logo_data_url")
    op.execute("alter table tenants drop column if exists share_token")
    op.execute("alter table tenants drop column if exists custom_fields")
