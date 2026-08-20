"""The website: pages a person can actually open."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.enums import ProfileStatus
from app.main import app
from app.models import Candidate, ProofLink

from .conftest import make_candidate

ADMIN = ("boardadmin", "s3cret-for-tests")


@pytest.fixture()
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", ADMIN[0])
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])


def valid_form(**overrides) -> dict:
    form = {
        "full_name": "Rupa Kulkarni",
        "email": "rupa@example.com",
        "phone": "+91 90000 00000",
        "headline": "Backend engineer, payments and ledgers",
        "city": "Bangalore",
        "role_sought": "Backend Engineer",
        "years_prior_experience": "6",
        "skills": "Python, SQL",
        "summary": "Built payment infrastructure.",
        "gap_start": "2023-08",
        "gap_end": "2025-08",
        "gap_reason": "caregiving",
        "gap_activity": "Rebuilt a ledger reconciler as a side project.",
        "notice_period_days": "0",
        "expected_ctc": "1800000",
        "flexibility_note": "Four-day weeks for the first two months.",
        "proof_label": ["GitHub - ledger reconciler", "", ""],
        "proof_url": ["https://github.com/example/ledger", "", ""],
        "proof_kind": ["code", "code", "code"],
    }
    form.update(overrides)
    return form


# --- public pages -----------------------------------------------------------


def test_landing(client):
    body = client.get("/").text
    assert "A career gap is a fact" in body
    assert 'href="/submit"' in body


def test_landing_counts_live_profiles(client, session, tags):
    make_candidate(session, tags)
    assert "Browse 1 candidate<" in client.get("/").text


def test_browse_empty_state(client):
    assert "No profiles are live yet" in client.get("/browse").text


def test_browse_lists_live_profiles(client, session, tags):
    make_candidate(session, tags, full_name="Rupa Kulkarni")
    body = client.get("/browse").text
    assert "Rupa K." in body
    assert "Kulkarni" not in body       # public name only, on the HTML too


def test_browse_hides_non_live(client, session, tags):
    make_candidate(session, tags, full_name="Draft Person", status=ProfileStatus.DRAFT)
    assert "No profiles are live yet" in client.get("/browse").text


def test_browse_filter_form_round_trips(client, session, tags):
    """Ticking a box, submitting, and getting the box still ticked is the whole
    difference between a filter panel and a set of buttons that lose your work."""
    make_candidate(session, tags, full_name="Py Dev", tag_slugs=("python",))
    body = client.get("/browse", params={"tags": "python", "open_to_remote": "true"}).text
    assert 'value="python"' in body
    assert "checked" in body


def test_browse_empty_form_fields_are_not_a_validation_error(client, session, tags):
    """A browser submits "" for every blank box. Those must be dropped, not
    passed to a typed optional."""
    make_candidate(session, tags)
    resp = client.get(
        "/browse",
        params={"city": "", "min_years": "", "max_years": "", "max_notice_days": ""},
    )
    assert resp.status_code == 200


def test_browse_filters_actually_filter(client, session, tags):
    make_candidate(session, tags, full_name="Bangalore Person", city="Bangalore")
    make_candidate(session, tags, full_name="Chennai Person", city="Chennai")

    body = client.get("/browse", params={"city": "Chennai"}).text
    assert "Chennai P." in body
    assert "Bangalore P." not in body


def test_profile_page_renders(client, session, tags):
    c = make_candidate(session, tags, full_name="Rupa Kulkarni")
    body = client.get(f"/p/{c.slug}").text

    assert "Rupa K." in body
    assert "Sign in as an employer" in body
    # The contact block is a gate, not a hidden div with the data in it.
    assert c.email not in body
    assert c.phone not in body
    assert "Kulkarni" not in body


def test_profile_404_is_a_page_not_json(client):
    resp = client.get("/p/nobody-x-0000", headers={"accept": "text/html"})
    assert resp.status_code == 404
    assert "Nothing here." in resp.text


def test_employer_page_is_honest_about_not_existing(client):
    assert "Not built yet" in client.get("/employer").text


# --- submission -------------------------------------------------------------


def test_submit_form_renders(client):
    assert "Add your profile" in client.get("/submit").text


def test_submit_creates_a_pending_profile(client, session):
    resp = client.post("/submit", data=valid_form())
    assert resp.status_code == 200
    assert "in the queue" in resp.text

    c = session.scalars(select_candidates()).one()
    assert c.status is ProfileStatus.PENDING_REVIEW
    assert c.full_name == "Rupa Kulkarni"
    assert c.gap_start == dt.date(2023, 8, 1)
    assert c.gap_months == 24
    assert c.skills_raw == "Python, SQL"
    assert len(c.proof_links) == 1


def test_submitted_profile_is_not_browsable(client, session):
    client.post("/submit", data=valid_form())
    assert "No profiles are live yet" in client.get("/browse").text


def test_submit_requires_a_proof_link(client, session):
    resp = client.post(
        "/submit", data=valid_form(proof_label=["", "", ""], proof_url=["", "", ""])
    )
    assert resp.status_code == 400
    assert "At least one proof link is required" in resp.text
    assert session.scalars(select_candidates()).all() == []


def test_submit_rejects_a_non_http_proof_link(client, session):
    resp = client.post(
        "/submit",
        data=valid_form(
            proof_label=["My drive", "", ""], proof_url=["ftp://example.com", "", ""]
        ),
    )
    assert resp.status_code == 400
    assert "http://" in resp.text


def test_submit_rejects_a_backwards_gap(client, session):
    resp = client.post("/submit", data=valid_form(gap_start="2025-08", gap_end="2023-08"))
    assert resp.status_code == 400
    assert "cannot end before it starts" in resp.text


def test_submit_keeps_what_was_typed_on_error(client):
    """Re-typing a long form because one field was wrong is how you lose a
    candidate who was already unsure about publishing."""
    resp = client.post("/submit", data=valid_form(proof_url=["", "", ""]))
    assert "Backend engineer, payments and ledgers" in resp.text
    assert "Rupa Kulkarni" in resp.text


def test_ongoing_gap_is_allowed(client, session):
    client.post("/submit", data=valid_form(gap_end=""))
    c = session.scalars(select_candidates()).one()
    assert c.gap_end is None
    assert c.gap_ongoing is True


def test_submit_without_a_reason_is_fine(client, session):
    client.post("/submit", data=valid_form(gap_reason=""))
    assert session.scalars(select_candidates()).one().gap_reason is None


# --- admin ------------------------------------------------------------------


def test_admin_needs_credentials(client, admin_env):
    assert client.get("/admin").status_code == 401


def test_admin_rejects_wrong_password(client, admin_env):
    assert client.get("/admin", auth=(ADMIN[0], "wrong")).status_code == 401


def test_admin_is_disabled_when_unconfigured(client, monkeypatch):
    """No default password. /admin can publish profiles and delete people's
    data - unreachable beats guessable."""
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    assert client.get("/admin", auth=ADMIN).status_code == 503


def test_admin_shows_the_queue(client, session, admin_env):
    client.post("/submit", data=valid_form())
    body = client.get("/admin", auth=ADMIN).text
    assert "Rupa Kulkarni" in body          # full name, for the reviewer
    assert "rupa@example.com" in body
    assert "python, sql" in body            # normalised suggestion from skills_raw


def test_approve_publishes_and_normalises(client, session, admin_env):
    client.post("/submit", data=valid_form())
    c = session.scalars(select_candidates()).one()

    resp = client.post(
        f"/admin/{c.id}/approve",
        data={"role_slug": "Backend Engineer", "tags": "Python, SQL"},
        auth=ADMIN,
        follow_redirects=False,
    )
    assert resp.status_code == 303

    session.expire_all()
    c = session.scalars(select_candidates()).one()
    assert c.status is ProfileStatus.LIVE
    assert c.role_sought_slug == "backend-engineer"
    assert sorted(ct.tag.slug for ct in c.tags) == ["python", "sql"]

    assert "Rupa K." in client.get("/browse").text


def test_approve_is_the_only_way_onto_the_board(client, session, admin_env):
    client.post("/submit", data=valid_form())
    assert "No profiles are live yet" in client.get("/browse").text


def test_request_changes_sends_it_back_with_a_note(client, session, admin_env):
    client.post("/submit", data=valid_form())
    c = session.scalars(select_candidates()).one()

    client.post(
        f"/admin/{c.id}/changes",
        data={"note": "Add a link for the freelance work."},
        auth=ADMIN,
        follow_redirects=False,
    )
    session.expire_all()
    c = session.scalars(select_candidates()).one()
    assert c.status is ProfileStatus.DRAFT
    assert c.review_note == "Add a link for the freelance work."


def test_pause_removes_from_browse(client, session, tags, admin_env):
    c = make_candidate(session, tags, full_name="Rupa Kulkarni")
    assert "Rupa K." in client.get("/browse").text

    client.post(f"/admin/{c.id}/pause", auth=ADMIN, follow_redirects=False)
    assert "Rupa K." not in client.get("/browse").text
    assert client.get(f"/p/{c.slug}").status_code == 404


def test_delete_removes_everything(client, session, tags, admin_env):
    """Guardrail 6: one click, and the profile plus its children are gone."""
    c = make_candidate(session, tags, full_name="Rupa Kulkarni", proof_links=2)
    cid = c.id

    client.post(f"/admin/{cid}/delete", auth=ADMIN, follow_redirects=False)
    session.expire_all()

    assert session.get(Candidate, cid) is None
    remaining = session.scalars(
        __import__("sqlalchemy").select(ProofLink).where(ProofLink.candidate_id == cid)
    ).all()
    assert remaining == []


def test_admin_actions_need_auth(client, session, tags, admin_env):
    c = make_candidate(session, tags)
    for path in (f"/admin/{c.id}/pause", f"/admin/{c.id}/delete"):
        assert client.post(path, follow_redirects=False).status_code == 401
    assert (
        client.post(
            f"/admin/{c.id}/approve", data={"role_slug": "x"}, follow_redirects=False
        ).status_code
        == 401
    )


def test_admin_is_noindex(client, admin_env):
    resp = client.get("/admin", auth=ADMIN)
    assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"


# --- helper -----------------------------------------------------------------


def select_candidates():
    from sqlalchemy import select

    return select(Candidate)
