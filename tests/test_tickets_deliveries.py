"""Tickets of merge requests and pull requests (the ticket finder plugged into the deliveries'
`tickets_of` seam): the
keys of your Jira projects in a delivery's branch, title and merge message — a GitHub owner never
— shown in *Delivered*, and counted in *Tickets* where no counted commit names them."""

from dataclasses import replace
from datetime import date
from pathlib import Path

from sijl.aggregate import TicketTotal
from sijl.report import caveats, render_markdown
from sijl.tickets import ticket_finder
from tests.gitfactory import COLLEAGUE, ME, RepoBuilder
from tests.test_deliveries import at, ship, started
from tests.test_report_deliveries import build
from tests.test_report_pushed import section


def test_ticket_finder_reads_a_branch_a_title_and_a_merge_message() -> None:
    find = ticket_finder(["PROJ"])
    assert find("feature/PROJ-1-x") == {"PROJ-1"}
    assert find("PROJ-2: a title (OPS-9)") == {"PROJ-2"}  # OPS is not named: not a ticket
    gitlab = "Merge branch 'Feature/proj 7' into 'main'\n\nA title\n\nCloses PROJ-3\n\n"
    gitlab += "See merge request acme/app!5\n"
    assert find(gitlab) == {"PROJ-7", "PROJ-3"}  # the branch name in the subject, leniently
    github = "Merge pull request #12 from proj-42/feature/proj-8\n\nA title\n"
    assert find(github) == {"PROJ-8"}  # the branch, never the owner `proj-42`
    assert find("UTF-8, TC-UI-07 and PROJ-007") == frozenset()  # the guard still applies
    assert ticket_finder([])("feature/PROJ-1") == frozenset()  # no projects, no tickets


def branch_without_keys(repo: RepoBuilder, name: str, day: int, who=ME) -> None:
    """A branch whose one commit names no ticket: its tickets come from the request alone."""
    repo.branch(name, "main")
    repo.switch(name)
    repo.commit(f"feat: work {day}", at=at(day), who=who, files={f"work-{day}.txt": "x"})
    repo.switch("main")


def gitlab_with_tickets(make_repo, tmp_path: Path) -> RepoBuilder:
    """Your merge request: PROJ-77 in its branch, PROJ-78 in its title, PROJ-79 in its
    description — and in no commit. A colleague's you merged for them names PROJ-90."""
    repo = started(make_repo)
    branch_without_keys(repo, "feature/proj-77-export", 4)
    repo.gitlab_mr_merge("feature/proj-77-export", number=42, title="Export (PROJ-78)",
                         at=at(6), description="Closes PROJ-79")  # fmt: skip
    branch_without_keys(repo, "feature/theirs", 7, who=COLLEAGUE)
    repo.gitlab_mr_merge("feature/theirs", number=151, title="PROJ-90: theirs", at=at(8))
    ship(repo, tmp_path)
    return repo


def test_a_delivery_carries_the_tickets_of_your_projects(make_repo, tmp_path) -> None:
    path = gitlab_with_tickets(make_repo, tmp_path).path
    [rc] = build(path, jira_projects=["PROJ"]).collection.repos
    assert {d.number: d.tickets for d in rc.deliveries} == {
        42: {"PROJ-77", "PROJ-78", "PROJ-79"},
        151: {"PROJ-90"},
    }
    [rc] = build(path).collection.repos  # no project named: no ticket read
    assert {d.tickets for d in rc.deliveries} == {frozenset()}


