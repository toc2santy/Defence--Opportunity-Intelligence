"""Add value_amount/value_currency to contract_awards

Revision ID: 0040_contract_award_value
Revises: 0039_paraguay_source
Create Date: 2026-09-18

"""
from pathlib import Path

from alembic import op

revision = "0040_contract_award_value"
down_revision = "0039_paraguay_source"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "040_contract_award_value.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("alter table contract_awards drop column if exists value_amount")
    op.execute("alter table contract_awards drop column if exists value_currency")
