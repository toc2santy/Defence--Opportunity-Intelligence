"""CPV mapping v2 — full defence-branch coverage for award-driven engines

Revision ID: 0025_cpv_mapping_expansion
Revises: 0024_tender_opened_counter
Create Date: 2026-09-15

"""
from pathlib import Path

from alembic import op

revision = "0025_cpv_mapping_expansion"
down_revision = "0024_tender_opened_counter"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "025_cpv_mapping_expansion.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Only the rows this migration added — the v1 mapping stays.
    op.execute("""
        delete from taxonomy_cpv_mapping
        where cpv_code in (
            '35640000','35641000','35641100','35610000','35611100','35611400','35611500',
            '35613000','35512400','34711200','35700000','35710000','35711000','35712000',
            '35720000','32570000','32573000','32500000','38113000','32342400','35631200',
            '35631300','32533000','35730000','35722000','35723000','32352200','35121900',
            '35721000','35125000','38631000','38635000','38636000','38651000','35125100',
            '35125110','30237475','35322100','35620000','35622600','35342000','35511000',
            '35513200','35520000','35521000','35521100','35522000','35412100','35412200',
            '35412500','35420000','35421000','35320000','35330000','35340000','35341000',
            '50600000','50610000','50842000','48730000','72212730'
        )
    """)
