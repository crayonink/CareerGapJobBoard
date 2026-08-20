"""The read-only HTTP surface.

Also the regression net for "the deploy is green but the app serves nothing" -
every route Railway can reach is exercised here.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.enums import GapReason, ProfileStatus
from app.main import app

from .conftest import make_candidate


@pytest.fixture()
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["has_live_profiles"] is False
    # Locally this is always False; on Railway without a Volume it is True.
    assert body["storage_ephemeral"] is False


def test_health_reports_live_profiles(client, session, tags):
    make_candidate(session, tags)
    assert client.get("/health").json()["has_live_profiles"] is True


def test_browse_returns_live_only(client, session, tags):
    live = make_candidate(session, tags, full_name="Live One")
    make_candidate(session, tags, full_name="Draft One", status=ProfileStatus.DRAFT)

    body = client.get("/browse").json()
    assert body["total"] == 1
    assert [r["slug"] for r in body["results"]] == [live.slug]


def test_browse_applies_query_filters(client, session, tags):
    py = make_candidate(session, tags, full_name="Py Dev", tag_slugs=("python",))
    make_candidate(session, tags, full_name="Ui Dev", tag_slugs=("figma",))

    body = client.get("/browse", params={"tags": ["python"]}).json()
    assert [r["slug"] for r in body["results"]] == [py.slug]


def test_browse_repeated_params_and_paging(client, session, tags):
    for i in range(3):
        make_candidate(session, tags, full_name=f"Person {i}")

    body = client.get("/browse", params={"page": 2, "page_size": 2}).json()
    assert body["total"] == 3          # total is unpaged
    assert len(body["results"]) == 1
    assert body["page"] == 2


@pytest.mark.parametrize(
    "params",
    [
        {"gap_reason": "health"},
        {"expected_ctc": 1000000},
        {"max_ctc": 1000000},
        {"sort": "expected_ctc"},
        {"full_name": "Rupa"},
    ],
)
def test_browse_rejects_forbidden_filters(client, params):
    """A silently ignored parameter would look like a working filter. 422 is
    the whole point of extra="forbid"."""
    assert client.get("/browse", params=params).status_code == 422


def test_browse_leaks_nothing(client, session, tags):
    candidate = make_candidate(session, tags, full_name="Rupa Kulkarni")
    raw = client.get("/browse").text

    assert candidate.email not in raw
    assert candidate.phone not in raw
    assert "Kulkarni" not in raw
    assert "Rupa K." in raw


def test_profile_page(client, session, tags):
    candidate = make_candidate(session, tags, full_name="Rupa Kulkarni")
    body = client.get(f"/p/{candidate.slug}").json()

    assert body["public_name"] == "Rupa K."
    assert body["proof_links"][0]["url"].startswith("https://github.com/")
    assert "email" not in body
    assert "phone" not in body
    assert "full_name" not in body


def test_profile_shows_gap_reason_when_disclosed(client, session, tags):
    told = make_candidate(session, tags, full_name="Told You", gap_reason=GapReason.LAYOFF)
    quiet = make_candidate(session, tags, full_name="Not Telling", gap_reason=None)

    assert client.get(f"/p/{told.slug}").json()["gap"]["reason"] == "layoff"
    assert client.get(f"/p/{quiet.slug}").json()["gap"]["reason"] is None


@pytest.mark.parametrize(
    "status", [ProfileStatus.DRAFT, ProfileStatus.PENDING_REVIEW, ProfileStatus.PAUSED]
)
def test_non_live_profiles_404(client, session, tags, status):
    """404 rather than 403, so an unlisted profile is indistinguishable from
    one that never existed."""
    hidden = make_candidate(session, tags, full_name="Hidden Person", status=status)
    assert client.get(f"/p/{hidden.slug}").status_code == 404


def test_unknown_slug_404(client):
    assert client.get("/p/nobody-x-0000").status_code == 404


def test_robots_policy(client, session, tags):
    """Guardrail 4: profiles indexed, everything else not."""
    candidate = make_candidate(session, tags)

    assert "X-Robots-Tag" not in client.get(f"/p/{candidate.slug}").headers
    assert client.get("/health").headers["X-Robots-Tag"] == "noindex, nofollow"
    assert client.get("/browse").headers["X-Robots-Tag"] == "noindex, nofollow"


def test_no_write_route_exists():
    """Until /submit and /admin land, nothing over HTTP may modify the board."""
    methods = {m for route in app.routes for m in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD"}, f"unexpected write methods: {methods}"


def test_no_route_can_return_contact_details():
    """Every response model registered on the app, checked for contact fields.
    Fails the moment a reveal route is added without a deliberate exemption."""
    banned = {"email", "phone", "full_name", "resume_path"}
    for route in app.routes:
        model = getattr(route, "response_model", None)
        if model is None or not hasattr(model, "model_fields"):
            continue
        leaked = banned & set(model.model_fields)
        assert not leaked, f"{route.path} exposes {leaked}"


def test_index_lists_what_exists(client):
    """The root 404 was just a missing route, not a broken deploy - but a 404
    at the site root reads as "the site is down" to anyone who visits it."""
    body = client.get("/").json()
    assert body["service"] == "CareerGapJobBoard"
    assert body["routes"]["browse"] == "/browse"


def test_index_stays_indexable(client):
    """Guardrail 4: / and /p/{slug} are the two indexable routes, because
    search traffic is the point. This one becomes the landing page in step 5."""
    assert "X-Robots-Tag" not in client.get("/").headers
