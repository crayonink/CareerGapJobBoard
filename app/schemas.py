"""Wire shapes: the /browse filter spec, and the three tiers of serialisation.

The tiers exist so that leaking contact details takes deleting a line of code
rather than forgetting to add one:

    CandidateCard    - /browse results. No identity, no contact.
    CandidatePublic  - /p/{slug}. Everything except how to reach them.
    CandidateContact - the reveal endpoint only, to a verified employer only,
                       and only once a ContactReveal row has been written.

None of the three carries `email`, `phone`, `resume_path` or `full_name` except
the last one, and `resume_path` never goes over the wire at all.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import GapBucket, GapReason, ProofKind

# ---------------------------------------------------------------------------
# Filter spec
# ---------------------------------------------------------------------------


class BrowseFilters(BaseModel):
    """Every facet /browse can slice the directory by. This list is exhaustive
    by design.

    Three fields are deliberately absent, and each absence is a decision:

    `gap_reason`  - health and caregiving are sensitive personal data under the
                    DPDP Act, and a reason filter is a discrimination tool.
                    Shown on the profile when the candidate opted in; never a
                    query parameter.
    `expected_ctc`- visible on the profile, not a sorting axis. A cheapness
                    filter turns voluntary disclosure into a race to the bottom.
    `sort`        - one order, recently updated. No relevance ranking and no
                    promoted slots, so there is nothing for anyone to game and
                    no reason for a candidate to re-save a profile daily to
                    stay at the top.
    """

    model_config = ConfigDict(extra="forbid")

    # --- skills / tags ---------------------------------------------------
    tags: list[str] = Field(
        default_factory=list,
        description="Canonical tag slugs. A candidate matching ANY of them matches.",
    )

    # --- what they want next ----------------------------------------------
    role_sought: list[str] = Field(
        default_factory=list,
        description="Normalised role slugs, as set during review.",
    )

    # --- location -----------------------------------------------------------
    city: str | None = Field(default=None, max_length=80)
    open_to_remote: bool | None = Field(
        default=None, description="True narrows to candidates open to remote work."
    )

    # --- career before the gap ------------------------------------------------
    min_years: int | None = Field(default=None, ge=0, le=50)
    max_years: int | None = Field(default=None, ge=0, le=50)

    # --- gap length ------------------------------------------------------------
    gap_bucket: list[GapBucket] = Field(
        default_factory=list,
        description="Length only, never reason. Multiple buckets OR together.",
    )

    # --- availability ------------------------------------------------------------
    max_notice_days: int | None = Field(default=None, ge=0, le=365)
    open_to_trial: bool | None = None

    # --- paging --------------------------------------------------------------------
    page: int = Field(default=1, ge=1, le=200)
    page_size: int = Field(default=20, ge=1, le=50)

    @field_validator("tags", "role_sought", mode="after")
    @classmethod
    def _normalise_slugs(cls, v: list[str]) -> list[str]:
        return [s.strip().lower() for s in v if s.strip()]

    @field_validator("city", mode="after")
    @classmethod
    def _clean_city(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    def model_post_init(self, _context) -> None:
        # Swapping an inverted range beats returning nothing and letting the
        # employer conclude the board is empty.
        if self.min_years is not None and self.max_years is not None:
            if self.min_years > self.max_years:
                self.min_years, self.max_years = self.max_years, self.min_years


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    label: str


class ProofLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    url: str
    kind: ProofKind


class GapOut(BaseModel):
    """The gap rendered as a fact rather than a hole.

    `reason` is present here and only here on the public side - display, never
    filter. It is None whenever the candidate did not disclose one.
    """

    start: str                 # "2021-04" - month precision is all anyone remembers
    end: str | None            # None => still on the break
    months: int
    length_label: str          # "2 yr 4 mo"
    bucket: GapBucket
    ongoing: bool
    reason: GapReason | None


class CandidateCard(BaseModel):
    """One /browse result. Enough to decide whether to open the profile."""

    slug: str
    public_name: str           # "Rupa K." - never the full name
    headline: str
    city: str
    open_to_remote: bool
    role_sought: str
    years_prior_experience: int
    gap: GapOut
    tags: list[TagOut]
    proof_link_count: int      # the number an employer actually scans for
    notice_period_days: int
    open_to_trial: bool
    updated_at: dt.datetime


class CandidatePublic(CandidateCard):
    """/p/{slug}. Everything except how to reach them."""

    summary: str | None
    gap_activity: str | None
    proof_links: list[ProofLinkOut]
    open_to_relocate: bool
    expected_ctc: int | None          # annual INR, forward-facing ask
    flexibility_note: str | None
    has_resume: bool                  # the flag is public; the file is not


class CandidateContact(BaseModel):
    """Released to one verified employer, and logged when it is."""

    slug: str
    full_name: str
    email: str
    phone: str | None
    resume_url: str | None            # authenticated endpoint, not a file path
    revealed_at: dt.datetime


class BrowseResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[CandidateCard]
