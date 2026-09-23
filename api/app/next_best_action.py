"""
Next-Best-Action — the original roadmap's Phase 4, last of the five
items. Rules-based by design, matching this whole project's stance
on AI: `capability_taxonomy` classification is a weighted keyword
match a human confirms, `programme_matching` is a scored rule, not a
model — nothing here pretends to be machine learning either. A
plain-language rule that's wrong is a bug to fix; a black-box
suggestion that's wrong has no fix, just a shrug.

Populates `opportunities.next_action` — a column that has existed
since the Phase 0 schema (see db/schema.sql) and been written to by
nothing until now.

Pure and unit-tested — no I/O, same separation as app/scoring.py and
app/matching_scoring.py. Deadline urgency is computed relative to a
passed-in `today` rather than calling datetime.now() internally,
specifically so the urgency rules are testable without mocking the
clock.
"""

from datetime import date, datetime
from typing import Optional

# Below this many days to a response deadline, the deadline itself
# dominates whatever the stage/confidence would otherwise suggest —
# a real deadline always outranks a soft nudge to "review confidence."
URGENT_DEADLINE_DAYS = 7

_CLOSED_STAGES = {"won", "lost"}
_LATE_STAGES = {"proposal", "negotiation"}
_MID_STAGES = {"rfi", "rfp", "evaluation", "trial", "demo"}
_EARLY_STAGES = {"lead", "qualified", "technical_discussion", "nda"}


def _days_until(deadline: Optional[str], today: date) -> Optional[int]:
    if not deadline:
        return None
    # response_deadline arrives as an ISO-ish string from every
    # normalizer (see e.g. app/cppp_india_normalize.parse_date) —
    # only the date portion matters for urgency, so a bare date parse
    # is attempted first and a full ISO datetime second, rather than
    # requiring one exact format.
    for parser in (date.fromisoformat, lambda s: datetime.fromisoformat(s).date()):
        try:
            return (parser(deadline[:19] if "T" in deadline else deadline) - today).days
        except ValueError:
            continue
    return None


def _base_suggestion(stage: str, confidence: Optional[str], days_left: Optional[int]) -> str:
    if stage in _CLOSED_STAGES:
        return (
            "Closed won — begin contract onboarding." if stage == "won"
            else "Closed lost — no further action; review why for future bids."
        )

    if days_left is not None:
        if days_left < 0:
            return "Response deadline has passed — confirm whether this opportunity is still live before investing further time."
        if days_left <= URGENT_DEADLINE_DAYS:
            return f"Urgent — response deadline in {days_left} day{'s' if days_left != 1 else ''}. Prioritise this over other opportunities."

    if stage in _EARLY_STAGES:
        if confidence == "low":
            return "Low confidence — re-check the keyword/category match before investing further effort."
        if confidence == "high":
            return "High-confidence early opportunity — reach out to the buying organisation to confirm requirement fit."
        return "Review the match details and decide whether to pursue."

    if stage in _MID_STAGES:
        return "Continue technical qualification — confirm compliance against the stated requirement."

    if stage in _LATE_STAGES:
        if days_left is not None:
            return f"Finalise and submit before the deadline ({days_left} days remaining)."
        return "Finalise and submit the proposal."

    return "Review current stage and confirm the next concrete step."


def suggest_next_action(
    stage: str,
    confidence: Optional[str],
    response_deadline: Optional[str],
    today: Optional[date] = None,
    set_aside_code: Optional[str] = None,
    set_aside_description: Optional[str] = None,
    has_owner: bool = True,
    due_date: Optional[str] = None,
) -> str:
    """
    Returns one plain-language sentence — the base rule (stage,
    confidence, deadline urgency, unchanged from before) with two
    real, already-stored facts layered on as a prefix/suffix rather
    than new branches, so every existing rule and its wording stays
    exactly as it was:

      - a published set-aside/eligibility restriction (e.g. "Total
        Small Business Set-Aside") is real data this platform already
        stores (see db/migrations, `set_aside_code`) but never
        surfaced in the one place a user is told what to DO next —
        appended only while the opportunity is still in an early
        stage, since eligibility is exactly what needs checking
        BEFORE investing further effort, not after.
      - a missing owner (`owner_user_id` is null) is prepended as a
        blocking-looking prefix, since "do X" advice is not
        actionable if nobody on the team is assigned to do it.

    `today` defaults to the real current date and exists as a
    parameter purely for testability — callers should never need to
    pass it explicitly.
    """
    today = today or date.today()
    days_left = _days_until(response_deadline, today)
    message = _base_suggestion(stage, confidence, days_left)

    if set_aside_code and stage in _EARLY_STAGES and stage not in _CLOSED_STAGES:
        label = set_aside_description or set_aside_code
        message += f" This tender carries a published eligibility restriction ({label}) — confirm you qualify before proceeding."

    # due_date is the TENANT's own internal follow-up date — a
    # genuinely different thing from response_deadline (the
    # GOVERNMENT's tender deadline, already handled above). It existed
    # in the schema and was settable via PATCH since Phase 0 but
    # nothing ever checked whether it had quietly passed — a team
    # could set "follow up by Friday" and the platform would never
    # once say so.
    if due_date and stage not in _CLOSED_STAGES:
        try:
            overdue_days = (today - date.fromisoformat(due_date)).days
        except ValueError:
            overdue_days = None
        if overdue_days is not None and overdue_days > 0:
            message = (
                f"⚠ Overdue by {overdue_days} day{'s' if overdue_days != 1 else ''} "
                f"(follow-up was due {due_date}). "
            ) + message

    if not has_owner and stage not in _CLOSED_STAGES:
        message = "No owner assigned yet — assign one first. " + message

    return message
