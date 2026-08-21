"""
Source-agnostic ingestion infrastructure.

This exists because the scheduler used to be hardcoded to exactly
one source (SAM.gov) — adding a second real source (UK Contracts
Finder, planned next) would have meant duplicating the scheduling,
error-handling, and status-reporting logic rather than reusing it.
This module is what makes adding a source a matter of registering
one more IngestionSourceConfig, not writing a parallel scheduler.

Genuinely proven by refactoring the EXISTING SAM.gov ingestion to
go through this, not by designing it in the abstract for a
hypothetical future source — see app/sam_gov_ingestion.py.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class IngestionConfigError(Exception):
    """Raised when a source can't run because required config (e.g. an API key) is missing."""
    pass


@dataclass
class IngestionSourceConfig:
    code: str                                          # stable key, e.g. 'sam_gov' — used in job IDs, rotation rows
    display_name: str                                  # matches the sources.name row this source writes evidence against
    interval_hours: int                                # how often the scheduler runs this source
    api_key_env_var: Optional[str]                     # which env var holds its credential, for status reporting
    run_fn: Callable[..., Awaitable[dict]]              # async def run_fn(session, triggered_by_user_id, **kwargs) -> dict
    scheduler_job_id: str                               # stable APScheduler job ID for this source


async def run_scheduled_source(source_config: IngestionSourceConfig, session_factory):
    """
    Generic scheduled-run wrapper — same shape for every source,
    regardless of what that source actually does internally. Any
    new source registered in INGESTION_SOURCES gets this behavior
    for free: quiet skip on missing config, logged failure
    otherwise, no scheduler crash either way.
    """
    async with session_factory() as session:
        try:
            result = await source_config.run_fn(session, triggered_by_user_id="system-scheduler")
            print(f"[scheduled ingestion:{source_config.code}] {result}")
        except IngestionConfigError as e:
            print(f"[scheduled ingestion:{source_config.code}] skipped: {e}")
        except Exception as e:
            print(f"[scheduled ingestion:{source_config.code}] failed: {e}")


# ---------------------------------------------------------------
# Rotation helpers — generalized from what was originally SAM.gov-
# specific hardcoded SQL (a literal 'SAM_GOV_NAICS_ROTATION' string
# baked into the query). Any source can now have its own rotation
# cursor row, keyed by its own rotation_key, without colliding with
# another source's rotation state.
# ---------------------------------------------------------------
async def get_rotation_index(session: AsyncSession, rotation_key: str) -> int:
    result = await session.execute(
        text("select rotation_index from ingestion_rotation_state where source_name = :key"),
        {"key": rotation_key},
    )
    row = result.first()
    return row.rotation_index if row else 0


async def advance_rotation_index(session: AsyncSession, rotation_key: str, total_groups: int):
    await session.execute(
        text("""
            update ingestion_rotation_state
            set rotation_index = (rotation_index + 1) % :total_groups, updated_at = now()
            where source_name = :key
        """),
        {"total_groups": total_groups, "key": rotation_key},
    )
    await session.commit()
