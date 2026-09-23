"""Programme procurement contacts — Engagement Intelligence (contact half)

Revision ID: 0017_programme_contacts
Revises: 0016_contract_awards
Create Date: 2026-08-22

"""
from pathlib import Path

from alembic import op

revision = "0017_programme_contacts"
down_revision = "0016_contract_awards"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "017_programme_contacts.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        alter table programmes
            drop column if exists contact_name,
            drop column if exists contact_email,
            drop column if exists contact_phone
    """)
