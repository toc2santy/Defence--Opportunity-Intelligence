"""
Report Intel — what every one of the ten engines actually produced
for ONE product.

WHY THIS EXISTS: the Architecture Map shows the ten engines as boxes,
and each engine has its own tab showing a market-wide view. Neither
answers the question a customer actually asks — "what did all of this
do for MY product?" This assembles that answer from real stored rows,
one block per engine.

WHY IT IS GATED ON A PRODUCT THAT COMPLETED THE CYCLE: a product that
was created but never classified, or classified but never
analyst-confirmed, or matched but never taken as far as opening a
tender, has only run part of the pipeline — a report for it would be
mostly empty blocks, which reads as the engines being broken rather
than the product not having got there yet. Eligibility is therefore
"at least one opportunity whose tender was actually opened from a
briefing" (opportunities.tender_opened_count > 0) — the last step of
the cycle, and a server-recorded one.

WHAT THIS DELIBERATELY DOES NOT DO: invent a block where there is no
data. An engine with nothing to say for this product returns
ran=False and a specific reason — "no award has been recorded against
these programmes yet" is a different fact from "this engine did not
run", and collapsing the two would be the exact dishonesty the
evidence model in this project exists to prevent.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.capability_resolver import build_code_index, resolve_capabilities


# A real user-reported "why is this in the report but not in my
# Active pipeline" confusion (2026-09): a tender can score high here
# (genuinely a good capability/keyword match) while ALSO already
# being awarded to someone else or past its own response deadline —
# the Opportunity Dashboard's Active/Historical split (see the
# frontend's isClosedTender/programmeStatusRemark) already reflects
# this, but the report itself said nothing, so a high score read as
# "go bid on this" with no hint it was actually history. Mirrors that
# same frontend logic exactly (same two conditions, same wording
# style) rather than inventing a second, potentially-drifting
# definition of "closed".
def _historical_reason(stage: Optional[str], deadline: Optional[str], today: date) -> Optional[str]:
    if stage == "contract_awarded":
        return "Already awarded — not open for bidding"
    if stage == "in_service":
        return "Already in service — past procurement"
    if deadline:
        # Same lenient parse as next_best_action.py's _days_until —
        # response_deadline is a free-text field from ten different
        # normalizers, not a real timestamp column, so a bare-date
        # parse is tried before a full ISO-datetime one rather than
        # requiring one exact shape.
        for parser in (date.fromisoformat, lambda s: datetime.fromisoformat(s).date()):
            try:
                parsed = parser(deadline[:19] if "T" in deadline else deadline)
                if parsed < today:
                    return "Response deadline has passed"
                break
            except ValueError:
                continue
    return None


# How many rows a card shows before "see all" takes over. A headline
# saying "414 actions standing" next to 6 visible rows is only honest
# if all 414 are actually reachable through "see all" — there used to
# be a ROW_CAP=60 hard-truncating what travelled with the block at
# all, which meant "See all 414" silently showed the same 60 rows the
# card already had. A real user-reported bug, not a hypothetical one:
# found because the "See all N" button's own N came from
# total_rows (the real count) while what it actually opened was
# rows[:60]. Removed rather than raised — these rows are short
# label/value/sub strings, a genuinely large product report is still
# a modest JSON payload, and there was no real problem this cap was
# solving, only one it was causing.
ROW_PREVIEW = 6


def _block(
    engine: str, title: str, what_it_does: str, ran: bool,
    headline: Optional[str] = None, rows: Optional[list] = None,
    note: Optional[str] = None, preview: int = ROW_PREVIEW,
) -> dict:
    rows = rows or []
    return {
        "engine": engine,
        "title": title,
        "what_it_does": what_it_does,
        "ran": ran,
        "headline": headline,
        "rows": rows,
        "total_rows": len(rows),
        "preview": preview,
        "note": note,
    }


async def _product(session: AsyncSession, product_id: str) -> Optional[dict]:
    result = await session.execute(
        text("select id, name, description, trl, created_at from products where id = :id"),
        {"id": product_id},
    )
    row = result.first()
    return dict(row._mapping) if row else None


async def _capabilities(session: AsyncSession, product_id: str) -> list[dict]:
    result = await session.execute(
        text("""
            select ct.code, ct.label, pc.classified_by, pc.confidence, pc.capability_id
            from product_capabilities pc
            join capability_taxonomy ct on ct.id = pc.capability_id
            where pc.product_id = :pid
            order by pc.classified_by, ct.code
        """),
        {"pid": product_id},
    )
    return [dict(r._mapping) for r in result]


async def _opportunities(session: AsyncSession, product_id: str) -> list[dict]:
    result = await session.execute(
        text("""
            select o.id, o.score, o.confidence, o.stage, o.next_action,
                   o.tender_opened_count, o.tender_last_opened_at,
                   p.id as programme_id, p.name as programme_name, p.country,
                   p.stage as programme_stage, p.naics_code, p.response_deadline,
                   p.organization_id, org.name as organization_name,
                   s.name as source_name
            from opportunities o
            left join programmes p on p.id = o.programme_id
            left join organizations org on org.id = p.organization_id
            left join sources s on s.id = p.source_id
            where o.product_id = :pid
            order by o.score desc nulls last
        """),
        {"pid": product_id},
    )
    return [dict(r._mapping) for r in result]


# --- the ten engines ------------------------------------------------
#
# NUMBERS AND NAMES HERE MUST MATCH the customer-facing ENGINES list
# in the frontend (01 Capability … 10 Next-Best-Action). The
# Architecture Map draws its ten engine boxes in a different order,
# which is fine for a diagram, but a report that called Competitor
# Intel "03" while the Engines tab calls it "06" would leave a
# customer holding two contradictory numbering schemes. Engine 05 is
# one engine with two halves (OEM and Partner) in that list, and is
# kept as one block here for the same reason.

def _engine_capability(caps: list[dict]) -> dict:
    confirmed = [c for c in caps if c["classified_by"] == "analyst"]
    suggested = [c for c in caps if c["classified_by"] != "analyst"]
    if not caps:
        return _block(
            "01", "Capability Intelligence",
            "Reads your product's description and proposes capabilities from the shared taxonomy — a suggestion a human then confirms or rejects.",
            False, note="This product has not been classified yet. Nothing downstream can run until it is.",
        )
    return _block(
        "01", "Capability Intelligence",
        "Reads your product's description and proposes capabilities from the shared taxonomy — a suggestion a human then confirms or rejects.",
        True,
        headline=f"{len(confirmed)} confirmed of {len(caps)} proposed",
        rows=[{
            "label": c["label"], "code": c["code"],
            "status": "Confirmed by your analyst" if c["classified_by"] == "analyst" else "⭐ Software suggested — awaiting confirmation",
            "confirmed": c["classified_by"] == "analyst",
        } for c in caps],
        note=(
            None if confirmed else
            "Nothing here is confirmed yet, so the matching engine has nothing to work from — a suggestion is never used for matching until a human confirms it."
        ) if suggested else None,
    )


def _engine_scoring(opps: list[dict], today: date) -> dict:
    if not opps:
        return _block(
            "07", "Opportunity Intelligence",
            "Scores each candidate programme: +3 for a classification-code match, plus keyword weight from the tender's own title.",
            False, note="No match has been run for this product yet.",
        )
    by_conf = {"high": 0, "medium": 0, "low": 0}
    for o in opps:
        if o["confidence"] in by_conf:
            by_conf[o["confidence"]] += 1
    historical_count = sum(
        1 for o in opps if _historical_reason(o["programme_stage"], o["response_deadline"], today)
    )
    return _block(
        "07", "Opportunity Intelligence",
        "Scores each candidate programme: +3 for a classification-code match, plus keyword weight from the tender's own title.",
        True,
        headline=f"{len(opps)} scored — {by_conf['high']} high, {by_conf['medium']} medium, {by_conf['low']} low"
        + (f" ({historical_count} historical — see badges below)" if historical_count else ""),
        rows=[{
            "label": o["programme_name"] or "—",
            "value": f"{o['score'] if o['score'] is not None else '—'}",
            "confidence": o["confidence"],
            # A high score here is a real, honest statement about the
            # match — it is NOT a statement about whether the tender
            # is still biddable. historical_reason makes that second,
            # separate fact visible right on the row instead of
            # requiring a trip to the Opportunity Dashboard to notice
            # the same tender sits in the Historical tab.
            "historical_reason": _historical_reason(o["programme_stage"], o["response_deadline"], today),
        } for o in opps],
        note="A code-only match with no keyword corroboration can never be rated better than low — that cap exists because a real false positive was found in live data. A high score can still belong to an already-awarded or deadline-passed tender — see the Historical badge on those rows; the score is about fit, not about whether it is still open.",
    )


def _engine_customer(opps: list[dict]) -> dict:
    buyers: dict[str, dict] = {}
    for o in opps:
        name = o["organization_name"]
        if not name:
            continue
        b = buyers.setdefault(name, {"label": name, "count": 0, "country": o["country"]})
        b["count"] += 1
    if not buyers:
        return _block(
            "04", "Customer Intelligence",
            "Groups the tenders your product matched by the government body actually buying — who your customer is, not just what the tender says.",
            False, note="The sources did not publish a named buying organisation on any of this product's matched tenders.",
        )
    ordered = sorted(buyers.values(), key=lambda b: -b["count"])
    return _block(
        "04", "Customer Intelligence",
        "Groups the tenders your product matched by the government body actually buying — who your customer is, not just what the tender says.",
        True,
        headline=f"{len(ordered)} buying organisation{'s' if len(ordered) != 1 else ''} reached by this product",
        rows=[{"label": b["label"], "value": f"{b['count']} tender{'s' if b['count'] != 1 else ''}", "sub": b["country"]} for b in ordered],
    )


def _engine_procurement(opps: list[dict]) -> dict:
    stages: dict[str, int] = {}
    for o in opps:
        st = o["programme_stage"]
        if st:
            stages[st] = stages.get(st, 0) + 1
    if not stages:
        return _block(
            "08", "Procurement Intelligence",
            "Reads the government's own lifecycle stage on each tender — is this a requirement being scoped, an open RFP, or an award already made.",
            False, note="No procurement stage was published on this product's matched tenders.",
        )
    return _block(
        "08", "Procurement Intelligence",
        "Reads the government's own lifecycle stage on each tender — is this a requirement being scoped, an open RFP, or an award already made.",
        True,
        headline=f"{sum(stages.values())} tenders across {len(stages)} procurement stage{'s' if len(stages) != 1 else ''}",
        rows=[{"label": k.replace("_", " ").title(), "value": str(v)} for k, v in sorted(stages.items(), key=lambda kv: -kv[1])],
        note="This is the BUYER's stage for the tender — separate from your own sales stage on the opportunity, which Engagement Intel below tracks.",
    )


def _engine_markets(opps: list[dict]) -> dict:
    markets: dict[str, dict] = {}
    for o in opps:
        c = o["country"]
        if not c:
            continue
        m = markets.setdefault(c, {"label": c, "count": 0, "sources": set()})
        m["count"] += 1
        if o["source_name"]:
            m["sources"].add(o["source_name"])
    if not markets:
        return _block(
            "02", "Market Intelligence",
            "Shows which national markets this product actually reached, and through which government source.",
            False, note="No country was recorded on this product's matched tenders.",
        )
    ordered = sorted(markets.values(), key=lambda m: -m["count"])
    return _block(
        "02", "Market Intelligence",
        "Shows which national markets this product actually reached, and through which government source.",
        True,
        headline=f"{len(ordered)} market{'s' if len(ordered) != 1 else ''} reached",
        rows=[{"label": m["label"], "value": f"{m['count']} tender{'s' if m['count'] != 1 else ''}", "sub": ", ".join(sorted(m["sources"])) or None} for m in ordered],
    )


def _engine_engagement(opps: list[dict]) -> dict:
    opened = [o for o in opps if (o["tender_opened_count"] or 0) > 0]
    visits = sum(o["tender_opened_count"] or 0 for o in opps)
    stages: dict[str, int] = {}
    for o in opps:
        stages[o["stage"]] = stages.get(o["stage"], 0) + 1
    return _block(
        "09", "Engagement Intelligence",
        "Records what your team actually did — which tenders were opened from a briefing, and where each opportunity sits in your own pipeline.",
        bool(opened),
        headline=f"{len(opened)} tender{'s' if len(opened) != 1 else ''} opened · {visits} visit{'s' if visits != 1 else ''} recorded",
        rows=(
            [{"label": o["programme_name"] or "—", "value": f"{o['tender_opened_count']}×",
              "sub": o["tender_last_opened_at"].isoformat() if o["tender_last_opened_at"] else None}
             for o in sorted(opened, key=lambda o: -(o["tender_opened_count"] or 0))]
            + [{"label": f"Pipeline: {k.replace('_', ' ').title()}", "value": str(v)} for k, v in stages.items()]
        ),
        note=None if opened else "No tender has been opened from a briefing for this product yet.",
    )


def _engine_nba(opps: list[dict], today: date) -> dict:
    # A real user request (2026-09): unlike engine 07 (Opportunity
    # Intelligence), which keeps a historical tender visible with a
    # badge because its SCORE is still a real, honest fact worth
    # showing, a "next action" for an already-awarded or deadline-
    # passed tender is not useful data — there is no real next step
    # to take on something no longer open, and showing one anyway
    # (however factually derived) reads as a live suggestion list
    # that isn't. Filtered out here, not merely badged.
    with_action = [o for o in opps if o["next_action"]]
    excluded_historical = [o for o in with_action if _historical_reason(o["programme_stage"], o["response_deadline"], today)]
    live = [o for o in with_action if o not in excluded_historical]
    if not live:
        note = (
            "No opportunity for this product carries a next action yet." if not with_action
            else f"{len(excluded_historical)} action{'s' if len(excluded_historical) != 1 else ''} existed but every one was for an already-closed tender (awarded or past deadline) — see Opportunity Intelligence above for the historical detail."
        )
        return _block(
            "10", "Next-Best-Action",
            "Suggests the concrete next step for each opportunity from its stage, confidence and deadline — rules you can read, not a black box.",
            False, note=note,
        )
    return _block(
        "10", "Next-Best-Action",
        "Suggests the concrete next step for each opportunity from its stage, confidence and deadline — rules you can read, not a black box.",
        True,
        headline=f"{len(live)} action{'s' if len(live) != 1 else ''} standing"
        + (f" ({len(excluded_historical)} closed tender{'s' if len(excluded_historical) != 1 else ''} excluded — not a live suggestion)" if excluded_historical else ""),
        rows=[{"label": o["programme_name"] or "—", "value": o["next_action"], "sub": o["stage"].replace("_", " ").title()} for o in live],
        # Explains the compound sentences in the rows above ONCE,
        # here, instead of repeating the same framing on every single
        # row — see app/next_best_action.py's own docstring for the
        # real layering (owner/deadline prefixes stacked onto the
        # base stage-driven suggestion).
        note="Each action can stack real facts onto the core recommendation — no owner assigned, an overdue follow-up, a set-aside restriction — rather than decorate it. The full sentence is the actual rule output, not a template.",
    )


async def _mapped_schemes(session: AsyncSession, capability_ids: list[str]) -> dict[str, set[str]]:
    """
    Which classification schemes each capability is actually mapped in.

    This exists so the award-driven engines below can tell two very
    different situations apart. Award data in this platform is
    extracted from EU TED and UK Find a Tender only, so it is
    CPV-coded. A capability with no CPV mapping row therefore CANNOT
    produce a competitor hit no matter how much award data is stored —
    that is a gap in the curated v1 mapping table, not an absence of
    competitors, and saying "no competitors found" there would be a
    false statement about the market.
    """
    if not capability_ids:
        return {}
    result = await session.execute(
        text("""
            select capability_id, 'NAICS' as scheme from taxonomy_naics_mapping where capability_id = any(:ids)
            union all
            select capability_id, 'CPV' from taxonomy_cpv_mapping where capability_id = any(:ids)
            union all
            select capability_id, 'UNSPSC' from taxonomy_unspsc_mapping where capability_id = any(:ids)
        """),
        {"ids": capability_ids},
    )
    schemes: dict[str, set[str]] = {}
    for row in result:
        schemes.setdefault(str(row.capability_id), set()).add(row.scheme)
    return schemes


async def _award_rows(session: AsyncSession) -> list:
    result = await session.execute(text("""
        select ca.programme_id, ca.winner_organization_id, org.name as winner_name,
               org.country as winner_country, p.naics_code, p.organization_id as buyer_id,
               p.name as programme_name
        from contract_awards ca
        join organizations org on org.id = ca.winner_organization_id
        join programmes p on p.id = ca.programme_id
    """))
    return list(result)


# Award data is extracted from EU TED and UK Find a Tender only (see
# CLAUDE.md) and both publish CPV — so CPV is the scheme the two
# award-driven engines can actually join on.
AWARD_SCHEME = "CPV"

_OEM_PARTNER_DESC = (
    "Two halves of one engine on the same award data — OEMs recorded winning the very tenders "
    "your product matched, and companies selling to those same buyers in capability areas you "
    "do NOT cover, which is what makes them a teaming candidate rather than a rival."
)


def _award_hit(bucket: dict, a, capability_labels: Optional[list[str]] = None) -> None:
    """
    Accumulates one award onto a company, keeping the evidence a
    reader needs to believe the number: what they won, and (for the
    capability-keyed engines) which of your capability areas it fell
    in. A bare count with no example is exactly the unexplained figure
    this project's evidence model exists to avoid.
    """
    h = bucket.setdefault(a.winner_name, {
        "label": a.winner_name, "count": 0, "sub": a.winner_country,
        "examples": [], "capabilities": set(),
    })
    h["count"] += 1
    if len(h["examples"]) < 3 and a.programme_name:
        h["examples"].append(a.programme_name)
    for label in capability_labels or []:
        h["capabilities"].add(label)


def _award_rows_out(bucket: dict, group: Optional[str] = None) -> list:
    out = []
    for h in sorted(bucket.values(), key=lambda h: -h["count"]):
        row = {
            "label": h["label"],
            "value": f"{h['count']} award{'s' if h['count'] != 1 else ''}",
            "sub": h["sub"],
            "detail": " · ".join(sorted(h["capabilities"])) or None,
            "examples": h["examples"],
        }
        if group:
            row["group"] = group
        out.append(row)
    return out


def _engine_oem_partner(awards: list, index, confirmed_codes: set[str], opps: list[dict], cpv_mapped: bool) -> dict:
    """
    Engine 05 is ONE engine with two halves in the customer-facing
    engine list, so it stays one block here. The two halves read the
    same award rows through opposite filters: same programme (OEM) vs
    same buyer but different capability (Partner).
    """
    programme_ids = {str(o["programme_id"]) for o in opps if o["programme_id"]}
    buyer_ids = {str(o["organization_id"]) for o in opps if o["organization_id"]}

    oem: dict[str, dict] = {}
    partner: dict[str, dict] = {}
    for a in awards:
        resolved = resolve_capabilities(a.naics_code, index)
        labels = [label for _, label in resolved]
        if str(a.programme_id) in programme_ids:
            _award_hit(oem, a, labels)
        if str(a.buyer_id) in buyer_ids:
            if any(code in confirmed_codes for code, _ in resolved):
                continue  # same capability as you — that is a competitor, not a partner
            _award_hit(partner, a, labels)

    rows = (
        _award_rows_out(oem, "Won one of your matched tenders")
        + _award_rows_out(partner, "Sells to your buyers, in another capability")
    )

    if not rows:
        notes = []
        if not programme_ids:
            notes.append("This product has not matched any programme yet.")
        else:
            notes.append(
                "No award has been published yet against any tender this product matched — most of "
                "them are still open, which is why your product is seeing them at all."
            )
        if not buyer_ids:
            notes.append("No named buyer was published on these tenders, so there is no buyer overlap to search on.")
        elif not cpv_mapped:
            notes.append(
                "Partner matching is also weaker than it should be here: this product's capabilities "
                "carry no CPV mapping, and award data is CPV-coded — see Competitor Intelligence."
            )
        return _block("05", "OEM & Partner Matching", _OEM_PARTNER_DESC, False, note=" ".join(notes))

    parts = []
    if oem:
        parts.append(f"{len(oem)} OEM{'s' if len(oem) != 1 else ''} on your matched tenders")
    if partner:
        parts.append(f"{len(partner)} teaming candidate{'s' if len(partner) != 1 else ''} at your buyers")
    # Two groups in one block, so the preview shows a few of each
    # rather than filling up entirely with the larger group.
    preview = min(len(rows), 8)
    return _block(
        "05", "OEM & Partner Matching", _OEM_PARTNER_DESC, True,
        headline=" · ".join(parts), rows=rows, preview=preview,
        note=None if partner else "No teaming candidate found yet — every award at these buyers so far is in a capability you already cover.",
    )


def _engine_programme(opps: list[dict]) -> dict:
    """
    Engine 03 is the matching step itself: which real government
    programmes this product reached, and through which source. Kept
    separate from engine 07 (Opportunity Intelligence), which is about
    how those matches were SCORED — the tenders found and the ranking
    applied to them are two different claims.
    """
    if not opps:
        return _block(
            "03", "Programme Intelligence",
            "Matches your confirmed capabilities to real government programmes — exact classification-code match, UNSPSC prefix walk, and an organisation sentinel that needs keyword corroboration.",
            False, note="No match has been run for this product yet.",
        )
    by_source: dict[str, int] = {}
    for o in opps:
        key = o["source_name"] or "Unattributed"
        by_source[key] = by_source.get(key, 0) + 1
    coded = sum(1 for o in opps if o["naics_code"])
    return _block(
        "03", "Programme Intelligence",
        "Matches your confirmed capabilities to real government programmes — exact classification-code match, UNSPSC prefix walk, and an organisation sentinel that needs keyword corroboration.",
        True,
        headline=f"{len(opps)} real programmes matched across {len(by_source)} source{'s' if len(by_source) != 1 else ''}",
        rows=[{"label": k, "value": f"{v} programme{'s' if v != 1 else ''}"} for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])],
        note=f"{coded} of {len(opps)} carry a published classification code; the rest matched on the organisation sentinel plus keyword corroboration.",
    )


def _engine_competitor(awards: list, index, confirmed_codes: set[str], cpv_mapped: bool) -> dict:
    """
    The mirror of engine 05's partner half: same award rows, kept when
    the winner's capability IS one of yours rather than when it isn't.
    """
    desc = (
        "Names companies that have won contracts in the capability areas your product is confirmed "
        "in — the field you would be bidding against. Never asserts a weakness: none is observable "
        "from award data."
    )
    if not confirmed_codes:
        return _block(
            "06", "Competitor Intelligence", desc, False,
            note="This product has no analyst-confirmed capability, so there is no capability area to compare against.",
        )
    hits: dict[str, dict] = {}
    for a in awards:
        # Only the capabilities that are BOTH the award's and yours —
        # naming a competitor's unrelated capability areas would
        # overstate the overlap the row is claiming.
        shared = [label for code, label in resolve_capabilities(a.naics_code, index) if code in confirmed_codes]
        if not shared:
            continue
        _award_hit(hits, a, shared)
    if not hits:
        return _block(
            "06", "Competitor Intelligence", desc, False,
            note=(
                "No recorded award falls in this product's confirmed capability areas yet."
                if cpv_mapped else
                "Not a finding about the market — a known gap on our side. Award winners are extracted "
                "from EU TED and UK Find a Tender only, and both publish CPV codes, but none of this "
                "product's confirmed capabilities has a CPV mapping in the v1 mapping table yet. Until "
                "one is added this engine has nothing to join award data on, so read this as 'cannot "
                "answer', not 'no competitors'."
            ),
        )
    rows = _award_rows_out(hits)
    return _block(
        "06", "Competitor Intelligence", desc, True,
        headline=f"{len(rows)} compan{'y' if len(rows) == 1 else 'ies'} winning in your capability areas",
        rows=rows,
    )


def _nba_coverage_summary(opps: list[dict], today: date) -> dict:
    """
    A real gap this surfaced (2026-09): 30,926 opportunities platform-
    wide (created 2026-08-18 to 08-22, before Next-Best-Action was
    wired into match-creation) sat with next_action permanently NULL
    — invisible in the report, since engine 10 only ever showed what
    WAS there, never said what was missing. Backfilled as a one-time
    fix, but the underlying risk (a live opportunity that could have
    converted to a real suggestion, silently absent from engine 10
    with no visible sign anything was wrong) is worth catching
    automatically on every report render from now on, not just when
    someone happens to do the arithmetic by hand and notices a gap —
    same "make the gap visible instead of requiring a human to
    notice" discipline as the source-health monitoring feature.

    Deliberately scoped to THIS product, matching Report Intel's own
    stated design (see this module's docstring) — a tenant-wide
    version of this same idea belongs on the Opportunity Dashboard,
    not inside a per-product report.
    """
    total = len(opps)
    historical = [o for o in opps if _historical_reason(o["programme_stage"], o["response_deadline"], today)]
    live = [o for o in opps if o not in historical]
    live_with_action = [o for o in live if o["next_action"]]
    live_without_action = [o for o in live if not o["next_action"]]
    return {
        "total_opportunities": total,
        "historical_count": len(historical),
        "live_count": len(live),
        "live_with_next_action": len(live_with_action),
        "live_missing_next_action": len(live_without_action),
        "note": (
            f"{len(live_without_action)} live opportunit{'y' if len(live_without_action) == 1 else 'ies'} "
            "could have a real next-best-action but currently don't — this is a real gap, not a rounding "
            "artefact; check why a suggestion was never computed for these."
            if live_without_action else
            "Every live opportunity for this product already has a computed next action — no coverage gap."
        ),
    }


async def build_product_intel_report(session: AsyncSession, product_id: str) -> Optional[dict]:
    product = await _product(session, product_id)
    if product is None:
        return None

    caps = await _capabilities(session, product_id)
    opps = await _opportunities(session, product_id)
    confirmed_codes = {c["code"] for c in caps if c["classified_by"] == "analyst"}

    # Award-driven engines share one pull of the award table and one
    # code index — three engines reading the same 1,500 rows three
    # times would be the same answer at three times the cost.
    awards = await _award_rows(session)
    index = await build_code_index(session)
    schemes = await _mapped_schemes(
        session, [str(c["capability_id"]) for c in caps if c["classified_by"] == "analyst"]
    )
    cpv_mapped = any(AWARD_SCHEME in s for s in schemes.values())

    opened = [o for o in opps if (o["tender_opened_count"] or 0) > 0]
    today = date.today()

    engines = [
        _engine_capability(caps),
        _engine_markets(opps),
        _engine_programme(opps),
        _engine_customer(opps),
        _engine_oem_partner(awards, index, confirmed_codes, opps, cpv_mapped),
        _engine_competitor(awards, index, confirmed_codes, cpv_mapped),
        _engine_scoring(opps, today),
        _engine_procurement(opps),
        _engine_engagement(opps),
        _engine_nba(opps, today),
    ]
    engines.sort(key=lambda e: e["engine"])

    return {
        "product": {
            "id": str(product["id"]),
            "name": product["name"],
            "description": product["description"],
            "trl": product["trl"],
        },
        # The cycle this report is a record of, each step counted from
        # real rows rather than asserted.
        "cycle": {
            "classified": len(caps) > 0,
            "confirmed": len(confirmed_codes) > 0,
            "matched": len(opps) > 0,
            "briefed": len(opened) > 0,
            "capabilities_confirmed": len(confirmed_codes),
            "opportunities": len(opps),
            "tenders_opened": len(opened),
        },
        "engines_ran": sum(1 for e in engines if e["ran"]),
        "engines_total": len(engines),
        "engines": engines,
        # Rendered right after the engine 10 (Next-Best-Action) card
        # on the frontend — see _nba_coverage_summary's own docstring.
        "nba_coverage": _nba_coverage_summary(opps, today),
    }
