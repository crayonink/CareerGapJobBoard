"""/browse filter behaviour."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from app.enums import GapBucket, ProfileStatus
from app.schemas import BrowseFilters
from app.search import count_matches, fetch_page

from .conftest import TODAY, make_candidate


def slugs(session, **kwargs) -> set[str]:
    f = BrowseFilters(**kwargs)
    page = fetch_page(session, f)
    # The count must agree with the page, or the pager lies.
    assert count_matches(session, f) >= len(page)
    return {c.slug for c in page}


def test_only_live_profiles_are_browsable(session, tags):
    live = make_candidate(session, tags, full_name="Live One")
    for status in (ProfileStatus.DRAFT, ProfileStatus.PENDING_REVIEW, ProfileStatus.PAUSED):
        make_candidate(session, tags, full_name=f"Hidden {status.value}", status=status)

    assert slugs(session) == {live.slug}


def test_no_filters_returns_everything_live(session, tags):
    a = make_candidate(session, tags, full_name="Asha Rao")
    b = make_candidate(session, tags, full_name="Biju Nair")
    assert slugs(session) == {a.slug, b.slug}


def test_tag_filter_matches_any(session, tags):
    py = make_candidate(session, tags, full_name="Py Dev", tag_slugs=("python",))
    ui = make_candidate(session, tags, full_name="Ui Dev", tag_slugs=("figma",))
    make_candidate(session, tags, full_name="Sql Only", tag_slugs=("sql",))

    assert slugs(session, tags=["python", "figma"]) == {py.slug, ui.slug}


def test_tag_filter_does_not_duplicate_rows(session, tags):
    """A candidate holding two of the requested tags appears once, not twice."""
    both = make_candidate(session, tags, full_name="Both Tags", tag_slugs=("python", "sql"))
    f = BrowseFilters(tags=["python", "sql"])
    assert [c.slug for c in fetch_page(session, f)] == [both.slug]
    assert count_matches(session, f) == 1


def test_unknown_tag_matches_nobody(session, tags):
    make_candidate(session, tags, full_name="Someone Here")
    assert slugs(session, tags=["cobol"]) == set()


def test_city_is_a_substring_match(session, tags):
    north = make_candidate(session, tags, full_name="North Person", city="Bangalore North")
    make_candidate(session, tags, full_name="Pune Person", city="Pune")

    assert north.slug in slugs(session, city="bangalore")
    assert slugs(session, city="Pune") != set()


def test_remote_filter(session, tags):
    remote = make_candidate(session, tags, full_name="Remote Ok", open_to_remote=True)
    onsite = make_candidate(session, tags, full_name="Onsite Only", open_to_remote=False)

    assert slugs(session, open_to_remote=True) == {remote.slug}
    assert slugs(session, open_to_remote=False) == {onsite.slug}
    assert slugs(session) == {remote.slug, onsite.slug}  # None means no filter


def test_years_range(session, tags):
    junior = make_candidate(session, tags, full_name="Junior Dev", years=2)
    mid = make_candidate(session, tags, full_name="Mid Dev", years=7)
    senior = make_candidate(session, tags, full_name="Senior Dev", years=15)

    assert slugs(session, min_years=5) == {mid.slug, senior.slug}
    assert slugs(session, max_years=7) == {junior.slug, mid.slug}
    assert slugs(session, min_years=5, max_years=10) == {mid.slug}


def test_inverted_years_range_is_swapped_not_empty(session, tags):
    mid = make_candidate(session, tags, full_name="Mid Dev", years=7)
    f = BrowseFilters(min_years=10, max_years=5)
    assert (f.min_years, f.max_years) == (5, 10)
    assert {c.slug for c in fetch_page(session, f)} == {mid.slug}


def test_gap_bucket_boundaries(session, tags):
    short = make_candidate(
        session, tags, full_name="Short Gap",
        gap_start=dt.date(2026, 1, 1), gap_end=dt.date(2026, 7, 1),      # 6 mo
    )
    mid = make_candidate(
        session, tags, full_name="Mid Gap",
        gap_start=dt.date(2024, 8, 1), gap_end=dt.date(2026, 8, 1),      # 24 mo
    )
    long = make_candidate(
        session, tags, full_name="Long Gap",
        gap_start=dt.date(2018, 8, 1), gap_end=dt.date(2026, 8, 1),      # 96 mo
    )

    assert slugs(session, gap_bucket=[GapBucket.UNDER_1Y]) == {short.slug}
    assert slugs(session, gap_bucket=[GapBucket.ONE_TO_3Y]) == {mid.slug}
    assert slugs(session, gap_bucket=[GapBucket.OVER_5Y]) == {long.slug}
    assert slugs(session, gap_bucket=[GapBucket.UNDER_1Y, GapBucket.OVER_5Y]) == {
        short.slug,
        long.slug,
    }


def test_ongoing_gap_is_measured_to_today(session, tags):
    ongoing = make_candidate(
        session, tags, full_name="Still Out",
        gap_start=dt.date(2025, 2, 20), gap_end=None,                    # 18 mo as of TODAY
    )
    assert ongoing.gap_months == 18
    assert slugs(session, gap_bucket=[GapBucket.ONE_TO_3Y]) == {ongoing.slug}


def test_notice_period_and_trial(session, tags):
    immediate = make_candidate(
        session, tags, full_name="Start Now", notice_days=0, open_to_trial=True
    )
    noticed = make_candidate(
        session, tags, full_name="Ninety Days", notice_days=90, open_to_trial=False
    )

    assert slugs(session, max_notice_days=30) == {immediate.slug}
    assert slugs(session, open_to_trial=True) == {immediate.slug}
    assert slugs(session, max_notice_days=120) == {immediate.slug, noticed.slug}


def test_role_sought_uses_the_normalised_slug(session, tags):
    backend = make_candidate(session, tags, full_name="Back End", role_slug="backend-engineer")
    make_candidate(session, tags, full_name="Data Person", role_slug="data-analyst")

    assert slugs(session, role_sought=["backend-engineer"]) == {backend.slug}


def test_filters_compose(session, tags):
    match = make_candidate(
        session, tags, full_name="Exact Match", city="Bangalore",
        tag_slugs=("python",), years=8, notice_days=15, open_to_trial=True,
        gap_start=dt.date(2024, 8, 1), gap_end=dt.date(2026, 8, 1),
    )
    make_candidate(
        session, tags, full_name="Wrong City", city="Chennai",
        tag_slugs=("python",), years=8, notice_days=15, open_to_trial=True,
        gap_start=dt.date(2024, 8, 1), gap_end=dt.date(2026, 8, 1),
    )

    assert slugs(
        session,
        tags=["python"],
        city="Bangalore",
        min_years=5,
        gap_bucket=[GapBucket.ONE_TO_3Y],
        max_notice_days=30,
        open_to_trial=True,
    ) == {match.slug}


def test_pagination_is_stable_and_total_is_unpaged(session, tags):
    for i in range(5):
        make_candidate(session, tags, full_name=f"Person {i}")

    f1 = BrowseFilters(page=1, page_size=2)
    f2 = BrowseFilters(page=2, page_size=2)
    f3 = BrowseFilters(page=3, page_size=2)

    assert count_matches(session, f1) == 5
    pages = [[c.slug for c in fetch_page(session, f)] for f in (f1, f2, f3)]
    assert [len(p) for p in pages] == [2, 2, 1]
    flat = [s for p in pages for s in p]
    assert len(set(flat)) == 5  # no repeats across pages, no dropped rows


def test_slug_normalisation_is_forgiving(session, tags):
    py = make_candidate(session, tags, full_name="Py Dev", tag_slugs=("python",))
    assert slugs(session, tags=["  PYTHON  "]) == {py.slug}


@pytest.mark.parametrize("bad", [{"page": 0}, {"page_size": 0}, {"page_size": 500}])
def test_paging_bounds_are_enforced(bad):
    with pytest.raises(ValidationError):
        BrowseFilters(**bad)
