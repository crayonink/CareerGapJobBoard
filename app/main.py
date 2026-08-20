"""ASGI entrypoint.

Read-only JSON for now: this exists so the deploy pipeline is real and
verifiable end to end while the surface is still small enough to check by eye.
`/submit`, `/admin`, employer auth and the reveal gate are build steps 2 and 4;
until they land there is deliberately no way to write to this database over
HTTP, and no route that can return a candidate's contact details.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import DB_PATH, get_session, storage_is_ephemeral
from .enums import ProfileStatus
from .models import Candidate
from .present import to_card, to_public
from .schemas import BrowseFilters, BrowseResponse, CandidatePublic
from .search import count_matches, fetch_page

app = FastAPI(
    title="CareerGapJobBoard",
    description="Reverse job board for candidates with employment gaps.",
    version="0.1.0",
)


@app.middleware("http")
async def robots_policy(request: Request, call_next) -> Response:
    """Guardrail 4. Public profiles are the point of the site and should be
    indexed; everything else - health, docs, and later /admin and the employer
    routes - should not be. Default to noindex and opt routes in."""
    response = await call_next(request)
    path = request.url.path
    indexable = path == "/" or path.startswith("/p/")
    if not indexable:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.get("/health", include_in_schema=False)
def health(session: Annotated[Session, Depends(get_session)]) -> dict:
    """Railway healthcheck target.

    Reports `storage_ephemeral` because a board running happily on a disk that
    the next redeploy will erase looks exactly like a healthy one until it
    doesn't.
    """
    session.execute(text("SELECT 1"))
    live = session.scalar(
        select(Candidate).where(Candidate.status == ProfileStatus.LIVE).limit(1)
    )
    return {
        "status": "ok",
        "database": str(DB_PATH),
        "storage_ephemeral": storage_is_ephemeral(),
        "has_live_profiles": live is not None,
    }


@app.get("/browse", response_model=BrowseResponse)
def browse(
    filters: Annotated[BrowseFilters, Query()],
    session: Annotated[Session, Depends(get_session)],
) -> BrowseResponse:
    """The directory.

    `BrowseFilters` is `extra="forbid"`, so an unknown query parameter is a 422
    rather than a silently ignored filter - which is what keeps
    `?gap_reason=health` from ever looking like it worked.
    """
    return BrowseResponse(
        total=count_matches(session, filters),
        page=filters.page,
        page_size=filters.page_size,
        results=[to_card(c) for c in fetch_page(session, filters)],
    )


@app.get("/p/{slug}", response_model=CandidatePublic)
def profile(
    slug: str,
    session: Annotated[Session, Depends(get_session)],
) -> CandidatePublic:
    """A public profile. Never includes contact details - reaching someone goes
    through the reveal gate, which is build step 4."""
    candidate = session.scalar(
        select(Candidate).where(
            Candidate.slug == slug,
            # A 404 for anything not live, so an unlisted profile is
            # indistinguishable from one that never existed.
            Candidate.status == ProfileStatus.LIVE,
        )
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="No such profile.")
    return to_public(candidate)
