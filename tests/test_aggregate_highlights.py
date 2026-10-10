"""sijl.aggregate: the Highlights — the work named first in the report, picked by a fixed,
documented rule: the busiest month, your merge requests and pull
requests with the most commits of yours, and the tickets named in the most commits. Like every
other number they live in the one Insights model, so every renderer shows the same ones."""

from dataclasses import replace
from datetime import date

from sijl.aggregate import HIGHLIGHTS_SHOWN, Highlights, MonthTotal, aggregate
from sijl.models import AUTHOR, BOTH, GITHUB, GITLAB, MERGER, Delivery
from tests.gitfactory import raw_commit
from tests.test_aggregate import API, WEBAPP, WINDOW, collection, commit, fixture, repo_commits


def delivery(number: int, day: str, yours: int, *, role: str = AUTHOR, forge: str = GITLAB):
    merge = raw_commit(at=f"{day}T12:00:00+02:00", sha=f"{number:040x}")
    return Delivery(forge, number, f"Request {number}", "", (), merge, role, yours + 1, yours)


def test_the_busiest_month_is_the_one_with_the_most_commits() -> None:
    insights = aggregate(fixture(), WINDOW)
    assert insights.highlights.busiest_month == MonthTotal("2026-04", commits=7, active_days=6)


def test_a_tie_between_months_goes_to_the_earlier_one() -> None:
    commits = [
        commit(1, "2026-05-04T10:00:00+02:00", "feat: may"),
        commit(2, "2026-04-06T10:00:00+02:00", "feat: april"),
    ]
    insights = aggregate(collection(repo_commits(API, commits)), WINDOW)
    assert insights.highlights.busiest_month == MonthTotal("2026-04", commits=1, active_days=1)


def test_nothing_counted_highlights_nothing() -> None:
    assert aggregate(collection(repo_commits(API)), WINDOW).highlights == Highlights()


def test_the_largest_deliveries_are_yours_with_the_most_commits_of_yours() -> None:
    api = repo_commits(API, [commit(1, "2026-05-04T10:00:00+02:00", "feat: x")])
    webapp = repo_commits(WEBAPP, [commit(2, "2026-05-05T10:00:00+02:00", "feat: y")])
    small = delivery(1, "2026-05-06", 1)
    large = delivery(2, "2026-05-07", 9, role=BOTH)
    for_others = delivery(3, "2026-05-08", 0, role=MERGER)  # yours=0: merged for a colleague
    tie_old = delivery(4, "2026-05-01", 4)
    tie_new = delivery(5, "2026-05-09", 4, forge=GITHUB)
    api = replace(api, deliveries=(for_others, large, small))
    webapp = replace(webapp, deliveries=(tie_new, tie_old))
    insights = aggregate(collection(api, webapp), WINDOW)
    # most commits of yours first; on a tie the newer; never one merged for others; three at most
    assert HIGHLIGHTS_SHOWN == 3
    assert insights.highlights.deliveries == ((API, large), (WEBAPP, tie_new), (WEBAPP, tie_old))


def test_a_tie_on_commits_and_day_is_decided_by_repository_then_number() -> None:
    first, second = delivery(7, "2026-05-06", 2), delivery(8, "2026-05-06", 2)
    api = repo_commits(API, [commit(1, "2026-05-04T10:00:00+02:00", "feat: x")])
    webapp = repo_commits(WEBAPP, [commit(2, "2026-05-05T10:00:00+02:00", "feat: y")])
    api = replace(api, deliveries=(second,))
    webapp = replace(webapp, deliveries=(first,))
    insights = aggregate(collection(webapp, api), WINDOW)
    assert insights.highlights.deliveries == ((API, second), (WEBAPP, first))  # "api" < "webapp"


def test_the_most_active_tickets_are_the_first_of_the_ticket_table() -> None:
    commits = [
        commit(n, f"2026-05-{n:02d}T10:00:00+02:00", subject)
        for n, subject in enumerate(
            ["feat(PROJ-1): a", "fix(PROJ-2): b", "fix(PROJ-2): c", "feat(PROJ-3): d",
             "feat(PROJ-4): e", "feat(PROJ-4): f", "feat(PROJ-4): g"],
            start=4,
        )
    ]  # fmt: skip
    insights = aggregate(collection(repo_commits(API, commits)), WINDOW, projects=["PROJ"])
    keys = [t.key for t in insights.highlights.tickets]
    assert keys == ["PROJ-4", "PROJ-2", "PROJ-1"]  # most commits; a tie in key order
    assert insights.highlights.tickets == insights.tickets.per_ticket[:3]


def test_highlights_change_no_other_number() -> None:
    insights = aggregate(fixture(), WINDOW)
    assert insights.total == 12 and insights.highlights.busiest_month is not None
    assert insights.highlights.busiest_month.month in {m.month for m in insights.per_month}


def test_the_busiest_month_is_a_month_of_the_period() -> None:
    insights = aggregate(fixture(), WINDOW, today=date(2026, 7, 31))
    assert insights.highlights.busiest_month in insights.per_month
