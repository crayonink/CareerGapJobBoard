"""Employer verification and the reveal rate limit."""

from __future__ import annotations

import datetime as dt

import pytest

from app.models import ContactReveal, Employer
from app.policy import DAILY_REVEAL_LIMIT, can_reveal, is_work_email, reveals_today

from .conftest import make_candidate

NOW = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)


@pytest.mark.parametrize(
    "email, ok",
    [
        ("priya@acme.com", True),
        ("talent@dell.com", True),
        ("someone@gmail.com", False),
        ("someone@GMAIL.COM", False),
        ("someone@yahoo.co.in", False),
        ("someone@outlook.com", False),
        ("notanemail", False),
        ("someone@localhost", False),
    ],
)
def test_work_email_check(email, ok):
    assert is_work_email(email) is ok


def _employer(session, *, verified: bool = True) -> Employer:
    employer = Employer(
        company_name="Acme",
        contact_name="Priya",
        work_email="priya@acme.com",
        email_verified_at=NOW if verified else None,
    )
    session.add(employer)
    session.flush()
    return employer


def test_unverified_employer_cannot_reveal(session, tags):
    employer = _employer(session, verified=False)
    candidate = make_candidate(session, tags)

    allowed, reason = can_reveal(session, employer, candidate.id, NOW)
    assert allowed is False
    assert "Verify" in reason


def test_verified_employer_can_reveal(session, tags):
    employer = _employer(session)
    candidate = make_candidate(session, tags)

    allowed, reason = can_reveal(session, employer, candidate.id, NOW)
    assert allowed is True
    assert reason is None


def test_daily_limit_blocks_further_reveals(session, tags):
    employer = _employer(session)
    for i in range(DAILY_REVEAL_LIMIT):
        candidate = make_candidate(session, tags, full_name=f"Person {i}")
        session.add(
            ContactReveal(
                employer_id=employer.id, candidate_id=candidate.id, revealed_at=NOW
            )
        )
    session.flush()

    assert reveals_today(session, employer.id, NOW) == DAILY_REVEAL_LIMIT
    fresh = make_candidate(session, tags, full_name="One Too Many")
    allowed, reason = can_reveal(session, employer, fresh.id, NOW)
    assert allowed is False
    assert str(DAILY_REVEAL_LIMIT) in reason


def test_reopening_an_existing_reveal_is_free(session, tags):
    """An employer who bookmarked a candidate should not burn quota re-reading
    their own shortlist."""
    employer = _employer(session)
    already = make_candidate(session, tags, full_name="Already Seen")
    session.add(
        ContactReveal(employer_id=employer.id, candidate_id=already.id, revealed_at=NOW)
    )
    for i in range(DAILY_REVEAL_LIMIT):
        other = make_candidate(session, tags, full_name=f"Other {i}")
        session.add(
            ContactReveal(employer_id=employer.id, candidate_id=other.id, revealed_at=NOW)
        )
    session.flush()

    allowed, _ = can_reveal(session, employer, already.id, NOW)
    assert allowed is True


def test_quota_is_a_rolling_24_hours(session, tags):
    employer = _employer(session)
    stale = NOW - dt.timedelta(days=2)
    for i in range(DAILY_REVEAL_LIMIT):
        candidate = make_candidate(session, tags, full_name=f"Old {i}")
        session.add(
            ContactReveal(
                employer_id=employer.id, candidate_id=candidate.id, revealed_at=stale
            )
        )
    session.flush()

    assert reveals_today(session, employer.id, NOW) == 0
    fresh = make_candidate(session, tags, full_name="New Day")
    allowed, _ = can_reveal(session, employer, fresh.id, NOW)
    assert allowed is True
