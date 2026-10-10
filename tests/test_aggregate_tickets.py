"""sijl.aggregate: tickets in the one Insights model — per-ticket commits and first and
last day for the allowlisted projects, suggestions for the other ticket-shaped prefixes, and the
ids the guard refused. Every expected number is worked out by hand from the fixture below."""

from dataclasses import replace
from datetime import date

from sijl.aggregate import Suggestion, TicketInsights, TicketTotal, aggregate
from sijl.window import Window
from tests.gitfactory import raw_commit
from tests.test_aggregate import API, WEBAPP, by_kind, collection, commit, repo_commits

WINDOW = Window(date(2026, 4, 1), date(2026, 5, 31))

WEBAPP_COMMITS = [
    commit(1, "2026-04-02T09:00:00+02:00", "feat(PROJ-12): billing"),  # PROJ-12 as the scope
    commit(2, "2026-04-06T09:00:00+02:00", "fix: retry (PROJ-12)",
           "fix: retry (PROJ-12)\n\nRefs PROJ-12, PROJ-13\n"),  # PROJ-12 twice: once per commit
    commit(3, "2026-04-07T09:00:00+02:00", "PROJ-13: wire the API"),  # before the subject
    commit(4, "2026-04-08T09:00:00+02:00", "test: cover QC-4 and QC-5 (QC-4)"),  # not a project
    commit(5, "2026-04-09T09:00:00+02:00", "docs: UTF-8 and SHA-256 notes"),  # standards
]  # fmt: skip
API_COMMITS = [
    commit(6, "2026-05-04T09:00:00+02:00", "chore: bump", "chore: bump\n\nCloses TASK-2\n"),
    commit(7, "2026-05-05T09:00:00+02:00", "feat: TC-UI-07 flow, see TC-13"),  # refused; text
    commit(8, "2026-05-06T09:00:00+02:00", "Add something"),  # no key at all
    raw_commit(at="2026-05-07T09:00:00+02:00", sha=f"{9:040x}", subject="refactor: x",
               body="refactor: x\n\nJira: PROJ-14\n", trailers=(("jira", "PROJ-14"),)),
]  # fmt: skip
NOT_PUSHED = commit(10, "2026-05-08T09:00:00+02:00", "feat(PROJ-99): never pushed")


def fixture():
    return collection(
        repo_commits(WEBAPP, WEBAPP_COMMITS, local_only=[NOT_PUSHED]),
        repo_commits(API, API_COMMITS),
    )


def test_tickets_of_your_projects_equal_their_hand_computed_values() -> None:
    insights = aggregate(fixture(), WINDOW, categorise=by_kind, projects=["TASK", "PROJ"])
    assert insights.tickets == TicketInsights(
        projects=("PROJ", "TASK"),  # sorted
        per_ticket=(
            TicketTotal("PROJ-12", commits=2, first=date(2026, 4, 2), last=date(2026, 4, 6)),
            TicketTotal("PROJ-13", commits=2, first=date(2026, 4, 6), last=date(2026, 4, 7)),
            TicketTotal("PROJ-14", commits=1, first=date(2026, 5, 7), last=date(2026, 5, 7)),
            TicketTotal("TASK-2", commits=1, first=date(2026, 5, 4), last=date(2026, 5, 4)),
        ),  # most commits first; a tie by project, then number. PROJ-99 is not pushed: absent
        with_ticket=5,  # #1, #2, #3, #6, #9
        suggestions=(
            Suggestion("QC", commits=1, in_position=1, example="QC-4"),  # #4: in brackets
            Suggestion("TC", commits=1, in_position=0, example="TC-13"),  # #7: running text
        ),
        outside=2,  # #4 and #7
        rejected=(("SHA-256", 1), ("TC-UI-07", 1), ("UTF-8", 1)),
        rejected_commits=2,  # #5 and #7
        by_commit=BY_COMMIT,
    )


BY_COMMIT = {  # each counted commit naming a ticket → its tickets, in key order
    f"{1:040x}": ("PROJ-12",),
    f"{2:040x}": ("PROJ-12", "PROJ-13"),  # the subject's bracket and the Refs line
    f"{3:040x}": ("PROJ-13",),
    f"{6:040x}": ("TASK-2",),
    f"{9:040x}": ("PROJ-14",),
}  # not #4 (QC is no project of yours), nor #10 (not pushed: not counted)


def test_each_counted_commit_keeps_the_tickets_it_names() -> None:
    """The digest groups commits by ticket from this one reading — never a second one, so
    a commit is under exactly the tickets it is counted for."""
    tickets = aggregate(fixture(), WINDOW, categorise=by_kind, projects=["TASK", "PROJ"]).tickets
    assert tickets.by_commit == BY_COMMIT
    for total in tickets.per_ticket:
        named = [sha for sha, keys in tickets.by_commit.items() if total.key in keys]
        assert len(named) == total.commits
    assert aggregate(fixture(), WINDOW, categorise=by_kind).tickets.by_commit == {}


