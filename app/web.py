"""HTML routes - the actual website.

Server-rendered Jinja, no build step and no client-side framework, because the
whole site is five pages and three of them are lists.

Route map:

    /           landing
    /browse     filtered directory      (public)
    /p/{slug}   profile, contact gated  (public, indexed)
    /submit     candidate form          (public, writes pending_review)
    /employer   placeholder for step 4
    /admin      review queue            (HTTP Basic)

The only write path a stranger can reach is POST /submit, and everything it
creates lands in PENDING_REVIEW. Nothing becomes visible without someone
clicking approve in /admin.
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_session
from .derive import make_slug, refresh_gap_months, slugify
from .enums import (
    GAP_BUCKET_LABELS,
    GapBucket,
    GapReason,
    ProfileStatus,
    ProofKind,
)
from .models import Candidate, CandidateTag, ProofLink, Tag
from .present import to_card, to_public
from .schemas import BrowseFilters
from .search import count_matches, fetch_page

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def support_url() -> str:
    """Where the "buy me a coffee" links point, from SUPPORT_URL.

    A callable rather than a value read at import, so it can be changed without
    a redeploy and so tests can set it per-case. Empty hides every support link
    on the site - a dead button is worse than no button.
    """
    return os.environ.get("SUPPORT_URL", "").strip()


TEMPLATES.env.globals["support_url"] = support_url

router = APIRouter()
basic = HTTPBasic(auto_error=False)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _live_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Candidate)
            .where(Candidate.status == ProfileStatus.LIVE)
        )
        or 0
    )


def filters_from_query(request: Request) -> BrowseFilters:
    """Build BrowseFilters from a browser form submission.

    An HTML form posts empty strings for every field the user left blank.
    BrowseFilters is extra="forbid" with typed optionals, so those have to be
    dropped rather than passed through as "" - otherwise clicking Apply with an
    empty city box is a validation error.
    """
    params = request.query_params
    data: dict = {}
    for key in ("tags", "role_sought", "gap_bucket"):
        values = [v for v in params.getlist(key) if v.strip()]
        if values:
            data[key] = values
    for key in (
        "city",
        "open_to_remote",
        "open_to_trial",
        "min_years",
        "max_years",
        "max_notice_days",
        "page",
        "page_size",
    ):
        value = params.get(key)
        if value is not None and value.strip():
            data[key] = value
    try:
        return BrowseFilters(**data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _page_url_factory(request: Request):
    """Preserve the current filters when paging."""

    def page_url(page: int) -> str:
        params = [(k, v) for k, v in request.query_params.multi_items() if k != "page"]
        params.append(("page", str(page)))
        return f"/browse?{urlencode(params)}"

    return page_url


def _parse_month(value: str | None) -> dt.date | None:
    """<input type="month"> gives "2023-08". Day precision is not asked for."""
    if not value or not value.strip():
        return None
    try:
        year, month = value.split("-")
        return dt.date(int(year), int(month), 1)
    except (ValueError, TypeError):
        return None


def require_admin(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)],
) -> str:
    """HTTP Basic against ADMIN_USER / ADMIN_PASSWORD.

    Refuses to run at all when they are unset rather than falling back to a
    default. /admin can publish profiles and delete people's data; a guessable
    default password on a public URL is worse than an unreachable page.
    """
    user = os.environ.get("ADMIN_USER")
    password = os.environ.get("ADMIN_PASSWORD")
    if not user or not password:
        raise HTTPException(
            status_code=503,
            detail="Admin is disabled: set ADMIN_USER and ADMIN_PASSWORD.",
        )
    if credentials is None or not (
        secrets.compare_digest(credentials.username, user)
        and secrets.compare_digest(credentials.password, password)
    ):
        raise HTTPException(
            status_code=401,
            detail="Not authorised.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ---------------------------------------------------------------------------
# public pages
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, session: Annotated[Session, Depends(get_session)]):
    return TEMPLATES.TemplateResponse(
        request, "index.html", {"live_count": _live_count(session), "nav": "home"}
    )


@router.get("/browse", response_class=HTMLResponse)
def browse_page(request: Request, session: Annotated[Session, Depends(get_session)]):
    filters = filters_from_query(request)
    total = count_matches(session, filters)
    results = [to_card(c) for c in fetch_page(session, filters)]

    # Only offer tags somebody actually has - a filter that can only ever
    # return nothing is worse than no filter.
    all_tags = list(
        session.scalars(
            select(Tag).join(CandidateTag, CandidateTag.tag_id == Tag.id).distinct().order_by(Tag.label)
        )
    )
    page_count = max(1, -(-total // filters.page_size))

    return TEMPLATES.TemplateResponse(
        request,
        "browse.html",
        {
            "nav": "browse",
            "f": filters,
            "selected_buckets": [b.value for b in filters.gap_bucket],
            "total": total,
            "results": results,
            "all_tags": all_tags,
            "gap_buckets": [(b.value, GAP_BUCKET_LABELS[b]) for b in GapBucket],
            "page_count": page_count,
            "page_url": _page_url_factory(request),
            "is_filtered": bool(request.query_params),
        },
    )


@router.get("/p/{slug}", response_class=HTMLResponse)
def profile_page(
    slug: str, request: Request, session: Annotated[Session, Depends(get_session)]
):
    candidate = session.scalar(
        select(Candidate).where(
            Candidate.slug == slug, Candidate.status == ProfileStatus.LIVE
        )
    )
    if candidate is None:
        # 404 rather than 403, so an unlisted profile is indistinguishable from
        # one that never existed.
        raise HTTPException(status_code=404, detail="No such profile.")
    return TEMPLATES.TemplateResponse(
        request, "profile.html", {"c": to_public(candidate), "nav": "browse"}
    )


@router.get("/employer", response_class=HTMLResponse)
def employer_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "employer.html", {"nav": "employer"})


# ---------------------------------------------------------------------------
# candidate submission
# ---------------------------------------------------------------------------

_EMPTY_FORM = {
    "full_name": "",
    "email": "",
    "phone": "",
    "headline": "",
    "city": "",
    "role_sought": "",
    "years_prior_experience": "",
    "skills": "",
    "summary": "",
    "gap_start": "",
    "gap_end": "",
    "gap_reason": "",
    "gap_activity": "",
    "notice_period_days": "0",
    "expected_ctc": "",
    "flexibility_note": "",
    "open_to_remote": True,
    "open_to_relocate": False,
    "open_to_trial": False,
}


def _submit_context(request: Request, values: dict, errors: list[str] | None = None):
    return TEMPLATES.TemplateResponse(
        request,
        "submit.html",
        {
            "nav": "submit",
            "v": values,
            "errors": errors or [],
            "gap_reasons": [
                (r.value, r.value.replace("_", " ").capitalize())
                for r in GapReason
                if r is not GapReason.PREFER_NOT_TO_SAY
            ],
            "proof_kinds": [(k.value, k.value.capitalize()) for k in ProofKind],
        },
        status_code=400 if errors else 200,
    )


@router.get("/submit", response_class=HTMLResponse)
def submit_form(request: Request):
    return _submit_context(request, dict(_EMPTY_FORM))


@router.post("/submit", response_class=HTMLResponse)
def submit_profile(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    full_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    headline: Annotated[str, Form()],
    city: Annotated[str, Form()],
    role_sought: Annotated[str, Form()],
    years_prior_experience: Annotated[int, Form()],
    gap_start: Annotated[str, Form()],
    gap_activity: Annotated[str, Form()],
    notice_period_days: Annotated[int, Form()],
    proof_label: Annotated[list[str], Form()] = [],
    proof_url: Annotated[list[str], Form()] = [],
    proof_kind: Annotated[list[str], Form()] = [],
    phone: Annotated[str, Form()] = "",
    skills: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    gap_end: Annotated[str, Form()] = "",
    gap_reason: Annotated[str, Form()] = "",
    expected_ctc: Annotated[str, Form()] = "",
    flexibility_note: Annotated[str, Form()] = "",
    open_to_remote: Annotated[str, Form()] = "",
    open_to_relocate: Annotated[str, Form()] = "",
    open_to_trial: Annotated[str, Form()] = "",
):
    """Creates a PENDING_REVIEW profile. Never anything else - there is no
    parameter a submitter can send that reaches the board directly."""
    values = dict(_EMPTY_FORM) | {
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "headline": headline,
        "city": city,
        "role_sought": role_sought,
        "years_prior_experience": years_prior_experience,
        "skills": skills,
        "summary": summary,
        "gap_start": gap_start,
        "gap_end": gap_end,
        "gap_reason": gap_reason,
        "gap_activity": gap_activity,
        "notice_period_days": notice_period_days,
        "expected_ctc": expected_ctc,
        "flexibility_note": flexibility_note,
        "open_to_remote": bool(open_to_remote),
        "open_to_relocate": bool(open_to_relocate),
        "open_to_trial": bool(open_to_trial),
    }

    errors: list[str] = []

    start = _parse_month(gap_start)
    end = _parse_month(gap_end)
    if start is None:
        errors.append("Give the month your gap started.")
    elif end is not None and end < start:
        errors.append("The gap cannot end before it starts.")
    elif start > dt.date.today():
        errors.append("The gap cannot start in the future.")

    links = [
        (label.strip(), url.strip(), kind)
        for label, url, kind in zip(proof_label, proof_url, proof_kind)
        if label.strip() and url.strip()
    ]
    if not links:
        errors.append(
            "At least one proof link is required - a repo, a piece of writing, "
            "a client, a course. It is the part that changes how the gap reads."
        )
    if any(not url.startswith(("http://", "https://")) for _, url, _ in links):
        errors.append("Proof links must start with http:// or https://")

    reason: GapReason | None = None
    if gap_reason:
        try:
            reason = GapReason(gap_reason)
        except ValueError:
            errors.append("Unknown reason.")

    ctc: int | None = None
    if expected_ctc.strip():
        try:
            ctc = int(expected_ctc)
            if ctc < 0:
                raise ValueError
        except ValueError:
            errors.append("Expected pay must be a whole number.")

    if errors:
        return _submit_context(request, values, errors)

    candidate = Candidate(
        slug=make_slug(session, full_name),
        status=ProfileStatus.PENDING_REVIEW,
        full_name=full_name.strip(),
        email=email.strip(),
        phone=phone.strip() or None,
        headline=headline.strip(),
        city=city.strip(),
        open_to_remote=bool(open_to_remote),
        open_to_relocate=bool(open_to_relocate),
        role_sought=role_sought.strip(),
        role_sought_slug=None,          # set during review
        years_prior_experience=years_prior_experience,
        summary=summary.strip() or None,
        skills_raw=skills.strip() or None,
        gap_start=start,
        gap_end=end,
        gap_reason=reason,
        gap_activity=gap_activity.strip(),
        expected_ctc=ctc,
        notice_period_days=notice_period_days,
        open_to_trial=bool(open_to_trial),
        flexibility_note=flexibility_note.strip() or None,
    )
    refresh_gap_months(candidate)
    session.add(candidate)
    session.flush()

    for label, url, kind in links:
        try:
            parsed_kind = ProofKind(kind)
        except ValueError:
            parsed_kind = ProofKind.OTHER
        session.add(
            ProofLink(candidate_id=candidate.id, label=label, url=url, kind=parsed_kind)
        )
    session.commit()

    return TEMPLATES.TemplateResponse(
        request, "submit_done.html", {"email": candidate.email, "nav": "submit"}
    )


# ---------------------------------------------------------------------------
# admin review queue
# ---------------------------------------------------------------------------


@router.get("/admin", response_class=HTMLResponse)
def admin_queue(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[str, Depends(require_admin)],
):
    pending = list(
        session.scalars(
            select(Candidate)
            .where(Candidate.status == ProfileStatus.PENDING_REVIEW)
            .order_by(Candidate.created_at)
        )
    )
    live = list(
        session.scalars(
            select(Candidate)
            .where(Candidate.status == ProfileStatus.LIVE)
            .order_by(Candidate.updated_at.desc())
        )
    )

    def tag_string(candidate: Candidate) -> str:
        if candidate.tags:
            return ", ".join(ct.tag.slug for ct in candidate.tags)
        # Fall back to what the candidate typed, so the reviewer normalises
        # rather than retypes.
        return ", ".join(slugify(s) for s in (candidate.skills_raw or "").split(",") if s.strip())

    return TEMPLATES.TemplateResponse(
        request,
        "admin.html",
        {
            "nav": "admin",
            "pending": pending,
            "live": live,
            "live_count": len(live),
            "tag_string": tag_string,
        },
    )


def _get_or_404(session: Session, candidate_id: int) -> Candidate:
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="No such profile.")
    return candidate


@router.post("/admin/{candidate_id}/approve")
def admin_approve(
    candidate_id: int,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[str, Depends(require_admin)],
    role_slug: Annotated[str, Form()],
    tags: Annotated[str, Form()] = "",
):
    """Publish. This is the only code path that sets a profile LIVE.

    Normalisation happens here rather than at submit time: tags typed by the
    reviewer are canonical by definition, which is what keeps `react`,
    `reactjs` and `react-js` from becoming three filters.
    """
    candidate = _get_or_404(session, candidate_id)
    candidate.role_sought_slug = slugify(role_slug)
    candidate.status = ProfileStatus.LIVE
    candidate.review_note = None
    refresh_gap_months(candidate)

    wanted = [slugify(t) for t in tags.split(",") if t.strip()]
    for link in list(candidate.tags):
        session.delete(link)
    session.flush()
    for slug in dict.fromkeys(wanted):
        tag = session.scalar(select(Tag).where(Tag.slug == slug))
        if tag is None:
            tag = Tag(slug=slug, label=slug.replace("-", " ").title())
            session.add(tag)
            session.flush()
        session.add(CandidateTag(candidate_id=candidate.id, tag_id=tag.id))

    session.commit()
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/{candidate_id}/changes")
def admin_request_changes(
    candidate_id: int,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[str, Depends(require_admin)],
    note: Annotated[str, Form()],
):
    candidate = _get_or_404(session, candidate_id)
    candidate.status = ProfileStatus.DRAFT
    candidate.review_note = note.strip()
    session.commit()
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/{candidate_id}/pause")
def admin_pause(
    candidate_id: int,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[str, Depends(require_admin)],
):
    candidate = _get_or_404(session, candidate_id)
    candidate.status = ProfileStatus.PAUSED
    session.commit()
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/{candidate_id}/delete")
def admin_delete(
    candidate_id: int,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[str, Depends(require_admin)],
):
    """Guardrail 6: everything goes, including the file.

    Cascades handle proof links, tags and reveal rows; the resume is on disk and
    has to be unlinked explicitly, because a row disappearing while the PDF
    stays on the volume is exactly the failure DPDP erasure is about.
    """
    candidate = _get_or_404(session, candidate_id)
    if candidate.resume_path:
        Path(candidate.resume_path).unlink(missing_ok=True)
    session.delete(candidate)
    session.commit()
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
