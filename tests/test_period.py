import argparse
from datetime import datetime, timedelta

import pytest

from conftest import cu, record, write_transcript


def args(since=None, until=None, last=None):
    return argparse.Namespace(since=since, until=until, last=last)


@pytest.fixture
def today(monkeypatch):
    """Pin the script's datetime.now() to 2026-10-05 15:30 local time."""
    class Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 5, 15, 30)
    monkeypatch.setattr(cu, "datetime", Pinned)


@pytest.mark.parametrize("spec, days", [("7d", 7), ("4w", 28), ("3m", 90), ("0d", 0)])
def test_last_delta(spec, days):
    assert cu.last_delta(spec) == timedelta(days=days)


@pytest.mark.parametrize("spec", ["7", "d", "7y", "-1d", "1.5d", "", None])
def test_last_delta_rejects_other_specs(spec):
    with pytest.raises(SystemExit, match="--last expects"):
        cu.last_delta(spec)


def test_parse_date_rejects_other_formats():
    with pytest.raises(SystemExit, match="dates must be YYYY-MM-DD, got '05.10.2026'"):
        cu.parse_date("05.10.2026")


def test_until_is_inclusive_so_the_bound_is_the_next_midnight(today):
    assert cu.period_bounds(args(since="2026-09-01", until="2026-09-30")) == (
        datetime(2026, 9, 1), datetime(2026, 10, 1))


def test_last_counts_back_from_this_midnight(today):
    assert cu.period_bounds(args(last="7d")) == (datetime(2026, 9, 28), None)


def test_since_wins_over_last(today):
    assert cu.period_bounds(args(since="2026-01-01", last="7d"))[0] == datetime(2026, 1, 1)


def test_no_bounds_means_everything(today):
    assert cu.period_bounds(args()) == (None, None)


@pytest.mark.parametrize("tz, utc", [("UTC", "2026-09-15T00:00:00"), ("Europe/Berlin", "2026-09-14T22:00:00"),
                                     ("America/New_York", "2026-09-15T04:00:00")])
def test_local_midnight_converts_to_utc(local_tz, tz, utc):
    local_tz(tz)
    assert cu.to_utc_iso(datetime(2026, 9, 15)) == utc


def test_in_period_includes_since_and_excludes_until():
    r = {"t": "2026-09-15T00:00:00.000Z"}
    assert cu.in_period(r, "2026-09-15T00:00:00", None)
    assert not cu.in_period(r, None, "2026-09-15T00:00:00")
    assert cu.in_period(r, None, None)


@pytest.mark.parametrize("tz, day", [("UTC", "2026-09-14"), ("Europe/Berlin", "2026-09-15")])
def test_day_buckets_use_the_local_timezone(local_tz, tz, day):
    local_tz(tz)
    assert cu.GROUPINGS["day"][1]({"t": "2026-09-14T23:30:00.000Z"}) == day


@pytest.mark.parametrize("t, week", [
    ("2026-01-01T12:00:00.000Z", "2026-W01"),
    ("2027-01-01T12:00:00.000Z", "2026-W53"),
    ("2025-12-29T12:00:00.000Z", "2026-W01"),
])
def test_week_buckets_use_iso_weeks(local_tz, t, week):
    local_tz("UTC")
    assert cu.GROUPINGS["week"][1]({"t": t}) == week


def test_unparsable_timestamp_lands_in_unknown():
    assert cu.GROUPINGS["month"][1]({"t": "yesterday"}) == "unknown"


def test_report_filters_by_local_day(report, projects_dir, local_tz):
    local_tz("Europe/Berlin")
    write_transcript(projects_dir, "a.jsonl", [
        record(msg_id="before", ts="2026-09-14T21:59:59.000Z"),
        record(msg_id="first", ts="2026-09-14T22:00:00.000Z"),
        record(msg_id="last", ts="2026-09-15T21:59:59.000Z"),
        record(msg_id="after", ts="2026-09-15T22:00:00.000Z"),
    ])
    _, doc, _ = report("--no-warehouse", "--since", "2026-09-15", "--until", "2026-09-15", "--by", "day")
    assert doc["totals"]["calls"] == 2
    assert doc["period"] == {"since": "2026-09-15", "until_inclusive": "2026-09-15"}
    assert list(doc["by"]["day"]) == ["2026-09-15"]
