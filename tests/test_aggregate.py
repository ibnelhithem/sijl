"""sijl.aggregate: one Insights model computed from the collapsed collection — totals
per repository, month, kind and review category, breaking changes, active days and the longest
streak with weekends neutral. Every expected number below is worked out by hand from the fixture.
"""

import inspect
from datetime import date
from pathlib import Path

import pytest

from sijl import classify
from sijl.aggregate import (
    Highlights,
    Insights,
    MonthTotal,
    Streak,
    Suggestion,
    TicketInsights,
    aggregate,
    longest_streak,
)
from sijl.classify import REVIEW_CATEGORIES, Conventional
from sijl.collect import Collection, RepoCommits
from sijl.git import GitRunner
from sijl.models import RawCommit, Reach, Repo
from sijl.normalize import collapse
from sijl.window import Window
from tests.gitfactory import raw_commit

# April to July 2026: July stays empty, so a month without commits must still be listed.
#      April 2026              May 2026               June 2026
# Mo Tu We Th Fr Sa Su   Mo Tu We Th Fr Sa Su   Mo Tu We Th Fr Sa Su
#        1  2  3  4  5                1  2  3    1  2  3  4  5  6  7
#  6  7  8  9 10 11 12    4  5  6  7  8  9 10    8  9 10 11 12 13 14
# 13 …            … 26   11 12 13 14 15 16 17   …
# 27 28 29 30            18 …
WINDOW = Window(date(2026, 4, 1), date(2026, 7, 31))

WEBAPP = Repo("web-tests", Path("/r/web-tests"), Path("/r/web-tests/.git"), False)
API = Repo("api", Path("/r/api"), Path("/r/api/.git"), False)
SCRATCH = Repo("scratch", Path("/r/scratch"), Path("/r/scratch/.git"), False)


def commit(n: int, at: str, subject: str, body: str = "") -> RawCommit:
    return raw_commit(at=at, sha=f"{n:040x}", subject=subject, body=body or f"{subject}\n")


def repo_commits(repo: Repo, counted=(), local_only=()) -> RepoCommits:
    reach = {c.sha: Reach.PUSHED for c in counted} | {c.sha: Reach.LOCAL for c in local_only}
    return RepoCommits(repo, tuple(counted), tuple(local_only), reach, has_remote=True)


def collection(*repos: RepoCommits) -> Collection:
    return Collection(repos=repos, skipped=(), merges_left_out=0, seen_in_other_clones=0)


# Twelve counted commits, numbered as in the comments of the expected values below.
WEBAPP_COMMITS = [
    commit(1, "2026-04-02T09:00:00+02:00", "feat(PROJ-1): billing flow"),  # Thu
    commit(2, "2026-04-02T15:00:00+02:00", "test: cover retry"),  # Thu again: same day
    commit(3, "2026-04-03T09:00:00+02:00", "fix(api)!: handle 404"),  # Fri, breaking (!)
    commit(4, "2026-04-04T11:00:00+02:00", "docs: readme"),  # Sat: active, not in a streak
    commit(5, "2026-04-06T09:00:00+02:00", "Add something"),  # Mon: no prefix at all
    commit(6, "2026-04-07T09:00:00+02:00", "ui: new header"),  # Tue: a team prefix, area "ui"
    commit(12, "2026-04-30T23:30:00-04:00", "perf: cache"),  # Thu 30 April in New York
]  # — already 1 May in UTC; the author's own day decides, so it is April's
FOOTER = "chore: bump deps\n\nBREAKING CHANGE: needs Python 3.12\n"
API_COMMITS = [
    commit(7, "2026-05-11T09:00:00+02:00", "chore: bump deps", FOOTER),  # Mon, breaking (footer)
    commit(8, "2026-05-12T09:00:00+02:00", "feat: search"),  # Tue
    commit(9, "2026-05-13T09:00:00+02:00", 'Revert "feat: search"'),  # Wed; Thu 14 is empty
    commit(10, "2026-05-15T09:00:00+02:00", "shell(zsh): faster prompt"),  # Fri, area "shell"
    commit(11, "2026-06-07T09:00:00+02:00", "feat: weekend idea"),  # Sun
]
NOT_PUSHED = commit(13, "2026-04-08T09:00:00+02:00", "feat: never pushed")  # Wed after run A
SCRATCH_ONLY = commit(14, "2026-05-14T09:00:00+02:00", "feat: experiment")  # Thu 14 May


