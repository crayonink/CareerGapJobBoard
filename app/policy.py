"""Access rules that guard the contact data.

The reveal gate is build step 4, but the rules live here from step 1 so that
models.py has something concrete to point at and nothing gets invented twice
later.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ContactReveal, Employer

#: Guardrail 3. A recruiter revealing 200 profiles in an hour is building a
#: list to resell, not hiring. Twenty is generous for anyone actually reading.
DAILY_REVEAL_LIMIT = 20

#: Not exhaustive and does not need to be - it filters out the drive-by
#: signups. Anyone determined enough to register a domain to scrape the board
#: gets caught by the rate limit instead.
FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.in",
        "yahoo.co.uk",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "aol.com",
        "icloud.com",
        "me.com",
        "protonmail.com",
        "proton.me",
        "zoho.com",
        "rediffmail.com",
        "mail.com",
        "yandex.com",
        "gmx.com",
        "tutanota.com",
    }
)


def email_domain(email: str) -> str:
    _, _, domain = email.strip().lower().rpartition("@")
    return domain


def is_work_email(email: str) -> bool:
    """Guardrail: employers sign up with a company address.

    Independent recruiters are a real exception - they are the day-one demand
    side and half of them do use Gmail. Handle those by verifying them by hand
    rather than by loosening this check.
    """
    domain = email_domain(email)
    return bool(domain) and "." in domain and domain not in FREE_EMAIL_DOMAINS


def reveals_today(session: Session, employer_id: int, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=1)
    return (
        session.scalar(
            select(func.count())
            .select_from(ContactReveal)
            .where(
                ContactReveal.employer_id == employer_id,
                ContactReveal.revealed_at >= since,
            )
        )
        or 0
    )


def already_revealed(session: Session, employer_id: int, candidate_id: int) -> ContactReveal | None:
    return session.scalar(
        select(ContactReveal).where(
            ContactReveal.employer_id == employer_id,
            ContactReveal.candidate_id == candidate_id,
        )
    )


def can_reveal(
    session: Session,
    employer: Employer,
    candidate_id: int,
    now: dt.datetime | None = None,
) -> tuple[bool, str | None]:
    """Returns (allowed, refusal_reason).

    Re-opening a profile you already unlocked is free and does not count
    against the daily limit - otherwise an employer who bookmarks a candidate
    burns quota re-reading their own shortlist.
    """
    if not employer.is_verified:
        return False, "Verify your work email before viewing contact details."
    if already_revealed(session, employer.id, candidate_id) is not None:
        return True, None
    if reveals_today(session, employer.id, now) >= DAILY_REVEAL_LIMIT:
        return False, f"Daily limit of {DAILY_REVEAL_LIMIT} contact reveals reached."
    return True, None
