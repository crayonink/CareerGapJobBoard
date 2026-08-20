"""Gap arithmetic.

`gap_start` / `gap_end` are dates the candidate enters. `gap_months` is derived
and stored so that bucket filtering is an indexed integer range scan instead of
a date-diff expression SQLite cannot use an index for.

Derived means something has to keep it honest - see derive.nightly_refresh.
"""

from __future__ import annotations

import datetime as dt

from .enums import GAP_BUCKET_MONTHS, GapBucket


def months_between(start: dt.date, end: dt.date | None, today: dt.date | None = None) -> int:
    """Whole months from `start` to `end`, or to today if the gap is ongoing."""
    effective_end = end or today or dt.date.today()
    months = (effective_end.year - start.year) * 12 + (effective_end.month - start.month)
    if effective_end.day < start.day:
        months -= 1
    return max(0, months)


def bucket_for(gap_months: int) -> GapBucket:
    for bucket, (low, high) in GAP_BUCKET_MONTHS.items():
        if gap_months >= low and (high is None or gap_months < high):
            return bucket
    return GapBucket.OVER_5Y


def format_length(gap_months: int) -> str:
    """Human phrasing for the profile card: "2 yr 4 mo"."""
    years, months = divmod(gap_months, 12)
    if years and months:
        return f"{years} yr {months} mo"
    if years:
        return f"{years} yr"
    return f"{months} mo"
