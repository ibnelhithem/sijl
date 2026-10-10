"""Period presets: month arithmetic that clamps month ends, and `--last`, `--quarter`,
`--half`, `--year` resolved to exact inclusive days. "Today" is always passed in — never read
from the clock — so every expectation below is a fixed date."""

import calendar
import time
from datetime import date, timedelta

import pytest

from sijl import window
from sijl.window import NoPeriod, Window, add_months, parse_last, resolve


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2026, 8, 31), -6, date(2026, 2, 28)),  # no 31 February: the month's last day
        (date(2024, 8, 31), -6, date(2024, 2, 29)),  # a leap year keeps its leap day
        (date(2024, 2, 29), 12, date(2025, 2, 28)),  # a leap day a year on: no 29 Feb 2025
        (date(2024, 2, 29), -48, date(2020, 2, 29)),  # four years back: a leap day again
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
        (date(2026, 5, 31), 1, date(2026, 6, 30)),  # 30-day months clamp too
        (date(2026, 1, 15), -1, date(2025, 12, 15)),  # across a year boundary, backwards
        (date(2026, 11, 30), 3, date(2027, 2, 28)),  # and forwards
        (date(2026, 10, 8), -6, date(2026, 4, 8)),  # an ordinary day keeps its number
        (date(2026, 10, 8), 0, date(2026, 10, 8)),
    ],
)
def test_add_months_clamps_to_the_last_day_of_the_month(day, months, expected) -> None:
    assert add_months(day, months) == expected


def test_add_months_refuses_to_leave_the_calendar() -> None:
    with pytest.raises(ValueError):
        add_months(date(1, 3, 1), -3)


TODAY = date(2026, 10, 8)  # a Thursday in Q4 and H2 2026


def d(iso: str) -> date:
    return date.fromisoformat(iso)


@pytest.mark.parametrize(
    ("choice", "today", "start", "end", "label"),
    [
        # --last N units: N whole units that end today, today included.
        ({"last": "6m"}, TODAY, "2026-04-09", "2026-10-08", "the last 6 months"),
        ({"last": "6m"}, d("2026-08-31"), "2026-03-01", "2026-08-31", "the last 6 months"),
        ({"last": "6m"}, d("2024-08-31"), "2024-03-01", "2024-08-31", "the last 6 months"),
        ({"last": "1m"}, d("2026-03-31"), "2026-03-01", "2026-03-31", "the last 1 month"),
        ({"last": "1y"}, d("2025-02-28"), "2024-03-01", "2025-02-28", "the last 1 year"),
        ({"last": "1y"}, d("2024-02-29"), "2023-03-01", "2024-02-29", "the last 1 year"),
        ({"last": "1m"}, d("2026-02-28"), "2026-02-01", "2026-02-28", "the last 1 month"),
        ({"last": "1m"}, d("2024-02-29"), "2024-02-01", "2024-02-29", "the last 1 month"),
        ({"last": "1m"}, d("2024-03-28"), "2024-02-29", "2024-03-28", "the last 1 month"),
        ({"last": "3m"}, d("2026-06-30"), "2026-04-01", "2026-06-30", "the last 3 months"),
        ({"last": "1d"}, TODAY, "2026-10-08", "2026-10-08", "the last 1 day"),
        ({"last": "7d"}, TODAY, "2026-10-02", "2026-10-08", "the last 7 days"),
        ({"last": "2w"}, TODAY, "2026-09-25", "2026-10-08", "the last 2 weeks"),
        ({"last": "2Y"}, TODAY, "2024-10-09", "2026-10-08", "the last 2 years"),
        # Calendar quarters, halves and years: whole when over, cut at today while running.
        ({"quarter": "2026Q1"}, TODAY, "2026-01-01", "2026-03-31", "Q1 2026"),
        ({"quarter": "2024q1"}, TODAY, "2024-01-01", "2024-03-31", "Q1 2024"),
        ({"quarter": "2026Q3"}, TODAY, "2026-07-01", "2026-09-30", "Q3 2026"),
        ({"quarter": "2026Q3"}, d("2026-09-30"), "2026-07-01", "2026-09-30", "Q3 2026"),
        ({"quarter": "2026Q4"}, TODAY, "2026-10-01", "2026-10-08", "Q4 2026 to date"),
        ({"half": "2026H1"}, TODAY, "2026-01-01", "2026-06-30", "H1 2026"),
        ({"half": "2025H2"}, TODAY, "2025-07-01", "2025-12-31", "H2 2025"),
        ({"half": "2026H2"}, TODAY, "2026-07-01", "2026-10-08", "H2 2026 to date"),
        ({"year": 2024}, TODAY, "2024-01-01", "2024-12-31", "2024"),
        ({"year": 2026}, TODAY, "2026-01-01", "2026-10-08", "2026 to date"),
        # --since/--until: the days as given; --until defaults to today; no label.
        ({"since": "2026-04-06", "until": "2026-10-08"}, TODAY, "2026-04-06", "2026-10-08", ""),
        ({"since": "2026-04-06"}, TODAY, "2026-04-06", "2026-10-08", ""),
    ],
)
def test_every_preset_resolves_to_exact_inclusive_days(choice, today, start, end, label) -> None:
    window = resolve(today=today, **choice)
    assert (window.start, window.end, window.label) == (d(start), d(end), label)


