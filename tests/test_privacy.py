"""The guardrails, as executable assertions.

Every test here fails loudly the day someone adds a "convenient" filter or
widens a serialiser. That is the entire point of the file.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.enums import GapBucket, GapReason
from app.present import to_card, to_contact, to_public
from app.schemas import BrowseFilters
from app.search import base_query

from .conftest import make_candidate


# --- filters that must not exist -------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        {"gap_reason": ["caregiving"]},
        {"gap_reason": "health"},
        {"expected_ctc": 1000000},
        {"max_ctc": 1000000},
        {"min_ctc": 500000},
        {"flexibility_note": "part time"},
        {"sort": "expected_ctc"},          # no ranking axis at all
        {"full_name": "Rupa"},             # browsing is the point, not lookup
        {"email": "x@y.com"},
    ],
)
def test_sensitive_filters_are_rejected(forbidden):
    with pytest.raises(ValidationError):
        BrowseFilters(**forbidden)


def test_no_query_touches_gap_reason_or_ctc(session, tags):
    """Belt and braces: compile a query with every filter set at once and read
    the SQL. If either column ever appears in a WHERE clause, this catches it."""
    everything = BrowseFilters(
        tags=["python"],
        role_sought=["backend-engineer"],
        city="Bangalore",
        open_to_remote=True,
        min_years=1,
        max_years=20,
        gap_bucket=[GapBucket.ONE_TO_3Y, GapBucket.OVER_5Y],
        max_notice_days=30,
        open_to_trial=True,
    )
    sql = str(base_query(everything).compile(compile_kwargs={"literal_binds": True}))
    where = sql.split("WHERE", 1)[1]
    assert "gap_reason" not in where
    assert "expected_ctc" not in where
    assert "flexibility_note" not in where


# --- serialisers that must not leak -----------------------------------------

SECRET_FIELDS = ("email", "phone", "full_name", "resume_path")


def test_card_carries_no_identity(session, tags):
    candidate = make_candidate(session, tags, full_name="Rupa Kulkarni")
    payload = to_card(candidate).model_dump()

    for field in SECRET_FIELDS:
        assert field not in payload
    blob = str(payload)
    assert candidate.email not in blob
    assert candidate.phone not in blob
    assert "Kulkarni" not in blob


def test_public_profile_carries_no_contact(session, tags):
    candidate = make_candidate(session, tags, full_name="Rupa Kulkarni")
    candidate.resume_path = "/var/private/resumes/rupa.pdf"
    payload = to_public(candidate).model_dump()

    for field in SECRET_FIELDS:
        assert field not in payload
    assert "/var/private" not in str(payload)
    # The existence of a resume is public; the file is not.
    assert payload["has_resume"] is True


def test_public_name_is_first_name_plus_initial(session, tags):
    candidate = make_candidate(session, tags, full_name="Rupa Kulkarni")
    assert to_card(candidate).public_name == "Rupa K."


def test_slug_is_not_guessable_from_the_name(session, tags):
    """Two people with the same name get different URLs, and neither URL is a
    pure function of the name - so /p/<guess> is not an enumeration oracle for
    "is this person job-hunting"."""
    a = make_candidate(session, tags, full_name="Rupa Kulkarni")
    b = make_candidate(session, tags, full_name="Rupa Kulkarni")

    assert a.slug != b.slug
    assert a.slug.startswith("rupa-k-")
    assert len(a.slug.split("-")[-1]) == 4


def test_gap_reason_is_displayed_but_optional(session, tags):
    disclosed = make_candidate(
        session, tags, full_name="Told You", gap_reason=GapReason.CAREGIVING
    )
    private = make_candidate(session, tags, full_name="Not Telling", gap_reason=None)

    assert to_public(disclosed).gap.reason is GapReason.CAREGIVING
    assert to_public(private).gap.reason is None


def test_gap_dates_are_month_precision(session, tags):
    candidate = make_candidate(session, tags, full_name="Rupa Kulkarni")
    gap = to_public(candidate).gap
    assert gap.start == "2023-08"
    assert gap.end == "2025-08"


def test_contact_payload_is_the_only_one_with_the_goods(session, tags):
    import datetime as dt

    candidate = make_candidate(session, tags, full_name="Rupa Kulkarni")
    candidate.resume_path = "/var/private/resumes/rupa.pdf"
    now = dt.datetime.now(dt.timezone.utc)

    contact = to_contact(candidate, now).model_dump()
    assert contact["full_name"] == "Rupa Kulkarni"
    assert contact["email"] == candidate.email
    assert contact["phone"] == candidate.phone
    # Even here the filesystem path never ships - only a gated URL.
    assert contact["resume_url"] == f"/employer/resume/{candidate.slug}"
    assert "/var/private" not in str(contact)
