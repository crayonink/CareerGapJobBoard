"""ORM -> wire.

The only place a Candidate row becomes JSON. Keeping every serialisation in one
file is what makes "does anything leak the phone number?" a question you can
answer by reading eighty lines.
"""

from __future__ import annotations

import datetime as dt

from .derive import public_name
from .gap import bucket_for, format_length
from .models import Candidate
from .schemas import (
    CandidateCard,
    CandidateContact,
    CandidatePublic,
    GapOut,
    ProofLinkOut,
    TagOut,
)


def _month(value: dt.date | None) -> str | None:
    """Month precision. The exact day someone stopped working is nobody's
    business and nobody remembers it anyway."""
    return None if value is None else f"{value.year:04d}-{value.month:02d}"


def gap_out(candidate: Candidate) -> GapOut:
    return GapOut(
        start=_month(candidate.gap_start),
        end=_month(candidate.gap_end),
        months=candidate.gap_months,
        length_label=format_length(candidate.gap_months),
        bucket=bucket_for(candidate.gap_months),
        ongoing=candidate.gap_ongoing,
        reason=candidate.gap_reason,
    )


def _card_fields(candidate: Candidate) -> dict:
    return {
        "slug": candidate.slug,
        "public_name": public_name(candidate.full_name),
        "headline": candidate.headline,
        "city": candidate.city,
        "open_to_remote": candidate.open_to_remote,
        "role_sought": candidate.role_sought,
        "years_prior_experience": candidate.years_prior_experience,
        "gap": gap_out(candidate),
        "tags": [TagOut(slug=ct.tag.slug, label=ct.tag.label) for ct in candidate.tags],
        "proof_link_count": len(candidate.proof_links),
        "notice_period_days": candidate.notice_period_days,
        "open_to_trial": candidate.open_to_trial,
        "updated_at": candidate.updated_at,
    }


def to_card(candidate: Candidate) -> CandidateCard:
    return CandidateCard(**_card_fields(candidate))


def to_public(candidate: Candidate) -> CandidatePublic:
    return CandidatePublic(
        **_card_fields(candidate),
        summary=candidate.summary,
        gap_activity=candidate.gap_activity,
        proof_links=[
            ProofLinkOut(label=p.label, url=p.url, kind=p.kind) for p in candidate.proof_links
        ],
        open_to_relocate=candidate.open_to_relocate,
        expected_ctc=candidate.expected_ctc,
        flexibility_note=candidate.flexibility_note,
        # The flag is public so an employer knows a resume exists; the path is
        # not, and the file is served only through the authenticated endpoint.
        has_resume=candidate.resume_path is not None,
    )


def to_contact(candidate: Candidate, revealed_at: dt.datetime) -> CandidateContact:
    """Only ever called after a ContactReveal row exists for this pair."""
    return CandidateContact(
        slug=candidate.slug,
        full_name=candidate.full_name,
        email=candidate.email,
        phone=candidate.phone,
        resume_url=(
            f"/employer/resume/{candidate.slug}" if candidate.resume_path else None
        ),
        revealed_at=revealed_at,
    )