def test_a_leap_day_is_inside_the_periods_that_contain_it() -> None:
    leap_year = resolve(today=TODAY, year=2024)
    assert leap_year.includes(date(2024, 2, 29))
    assert (leap_year.end - leap_year.start).days + 1 == 366
    assert resolve(today=TODAY, quarter="2024Q1").includes(date(2024, 2, 29))
    assert resolve(today=date(2024, 3, 28), last="1m").start == date(2024, 2, 29)
    assert resolve(today=date(2024, 2, 29), last="1y").includes(date(2024, 2, 29))


def test_an_unfinished_period_is_cut_at_today_and_labelled_to_date() -> None:
    running = resolve(today=TODAY, quarter="2026Q4")
    assert (running.end, running.to_date, running.name) == (TODAY, True, "Q4 2026")
    assert running.describe() == "1 October 2026 to 8 October 2026 — Q4 2026 to date"
    finished = resolve(today=TODAY, quarter="2026Q3")
    assert not finished.to_date
    assert finished.describe() == "1 July 2026 to 30 September 2026 — Q3 2026"
    plain = resolve(today=TODAY, since="2026-04-06")
    assert plain.describe() == "6 April 2026 to 8 October 2026"  # nothing to name


@pytest.mark.parametrize("choice", [{"quarter": "2027Q1"}, {"half": "2027H1"}, {"year": 2027}])
def test_a_period_that_has_not_started_is_refused(choice) -> None:
    with pytest.raises(ValueError, match=r"has not started yet \(today is 8 October 2026\)"):
        resolve(today=TODAY, **choice)


@pytest.mark.parametrize(
    ("choice", "message"),
    [
        ({"last": "6"}, "like 30d, 6w, 6m or 1y"),
        ({"last": "6 months"}, "like 30d, 6w, 6m or 1y"),
        ({"last": "-6m"}, "like 30d, 6w, 6m or 1y"),
        ({"last": "0m"}, "at least 1"),
        ({"last": "99999999999d"}, "before the year 1"),
        ({"last": "9999y"}, "before the year 1"),
        ({"quarter": "2026Q5"}, "like 2026Q3"),
        ({"quarter": "Q3 2026"}, "like 2026Q3"),
        ({"quarter": "0000Q1"}, "years 1970 to 9999"),
        ({"half": "2026H3"}, "like 2026H1 or 2026H2"),
        ({"year": 0}, "years 1970 to 9999"),
        ({"year": 10000}, "years 1970 to 9999"),
        # git cannot read dates before 1970, so no year before it can be asked for by name.
        ({"year": 1969}, "years 1970 to 9999"),
        ({"year": 1}, "years 1970 to 9999"),
        ({"quarter": "1969Q4"}, "years 1970 to 9999"),
        ({"quarter": "0001Q1"}, "years 1970 to 9999"),
        ({"half": "1969H2"}, "years 1970 to 9999"),
        ({"half": "0001H1"}, "years 1970 to 9999"),
        ({"since": "1969-12-31"}, "years 1970 to 9999"),
        ({"since": "1970-01-01", "until": "1969-12-31"}, "years 1970 to 9999"),
    ],
)
def test_malformed_presets_are_refused_with_the_expected_shape(choice, message) -> None:
    with pytest.raises(ValueError, match=message):
        resolve(today=TODAY, **choice)


