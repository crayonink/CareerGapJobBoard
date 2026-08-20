"""ASGI entrypoint.

The site is server-rendered HTML (see web.py). The JSON API lives under /api
and is a thin second face on the same query layer - useful for checking things
without a browser, and for whatever comes after.

Employer accounts and the contact-reveal gate are build step 4. Until they land
there is no route on this app that can return a candidate's email, phone or
resume, and a test enforces that by walking the route table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from .db import DB_PATH, get_session, storage_is_ephemeral
from .enums import ProfileStatus
from .models import Candidate
from .present import to_card, to_public
from .schemas import BrowseFilters, BrowseResponse, CandidatePublic
from .search import count_matches, fetch_page
from .web import TEMPLATES, router as web_router

app = FastAPI(
    title="CareerGapJobBoard",
    description="Reverse job board for candidates with employment gaps.",
    version="0.2.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)


@app.middleware("http")
async def robots_policy(request: Request, call_next) -> Response:
    """Guardrail 4. Public profiles are the point and should be indexed;
    /admin, employer routes, the API and health should not be. Default to
    noindex and opt routes in, so a new route is private until someone decides
    otherwise."""
    response = await call_next(request)
    path = request.url.path
    indexable = path == "/" or path.startswith("/p/") or path == "/browse"
    if not indexable:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.exception_handler(StarletteHTTPException)
async def html_errors(request: Request, exc: StarletteHTTPException):
    """Browsers get a page, API clients get JSON.

    Without this a mistyped profile URL renders as a raw JSON blob, which reads
    as a broken site rather than a missing page.
    """
    wants_html = "text/html" in request.headers.get("accept", "")
    if wants_html and not request.url.path.startswith("/api"):
        return TEMPLATES.TemplateResponse(
            request,
            "error.html",
            {"code": exc.status_code, "detail": exc.detail, "nav": None},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )
    return JSONResponse(
        {"detail": exc.detail},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


app.include_router(web_router)


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


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


@app.get("/api/browse", response_model=BrowseResponse)
def api_browse(
    filters: Annotated[BrowseFilters, Query()],
    session: Annotated[Session, Depends(get_session)],
) -> BrowseResponse:
    """`BrowseFilters` is `extra="forbid"`, so an unknown query parameter is a
    422 rather than a silently ignored filter - which is what keeps
    `?gap_reason=health` from ever looking like it worked."""
    return BrowseResponse(
        total=count_matches(session, filters),
        page=filters.page,
        page_size=filters.page_size,
        results=[to_card(c) for c in fetch_page(session, filters)],
    )


@app.get("/api/p/{slug}", response_model=CandidatePublic)
def api_profile(
    slug: str,
    session: Annotated[Session, Depends(get_session)],
) -> CandidatePublic:
    candidate = session.scalar(
        select(Candidate).where(
            Candidate.slug == slug, Candidate.status == ProfileStatus.LIVE
        )
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="No such profile.")
    return to_public(candidate)
