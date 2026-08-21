"""Phase 1: capability taxonomy keywords, product_capabilities RLS

Revision ID: 0002_capability_keywords
Revises: 0001_baseline
Create Date: 2026-08-19

"""
from pathlib import Path

from alembic import op

revision = "0002_capability_keywords"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "002_capability_keywords.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Reverses the structural additions only. Deliberately does NOT
    # delete the expanded taxonomy rows or seeded keywords — real
    # product_capabilities rows may reference them by the time
    # anyone runs a downgrade, and capability_taxonomy has no
    # ON DELETE CASCADE from product_capabilities (default RESTRICT),
    # so an attempt to delete referenced taxonomy rows would fail
    # loudly rather than silently destroy real classification data.
    op.execute("""
        drop policy if exists tenant_isolation_product_capabilities on product_capabilities;
        alter table product_capabilities disable row level security;
        drop table if exists capability_taxonomy_keywords;
    """)
