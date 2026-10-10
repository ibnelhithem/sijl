"""sijl.deliveries, GitHub's titled merges: a squash merge is a one-parent commit GitHub
itself committed whose subject ends `(#N)`; a merge commit GitHub committed with that subject is a
pull request merged with its title as the message. A trailing `(#N)` alone is just an issue
reference. Pure functions over a commit; no git."""

import time
from datetime import datetime

import pytest

from sijl.deliveries import Request, parse_request, parse_squash
from sijl.models import GITHUB
from tests.gitfactory import GITHUB_WEB, ME, ME_GITHUB, raw_commit

ONE_PARENT = ("1" * 40,)
TWO_PARENTS = ("1" * 40, "2" * 40)


def by_github(subject: str, *, parents=ONE_PARENT, committer=GITHUB_WEB, body: str = ""):
    """A commit authored by your GitHub identity and committed by `committer`."""
    return raw_commit(
        who=ME_GITHUB,
        parents=parents,
        committer_name=committer.name,
        committer_email=committer.email,
        subject=subject,
        body=f"{subject}\n\n{body}" if body else f"{subject}\n",
    )


def test_a_github_squash_gives_its_pull_request_number_and_title() -> None:
    squash = by_github("Fix a typo (#12)", body="* fix: one\n\n* fix: two")
    assert parse_squash(squash) == Request(GITHUB, 12, "Fix a typo", "", ())


def test_the_last_number_is_the_pull_request_and_an_earlier_one_stays_in_the_title() -> None:
    # A pull request titled after its issue: GitHub appends its own number after the title's.
    request = parse_squash(by_github("Search, part 2: the thread model (#738) (#784)"))
    assert (request.number, request.title) == (784, "Search, part 2: the thread model (#738)")


@pytest.mark.parametrize(
    "commit",
    [
        raw_commit(who=ME, subject="fix: the typo (#12)"),  # yours, committed by you: an issue
        by_github("Fix a typo (#12)", parents=TWO_PARENTS),  # a merge commit, not a squash
        by_github("Fix a typo (#12)", parents=()),  # a first commit has no parent
        by_github("Update README.md"),  # GitHub's web editor, no pull request
        by_github("Fix a typo (#12) and more"),  # the number is not at the end
        by_github("Fix a typo (#0)"),
        by_github("Fix a typo (#1234567890)"),  # ten digits: no pull request number
        by_github("Fix a typo #12"),
        by_github("Fix a typo (#12"),
        by_github("Fix a typo (# 12)"),
        by_github("Fix a typo (#12) x"),
        by_github("Fix a typo (#012)"),  # GitHub never writes a leading zero
        by_github("first line\nFix a typo (#12)"),  # git's %s never holds a newline
    ],
)
def test_a_trailing_number_alone_is_not_a_squash(commit) -> None:
    assert parse_squash(commit) is None


@pytest.mark.parametrize(
    ("subject", "number", "title"),
    [
        ("(#12)", 12, ""),  # no title at all: still GitHub's shape
        ("x(#12)", 12, "x"),
        ("Fix\t (#7)\t \r", 7, "Fix"),  # spacing around the number is not the title
        ("  Fix (#123456789)  ", 123456789, "Fix"),  # nine digits at most
        ("(#1) (#2)", 2, "(#1)"),
        ("Fix (#1)(#2)", 2, "Fix (#1)"),
    ],
)
def test_edges_of_the_number_and_the_title(subject: str, number: int, title: str) -> None:
    request = parse_squash(by_github(subject))
    assert (request.number, request.title) == (number, title)


@pytest.mark.parametrize(
    "subject",
    [
        " " * 1_000_000 + "x",
        "\t" * 1_000_000 + "x",
        "(#1 " * 250_000,
        "(" * 1_000_000,
        "(#1)" + " " * 1_000_000 + "x",
        "x" + " \t" * 500_000 + "(#1",
    ],
    ids=["spaces", "tabs", "unclosed-numbers", "parentheses", "number-then-spaces", "mixed"],
)
def test_a_crafted_subject_is_read_in_linear_time(subject: str) -> None:
    # A subject is anyone's text. `.*?` before `[ \t]*` once made a long run of spaces quadratic:
    # a 1 MB subject would have stalled the report for half an hour.
    for commit in (by_github(subject), by_github(subject, parents=TWO_PARENTS)):
        started = time.perf_counter()
        assert parse_squash(commit) is None and parse_request(commit) is None
        assert time.perf_counter() - started < 0.1


def test_a_squash_is_stamped_at_merge_time_and_a_rebase_merge_is_not() -> None:
    # GitHub stamps a squash (and its merge commits) with one instant as author and committer
    # date. "Rebase and merge" keeps the original author date — so a
    # rebased `fix: x (#30)` is the commit you wrote, referencing issue #30, not a pull request.
    rebased = by_github("fix: handle empty input (#30)")
    rebased = rebased.__class__(
        **{**vars(rebased), "author_date": datetime.fromisoformat("2026-05-01T09:00:00+02:00")}
    )
    assert parse_squash(rebased) is None
    merged = rebased.__class__(**{**vars(rebased), "parents": TWO_PARENTS})
    assert parse_request(merged) is None
    same_instant = rebased.__class__(
        **{**vars(rebased), "author_date": rebased.committer_date.astimezone()}
    )
    assert parse_squash(same_instant).number == 30  # one instant, written in another offset


def test_githubs_address_is_compared_without_case() -> None:
    shouting = GITHUB_WEB.__class__("GitHub", "NoReply@GitHub.com")
    assert parse_squash(by_github("Fix (#3)", committer=shouting)).number == 3


def test_a_merge_titled_like_a_squash_is_a_pull_request_when_github_committed_it() -> None:
    merge = by_github("Fix a typo (#15)", parents=TWO_PARENTS)
    assert parse_request(merge) == Request(GITHUB, 15, "Fix a typo", "", ())
    assert parse_request(by_github("Fix a typo (#15)")) is None  # one parent: a squash, not this
    by_hand = raw_commit(who=ME, parents=TWO_PARENTS, subject="Merge fix (#15)")
    assert parse_request(by_hand) is None  # a merge you made that mentions an issue


def test_githubs_default_merge_message_still_wins() -> None:
    text = "Merge pull request #12 from alexgh/fix/typo"
    merge = by_github(text, parents=TWO_PARENTS, body="Fix a typo (#99)")
    assert parse_request(merge) == Request(GITHUB, 12, "Fix a typo (#99)", "fix/typo", ())
