"""The report and tickets: the *Tickets* section — tickets of your Jira projects, or
suggestions when none are set — and the Caveats about keys outside the allowlist, ids the guard
refused and merge branches not read; `--jira-project` on the command line."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl.aggregate import aggregate
from sijl.cli import app
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.report import build_report, caveats, render_markdown
from tests.gitfactory import ME, PERIOD_ARGS, REVIEW_PERIOD
from tests.test_aggregate import API, collection, commit, repo_commits
from tests.test_cli_report import plain
from tests.test_report_pushed import section


def scenario(make_repo) -> Path:
    """One repository without a remote, so all eight commits count; the merge does not.

    May 2026: Mon 4 · Tue 5 · Wed 6 · Thu 7 · Fri 8 · Mon 11 · Tue 12 · Wed 13 · Thu 14
    """
    repo = make_repo("app")
    repo.commit("feat(PROJ-12): billing", at="2026-05-04T09:00:00+02:00")  # 1: the scope
    repo.commit("fix: retry (PROJ-12)", at="2026-05-05T09:00:00+02:00",
                body="Refs PROJ-13")  # fmt: skip  # 2: brackets at the end, a Refs line
    repo.commit("PROJ-13: wire the API", at="2026-05-06T09:00:00+02:00")  # 3: before the subject
    repo.commit("test: cover QC-4 (QC-4)", at="2026-05-07T09:00:00+02:00")  # 4: not a project
    repo.commit("docs: UTF-8 and SHA-256 notes", at="2026-05-08T09:00:00+02:00")  # 5: standards
    repo.branch("feature/PROJ-77-x")
    repo.switch("feature/PROJ-77-x")
    repo.commit("chore: bump", at="2026-05-11T09:00:00+02:00", body="Closes TASK-2",
                files={"deps.txt": "bumped\n"})  # fmt: skip  # 6: a Closes line
    repo.switch("main")
    repo.commit("Add something", at="2026-05-12T09:00:00+02:00")  # 7: no key
    repo.commit("refactor: tidy", at="2026-05-13T09:00:00+02:00",
                trailers=["Jira: TASK-3"])  # fmt: skip  # 8: a git trailer
    repo.merge("feature/PROJ-77-x", message_text="Merge branch 'feature/PROJ-77-x' into 'main'\n",
               at="2026-05-14T09:00:00+02:00")  # fmt: skip  # not counted: PROJ-77 is not read
    return repo.path


def build(path: Path, projects=()):
    return build_report(
        roots=[],
        paths=[path],
        identities=IdentitySet.from_emails([ME.email]),
        window=REVIEW_PERIOD,
        git=GitRunner(),
        generated_on=date(2026, 10, 8),
        jira_projects=projects,
    )


POSITIONS_NOTE = (
    "*In a ticket position*: before the subject (`ABC-1: …`), as its scope (`feat(ABC-1): …`), "
    "in brackets at its end, in a merged branch's name, on a `Closes` or `Refs` line, or in a "
    "trailer. A prefix seen only in running text is a weaker hint."
)


def test_the_tickets_of_your_projects_are_listed(make_repo) -> None:
    text = section(render_markdown(build(scenario(make_repo), ["PROJ", "TASK"])), "Tickets")
    assert (
        "**4 tickets** in PROJ and TASK, named in 5 of your 8 commits (63%); the other 3 name "
        "none. A commit counts once under each ticket it names, wherever its message names it.\n"
        "\n"
        "| Ticket | Commits | First | Last |\n"
        "|---|---|---|---|\n"
        "| PROJ-12 | 2 | 2026-05-04 | 2026-05-05 |\n"
        "| PROJ-13 | 2 | 2026-05-05 | 2026-05-06 |\n"
        "| TASK-2 | 1 | 2026-05-11 | 2026-05-11 |\n"
        "| TASK-3 | 1 | 2026-05-13 | 2026-05-13 |\n"
        "\n"
        "Other prefixes that look like Jira project keys — not in `jira.projects`, so not "
        "counted as tickets:\n"
        "\n"
        "| Prefix | Commits | In a ticket position | Example |\n"
        "|---|---|---|---|\n"
        "| QC | 1 | 1 | QC-4 |\n"
        "\n" + POSITIONS_NOTE
    ) in text
    assert "PROJ-77" not in text  # only in the merge's branch name: merges are not counted


def test_without_projects_the_section_suggests_them_with_evidence(make_repo) -> None:
    text = section(render_markdown(build(scenario(make_repo))), "Tickets")
    assert (
        "No Jira projects are set, so no commit is grouped by ticket. The prefixes below look "
        "like Jira project keys in your commits; add yours to `jira.projects` in the config "
        "file, or pass `--jira-project`, to count their keys as tickets.\n"
        "\n"
        "| Prefix | Commits | In a ticket position | Example |\n"
        "|---|---|---|---|\n"
        "| PROJ | 3 | 3 | PROJ-12 |\n"
        "| TASK | 2 | 2 | TASK-2 |\n"
        "| QC | 1 | 1 | QC-4 |\n"
    ) in text
    assert "| Ticket |" not in text


def test_caveats_say_what_was_not_counted_as_a_ticket(make_repo) -> None:
    lines = caveats(build(scenario(make_repo), ["PROJ", "TASK"]))
    assert (
        "1 commit names keys of projects not in `jira.projects` — QC (1) — which are not "
        "counted as tickets."
    ) in lines
    assert (
        "1 commit mentions ids that only look like ticket keys — standards, versions or parts "
        "of longer ids — and are never counted as tickets: SHA-256 (1), UTF-8 (1)."
    ) in lines
    assert (  # the merge is by hand, not a merge request: its branch name is not read
        "Ticket keys are read from counted commits and from merge requests and pull requests "
        "(their branch, title and message); the other 1 merge commit you made is not read for "
        "tickets."
    ) in lines


def test_caveats_name_the_other_prefixes_by_how_many_commits_they_are_in(make_repo) -> None:
    commits = [
        commit(1, "2026-05-04T10:00:00+02:00", "feat(ABC-1): x"),  # ranked first: a scope
        commit(2, "2026-05-05T10:00:00+02:00", "fix: flaky TC-1"),
        commit(3, "2026-05-06T10:00:00+02:00", "fix: flaky TC-2"),
    ]
    insights = aggregate(collection(repo_commits(API, commits)), REVIEW_PERIOD, projects=["PROJ"])
    assert [s.project for s in insights.tickets.suggestions] == ["ABC", "TC"]
    lines = caveats(replace(build(shipped(make_repo), ["PROJ"]), insights=insights))
    assert (
        "3 commits name keys of projects not in `jira.projects` — TC (2), ABC (1) — which are "
        "not counted as tickets."
    ) in lines


def test_caveats_without_projects_point_at_the_suggestions(make_repo) -> None:
    lines = caveats(build(scenario(make_repo)))
    assert (
        "No Jira projects are set (`jira.projects`), so the ticket-shaped keys in 6 commits are "
        "not counted as tickets; *Tickets* suggests the likely projects."
    ) in lines


def test_tickets_sit_after_the_repositories_and_before_the_months(make_repo) -> None:
    text = render_markdown(build(scenario(make_repo), ["PROJ"]))
    order = ["## Per repository", "## Tickets", "## Per month", "## Caveats"]  # the report's order
    positions = [text.index(line) for line in order]
    assert positions == sorted(positions)


def test_tickets_change_no_other_number(make_repo) -> None:
    path = scenario(make_repo)
    with_projects, without = build(path, ["PROJ", "TASK"]), build(path)
    assert with_projects.total == without.total == 8
    # the Highlights name the most active tickets, so only their tickets differ too
    same = replace(
        with_projects.insights,
        tickets=without.insights.tickets,
        highlights=replace(with_projects.insights.highlights, tickets=()),
    )
    assert same == without.insights


def shipped(make_repo) -> Path:
    repo = make_repo("app")
    repo.commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    return repo.path


def test_no_keys_and_no_projects_no_section_and_nothing_said(make_repo) -> None:
    report = build(shipped(make_repo))
    assert "## Tickets" not in render_markdown(report)
    assert "ticket" not in " ".join(caveats(report)).lower()


def test_projects_set_but_no_ticket_found_says_so(make_repo) -> None:
    text = section(render_markdown(build(shipped(make_repo), ["PROJ"])), "Tickets")
    assert "None of your 1 commit names a ticket in PROJ (`jira.projects`)." in text


def committed(make_repo, *subjects: str) -> Path:
    """A repository without a remote: every one of these commits counts."""
    repo = make_repo("app")
    for subject in subjects:
        repo.commit(subject, at="2026-05-04T10:00:00+02:00")
    return repo.path


def test_a_long_list_of_suggestions_shows_ten_and_counts_the_rest(make_repo) -> None:
    report = build(committed(make_repo, *(f"fix: P{n:02d}-1" for n in range(12))))
    text = section(render_markdown(report), "Tickets")
    assert "| P09 | 1 | 0 | P09-1 |" in text and "| P10 |" not in text  # ties: by name
    assert "Plus 2 more prefixes, ranked lower." in text
    assert (
        "No Jira projects are set (`jira.projects`), so the ticket-shaped keys in 12 commits are "
        "not counted as tickets; *Tickets* suggests the likely projects."
    ) in caveats(report)


def test_with_projects_caveats_name_ten_other_prefixes_and_count_the_rest(make_repo) -> None:
    commits = [commit(n, "2026-05-04T10:00:00+02:00", f"fix: P{n:02d}-1") for n in range(12)]
    insights = aggregate(collection(repo_commits(API, commits)), REVIEW_PERIOD, projects=["PROJ"])
    lines = caveats(replace(build(shipped(make_repo), ["PROJ"]), insights=insights))
    listed = ", ".join(f"P{n:02d} (1)" for n in range(10))
    assert (
        f"12 commits name keys of projects not in `jira.projects` — {listed}, plus 2 more "
        "prefixes — which are not counted as tickets."
    ) in lines


def test_the_jira_project_flag_counts_tickets_and_refuses_a_bad_key(make_repo) -> None:
    path = scenario(make_repo)
    args = ["report", "--repo", str(path), "--author-email", ME.email, *PERIOD_ARGS]
    result = CliRunner().invoke(app, [*args, "--jira-project", "PROJ", "--jira-project", "TASK"])
    assert result.exit_code == 0, result.output
    assert "| PROJ-12 | 2 | 2026-05-04 | 2026-05-05 |" in result.stdout
    refused = CliRunner().invoke(app, [*args, "--jira-project", "proj"])
    assert refused.exit_code == 2
    assert "'proj' is not a Jira project key" in plain(refused.output)


def test_one_commit_without_a_ticket_reads_in_the_singular(make_repo) -> None:
    report = build(committed(make_repo, "feat(PROJ-1): x", "chore: y"), ["PROJ"])
    text = section(render_markdown(report), "Tickets")
    assert (
        "**1 ticket** in PROJ, named in 1 of your 2 commits (50%); the other 1 names none." in text
    )


def test_ticket_text_is_inert_markdown_whatever_the_model_holds(make_repo) -> None:
    """Keys, prefixes and refused ids are regex-shaped and cannot hold markup today; the section
    and its Caveats still route each through _md()/_cell(), so a future source cannot either."""
    pytest.importorskip("markdown_it")
    from sijl.aggregate import Suggestion, TicketInsights, TicketTotal
    from tests.test_report_markdown import tokens_of

    evil = "A`[x](https://evil.example/k)<img src=z>|"
    tickets = TicketInsights(
        projects=(evil,),
        per_ticket=(TicketTotal(evil, 1, date(2026, 5, 4), date(2026, 5, 4)),),
        with_ticket=1,
        suggestions=(Suggestion(evil, 1, 1, evil),),
        outside=1,
        rejected=((evil, 1),),
        rejected_commits=1,
    )
    report = build(shipped(make_repo), ["PROJ"])
    report = replace(report, insights=replace(report.insights, tickets=tickets))
    text = render_markdown(report)
    found = [t for t in tokens_of(text) if t.type in ("link_open", "html_inline", "image")]
    assert not found, text


def test_with_projects_set_caveats_never_ask_to_set_them(make_repo, tmp_path) -> None:
    """With Jira projects set and every merge commit a delivery, no merge is left unread — and the
    note that requests are read "once Jira projects are set" must not appear (the golden report
    once showed it)."""
    from tests.test_deliveries import at, branch, ship, started

    repo = started(make_repo)
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=7, title="X", at=at(6))
    repo.commit("fix: flaky QC-4", at=at(7), files={"qc.txt": "x"})  # a suggested prefix
    ship(repo, tmp_path)
    lines = caveats(build(repo.path, ["PROJ"]))
    assert any("QC (1)" in line for line in lines)
    assert not [line for line in lines if "once Jira projects are set" in line]
