"""Plural keyword forms for TACTICAL.PROTECTIVE_EQUIPMENT

Revision ID: 0034_ppe_plural_kw
Revises: 0033_tactical_ppe
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0034_ppe_plural_kw"
down_revision = "0033_tactical_ppe"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "034_tactical_ppe_plural_keywords.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        delete from capability_taxonomy_keywords
        where keyword in (
            'bullet-proof vests', 'bulletproof vests', 'ballistic vests',
            'combat uniforms', 'military uniforms', 'military helmets',
            'tactical vests', 'body armors'
        )
    """)
