"""Derived values.

`slug`, `gap_months` and the public display name are computed, never entered.
`gap_months` is a stored column because bucket filtering has to be fast, which
means something must keep it honest - that is `nightly_refresh` plus every
write path calling `refresh_gap_months`.
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from .gap import months_between
from .models import Candidate, Tag, TagAlias


def slugify(value: str, max_length: int = 60) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:max_length].strip("-")


def public_name(full_name: str) -> str:
    """"Rupa Kulkarni" -> "Rupa K."

    First name plus initial is the public identity. It is enough for an
    employer to talk about the profile without being enough to look the person
    up on LinkedIn and route around the reveal gate.
    """
    parts = [p for p in full_name.strip().split() if p]
    if not parts:
        return "Candidate"
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0].upper()}."


def make_slug(session: Session, full_name: str) -> str:
    """Public URL key, e.g. `rupa-k-9f3a`.

    The random suffix is not decoration: without it the slug is a guessable
    function of the name, and /p/<name> becomes an enumeration oracle for
    "is this person job-hunting".
    """
    base = slugify(public_name(full_name).replace(".", "")) or "candidate"
    for _ in range(10):
        slug = f"{base}-{secrets.token_hex(2)}"
        if session.scalar(select(Candidate.id).where(Candidate.slug == slug)) is None:
            return slug
    raise RuntimeError(f"could not allocate a unique slug for {base!r}")


def refresh_gap_months(candidate: Candidate, today: dt.date | None = None) -> None:
    """Recompute gap length from the dates.

    An open-ended gap grows by one every month, which is exactly why this
    cannot be a number the candidate typed in once.
    """
    candidate.gap_months = months_between(candidate.gap_start, candidate.gap_end, today)


def nightly_refresh(session: Session, today: dt.date | None = None) -> int:
    """Re-derive gap length for every ongoing gap. A few hundred rows, run daily.

    Without it, a profile reviewed in January still advertises an 18-month gap
    in June - the most visible way for the board to look stale.
    """
    rows = session.scalars(
        select(Candidate).where(Candidate.gap_end.is_(None))
    ).all()
    for candidate in rows:
        refresh_gap_months(candidate, today)
    session.commit()
    return len(rows)


def resolve_tag(session: Session, raw: str) -> Tag | None:
    """Map a candidate's typed skill onto a canonical Tag, via aliases.

    Returns None when the spelling is unknown - that is a prompt for you during
    review to either add an alias row or create the tag, not a reason to invent
    a new tag automatically. Auto-creating is how you end up with `reactjs`,
    `react-js` and `react` as three separate filters.
    """
    slug = slugify(raw)
    if not slug:
        return None
    tag = session.scalar(select(Tag).where(Tag.slug == slug))
    if tag is not None:
        return tag
    alias = session.scalar(select(TagAlias).where(TagAlias.alias == slug))
    if alias is not None:
        return session.get(Tag, alias.tag_id)
    return None
