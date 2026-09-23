"""Contract onboarding checklist state — Path to Contract (Won follow-up)

Revision ID: 0019_opportunity_checklist_state
Revises: 0018_programme_ui_link
Create Date: 2026-08-24

"""
from pathlib import Path

from alembic import op

revision = "0019_opportunity_checklist_state"
down_revision = "0018_programme_ui_link"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "019_opportunity_checklist_state.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table opportunities drop column if exists checklist_state")
