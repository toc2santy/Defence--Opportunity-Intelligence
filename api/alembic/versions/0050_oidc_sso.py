"""Add oidc_identities table for SSO (Sign in with Google/Microsoft/any OIDC provider)

Revision ID: 0050_oidc_sso
Revises: 0049_retention_policy
Create Date: 2026-09-25

"""
from pathlib import Path

from alembic import op

revision = "0050_oidc_sso"
down_revision = "0049_retention_policy"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "050_oidc_sso.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("drop table oidc_identities;")
