"""
Self-expanding taxonomy (2026-10, migration 052).

Why this exists: the taxonomy used to grow only through migrations. Measured
live (2026-10-06): of 11,565 stored tenders only ~53% were reachable by any
capability — via a code mapping (what makes a tender a CANDIDATE for a product
at all) or a title keyword (what scores it). This module mines the tenders
already stored and proposes both.

Two kinds of learning, with different trust levels on purpose:

  KEYWORDS — a title phrase that is strongly specific to ONE capability's
    code-mapped tenders. A keyword only changes the SCORE of a tender that is
    already a candidate; it cannot pull in new tenders. So high-precision ones
    are added automatically — always at weight 1 (scoring.py: one weight-1
    hit cannot reach even MEDIUM confidence on its own), marked
    origin='learned', fully audited, and deleting one marks it rejected so it
    is never re-learned.

  CODES — a classification code with no mapping whose tender titles keep
    scoring for one capability. A mapped code makes EVERY tender carrying it a
    candidate, so this is never automatic: it lands in a review queue and a
    platform admin approves it.

Guard-rails:
  - Learns only from PUBLIC tender text, never from a tenant's product text.
    The taxonomy is global; learning from tenant text would leak one
    customer's product description to every other tenant.
  - Tenders whose code maps to MORE than one capability are not used as
    evidence for any of them (they cannot say which capability a phrase
    belongs to).
  - Code suggestions are scored against CURATED keywords only, so learned
    keywords can never feed back into more learning (no drift loop).
  - Short single words (< 5 chars) are never learned — the same lesson as the
    "UAV KUMBHIGRAM" false positive documented in programme_matching.py.
  - Repeated identical titles (recurring tenders) count once.
  - A rejected suggestion is never proposed again.
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.scoring import fold, score_text, MEDIUM_CONFIDENCE_THRESHOLD

# Unicode letters only: no digits (kills part numbers, NSNs, tender ids) and
# works for Cyrillic / accented Latin, so Ukrainian and Spanish titles teach
# the taxonomy in their own language.
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

MAX_NGRAM = 3
MIN_SINGLE_WORD_LEN = 5
MIN_EDGE_TOKEN_LEN = 3

# Function words in the languages the stored tenders actually arrive in.
# A phrase may contain them inside ("parts of aircraft") but never start or
# end with one ("protective and", "and safety clothing") — found live in the
# first dry-run, where such fragments scored 100% precision purely because
# they are boilerplate of one category label.
_EDGE_STOPWORDS = frozenset("""
and the for with from into onto upon per via not all any other others etc
des les aux pour dans sur par avec sans une del los las por con sin para
und der die das den dem mit von fur bei ein eine zum zur als aus
dla oraz lub wraz przy dei delle della alla che per
""".split())

_TED_SEP = " \u2013 "  # "Country – CPV category label – real title"


def clean_title(title: str) -> str:
    """
    EU TED titles are "Germany – Fire engines – <the real title>": a country
    and the CPV category LABEL of the code the tender already carries. Both
    are tautological as evidence (the code is the thing being learned from)
    and the country is pure noise, so only the real title is mined.
    """
    t = title or ""
    if t.count(_TED_SEP) >= 2:
        t = _TED_SEP.join(t.split(_TED_SEP)[2:])
    return t

# --- keyword tiers ---------------------------------------------------------
PENDING_MIN_SUPPORT = 5
PENDING_MIN_PRECISION = 0.60
PENDING_MIN_LIFT = 3.0
PENDING_MIN_BUYERS = 2

AUTO_MIN_SUPPORT = 10
AUTO_MIN_PRECISION = 0.85
AUTO_MIN_LIFT = 5.0
AUTO_MIN_BUYERS = 3
# ...and to stay out of the automatic tier a phrase must not be one buying
# office's house style: either many buyers, or seen on more than one source.
AUTO_MIN_BUYERS_SINGLE_SOURCE = 8

LEARNED_KEYWORD_WEIGHT = 1
MAX_NEW_KEYWORDS_PER_CAPABILITY_PER_RUN = 15
MAX_LEARNED_KEYWORDS_PER_CAPABILITY = 150

# --- code suggestions ------------------------------------------------------
CODE_MIN_DOCS = 5
CODE_MIN_HITS = 4
CODE_MIN_HIT_RATE = 0.40
CODE_MIN_BUYERS = 2

# Which mapping table a suggested code belongs in, by the source that carries
# it (see programme_matching._load_classification_codes: NAICS+CPV are exact
# lookups, UNSPSC is a prefix lookup).
_NAICS_SOURCES = ("SAM.gov",)
_CPV_SOURCES = ("EU TED", "UK Find a Tender", "ProZorro")

EXAMPLES_PER_SUGGESTION = 3


@dataclass
class Doc:
    title: str
    buyer: str
    code: str
    source: str
    ngrams: frozenset = field(default_factory=frozenset)


def title_ngrams(title: str) -> frozenset:
    """1-3 word phrases of a title, folded, letters only, with the edge rules above."""
    tokens = _TOKEN_RE.findall(fold(clean_title(title)))
    grams = set()
    for n in range(1, MAX_NGRAM + 1):
        for i in range(len(tokens) - n + 1):
            window = tokens[i:i + n]
            if len(window[0]) < MIN_EDGE_TOKEN_LEN or len(window[-1]) < MIN_EDGE_TOKEN_LEN:
                continue
            if window[0] in _EDGE_STOPWORDS or window[-1] in _EDGE_STOPWORDS:
                continue
            if n == 1 and len(window[0]) < MIN_SINGLE_WORD_LEN:
                continue
            grams.add(" ".join(window))
    return frozenset(grams)


def _normalise_title(title: str) -> str:
    return " ".join(_TOKEN_RE.findall(fold(clean_title(title))))


def dedupe_docs(docs: list[Doc]) -> list[Doc]:
    """A recurring tender published every quarter must count once, not 40 times."""
    seen: set[str] = set()
    out = []
    for d in docs:
        key = _normalise_title(d.title)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


@dataclass
class KeywordSuggestion:
    capability_id: str
    keyword: str
    support: int
    buyers: int
    precision: float
    lift: float
    tier: str  # 'auto' | 'pending'
    examples: list


def _is_subphrase(shorter: str, longer: str) -> bool:
    if shorter == longer:
        return False
    return f" {shorter} " in f" {longer} "


def mine_keywords(
    docs: list[Doc],
    capability_of_code: dict[str, str | None],
    existing_keywords: dict[str, set[str]],
    skip: set[tuple[str, str]],
) -> list[KeywordSuggestion]:
    """
    `docs`: every stored tender (already deduped), with ngrams filled.
    `capability_of_code`: code -> capability id if the code maps to EXACTLY
      one capability, None if it maps to several. Unmapped codes are absent.
    `existing_keywords`: capability id -> folded keywords it already has.
    `skip`: (capability_id, keyword) pairs already decided (rejected) or
      already suggested — never proposed again.
    """
    total_docs = len(docs)
    global_df: Counter = Counter()
    token_df: Counter = Counter()
    for d in docs:
        global_df.update(d.ngrams)
        token_df.update(set(_TOKEN_RE.findall(fold(clean_title(d.title)))))
    vocab_by_first = defaultdict(list)
    for tok in token_df:
        vocab_by_first[tok[:3]].append(tok)

    def _has_truncated_token(gram: str) -> bool:
        """SAM.gov titles are cut at a fixed width ("BLADE SET,FAN,AIRCR"), leaving stubs like "aircr" that must not become keywords."""
        for tok in gram.split():
            if any(o != tok and o.startswith(tok) and token_df[o] >= token_df[tok]
                   for o in vocab_by_first.get(tok[:3], ())):
                return True
        return False

    by_cap: dict[str, list[Doc]] = defaultdict(list)
    for d in docs:
        cap = capability_of_code.get(d.code)
        if cap:
            by_cap[cap].append(d)

    suggestions: list[KeywordSuggestion] = []
    for cap_id, cap_docs in by_cap.items():
        n_cap = len(cap_docs)
        cap_df: Counter = Counter()
        buyers: dict[str, set] = defaultdict(set)
        sources: dict[str, set] = defaultdict(set)
        examples: dict[str, list] = defaultdict(list)
        for d in cap_docs:
            cap_df.update(d.ngrams)
            for g in d.ngrams:
                buyers[g].add(d.buyer)
                sources[g].add(d.source)
                if len(examples[g]) < EXAMPLES_PER_SUGGESTION:
                    examples[g].append(d.title[:140])

        have = existing_keywords.get(cap_id, set())
        scored = []
        for g, df_c in cap_df.items():
            if df_c < PENDING_MIN_SUPPORT or g in have or (cap_id, g) in skip:
                continue
            if _has_truncated_token(g):
                continue
            n_buyers = len(buyers[g])
            if n_buyers < PENDING_MIN_BUYERS:
                continue
            precision = df_c / global_df[g]
            rate_in = df_c / n_cap
            rate_out = (global_df[g] - df_c + 1) / (total_docs - n_cap + 2)
            lift = rate_in / rate_out
            if precision < PENDING_MIN_PRECISION or lift < PENDING_MIN_LIFT:
                continue
            is_auto = (
                df_c >= AUTO_MIN_SUPPORT and precision >= AUTO_MIN_PRECISION
                and lift >= AUTO_MIN_LIFT and n_buyers >= AUTO_MIN_BUYERS
                and (len(sources[g]) >= 2 or n_buyers >= AUTO_MIN_BUYERS_SINGLE_SOURCE)
            )
            scored.append((df_c * precision, KeywordSuggestion(
                cap_id, g, df_c, n_buyers, round(precision * 100, 1), round(lift, 1),
                "auto" if is_auto else "pending", examples[g],
            )))

        # Ties go to the longer phrase, then alphabetical, so a run is deterministic.
        scored.sort(key=lambda t: (-t[0], -len(t[1].keyword), t[1].keyword))
        # Prefer the longer phrase when it carries (almost) the same support:
        # "ballistic vest" over the bare "ballistic".
        chosen: list[KeywordSuggestion] = []
        for _, s in scored:
            redundant = any(
                _is_subphrase(s.keyword, c.keyword) and c.support >= 0.8 * s.support
                for c in chosen
            ) or any(
                _is_subphrase(c.keyword, s.keyword) and s.support >= 0.8 * c.support
                for c in chosen
            )
            if redundant:
                continue
            chosen.append(s)
        suggestions.extend(chosen)
    return suggestions


@dataclass
class CodeSuggestion:
    capability_id: str
    code: str
    source: str
    docs: int
    hits: int
    buyers: int
    hit_rate: float
    examples: list


def mine_codes(
    docs: list[Doc],
    mapped_codes: set[str],
    curated_keyword_rows: list,
    skip: set[tuple[str, str]],
) -> list[CodeSuggestion]:
    """
    For every code with NO mapping at all, score its tenders' titles against
    the CURATED keywords. If one capability keeps winning, suggest the code.
    `curated_keyword_rows`: (capability_id, code, label, sector, keyword, weight).
    """
    by_code: dict[str, list[Doc]] = defaultdict(list)
    for d in docs:
        if d.code and d.code not in mapped_codes:
            by_code[d.code].append(d)

    out: list[CodeSuggestion] = []
    for code, code_docs in by_code.items():
        if len(code_docs) < CODE_MIN_DOCS:
            continue
        hits: Counter = Counter()
        hit_docs: dict[str, list[Doc]] = defaultdict(list)
        for d in code_docs:
            cands = score_text(d.title, curated_keyword_rows)
            if cands and cands[0]["score"] >= MEDIUM_CONFIDENCE_THRESHOLD:
                cap = cands[0]["capability_id"]
                hits[cap] += 1
                hit_docs[cap].append(d)
        if not hits:
            continue
        cap_id, n_hits = hits.most_common(1)[0]
        rate = n_hits / len(code_docs)
        n_buyers = len({d.buyer for d in hit_docs[cap_id]})
        if (
            n_hits < CODE_MIN_HITS or rate < CODE_MIN_HIT_RATE
            or n_buyers < CODE_MIN_BUYERS or (cap_id, code) in skip
        ):
            continue
        out.append(CodeSuggestion(
            cap_id, code, code_docs[0].source, len(code_docs), n_hits, n_buyers,
            round(rate * 100, 1), [d.title[:140] for d in hit_docs[cap_id][:EXAMPLES_PER_SUGGESTION]],
        ))
    return out


def mapping_table_for_source(source_name: str | None) -> tuple[str, str]:
    """(table, column) a code seen on `source_name` belongs in."""
    name = source_name or ""
    if any(name.startswith(p) for p in _NAICS_SOURCES):
        return "taxonomy_naics_mapping", "naics_code"
    if any(name.startswith(p) for p in _CPV_SOURCES):
        return "taxonomy_cpv_mapping", "cpv_code"
    return "taxonomy_unspsc_mapping", "unspsc_prefix"


# ---------------------------------------------------------------------------
# DB orchestration
# ---------------------------------------------------------------------------
async def _load_state(session: AsyncSession):
    caps = (await session.execute(text("select id, code, label, sector from capability_taxonomy"))).all()
    kw_rows = (await session.execute(text(
        "select capability_id, keyword, origin, weight from capability_taxonomy_keywords"
    ))).all()
    code_rows = (await session.execute(text("""
        select capability_id, naics_code as code from taxonomy_naics_mapping
        union all select capability_id, cpv_code from taxonomy_cpv_mapping
    """))).all()
    unspsc = [r.unspsc_prefix for r in (await session.execute(text(
        "select distinct unspsc_prefix from taxonomy_unspsc_mapping"
    )))]
    prog = (await session.execute(text("""
        select p.name, p.platform, p.naics_code, p.organization_id, coalesce(s.name, '') as source_name
        from programmes p left join sources s on s.id = p.source_id
        where p.name is not null
    """))).all()
    decided = (await session.execute(text(
        "select kind, capability_id, value from taxonomy_learning_suggestions where status in ('rejected', 'approved', 'auto_added', 'pending')"
    ))).all()
    return caps, kw_rows, code_rows, unspsc, prog, decided


async def run_taxonomy_learning(session: AsyncSession, triggered_by: str = "scheduler", dry_run: bool = False) -> dict:
    job_id = None
    if not dry_run:
        job_id = (await session.execute(text(
            "insert into backup_jobs (status, job_type) values ('running', 'taxonomy_learning') returning id"
        ))).scalar_one()
        await session.commit()

    try:
        caps, kw_rows, code_rows, unspsc, prog, decided = await _load_state(session)

        existing_keywords: dict[str, set[str]] = defaultdict(set)
        learned_count: Counter = Counter()
        curated_rows = []
        cap_meta = {str(c.id): c for c in caps}
        for k in kw_rows:
            cid = str(k.capability_id)
            existing_keywords[cid].add(fold(k.keyword))
            if k.origin == "learned":
                learned_count[cid] += 1
            else:
                c = cap_meta.get(cid)
                if c:
                    curated_rows.append((cid, c.code, c.label, c.sector, k.keyword, k.weight))

        caps_of_code: dict[str, set[str]] = defaultdict(set)
        for r in code_rows:
            caps_of_code[r.code].add(str(r.capability_id))
        mapped_codes = set(caps_of_code)
        capability_of_code = {c: (next(iter(v)) if len(v) == 1 else None) for c, v in caps_of_code.items()}

        def is_mapped(code: str) -> bool:
            return code in mapped_codes or any(code.startswith(p) for p in unspsc)

        docs = dedupe_docs([
            Doc(
                title=p.name,
                buyer=str(p.organization_id) if p.organization_id else p.source_name,
                code=p.naics_code or "",
                source=p.source_name,
            ) for p in prog
        ])
        for d in docs:
            d.ngrams = title_ngrams(d.title)
        # UNSPSC-prefix-mapped codes are mapped but not capability-attributable
        # (prefix tables are not part of capability_of_code), so they are
        # simply not used as keyword evidence; they ARE excluded from code
        # suggestions below.
        skip = {(str(r.capability_id), r.value) for r in decided}

        kw_sugg = mine_keywords(docs, capability_of_code, existing_keywords, skip)
        unmapped_docs = [d for d in docs if not is_mapped(d.code)]
        code_sugg = mine_codes(unmapped_docs, set(), curated_rows, skip)

        added = pending_kw = pending_codes = 0
        per_cap_new: Counter = Counter()
        if not dry_run:
            for s in sorted(kw_sugg, key=lambda x: (x.tier != "auto", -x.support)):
                if per_cap_new[s.capability_id] >= MAX_NEW_KEYWORDS_PER_CAPABILITY_PER_RUN:
                    continue
                keyword_id = None
                status = "pending"
                if s.tier == "auto" and learned_count[s.capability_id] < MAX_LEARNED_KEYWORDS_PER_CAPABILITY:
                    keyword_id = (await session.execute(text("""
                        insert into capability_taxonomy_keywords (capability_id, keyword, weight, origin)
                        values (:cid, :kw, :w, 'learned') returning id
                    """), {"cid": s.capability_id, "kw": s.keyword, "w": LEARNED_KEYWORD_WEIGHT})).scalar_one()
                    status = "auto_added"
                    learned_count[s.capability_id] += 1
                    added += 1
                else:
                    pending_kw += 1
                per_cap_new[s.capability_id] += 1
                await session.execute(text("""
                    insert into taxonomy_learning_suggestions
                        (kind, capability_id, value, status, support_count, distinct_buyers,
                         precision_pct, lift, examples, keyword_id)
                    values ('keyword', :cid, :v, :st, :sup, :b, :pr, :lift, CAST(:ex AS jsonb), :kid)
                    on conflict (kind, capability_id, value) do nothing
                """), {
                    "cid": s.capability_id, "v": s.keyword, "st": status, "sup": s.support, "b": s.buyers,
                    "pr": s.precision, "lift": s.lift, "ex": json.dumps(s.examples), "kid": keyword_id,
                })
            for c in code_sugg:
                pending_codes += 1
                await session.execute(text("""
                    insert into taxonomy_learning_suggestions
                        (kind, capability_id, value, source_name, status, support_count, distinct_buyers,
                         precision_pct, lift, examples)
                    values ('code', :cid, :v, :src, 'pending', :sup, :b, :pr, NULL, CAST(:ex AS jsonb))
                    on conflict (kind, capability_id, value) do nothing
                """), {
                    "cid": c.capability_id, "v": c.code, "src": c.source, "sup": c.hits, "b": c.buyers,
                    "pr": c.hit_rate, "ex": json.dumps(c.examples),
                })

        details = {
            "programmes_considered": len(docs),
            "keyword_candidates": len(kw_sugg),
            "keywords_auto_added": added,
            "keywords_pending_review": pending_kw,
            "code_suggestions_pending_review": pending_codes if not dry_run else len(code_sugg),
            "dry_run": dry_run,
        }
        if dry_run:
            details["preview"] = {
                "keywords": [
                    {"capability": cap_meta[s.capability_id].code, "keyword": s.keyword, "tier": s.tier,
                     "support": s.support, "buyers": s.buyers, "precision_pct": s.precision, "lift": s.lift,
                     "examples": s.examples}
                    for s in sorted(kw_sugg, key=lambda x: (x.tier != "auto", -x.support))[:60]
                ],
                "codes": [
                    {"capability": cap_meta[c.capability_id].code, "code": c.code, "source": c.source,
                     "tenders_with_code": c.docs, "hits": c.hits, "hit_rate_pct": c.hit_rate, "examples": c.examples}
                    for c in sorted(code_sugg, key=lambda x: -x.hits)[:40]
                ],
            }
            return details

        await session.execute(text("""
            update backup_jobs set status = 'succeeded', finished_at = now(),
                details = CAST(:d AS jsonb) where id = :id
        """), {"id": job_id, "d": json.dumps(details)})
        await session.commit()
        return {"job_id": str(job_id), "status": "succeeded", "triggered_by": triggered_by, **details}
    except Exception as e:
        await session.rollback()
        if job_id is not None:
            await session.execute(text(
                "update backup_jobs set status = 'failed', finished_at = now(), error = :err where id = :id"
            ), {"id": job_id, "err": str(e)[:1000]})
            await session.commit()
        raise


async def learning_health(session: AsyncSession) -> dict:
    from app.backup import job_health
    return await job_health(session, "taxonomy_learning", threshold_hours=24 * 9)
