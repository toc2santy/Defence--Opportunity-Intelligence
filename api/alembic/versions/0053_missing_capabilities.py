"""New capabilities: weapons & ammunition, training & simulation, spares

Revision ID: 0053_missing_capabilities
Revises: 0052_taxonomy_learning
Create Date: 2026-10-06

"""
from pathlib import Path

from alembic import op

revision = "0053_missing_capabilities"
down_revision = "0052_taxonomy_learning"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "053_missing_capabilities.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # ON DELETE CASCADE removes the keywords, the three mapping tables and
    # any learned suggestions in the same statement.
    op.execute("""
        delete from capability_taxonomy
        where code in ('WEAPONS.AMMUNITION', 'TRAINING.SIMULATION', 'SPARES.REPLACEMENT')
    """)
