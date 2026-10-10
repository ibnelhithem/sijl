"""The Markdown report v1: header, summary, Highlights, Per repository (an overview),
Tickets, Per month, Kinds of work, Delivered, Not pushed yet, Caveats, the Appendix with every
counted commit, and the footer — in that order, every section and number of the report before it
kept."""

from datetime import date
from pathlib import Path

from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.report import Report, build_report, render_markdown
from tests.gitfactory import COLLEAGUE, ME, ME_GITHUB, REVIEW_PERIOD
from tests.test_deliveries import at, branch
from tests.test_report_deliveries import GITLAB_APP, gitlab_app
from tests.test_report_pushed import section

MINE = IdentitySet.from_emails([ME.email, ME_GITHUB.email])
FOOTER = "*These figures are evidence of activity, not a performance score.*"


def build(*paths: Path, projects=()) -> Report:
    return build_report(
        roots=[], paths=list(paths), identities=MINE, window=REVIEW_PERIOD, git=GitRunner(),
        generated_on=date(2026, 10, 8), jira_projects=projects,
    )  # fmt: skip


def test_the_sections_come_in_the_v1_order_and_the_footer_ends_the_report(make_repo, tmp_path):
    repo = gitlab_app(make_repo, tmp_path)
    repo.commit("feat(PROJ-5): local", at=at(20), files={"local.txt": "x"})  # not pushed
    text = render_markdown(build(repo.path, projects=["PROJ"]))
    order = [
        "# Work summary — ", "## Summary", "## Highlights", "## Per repository", "## Tickets",
        "## Per month", "## Kinds of work", "## Delivered", "## Not pushed yet", "## Caveats",
        "## Appendix: every counted commit", FOOTER,
    ]  # fmt: skip
    positions = [text.index(heading) for heading in order]
    assert positions == sorted(positions)
    assert text.endswith(f"\n\n---\n\n{FOOTER}\n")


def test_highlights_name_the_busiest_month_largest_requests_and_active_tickets(
    make_repo, tmp_path
) -> None:
    repo = gitlab_app(make_repo, tmp_path)  # !42: 2 commits of yours; !150: 1; !151: none
    report = build(repo.path, projects=["PROJ"])
    text = section(render_markdown(report), "Highlights")
    assert "- **Busiest month:** May 2026 — 3 commits on 3 active days." in text
    assert "- **Largest merge requests and pull requests** (2 of 2, by commits of yours):" in text
    assert (
        f"  - [!42]({GITLAB_APP}/-/merge_requests/42) Add the x report — 2 commits of yours, "
        "in app, merged 2026-05-06\n"
        f"  - [!150]({GITLAB_APP}/-/merge_requests/150) Escape a | pipe — 1 commit of yours, "
        "in app, merged 2026-05-08\n"
    ) in text
    assert "!151" not in text  # merged for a colleague: never a highlight of yours
    assert "Most active tickets" not in text  # no commit names a PROJ ticket


def test_highlighted_tickets_say_what_names_them(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    for day, subject in enumerate(["feat(PROJ-1): a", "fix(PROJ-1): b", "feat(PROJ-2): c"], 4):
        repo.commit(subject, at=at(day))
    text = section(render_markdown(build(repo.path, projects=["PROJ"])), "Highlights")
    assert (
        "- **Most active tickets** (2 of 2, by commits naming them):\n"
        "  - PROJ-1 — named in 2 commits, 2026-05-04 to 2026-05-05\n"
        "  - PROJ-2 — named in 1 commit, 2026-05-06 to 2026-05-06\n"
    ) in text


def test_nothing_to_highlight_no_section(make_repo) -> None:
    make_repo("app").commit("feat: long ago", at="2025-01-01T09:00:00+01:00")
    text = render_markdown(build(make_repo("app").path))
    assert "## Highlights" not in text and "## Appendix" not in text
    assert "## Per repository" in text  # still says which repositories had nothing


def test_per_repository_is_an_overview_and_the_appendix_holds_every_commit(make_repo, tmp_path):
    repo = gitlab_app(make_repo, tmp_path)
    other = make_repo("tools")
    other.commit("chore: one", at=at(4))
    make_repo("quiet").commit("feat: theirs", at=at(4), who=COLLEAGUE)
    text = render_markdown(build(repo.path, other.path, tmp_path / "quiet"))
    overview = section(text, "Per repository")
    assert (
        "| Repository | Commits | Share | Project |\n|---|---|---|---|\n"
        f"| app | 3 | 75% | [gitlab.com/acme/platform/app]({GITLAB_APP}) |\n"
        "| tools | 1 | 25% | — |\n"
    ) in overview
    assert "No commits of yours in this period: quiet." in overview
    assert "| Date | Commit | Subject |" not in overview  # no commit rows in the overview
    appendix = section(text, "Appendix: every counted commit")
    assert "### app — 3 commits" in appendix and "### tools — 1 commit" in appendix
    assert appendix.count("| 2026-05-") == 4  # every counted commit, once
    assert f"`{repo.path}` · [gitlab.com/acme/platform/app]({GITLAB_APP})" in appendix


def test_every_section_and_number_of_the_report_before_v1_is_kept(make_repo, tmp_path) -> None:
    repo = gitlab_app(make_repo, tmp_path)
    branch(repo, "feature/local", (25, ME))  # a local branch: not pushed yet
    text = render_markdown(build(repo.path, projects=["PROJ"]))
    before_v1 = ["## Summary", "## Kinds of work", "## Per month", "## Tickets", "## Delivered",
                 "## Per repository", "## Not pushed yet", "## Caveats"]  # fmt: skip
    assert all(heading in text for heading in before_v1)
    assert "you authored **3 commits** on **3 active days** across **1 repository**" in text
    assert "| May 2026 | 3 | 3 |" in text and "| Features | 3 | 100% |" in text
