"""Fix opportunities FK to cascade on product deletion

Revision ID: 0011_product_deletion_cascade
Revises: 0010_taxonomy_cpv_mapping
Create Date: 2026-08-20

"""
from pathlib import Path

from alembic import op

revision = "0011_product_deletion_cascade"
down_revision = "0010_taxonomy_cpv_mapping"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "011_product_deletion_cascade.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("""
        alter table opportunities drop constraint if exists opportunities_product_id_fkey;
        alter table opportunities add constraint opportunities_product_id_fkey
            foreign key (product_id) references products(id);
    """)
