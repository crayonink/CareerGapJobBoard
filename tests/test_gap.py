"""Gap arithmetic and bucketing."""

from __future__ import annotations

import datetime as dt

import pytest

from app.enums import GapBucket
from app.gap import bucket_for, format_length, months_between

TODAY = dt.date(2026, 8, 20)


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (dt.date(2025, 8, 1), dt.date(2026, 8, 1), 12),
        (dt.date(2025, 8, 15), dt.date(2026, 8, 14), 11),   # a day short of a year
        (dt.date(2026, 8, 1), dt.date(2026, 8, 1), 0),
        (dt.date(2020, 1, 31), dt.date(2020, 3, 1), 1),     # month-end rollover
    ],
)
def test_months_between_closed_gap(start, end, expected):
    assert months_between(start, end) == expected


def test_ongoing_gap_measures_to_today():
    assert months_between(dt.date(2025, 2, 20), None, TODAY) == 18


def test_gap_cannot_go_negative():
    assert months_between(dt.date(2026, 12, 1), None, TODAY) == 0


@pytest.mark.parametrize(
    "months, bucket",
    [
        (0, GapBucket.UNDER_1Y),
        (11, GapBucket.UNDER_1Y),
        (12, GapBucket.ONE_TO_3Y),     # boundaries are [low, high)
        (35, GapBucket.ONE_TO_3Y),
        (36, GapBucket.THREE_TO_5Y),
        (59, GapBucket.THREE_TO_5Y),
        (60, GapBucket.OVER_5Y),
        (400, GapBucket.OVER_5Y),
    ],
)
def test_bucket_boundaries(months, bucket):
    assert bucket_for(months) is bucket


@pytest.mark.parametrize(
    "months, label",
    [(0, "0 mo"), (5, "5 mo"), (12, "1 yr"), (28, "2 yr 4 mo")],
)
def test_format_length(months, label):
    assert format_length(months) == label
