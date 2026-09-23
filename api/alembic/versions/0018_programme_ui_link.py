"""Programme apply/detail link — Path to Contract

Revision ID: 0018_programme_ui_link
Revises: 0017_programme_contacts
Create Date: 2026-08-23

"""
from pathlib import Path

from alembic import op

revision = "0018_programme_ui_link"
down_revision = "0017_programme_contacts"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "018_programme_ui_link.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table programmes drop column if exists ui_link")