def test_a_pull_request_s_owner_is_never_a_ticket(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch_without_keys(repo, "feature/OPS-5-y", 4)
    repo.github_pr_merge("feature/OPS-5-y", number=12, title="OPS-6: y", at=at(6), owner="ops-99")
    ship(repo, tmp_path)
    [rc] = build(repo.path, jira_projects=["OPS"]).collection.repos
    assert [d.tickets for d in rc.deliveries] == [{"OPS-5", "OPS-6"}]


def test_delivered_rows_name_their_tickets(make_repo, tmp_path) -> None:
    path = gitlab_with_tickets(make_repo, tmp_path).path
    delivered = section(render_markdown(build(path, jira_projects=["PROJ"])), "Delivered")
    assert "| Merged | Request | Title | Your commits | Merged by | Tickets |" in delivered
    assert "| Export (PROJ-78) | 1 of 1 | you | PROJ-77, PROJ-78, PROJ-79 |" in delivered
    assert "| Merged | Request | Title | Commits | Tickets |" in delivered
    assert "| PROJ-90: theirs | 1 | PROJ-90 |" in delivered


def test_without_tickets_the_delivered_tables_are_unchanged(make_repo, tmp_path) -> None:
    delivered = section(render_markdown(build(gitlab_with_tickets(make_repo, tmp_path).path)),
                        "Delivered")  # fmt: skip
    assert "| Merged | Request | Title | Your commits | Merged by |\n" in delivered
    assert "Tickets" not in delivered


def test_an_explicit_tickets_of_still_wins(make_repo, tmp_path) -> None:
    path = gitlab_with_tickets(make_repo, tmp_path).path
    report = build(path, jira_projects=["PROJ"], tickets_of=lambda text: {"X-1"})
    assert {d.tickets for rc in report.collection.repos for d in rc.deliveries} == {
        frozenset({"X-1"})
    }
    assert date(2026, 5, 6) in {
        d.merged_on for rc in report.collection.repos for d in rc.deliveries
    }


MAY_6 = date(2026, 5, 6)


def test_tickets_only_your_requests_name_join_the_tickets_with_no_commit(make_repo, tmp_path):
    path = gitlab_with_tickets(make_repo, tmp_path).path
    report = build(path, jira_projects=["PROJ"])
    tickets = report.insights.tickets
    assert tickets.per_ticket == (  # !42 merged on 6 May; no counted commit names them
        TicketTotal("PROJ-77", 0, MAY_6, MAY_6, deliveries=1),
        TicketTotal("PROJ-78", 0, MAY_6, MAY_6, deliveries=1),
        TicketTotal("PROJ-79", 0, MAY_6, MAY_6, deliveries=1),
    )
    assert tickets.from_deliveries == ("PROJ-77", "PROJ-78", "PROJ-79")
    assert tickets.others_only == ("PROJ-90",)  # a colleague's work you merged: not yours
    assert tickets.with_ticket == 0
    without = build(path)
    # the Highlights pick the same requests (whose own tickets differ) and name tickets
    picked = [(repo, d.number) for repo, d in report.insights.highlights.deliveries]
    assert picked == [(repo, d.number) for repo, d in without.insights.highlights.deliveries]
    same = replace(
        report.insights,
        tickets=without.insights.tickets,
        highlights=without.insights.highlights,
    )
    assert same == without.insights


def test_a_ticket_named_by_a_commit_and_its_request_spans_both(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    repo.branch("feature/retry", "main")
    repo.switch("feature/retry")
    repo.commit("fix: PROJ-80 retry", at=at(4), files={"retry.txt": "x"})
    repo.switch("main")
    repo.gitlab_mr_merge("feature/retry", number=7, title="Retry (PROJ-80)", at=at(6))
    ship(repo, tmp_path)
    tickets = build(repo.path, jira_projects=["PROJ"]).insights.tickets
    assert tickets.per_ticket == (TicketTotal("PROJ-80", 1, date(2026, 5, 4), MAY_6, 1),)
    assert (tickets.from_deliveries, tickets.with_ticket) == ((), 1)


def test_the_tickets_section_counts_requests_and_says_which_tickets_only_they_name(
    make_repo, tmp_path
) -> None:
    report = build(gitlab_with_tickets(make_repo, tmp_path).path, jira_projects=["PROJ"])
    text = render_markdown(report)
    tickets = section(text, "Tickets")
    assert (
        "**3 tickets** in PROJ, named only by your merge requests and pull requests; none of your "
        "1 commit names one."
    ) in tickets
    assert "| Ticket | Commits | Requests | First | Last |" in tickets
    assert "| PROJ-77 | 0 | 1 | 2026-05-06 | 2026-05-06 |" in tickets
    lines = caveats(report)
    assert (
        "3 tickets are named only by your merge requests and pull requests — in a branch name, "
        "title or message — and by no counted commit: PROJ-77, PROJ-78, PROJ-79."
    ) in lines
    assert (
        "1 ticket named only by merge requests and pull requests you merged for colleagues is "
        "not counted as yours: PROJ-90."
    ) in lines


def squashes_naming_tickets(make_repo, tmp_path: Path) -> tuple[RepoBuilder, str, str]:
    """#9: its only branch commit names PROJ-7 in its body, and no tree, title or listed subject
    links it — only the ticket can. #10: a one-commit branch linked by its tree, whose squash
    title alone names PROJ-8. Both branches stay only on your laptop."""
    repo = started(make_repo)
    repo.branch("feature/PROJ-7", "main")
    repo.switch("feature/PROJ-7")
    seven = repo.commit("feat: the report", at=at(4), body="Part of PROJ-7.", files={"r": "x"})
    repo.switch("main")
    repo.commit("fix: a colleague's", at=at(5), who=COLLEAGUE, files={"c.txt": "x"})
    repo.github_squash("feature/PROJ-7", number=9, title="PROJ-7 report", at=at(6), body="Done.")
    branch_without_keys(repo, "fix/export", 7)
    repo.github_squash("fix/export", number=10, title="Export (PROJ-8)", at=at(8))
    repo.publish(tmp_path / "remotes" / "app.git", "main")
    [export] = [c for c in repo.git("log", "--format=%H", "fix/export", "-1").split()]
    return repo, seven, export


def test_squash_linking_reads_tickets_with_your_projects(make_repo, tmp_path) -> None:
    repo, seven, export = squashes_naming_tickets(make_repo, tmp_path)
    [rc] = build(repo.path).collection.repos  # no project named: the ticket links nothing
    assert seven not in rc.via and seven in {c.sha for c in rc.local_only}
    report = build(repo.path, jira_projects=["PROJ"])
    [rc] = report.collection.repos
    assert rc.via[seven].ref == "#9" and report.collection.squashes.by_ticket == 1
    assert rc.via[export].ref == "#10"  # by its tree, as before
    assert {d.ref: d.tickets for d in rc.deliveries} == {"#9": {"PROJ-7"}, "#10": {"PROJ-8"}}
    delivered = section(render_markdown(report), "Delivered")
    assert "| PROJ-7 report | 1 linked | not recorded (squash) | PROJ-7 |" in delivered


def test_tickets_from_squash_merges_join_insights_like_other_deliveries(make_repo, tmp_path):
    repo, _, _ = squashes_naming_tickets(make_repo, tmp_path)
    tickets = build(repo.path, jira_projects=["PROJ"]).insights.tickets
    assert tickets.per_ticket == (
        # the relabelled commit names PROJ-7 in its body; #9's title names it too
        TicketTotal("PROJ-7", 1, date(2026, 5, 4), MAY_6, deliveries=1),
        # only #10's title names PROJ-8: a ticket with no commit, merged on 8 May
        TicketTotal("PROJ-8", 0, date(2026, 5, 8), date(2026, 5, 8), deliveries=1),
    )
    assert tickets.from_deliveries == ("PROJ-8",)