def test_only_one_period_choice_at_a_time() -> None:
    with pytest.raises(ValueError, match="more than once"):
        resolve(today=TODAY, last="6m", quarter="2026Q3")
    with pytest.raises(ValueError, match="more than once"):
        resolve(today=TODAY, half="2026H1", until="2026-06-30")
    assert resolve(today=TODAY, since="2026-01-01", until="2026-06-30")  # one choice, two days


def test_no_period_at_all_is_its_own_error() -> None:
    with pytest.raises(NoPeriod):
        resolve(today=TODAY)
    with pytest.raises(NoPeriod):
        resolve(today=TODAY, until="2026-06-30")  # an end alone is not a period


def test_a_named_window_filters_like_any_other() -> None:
    window = resolve(today=TODAY, quarter="2026Q3")
    assert window.includes(date(2026, 7, 1)) and window.includes(date(2026, 9, 30))
    assert not window.includes(date(2026, 10, 1))
    assert window.git_since() == "2026-06-29T00:00:00"
    assert Window(date(2026, 7, 1), date(2026, 9, 30)).label == ""


def test_git_is_given_no_since_before_1970() -> None:
    # git 2.43 misreads any --since before the Unix epoch, so the prefilter is dropped when it
    # would reach before 2 January 1970 (a day of margin for timezones east of UTC).
    assert Window(date(1970, 1, 4), date(1970, 2, 1)).git_since() == "1970-01-02T00:00:00"
    assert Window(date(1970, 1, 3), date(1970, 2, 1)).git_since() is None
    assert Window(date(1970, 1, 1), date(1970, 2, 1)).git_since() is None
    hundred_years = resolve(today=TODAY, last="100y")
    assert hundred_years.start == date(1926, 10, 9)  # --last may reach before 1970
    assert hundred_years.git_since() is None


def month_ends(year: int) -> list[date]:
    return [date(year, m, calendar.monthrange(year, m)[1]) for m in range(1, 13)]


@pytest.mark.parametrize("year", [2024, 2026])  # a leap year and an ordinary one
def test_last_1m_run_on_every_month_end_covers_each_day_exactly_once(year) -> None:
    days: list[date] = []
    for end in month_ends(year):
        window = resolve(today=end, last="1m")
        days += [window.start + timedelta(n) for n in range((window.end - window.start).days + 1)]
    expected = [date(year, 1, 1) + timedelta(n) for n in range(366 if year == 2024 else 365)]
    assert days == expected  # in order, no day twice, none missing


@pytest.mark.parametrize(
    ("today", "last", "preset"),
    [
        (date(2026, 3, 31), "3m", {"quarter": "2026Q1"}),
        (date(2026, 6, 30), "3m", {"quarter": "2026Q2"}),
        (date(2026, 9, 30), "3m", {"quarter": "2026Q3"}),
        (date(2026, 6, 30), "6m", {"half": "2026H1"}),
        (date(2025, 12, 31), "6m", {"half": "2025H2"}),
        (date(2025, 12, 31), "1y", {"year": 2025}),
        (date(2024, 12, 31), "12m", {"year": 2024}),
    ],
)
def test_the_last_n_months_on_a_period_end_is_that_period(today, last, preset) -> None:
    by_length, by_name = resolve(today=today, last=last), resolve(today=today, **preset)
    assert (by_length.start, by_length.end) == (by_name.start, by_name.end)


def test_the_last_pattern_stays_linear_even_under_search() -> None:
    # Only ever matched whole (`--last 6m`), but without an anchor `search` would restart at every
    # digit of a long run and walk to its end: quadratic (the regex guard).
    for text in ("1" * 200_000, "1" * 200_000 + "x"):
        started = time.perf_counter()
        window._LAST.search(text)
        assert time.perf_counter() - started < 0.2
    assert parse_last("6M") == (6, "m") and window._LAST.fullmatch("x6m") is None