def fixture() -> Collection:
    return collection(
        repo_commits(API, API_COMMITS),
        repo_commits(WEBAPP, WEBAPP_COMMITS, local_only=[NOT_PUSHED]),
        repo_commits(SCRATCH, local_only=[SCRATCH_ONLY]),
    )


SIMPLE = {"feat": "feature", "fix": "fix", "docs": "docs", "test": "quality", "perf": "quality",
          "chore": "tooling"}  # fmt: skip


def by_kind(commit: Conventional, repo: Repo) -> str:
    """A stand-in for the user's review_category(): only the kind decides."""
    return SIMPLE.get(commit.kind, "other")


def test_every_metric_equals_its_hand_computed_value() -> None:
    insights = aggregate(fixture(), WINDOW, categorise=by_kind)
    assert insights == Insights(
        total=12,  # 7 in web-tests + 5 in api; #13 and #14 are not pushed, so not counted
        active_days=11,  # Apr 2, 3, 4, 6, 7, 30 · May 11, 12, 13, 15 · Jun 7 (#1, #2 share Apr 2)
        per_repo=((WEBAPP, 7), (API, 5)),  # busiest first; scratch has nothing counted
        per_month=(
            MonthTotal("2026-04", commits=7, active_days=6),  # #1 to #6, and #12
            MonthTotal("2026-05", commits=4, active_days=4),  # #7 to #10
            MonthTotal("2026-06", commits=1, active_days=1),  # #11
            MonthTotal("2026-07", commits=0, active_days=0),  # inside the window, empty
        ),
        per_kind=(
            ("feat", 3),  # #1, #8, #11
            ("fix", 1),  # #3
            ("docs", 1),  # #4
            ("style", 0),
            ("refactor", 0),
            ("perf", 1),  # #12
            ("test", 1),  # #2
            ("build", 0),
            ("ci", 0),
            ("chore", 1),  # #7
            ("revert", 1),  # #9
            ("other", 3),  # #5 (no prefix), #6 (ui), #10 (shell)
        ),
        per_category=(
            ("feature", 3),  # feat
            ("fix", 1),
            ("quality", 2),  # test + perf
            ("docs", 1),
            ("tooling", 1),  # chore
            ("other", 4),  # revert + the 3 of kind other
        ),
        areas=(("shell", 1), ("ui", 1)),  # most first, then by name
        breaking=2,  # #3 by `!`, #7 by its footer
        non_conventional=1,  # #5
        category_failures=0,
        first_category_failure="",
        # Weekdays with commits: Apr 2, 3, 6, 7, 30 · May 11, 12, 13, 15 (Apr 4 and Jun 7 are a
        # Saturday and a Sunday). Runs: Apr 2 → 7 is 4 weekdays across the weekend of Apr 4 and 5;
        # Apr 30 is 1; May 11 → 13 is 3 and stops at the empty Thursday 14; May 15 is 1.
        longest_streak=Streak(weekdays=4, first=date(2026, 4, 2), last=date(2026, 4, 7)),
        not_pushed=2,  # #13 in web-tests, #14 in scratch
        delivered=0,  # the fixture has no merge requests (deliveries have their own tests)
        delivered_by_you=0,
        delivered_squashed=0,
        merged_for_others=0,
        quiet_repos=(SCRATCH,),  # read, but nothing counted there
        # no Jira project is set, so PROJ-1 (#1's scope) is a suggestion, not a ticket
        tickets=TicketInsights(suggestions=(Suggestion("PROJ", 1, 1, "PROJ-1"),), outside=1),
        # April has the most commits; no delivery, no ticket (no Jira project is set)
        highlights=Highlights(busiest_month=MonthTotal("2026-04", commits=7, active_days=6)),
    )


def test_every_counted_commit_is_in_exactly_one_bucket_of_each_breakdown() -> None:
    insights = aggregate(fixture(), WINDOW, categorise=by_kind)
    assert sum(n for _, n in insights.per_repo) == insights.total
    assert sum(m.commits for m in insights.per_month) == insights.total
    assert sum(m.active_days for m in insights.per_month) == insights.active_days
    assert sum(n for _, n in insights.per_kind) == insights.total
    assert sum(n for _, n in insights.per_category) == insights.total


