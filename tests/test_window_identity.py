"""The two rules every commit passes: inside the period by its author day (the calendar day in
the author's own timezone), and authored by one of your exact emails — never a name match."""

from dataclasses import replace
from datetime import date

import pytest

from sijl.identity import IdentitySet
from sijl.window import Window, parse_day
from tests.gitfactory import LOOKALIKE, ME, ME_ALT_NAME, REVIEW_PERIOD, raw_commit


def day_of(iso: str) -> date:
    return raw_commit(at=iso).author_day


def test_both_ends_of_the_window_are_inclusive() -> None:
    assert REVIEW_PERIOD.includes(date(2026, 4, 1))
    assert REVIEW_PERIOD.includes(date(2026, 6, 30))
    assert not REVIEW_PERIOD.includes(date(2026, 7, 1))


def test_the_day_is_the_author_s_own_day_not_utc() -> None:
    # 23:30 on 31 March in New York is already 1 April in UTC: it still belongs to March.
    assert day_of("2026-03-31T23:30:00-05:00") == date(2026, 3, 31)
    # 00:30 on 1 April, two hours ahead of UTC, is still 31 March in UTC: it belongs to April.
    assert day_of("2026-04-01T00:30:00+02:00") == date(2026, 4, 1)


def test_git_is_asked_from_midnight_two_days_early() -> None:
    # An explicit time: git fills a bare date's time with the current time of day.
    assert REVIEW_PERIOD.git_since() == "2026-03-30T00:00:00"


def test_an_inverted_window_is_refused() -> None:
    with pytest.raises(ValueError, match="after"):
        Window(date(2026, 7, 1), date(2026, 6, 30))


def test_days_are_parsed_strictly() -> None:
    assert parse_day("2026-04-06") == date(2026, 4, 6)
    # date.fromisoformat alone accepts the compact and ISO-week forms: 2026-W14-1 is 30 March.
    for loose in ("06/04/2026", "20260406", "2026-W14-1", "2026-4-6", "2026-02-30"):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            parse_day(loose)


def test_exact_emails_match_case_insensitively() -> None:
    me = IdentitySet.from_emails([ME.email.upper()])
    assert me.matches(raw_commit(who=ME))
    assert me.matches(raw_commit(who=ME_ALT_NAME))  # another name, the same email


def test_a_look_alike_name_never_matches() -> None:
    me = IdentitySet.from_emails([ME.email])
    assert not me.matches(raw_commit(who=LOOKALIKE))
    assert not me.matches(raw_commit(who=LOOKALIKE, author_name=ME.name))  # even your exact name


def test_the_raw_or_the_mailmapped_email_may_match() -> None:
    me = IdentitySet.from_emails([ME.email])
    remapped = raw_commit(author_email_raw="old@acme.test")
    assert me.matches(remapped)
    assert me.matches(replace(remapped, author_email_raw=ME.email, author_email="x@y.test"))


def test_at_least_one_email_is_required() -> None:
    with pytest.raises(ValueError, match="email"):
        IdentitySet.from_emails(["  "])
