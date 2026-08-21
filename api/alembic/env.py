"""
Alembic environment. Async because the app uses asyncpg — a sync
Alembic setup would need a second, separate psycopg2 connection
string just for migrations, which is one more thing to keep in
sync with reality. This reads the same DATABASE_URL environment
variable app/main.py does.
"""

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DATABASE_URL = os.environ.get(
    "ALEMBIC_DATABASE_URL",
    os.environ.get("DATABASE_URL", "postgresql+asyncpg://doi_app:changeme@localhost:5432/doi"),
)
config.set_main_option("sqlalchemy.url", DATABASE_URL)
# Deliberately prefers ALEMBIC_DATABASE_URL over the app's own
# DATABASE_URL. The running app connects as `doi_app`, a role with
# no CREATE privilege on the schema by design — RLS + least
# privilege means that role structurally cannot create or alter
# tables, even if the API were ever compromised. Migrations need
# that DDL privilege, so they run under a separate, more privileged
# credential instead of weakening the app's own runtime role to get
# there. Set ALEMBIC_DATABASE_URL to a role that actually has
# CREATE (e.g. `postgres` in local dev — see docker-compose.yml).

# No declarative ORM models exist in this project (queries are raw
# SQL via SQLAlchemy Core text()), so target_metadata stays None —
# `alembic revision --autogenerate` cannot diff a schema that has
# no models to diff against. Every migration here is hand-written,
# same as the raw .sql files it wraps. This is a deliberate,
# honest limitation, not an oversight.
target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
