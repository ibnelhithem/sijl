"""The review period: inclusive calendar days, judged by each commit's author day.

`git log --since/--until` filter on the *committer* date, which every rebase and cherry-pick
resets. So git only gets `--since`, from midnight two days early, as a cheap prefilter (a
committer date is never earlier than its author date in practice), and the real decision is
made here, on `RawCommit.author_day`. git misreads any `--since` before the Unix
epoch, so a period reaching before 1970 gets no prefilter at all, and no year before 1970 can be
asked for by name.

A period is chosen exactly one way: `--since`/`--until`, `--last 6m` (six whole
months that end today, today included; run on month ends, such periods tile), `--quarter
2026Q3`, `--half 2026H1` or `--year 2026`. A quarter, half or year that is still running stops
at today and is labelled "to date".
"Today" is always an argument — only the CLI reads the clock — so every rule here is testable.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

PREFILTER_SLACK = timedelta(days=2)
# git 2.43 reads a --since before the Unix epoch as a recent date (1969-12-30 became 2012-12-29),
# so no --since earlier than this is ever passed; a day of margin for timezones east of UTC.
GIT_SINCE_FLOOR = date(1970, 1, 2)
FIRST_YEAR, LAST_YEAR = 1970, 9999  # the years a day, quarter, half or year may name
ONE_DAY = timedelta(days=1)
# Written beside the window's days wherever a period is shown: the report header, config show.
COUNTING_RULE = "inclusive; by author date, in each commit's own timezone"
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")  # fromisoformat alone also takes 20260406 and 2026-W14-1
# `\A`: only ever matched whole; unanchored, a `search` would restart at every digit of a long
# run and walk to its end — quadratic (the regex guard, tests/test_regex_budget.py).
_LAST = re.compile(r"\A(\d+)([dwmy])", re.IGNORECASE)
_QUARTER = re.compile(r"(\d{4})Q([1-4])", re.IGNORECASE)
_HALF = re.compile(r"(\d{4})H([12])", re.IGNORECASE)
_UNITS = {"d": "day", "w": "week", "m": "month", "y": "year"}


class NoPeriod(ValueError):
    """No period was chosen at all — an end day alone is not one."""


def parse_day(text: str) -> date:
    if _DAY.fullmatch(text):
        try:
            day = date.fromisoformat(text)
        except ValueError:
            pass  # e.g. 2026-02-30: the right shape, not a real day
        else:
            check_year(day.year)
            return day
    raise ValueError(f"{text!r} is not a day in YYYY-MM-DD form")


def long_day(day: date) -> str:
    """'8 October 2026': how the report and `config show` write a day."""
    return f"{day.day} {day.strftime('%B')} {day.year}"


def check_year(year: int) -> int:
    if not FIRST_YEAR <= year <= LAST_YEAR:
        raise ValueError(
            f"{year} is outside the years {FIRST_YEAR} to {LAST_YEAR} "
            "(git cannot read dates before 1970)"
        )
    return year


def parse_last(text: str) -> tuple[int, str]:
    """'6m' -> (6, 'm'): how many days (d), weeks (w), months (m) or years (y)."""
    match = _LAST.fullmatch(text)
    if not match:
        raise ValueError(f"{text!r} is not a length like 30d, 6w, 6m or 1y")
    count, unit = int(match[1]), match[2].lower()
    if count < 1:
        raise ValueError(f"{text!r} is an empty period: the number must be at least 1")
    return count, unit


def parse_quarter(text: str) -> tuple[int, int]:
    """'2026Q3' -> (2026, 3)."""
    match = _QUARTER.fullmatch(text)
    if not match:
        raise ValueError(f"{text!r} is not a quarter like 2026Q3")
    return check_year(int(match[1])), int(match[2])


def parse_half(text: str) -> tuple[int, int]:
    """'2026H1' -> (2026, 1): H1 is January to June, H2 July to December."""
    match = _HALF.fullmatch(text)
    if not match:
        raise ValueError(f"{text!r} is not a half year like 2026H1 or 2026H2")
    return check_year(int(match[1])), int(match[2])


def add_months(day: date, months: int) -> date:
    """The same day `months` later (earlier when negative), clamped to that month's last day:
    31 August minus 6 months is 28 February — 29 in a leap year — never 2 or 3 March.
    Raises ValueError outside the years 1 to 9999."""
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    month += 1  # divmod counts months from 0
    if not 1 <= year <= 9999:
        raise ValueError(f"{months:+} months from {day} leaves the calendar (years 1 to 9999)")
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class Window:
    start: date  # first day, inclusive
    end: date  # last day, inclusive
    name: str = ""  # the preset it came from, "the last 6 months" or "Q3 2026"; "" for --since
    to_date: bool = False  # a quarter, half or year still running, so it stops at today

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"the period starts {self.start}, after it ends ({self.end})")

    @property
    def label(self) -> str:
        """'Q4 2026 to date', 'the last 6 months', or '' for a --since/--until period."""
        return f"{self.name} to date" if self.to_date else self.name

    def describe(self) -> str:
        """'1 October 2026 to 8 October 2026 — Q4 2026 to date': the days, then the label."""
        days = f"{long_day(self.start)} to {long_day(self.end)}"
        return f"{days} — {self.label}" if self.label else days

    def includes(self, day: date) -> bool:
        return self.start <= day <= self.end

    def git_since(self) -> str | None:
        """git's prefilter: midnight two days before the start, or None — read all history —
        when that would reach before GIT_SINCE_FLOOR, which git would misread."""
        since = self.start - PREFILTER_SLACK
        if since < GIT_SINCE_FLOOR:
            return None
        # An explicit midnight: git fills a bare date's time of day with the current time.
        return f"{since.isoformat()}T00:00:00"


def resolve(
    *,
    today: date,
    since: str = "",
    until: str = "",
    last: str = "",
    quarter: str = "",
    half: str = "",
    year: int | None = None,
) -> Window:
    """The period, chosen exactly one way, as inclusive days. `today` ends a `last` period,
    cuts a quarter, half or year that is still running, and is the default `until`."""
    presets = {"last": last, "quarter": quarter, "half": half, "year": year}
    chosen = [name for name, value in presets.items() if value not in ("", None)]
    if since or until:
        chosen.insert(0, "since/until")
    if len(chosen) > 1:
        raise ValueError(f"the period is chosen more than once ({' and '.join(chosen)})")
    if last:
        return _last(last, today)
    if quarter:
        y, q = parse_quarter(quarter)
        return _calendar(f"Q{q} {y}", date(y, 3 * q - 2, 1), _month_end(y, 3 * q), today)
    if half:
        y, h = parse_half(half)
        return _calendar(f"H{h} {y}", date(y, 6 * h - 5, 1), _month_end(y, 6 * h), today)
    if year is not None:
        y = check_year(year)
        return _calendar(str(y), date(y, 1, 1), date(y, 12, 31), today)
    if not since:
        raise NoPeriod("the period has no first day")
    return Window(parse_day(since), parse_day(until) if until else today)


def _last(text: str, today: date) -> Window:
    """N whole days, weeks, months or years that end today, today included, so `1d` is today
    alone and `6m` on 8 October starts on 9 April. Days and weeks: go back N units, then forward
    one day. Months and years: go back N months from *tomorrow* — so on a month's last day the
    period starts on the 1st (`3m` on 30 June is exactly Q2) and runs on month ends tile."""
    count, unit = parse_last(text)
    name = f"the last {count} {_UNITS[unit]}{'' if count == 1 else 's'}"
    try:
        if unit in ("d", "w"):
            start = today - timedelta(days=count * (7 if unit == "w" else 1)) + ONE_DAY
        else:
            start = add_months(today + ONE_DAY, -count * (12 if unit == "y" else 1))
    except (OverflowError, ValueError):
        raise ValueError(f"{name} reaches back before the year 1") from None
    return Window(start, today, name=name)


def _calendar(name: str, start: date, end: date, today: date) -> Window:
    """A calendar quarter, half or year: whole once it is over, cut at today while it runs."""
    if start > today:
        raise ValueError(f"{name} has not started yet (today is {long_day(today)})")
    if end > today:
        return Window(start, today, name=name, to_date=True)
    return Window(start, end, name=name)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])
