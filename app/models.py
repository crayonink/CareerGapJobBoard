"""ORM models.

    Candidate 1--* ProofLink
    Candidate *--* Tag        (via CandidateTag)
    Employer  1--* ContactReveal *--1 Candidate

Two rules drive the design:

1. Anything /browse filters on is a column or a joined row, never prose.
2. Identity and contact fields live on the Candidate row but are never
   serialised by the public schemas - see schemas.CandidateCard vs
   schemas.CandidateContact.

Two things here are not in the spec's field tables and are marked ADDED:
`gap_months` (derived; the gap-length buckets need something indexable) and
the tag tables (the filter list requires skills/tags but no table was given).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .enums import GapReason, ProfileStatus, ProofKind


def _enum(py_enum):
    """Store enums as VARCHAR - SQLite has no native ENUM type, and enum
    migrations are the single most annoying thing to do later."""
    return Enum(py_enum, native_enum=False, validate_strings=True, length=32)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Candidate(Base):
    __tablename__ = "candidate"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)

    status: Mapped[ProfileStatus] = mapped_column(
        _enum(ProfileStatus), default=ProfileStatus.DRAFT, index=True
    )
    # Set by /admin on "request changes"; shown to the candidate, never public.
    review_note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, index=True
    )

    # --- identity: private until a reveal ---------------------------------
    # `full_name` is stored whole and rendered as "Rupa K." publicly; see
    # derive.public_name. The full string never leaves CandidateContact.
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(32))

    # --- public profile -----------------------------------------------------
    headline: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(80), index=True)
    open_to_remote: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    open_to_relocate: Mapped[bool] = mapped_column(Boolean, default=False)

    # Candidate types what they want; review normalises it into role_sought_slug,
    # which is the filterable half. Keeping both means the normalisation is
    # reversible and their own words still show on the profile.
    role_sought: Mapped[str] = mapped_column(String(120))
    role_sought_slug: Mapped[str | None] = mapped_column(String(60), index=True)

    years_prior_experience: Mapped[int] = mapped_column(Integer, default=0, index=True)
    summary: Mapped[str | None] = mapped_column(Text)

    # Skills exactly as the candidate typed them. Kept because review is where
    # they become canonical tags, and the reviewer needs to see the original to
    # do that - and to notice when a new spelling deserves a tag_alias row.
    skills_raw: Mapped[str | None] = mapped_column(String(300))

    # --- the gap -------------------------------------------------------------
    gap_start: Mapped[dt.date] = mapped_column(Date)
    gap_end: Mapped[dt.date | None] = mapped_column(Date)  # NULL => still on break
    # ADDED (not in spec): derived from the dates by derive.refresh_gap_months().
    # Stored and indexed because the bucket filter is a range scan on it.
    gap_months: Mapped[int] = mapped_column(Integer, default=0, index=True)

    # Display-only. Deliberately NOT indexed - an index here is an invitation
    # to filter on it, and filtering on it is the thing we refuse to build.
    gap_reason: Mapped[GapReason | None] = mapped_column(_enum(GapReason))
    gap_activity: Mapped[str | None] = mapped_column(Text)

    # --- availability ---------------------------------------------------------
    # Forward-facing ask, annual INR. Public on the profile, never a filter or
    # sort key: a cheapness filter turns voluntary disclosure into a race.
    expected_ctc: Mapped[int | None] = mapped_column(Integer)
    notice_period_days: Mapped[int] = mapped_column(Integer, default=0, index=True)
    open_to_trial: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    flexibility_note: Mapped[str | None] = mapped_column(String(200))

    # --- attachments ------------------------------------------------------------
    # Path outside the web root. Served only through an authenticated endpoint
    # that checks for a ContactReveal row. Deleted with the profile.
    resume_path: Mapped[str | None] = mapped_column(String(255))

    proof_links: Mapped[list[ProofLink]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="selectin"
    )
    tags: Mapped[list[CandidateTag]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("gap_end IS NULL OR gap_end >= gap_start", name="ck_gap_order"),
        CheckConstraint("notice_period_days >= 0", name="ck_notice_nonneg"),
        CheckConstraint("years_prior_experience >= 0", name="ck_years_nonneg"),
        CheckConstraint("expected_ctc IS NULL OR expected_ctc >= 0", name="ck_ctc_nonneg"),
        # The index the default sort leans on: live profiles, most recently updated.
        Index("ix_candidate_live", "status", "updated_at"),
    )

    @property
    def gap_ongoing(self) -> bool:
        return self.gap_end is None

    def __repr__(self) -> str:
        return f"<Candidate {self.slug} {self.status.value}>"


class Tag(Base):
    """ADDED (not in spec): canonical skill/tag.

    Aliases collapse into `slug` during review so that "React.js", "ReactJS"
    and "react" are one filter value rather than three.
    """

    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(60))

    def __repr__(self) -> str:
        return f"<Tag {self.slug}>"


class TagAlias(Base):
    """Write-time normalisation. Every new spelling you see while reviewing
    costs one row here and never comes back."""

    __tablename__ = "tag_alias"

    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id", ondelete="CASCADE"))


class CandidateTag(Base):
    __tablename__ = "candidate_tag"

    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True
    )

    candidate: Mapped[Candidate] = relationship(back_populates="tags")
    tag: Mapped[Tag] = relationship(lazy="joined")

    # Reversed order from the PK so "who has tag X" is also an index seek.
    __table_args__ = (Index("ix_candidate_tag_lookup", "tag_id", "candidate_id"),)


class ProofLink(Base):
    """A link with a claim attached: the repo, the course, the freelance client.

    A gap with three of these reads completely differently from an empty one,
    which is why the submit form requires at least one.
    """

    __tablename__ = "proof_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(140))
    url: Mapped[str] = mapped_column(String(500))
    kind: Mapped[ProofKind] = mapped_column(_enum(ProofKind), default=ProofKind.OTHER)

    candidate: Mapped[Candidate] = relationship(back_populates="proof_links")


class Employer(Base):
    __tablename__ = "employer"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(140))
    contact_name: Mapped[str] = mapped_column(String(120))
    # Free-domain addresses are rejected at signup - see policy.is_work_email.
    work_email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    email_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    reveals: Mapped[list[ContactReveal]] = relationship(
        back_populates="employer", cascade="all, delete-orphan"
    )

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None

    def __repr__(self) -> str:
        return f"<Employer {self.company_name}>"


class ContactReveal(Base):
    """One row per (employer, candidate) unlock.

    This table is the only real metric. `SELECT count(*) FROM contact_reveal
    WHERE revealed_at > date('now', '-7 days')` is the number that says whether
    the board is working.
    """

    __tablename__ = "contact_reveal"

    id: Mapped[int] = mapped_column(primary_key=True)
    employer_id: Mapped[int] = mapped_column(
        ForeignKey("employer.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), index=True
    )
    revealed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    employer: Mapped[Employer] = relationship(back_populates="reveals")
    candidate: Mapped[Candidate] = relationship()

    __table_args__ = (
        # Re-opening a profile you already unlocked is free and does not count
        # against the daily limit.
        UniqueConstraint("employer_id", "candidate_id", name="uq_reveal_once"),
        Index("ix_reveal_quota", "employer_id", "revealed_at"),
    )
