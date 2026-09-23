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

from app.org_name_format import normalize_org_name


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


async def _get_or_create_organization(
    session: AsyncSession, name: Optional[str], org_type: str, country: Optional[str]
) -> Optional[str]:
    """
    The one real upsert both public functions below delegate to.

    WHY THIS REPLACED A CHECK-THEN-INSERT: every one of this
    project's ingestion modules used to run its own private
    "SELECT — if found, return; else INSERT", and nothing in the
    schema stopped two such sequences from racing each other —
    confirmed live (migration 035): a Ukrainian military unit name
    was inserted twice, byte-for-byte identical, because two
    concurrent calls both found "not found" before either INSERT had
    committed. `organizations` now has a real `unique(name,
    org_type)` constraint (same migration), so this upserts against
    it with `ON CONFLICT ... DO UPDATE` — the standard trick to get a
    real `id` back from an upsert (`DO NOTHING` alone returns no row
    on conflict) — which makes the race structurally impossible
    rather than merely unlikely.
    """
    if not name:
        return None
    normalized = normalize_org_name(name)
    if not normalized:
        return None
    result = await session.execute(
        text("""
            insert into organizations (name, org_type, country, classification)
            values (:name, :org_type, :country, 'public')
            on conflict (name, org_type) do update set name = excluded.name
            returning id
        """),
        {"name": normalized, "org_type": org_type, "country": country},
    )
    return str(result.scalar_one())


async def get_or_create_oem_organization(
    session: AsyncSession, name: Optional[str], country: Optional[str]
) -> Optional[str]:
    """
    OEM Intelligence — shared by every source that captures award
    winners (TED, UK Find a Tender, Colombia, ProZorro, CanadaBuys),
    unlike each source's own per-file buyer-creation helper, which
    stays source-specific because buyer country handling genuinely
    differs per source (e.g. UK FT hardcodes 'United Kingdom', TED
    gets it from the notice). A winner is looked up by name only,
    regardless of which source reported it — the same real company
    should not get a duplicate organizations row just because two
    different sources both reported it winning something.
    """
    return await _get_or_create_organization(session, name, "oem", country)


async def get_or_create_government_buyer(
    session: AsyncSession, name: Optional[str], country: Optional[str]
) -> Optional[str]:
    """
    The buyer-side equivalent of get_or_create_oem_organization —
    added 2026-09 to replace 8 nearly-identical private
    `_get_or_create_organization` functions, one per ingestion module,
    that each independently had the same check-then-insert race (see
    _get_or_create_organization's docstring). Every ingestion module
    now calls this instead, passing whatever country literal/variable
    its own source resolves — that per-source difference is real and
    stays with each caller, only the upsert logic itself is shared.
    """
    return await _get_or_create_organization(session, name, "government_body", country)


async def record_contract_award(
    session: AsyncSession,
    programme_id: str,
    winner_organization_id: str,
    source_id: str,
    value_amount: Optional[float] = None,
    value_currency: Optional[str] = None,
) -> str:
    """
    Idempotent on (programme_id, winner_organization_id) — re-running
    an ingestion that reports the same award again must not create a
    duplicate row, matching the idempotency guarantee the programmes
    table itself has via (source_id, external_ref).

    value_amount/value_currency are optional and default to None —
    most callers don't have one to give (see db/migrations/040 for
    which sources genuinely publish an award-level total and which
    don't); passing neither leaves the columns exactly as null as
    before this parameter existed.
    """
    result = await session.execute(
        text("""
            insert into contract_awards (programme_id, winner_organization_id, source_id, value_amount, value_currency)
            values (:programme_id, :winner_organization_id, :source_id, :value_amount, :value_currency)
            on conflict (programme_id, winner_organization_id) do update
                set source_id = excluded.source_id,
                    value_amount = coalesce(excluded.value_amount, contract_awards.value_amount),
                    value_currency = coalesce(excluded.value_currency, contract_awards.value_currency)
            returning id
        """),
        {
            "programme_id": programme_id,
            "winner_organization_id": winner_organization_id,
            "source_id": source_id,
            "value_amount": value_amount,
            "value_currency": value_currency,
        },
    )
    return str(result.scalar_one())


async def advance_rotation_index(session: AsyncSession, rotation_key: str, total_groups: int):
    """
    A real UPSERT, not a bare UPDATE — found to matter live (2026-09):
    CPPP's rotation key was seeded by its own migration ahead of time,
    but SAM.gov's per-NAICS-code offset keys are dynamic (one per
    NAICS code ever queried, not a fixed known set), so nothing pre-
    seeds them. A bare UPDATE against a source_name with no existing
    row silently affects zero rows — get_rotation_index's own default
    of 0 makes reading look fine, so this failure mode is invisible
    until you specifically check whether the index ever actually
    moved, which is exactly how it was found: two successive SAM.gov
    runs for the same NAICS code both reported offset 0. ON CONFLICT
    means every rotation key self-seeds on first use, the same
    structural fix already applied to organizations (migration 035)
    for the equivalent check-then-write race.
    """
    await session.execute(
        text("""
            insert into ingestion_rotation_state (source_name, rotation_index)
            values (:key, 1 % :total_groups)
            on conflict (source_name) do update
                set rotation_index = (ingestion_rotation_state.rotation_index + 1) % :total_groups,
                    updated_at = now()
        """),
        {"total_groups": total_groups, "key": rotation_key},
    )
    await session.commit()
