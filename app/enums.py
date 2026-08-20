"""Controlled vocabularies.

Closed sets, because a directory where "Career break" and "career-break" are
different values cannot be filtered. Adding a value is cheap; letting people
type their own is not.

Note which of these are filter facets and which are display-only -
`GapReason` is display-only on purpose. See schemas.BrowseFilters.
"""

from enum import Enum


class ProfileStatus(str, Enum):
    """Curation gate. Only LIVE profiles appear on /browse."""

    DRAFT = "draft"                    # candidate editing, or sent back by review
    PENDING_REVIEW = "pending_review"  # submitted, waiting in the /admin queue
    LIVE = "live"                      # published and browsable
    PAUSED = "paused"                  # found a job / asked to hide


class GapReason(str, Enum):
    """Display-only. Never a filter, never a sort key.

    Health and caregiving are sensitive personal data under the DPDP Act, and
    a reason filter is a discrimination tool. The column is nullable because
    disclosing is the candidate's choice, and PREFER_NOT_TO_SAY is a real
    answer rather than an absence.
    """

    CAREGIVING = "caregiving"
    HEALTH = "health"
    LAYOFF = "layoff"
    STUDY = "study"
    RELOCATION = "relocation"
    BUILDING_SOMETHING = "building_something"
    OTHER = "other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class ProofKind(str, Enum):
    """Kinds of proof link. This is the whole thesis of the site."""

    CODE = "code"
    WRITING = "writing"
    FREELANCE = "freelance"
    COURSE = "course"
    BUSINESS = "business"
    OTHER = "other"


class GapBucket(str, Enum):
    """Gap length as an employer thinks about it.

    Buckets rather than a month slider: a slider invites "no more than 14
    months", which is a precision nobody actually has a policy about, and it
    makes short gaps look like the only acceptable ones.
    """

    UNDER_1Y = "under_1y"
    ONE_TO_3Y = "1_3y"
    THREE_TO_5Y = "3_5y"
    OVER_5Y = "5y_plus"


#: Inclusive lower bound, exclusive upper bound, in months. None = open-ended.
GAP_BUCKET_MONTHS: dict[GapBucket, tuple[int, int | None]] = {
    GapBucket.UNDER_1Y: (0, 12),
    GapBucket.ONE_TO_3Y: (12, 36),
    GapBucket.THREE_TO_5Y: (36, 60),
    GapBucket.OVER_5Y: (60, None),
}

GAP_BUCKET_LABELS: dict[GapBucket, str] = {
    GapBucket.UNDER_1Y: "Under 1 year",
    GapBucket.ONE_TO_3Y: "1-3 years",
    GapBucket.THREE_TO_5Y: "3-5 years",
    GapBucket.OVER_5Y: "5+ years",
}