def test_without_projects_nothing_is_a_ticket_and_every_prefix_is_suggested() -> None:
    tickets = aggregate(fixture(), WINDOW, categorise=by_kind).tickets
    assert (tickets.projects, tickets.per_ticket, tickets.with_ticket) == ((), (), 0)
    assert tickets.suggestions == (
        # #1 scope, #2 brackets and Refs, #3 lead, #9 trailer; PROJ-12 and PROJ-13 tie on two
        # commits each, so the example is the lower number
        Suggestion("PROJ", commits=4, in_position=4, example="PROJ-12"),
        Suggestion("QC", commits=1, in_position=1, example="QC-4"),
        Suggestion("TASK", commits=1, in_position=1, example="TASK-2"),  # a tie with QC: by name
        Suggestion("TC", commits=1, in_position=0, example="TC-13"),
    )
    assert tickets.outside == 7  # every commit with a key: #1, #2, #3, #4, #6, #7, #9


def test_commits_with_and_without_a_key_are_both_counted() -> None:
    without = aggregate(fixture(), WINDOW, categorise=by_kind)
    with_projects = aggregate(fixture(), WINDOW, categorise=by_kind, projects=["PROJ", "TASK"])
    assert with_projects.total == without.total == 9  # #8 has no key, #5 only standards
    # nothing else moves — but the Highlights, which name the most active tickets
    highlights = replace(with_projects.highlights, tickets=())
    assert replace(with_projects, tickets=without.tickets, highlights=highlights) == without
    assert with_projects.tickets.with_ticket == 5  # and 4 commits name no ticket


def test_suggestions_rank_by_ticket_positions_before_mentions() -> None:
    commits = [
        commit(1, "2026-05-04T10:00:00+02:00", "fix: flaky TC-1"),
        commit(2, "2026-05-05T10:00:00+02:00", "fix: flaky TC-2"),
        commit(3, "2026-05-06T10:00:00+02:00", "fix: flaky TC-3"),
        commit(4, "2026-05-07T10:00:00+02:00", "feat(ABC-1): x"),
        commit(5, "2026-05-08T10:00:00+02:00", "ZZ-1: x"),
        commit(6, "2026-05-11T10:00:00+02:00", "feat: y (ZZ-2)"),
    ]
    tickets = aggregate(collection(repo_commits(API, commits)), WINDOW).tickets
    assert [(s.project, s.in_position, s.commits) for s in tickets.suggestions] == [
        ("ZZ", 2, 2),  # twice where tickets are written
        ("ABC", 1, 1),
        ("TC", 0, 3),  # mentioned most, but only ever in running text
    ]


def test_a_ticket_spans_repositories_and_counts_each_commit_once() -> None:
    webapp = [commit(1, "2026-05-04T10:00:00+02:00", "feat(PROJ-7): x (PROJ-7)")]
    api = [commit(2, "2026-04-30T23:30:00-04:00", "fix: PROJ-7 again")]  # 30 April, New York
    tickets = aggregate(
        collection(repo_commits(WEBAPP, webapp), repo_commits(API, api)), WINDOW,
        projects=["PROJ"],
    ).tickets  # fmt: skip
    assert tickets.per_ticket == (TicketTotal("PROJ-7", 2, date(2026, 4, 30), date(2026, 5, 4)),)


def test_a_ticket_in_a_branch_name_is_counted_and_not_also_refused() -> None:
    """`_` separates words in branch names: PROJ-12 counts, `PROJ-12_fix` is not a refusal."""
    merge = commit(1, "2026-05-04T10:00:00+02:00", "Merge branch 'feature/PROJ-12_fix' into 'main'")
    tickets = aggregate(collection(repo_commits(API, [merge])), WINDOW, projects=["PROJ"]).tickets
    assert [t.key for t in tickets.per_ticket] == ["PROJ-12"]
    assert (tickets.rejected, tickets.rejected_commits) == ((), 0)


def test_a_refused_id_is_counted_as_itself_whatever_word_is_hyphenated_onto_it() -> None:
    commits = [
        commit(1, "2026-05-04T10:00:00+02:00", "docs: non-UTF-8 input"),
        commit(2, "2026-05-05T10:00:00+02:00", "docs: UTF-8 everywhere"),
        commit(3, "2026-05-06T10:00:00+02:00", "docs: UTF-8-encoded names"),
    ]
    tickets = aggregate(collection(repo_commits(API, commits)), WINDOW).tickets
    assert (tickets.rejected, tickets.rejected_commits) == ((("UTF-8", 3),), 3)
