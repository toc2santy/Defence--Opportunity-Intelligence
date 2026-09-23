"""Spanish keywords so Colombian titles can corroborate a code match

Revision ID: 0028_spanish_keywords
Revises: 0027_colombia_source
Create Date: 2026-09-16

"""
from pathlib import Path

from alembic import op

revision = "0028_spanish_keywords"
down_revision = "0027_colombia_source"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "migrations" / "028_spanish_keywords.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Only the Spanish rows. Identified by the exact keyword strings
    # this migration inserts rather than by a pattern, so a keyword an
    # analyst added by hand through /admin/taxonomy is never collateral.
    op.execute("""
        delete from capability_taxonomy_keywords
        where keyword in (
            'material aeronáutico','aeronáutico','repuestos aeronáuticos','aeronave','helicóptero',
            'aviación','avión militar','vehículo blindado','blindaje','blindado','vehículos militares',
            'automotor','llantas','buque','navegación marítima','embarcación','astillero','submarino',
            'guardacostas','munición','municiones','armamento','fusil','explosivo','uniformes','vestuario',
            'misil','misiles','cohete','radiocomunicación','comunicaciones tácticas','equipos de comunicaciones',
            'cifrado','mando y control','radares','vigilancia','reconocimiento','inteligencia militar',
            'visión nocturna','optrónica','cámara térmica','sensores','dron','drones','aeronave no tripulada',
            'no tripulado','autónomo','robótica','antidron','contra drones','guerra electrónica','satelital',
            'satélite','ciberseguridad','ciberdefensa','sonares','hidroacústico','mantenimiento','repuestos',
            'overhaul'
        )
    """)