def test_work_not_pushed_yet_counts_nowhere() -> None:
    """#13 (Wed 8 April) would stretch the 2 to 7 April streak to 5 weekdays if it counted."""
    with_it = collection(
        repo_commits(API, API_COMMITS), repo_commits(WEBAPP, [*WEBAPP_COMMITS, NOT_PUSHED])
    )
    assert aggregate(with_it, WINDOW, categorise=by_kind).longest_streak.weekdays == 5
    assert aggregate(fixture(), WINDOW, categorise=by_kind).longest_streak.weekdays == 4


def test_a_collapsed_copy_counts_once() -> None:
    original = commit(1, "2026-05-04T10:00:00+02:00", "feat: one piece of work")
    copy = raw_commit(
        at="2026-05-04T10:00:00+02:00", sha=f"{2:040x}", subject="feat: one piece of work",
        committer_date=original.committer_date.replace(day=6),  # a rebase: committed later
    )  # fmt: skip
    collapsed = collapse(collection(repo_commits(API, [original, copy])), GitRunner())
    insights = aggregate(collapsed, WINDOW, categorise=by_kind)
    assert (insights.total, dict(insights.per_kind)["feat"], insights.active_days) == (1, 1, 1)


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        # Thu, Fri, then Mon, Tue: the weekend between neither breaks nor extends the run
        (["2026-04-02", "2026-04-03", "2026-04-06", "2026-04-07"], (4, "2026-04-02", "2026-04-07")),
        # Mon, Tue, Wed, Fri: the empty Thursday ends the run
        (["2026-05-11", "2026-05-12", "2026-05-13", "2026-05-15"], (3, "2026-05-11", "2026-05-13")),
        # Fri, Sat, Sun, Mon: weekend commits add nothing — 2 weekdays, not 4
        (["2026-05-08", "2026-05-09", "2026-05-10", "2026-05-11"], (2, "2026-05-08", "2026-05-11")),
        # a Friday and the Monday a week later: one empty week in between
        (["2026-05-08", "2026-05-18"], (1, "2026-05-08", "2026-05-08")),
        # two runs of the same length: the earlier one is reported
        (["2026-05-04", "2026-05-05", "2026-05-11", "2026-05-12"], (2, "2026-05-04", "2026-05-05")),
        # one day listed twice is one day
        (["2026-05-04", "2026-05-04"], (1, "2026-05-04", "2026-05-04")),
    ],
)
def test_the_longest_streak_keeps_weekends_neutral(days: list[str], expected) -> None:
    weekdays, first, last = expected
    assert longest_streak(date.fromisoformat(d) for d in days) == Streak(
        weekdays, date.fromisoformat(first), date.fromisoformat(last)
    )


@pytest.mark.parametrize("days", [[], ["2026-05-09", "2026-05-10"]], ids=["none", "weekend"])
def test_no_weekday_with_commits_is_no_streak(days: list[str]) -> None:
    assert longest_streak(date.fromisoformat(d) for d in days) == Streak(0, None, None)


def test_nothing_counted_is_all_zeros_but_every_month_is_listed() -> None:
    insights = aggregate(collection(repo_commits(SCRATCH, local_only=[SCRATCH_ONLY])), WINDOW)
    assert (insights.total, insights.active_days, insights.per_repo) == (0, 0, ())
    assert [m.month for m in insights.per_month] == ["2026-04", "2026-05", "2026-06", "2026-07"]
    assert {n for _, n in (*insights.per_kind, *insights.per_category)} == {0}
    assert insights.longest_streak == Streak(0, None, None)


def test_months_run_across_a_new_year() -> None:
    window = Window(date(2025, 11, 20), date(2026, 2, 3))
    months = [m.month for m in aggregate(collection(), window).per_month]
    assert months == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_the_category_function_sees_the_parsed_commit_and_its_repository() -> None:
    seen: list[tuple[str, str, str, bool]] = []

    def spy(c: Conventional, repo: Repo) -> str:
        seen.append((repo.name, c.kind, c.area, c.breaking))
        return "other"

    aggregate(collection(repo_commits(API, API_COMMITS[:1] + API_COMMITS[3:4])), WINDOW,
              categorise=spy)  # fmt: skip
    assert seen == [("api", "chore", "", True), ("api", "other", "shell", False)]


def broken(c: Conventional, repo: Repo) -> str:
    if c.kind == "feat":
        raise KeyError("feat")
    if c.kind == "fix":
        return "bugs"  # not a category
    if c.kind == "docs":
        return None  # type: ignore[return-value]  # not even text
    return by_kind(c, repo)


