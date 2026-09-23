"""SECOP II Colombia — seventh ingestion source

Revision ID: 0027_colombia_source
Revises: 0026_drop_generic_cpv
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0027_colombia_source"
down_revision = "0026_drop_generic_cpv"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "027_colombia_source.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Only the source row. Any programmes already ingested through it
    # are left alone — deleting real ingested intelligence is never
    # what a schema rollback should do.
    op.execute("delete from sources where name = 'SECOP II (Colombia Compra Eficiente)'")
