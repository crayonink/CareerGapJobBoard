from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.derive import make_slug, refresh_gap_months
from app.enums import GapReason, ProfileStatus, ProofKind
from app.models import Candidate, CandidateTag, ProofLink, Tag

TODAY = dt.date(2026, 8, 20)


@pytest.fixture()
def session() -> Session:
    # StaticPool + check_same_thread=False: TestClient runs the ASGI app in a
    # worker thread, and an in-memory database only exists for the connection
    # that created it. Without both, every API test gets a different empty DB
    # or a cross-thread ProgrammingError.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


@pytest.fixture()
def tags(session: Session) -> dict[str, Tag]:
    made = {}
    for slug, label in [
        ("python", "Python"),
        ("react", "React"),
        ("sql", "SQL"),
        ("figma", "Figma"),
    ]:
        tag = Tag(slug=slug, label=label)
        session.add(tag)
        made[slug] = tag
    session.flush()
    return made


def make_candidate(
    session: Session,
    tags: dict[str, Tag],
    *,
    full_name: str = "Rupa Kulkarni",
    status: ProfileStatus = ProfileStatus.LIVE,
    city: str = "Bangalore",
    open_to_remote: bool = True,
    role_slug: str = "backend-engineer",
    years: int = 6,
    gap_start: dt.date = dt.date(2023, 8, 1),
    gap_end: dt.date | None = dt.date(2025, 8, 1),
    gap_reason: GapReason | None = GapReason.CAREGIVING,
    notice_days: int = 0,
    open_to_trial: bool = False,
    tag_slugs: tuple[str, ...] = ("python", "sql"),
    proof_links: int = 1,
    expected_ctc: int | None = 1_800_000,
    phone: str | None = "+91 90000 00000",
) -> Candidate:
    candidate = Candidate(
        slug=make_slug(session, full_name),
        status=status,
        full_name=full_name,
        email=f"{full_name.split()[0].lower()}@example.com",
        phone=phone,
        headline="Backend engineer, payments and ledgers",
        city=city,
        open_to_remote=open_to_remote,
        open_to_relocate=False,
        role_sought="Backend Engineer",
        role_sought_slug=role_slug,
        years_prior_experience=years,
        summary="Built and ran payment infrastructure.",
        gap_start=gap_start,
        gap_end=gap_end,
        gap_reason=gap_reason,
        gap_activity="Rebuilt a ledger reconciler as a side project.",
        expected_ctc=expected_ctc,
        notice_period_days=notice_days,
        open_to_trial=open_to_trial,
        flexibility_note="Prefer 4-day weeks for the first two months.",
    )
    refresh_gap_months(candidate, TODAY)
    session.add(candidate)
    session.flush()

    for slug in tag_slugs:
        session.add(CandidateTag(candidate_id=candidate.id, tag_id=tags[slug].id))
    for i in range(proof_links):
        session.add(
            ProofLink(
                candidate_id=candidate.id,
                label=f"GitHub - project {i + 1}",
                url=f"https://github.com/example/p{i + 1}",
                kind=ProofKind.CODE,
            )
        )
    session.flush()
    session.refresh(candidate)
    return candidate