def test_a_category_function_that_fails_puts_the_commit_under_other_and_counts_it() -> None:
    insights = aggregate(fixture(), WINDOW, categorise=broken)
    # 3 feat raise, 1 fix returns "bugs", 1 docs returns None: 5 failures, all "other"
    assert insights.category_failures == 5
    assert insights.first_category_failure == "raised KeyError: 'feat'"  # #8, api is read first
    assert dict(insights.per_category) == {
        "feature": 0, "fix": 0, "quality": 2, "docs": 0, "tooling": 1, "other": 9,
    }  # fmt: skip
    assert sum(n for _, n in insights.per_category) == insights.total


def raise_bare(c: Conventional, repo: Repo) -> str:
    raise ValueError


@pytest.mark.parametrize(
    ("categorise", "described"),
    [(lambda c, r: "bugs", "returned 'bugs'"), (lambda c, r: ["fix"], "returned ['fix']"),
     (raise_bare, "raised ValueError")],
)  # fmt: skip
def test_what_went_wrong_is_described(categorise, described: str) -> None:
    insights = aggregate(collection(repo_commits(API, [API_COMMITS[1]])), WINDOW,
                         categorise=categorise)  # fmt: skip
    assert (insights.category_failures, insights.first_category_failure) == (1, described)


def test_the_default_category_function_is_the_users_own() -> None:
    default = inspect.signature(aggregate).parameters["categorise"].default
    assert default is classify.review_category


def test_whatever_review_category_returns_every_commit_lands_in_one_category() -> None:
    """The user's function decides which category; whichever it is, nothing is lost."""
    insights = aggregate(fixture(), WINDOW)
    assert [name for name, _ in insights.per_category] == list(REVIEW_CATEGORIES)
    assert sum(n for _, n in insights.per_category) == 12


def test_months_after_today_are_never_listed() -> None:
    """A window that runs past today (`--until 9999-12-31`) lists months up to today only:
    nothing can have been written in a month that has not begun."""
    window = Window(date(2026, 4, 1), date(9999, 12, 31))
    months = [m.month for m in aggregate(collection(), window, today=date(2026, 5, 4)).per_month]
    assert months == ["2026-04", "2026-05"]


def test_a_window_from_1970_lists_every_month_up_to_today() -> None:
    window = Window(date(1970, 1, 1), date(2026, 6, 30))
    months = [m.month for m in aggregate(collection(), window, today=date(2026, 5, 31)).per_month]
    assert (months[0], months[-1], len(months)) == ("1970-01", "2026-05", 56 * 12 + 5)


def test_a_commit_dated_after_today_still_has_its_month() -> None:
    """A clock that ran fast can date a commit in the future: its month is listed, never lost."""
    late = commit(1, "2026-08-03T09:00:00+02:00", "feat: from the future")
    insights = aggregate(collection(repo_commits(API, [late])), WINDOW, today=date(2026, 5, 4))
    assert [m.month for m in insights.per_month] == ["2026-04", "2026-05", "2026-08"]


class Unspeakable(Exception):
    def __str__(self) -> str:
        raise RuntimeError("str failed")


class Unshowable:
    def __repr__(self) -> str:
        raise RuntimeError("repr failed")


class Touchy(str):
    def __eq__(self, other: object) -> bool:
        raise RuntimeError("eq failed")

    __hash__ = str.__hash__


def raise_unspeakable(c: Conventional, repo: Repo) -> str:
    raise Unspeakable


@pytest.mark.parametrize(
    ("categorise", "category", "described"),
    [
        (raise_unspeakable, "other", "raised Unspeakable (its message cannot be shown)"),
        (lambda c, r: Unshowable(), "other", "returned a Unshowable that cannot be shown"),
        (lambda c, r: Touchy("fix"), "fix", ""),  # the text is a category: it counts
        (lambda c, r: Touchy("bugs"), "other", "returned 'bugs'"),
    ],
)  # fmt: skip
def test_the_safety_net_holds_whatever_the_function_returns_or_raises(
    categorise, category: str, described: str
) -> None:
    """A category function's answer, or its exception, can itself fail when Sijl describes
    it; the commit is still counted, and what went wrong is still said."""
    insights = aggregate(collection(repo_commits(API, [API_COMMITS[1]])), WINDOW,
                         categorise=categorise)  # fmt: skip
    assert dict(insights.per_category)[category] == 1
    assert insights.first_category_failure == described
