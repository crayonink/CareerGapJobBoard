"""Filters -> SQL for /browse.

One function builds the WHERE clause; the count and the page both reuse it, so
a filter can never apply to the results but not the total.

Multi-valued filters are EXISTS subqueries rather than joins. A join against
candidate_tag multiplies rows, which needs DISTINCT, which then fights the
ORDER BY and corrupts the count. EXISTS keeps one row per candidate throughout.
"""

from __future__ import annotations

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from .enums import GAP_BUCKET_MONTHS, ProfileStatus
from .models import Candidate, CandidateTag, Tag
from .schemas import BrowseFilters

_LIKE_ESCAPE = "\\"


def _like_term(term: str) -> str:
    """Escape LIKE wildcards so searching for "C_" means C_, not C-anything."""
    escaped = (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def build_conditions(f: BrowseFilters) -> list:
    """Everything in the WHERE clause except the status gate.

    Nothing in here touches gap_reason or expected_ctc. That is the point.
    """
    conds = []

    # --- skills / tags ---------------------------------------------------
    if f.tags:
        tag_ids = select(Tag.id).where(Tag.slug.in_(f.tags))
        conds.append(
            exists().where(
                and_(
                    CandidateTag.candidate_id == Candidate.id,
                    CandidateTag.tag_id.in_(tag_ids),
                )
            )
        )

    # --- role sought -------------------------------------------------------
    if f.role_sought:
        conds.append(Candidate.role_sought_slug.in_(f.role_sought))

    # --- location -----------------------------------------------------------
    if f.city:
        # Substring match: "Bangalore" should find "Bangalore North" without
        # anyone having to standardise Indian city names first.
        conds.append(Candidate.city.ilike(_like_term(f.city), escape=_LIKE_ESCAPE))
    if f.open_to_remote is not None:
        conds.append(Candidate.open_to_remote.is_(f.open_to_remote))

    # --- career before the gap ------------------------------------------------
    if f.min_years is not None:
        conds.append(Candidate.years_prior_experience >= f.min_years)
    if f.max_years is not None:
        conds.append(Candidate.years_prior_experience <= f.max_years)

    # --- gap length -------------------------------------------------------------
    if f.gap_bucket:
        ranges = []
        for bucket in f.gap_bucket:
            low, high = GAP_BUCKET_MONTHS[bucket]
            clause = Candidate.gap_months >= low
            if high is not None:
                clause = and_(clause, Candidate.gap_months < high)
            ranges.append(clause)
        conds.append(or_(*ranges))

    # --- availability -------------------------------------------------------------
    if f.max_notice_days is not None:
        conds.append(Candidate.notice_period_days <= f.max_notice_days)
    if f.open_to_trial is not None:
        conds.append(Candidate.open_to_trial.is_(f.open_to_trial))

    return conds


def base_query(f: BrowseFilters) -> Select:
    """Live-only, filtered, unsorted, unpaged."""
    return select(Candidate).where(
        Candidate.status == ProfileStatus.LIVE, *build_conditions(f)
    )


def apply_sort(stmt: Select) -> Select:
    """The only order there is: recently updated.

    No relevance ranking and no promoted slots, so there is nothing to game.
    `id` breaks ties - without it two profiles saved in the same second can
    swap between page 1 and page 2 while someone is paging through.
    """
    return stmt.order_by(Candidate.updated_at.desc(), Candidate.id.desc())


def count_matches(session: Session, f: BrowseFilters) -> int:
    stmt = (
        select(func.count())
        .select_from(Candidate)
        .where(Candidate.status == ProfileStatus.LIVE, *build_conditions(f))
    )
    return session.scalar(stmt) or 0


def fetch_page(session: Session, f: BrowseFilters) -> list[Candidate]:
    stmt = apply_sort(base_query(f))
    stmt = stmt.offset((f.page - 1) * f.page_size).limit(f.page_size)
    return list(session.scalars(stmt).unique())
