"""The report's Delivered section: your merge requests and pull requests — merged by you or a
colleague — and the ones you merged for colleagues, each with its date, number, link and title;
what git cannot show (fast-forward, rebase and squash merges) said in Caveats; and every count the
report had before unchanged, because a merge is never a commit."""

from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from sijl.cli import app
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.report import Report, build_report, caveats, render_markdown, summary_paragraph
from tests.gitfactory import COLLEAGUE, ME, ME_GITHUB, PERIOD_ARGS, REVIEW_PERIOD, RepoBuilder
from tests.test_deliveries import GraphFails, at, branch, started
from tests.test_deliveries_links import hosted

MINE = IdentitySet.from_emails([ME.email, ME_GITHUB.email])
runner = CliRunner()
GITLAB_APP = "https://gitlab.com/acme/platform/app"
FAST_FORWARD = (  # GitHub's squash merges are recognised; the rest still needs the APIs
    "Merge requests and pull requests merged by fast-forward or rebase — or squashed anywhere but "
    "on GitHub's website — leave no merge commit git can recognise, so git alone cannot list them "
    "as deliveries (the GitLab and GitHub APIs can; a future version may ask them)."
)


def build(*paths: Path, **extra) -> Report:
    return build_report(
        roots=[],
        paths=list(paths),
        identities=MINE,
        window=REVIEW_PERIOD,
        git=GitRunner(),
        generated_on=date(2026, 10, 8),
        **extra,
    )


def gitlab_app(make_repo, tmp_path: Path) -> RepoBuilder:
    """Your merge request merged by a colleague, one you merged yourself, a colleague's you merged
    for them, and a colleague's they merged themselves (not listed)."""
    repo = started(make_repo)
    branch(repo, "feature/report", (4, ME), (5, ME))
    repo.gitlab_mr_merge("feature/report", number=42, title="Add the x report", at=at(6),
                         who=COLLEAGUE)  # fmt: skip
    branch(repo, "fix/pipe", (7, ME))
    repo.gitlab_mr_merge("fix/pipe", number=150, title="Escape a | pipe", at=at(8))
    branch(repo, "feature/theirs", (9, COLLEAGUE), (10, COLLEAGUE))
    repo.gitlab_mr_merge("feature/theirs", number=151, title="Theirs", at=at(11))
    branch(repo, "feature/not-mine", (12, COLLEAGUE))
    repo.gitlab_mr_merge("feature/not-mine", number=152, title="Not mine", at=at(13),
                         who=COLLEAGUE)  # fmt: skip
    hosted(repo, tmp_path, "git@gitlab.com:acme/platform/app.git")
    return repo


def test_delivered_lists_yours_and_what_you_merged_for_others(make_repo, tmp_path) -> None:
    text = render_markdown(build(gitlab_app(make_repo, tmp_path).path))
    section = text[text.index("## Delivered") : text.index("## Caveats")]
    assert (
        "**2 merge requests and pull requests** of yours were merged into a mainline — 1 by you, "
        "1 by a colleague — and you merged 1 for colleagues."
    ) in section
    yours = section[section.index("### Yours") : section.index("### Merged for others")]
    assert "#### app — 2 deliveries" in yours
    assert "| Merged | Request | Title | Your commits | Merged by |" in yours
    assert (
        f"| 2026-05-08 | [!150]({GITLAB_APP}/-/merge_requests/150) | Escape a \\| pipe | 1 of 1 | "
        "you |"
    ) in yours
    assert (
        f"| 2026-05-06 | [!42]({GITLAB_APP}/-/merge_requests/42) | Add the x report | 2 of 2 | "
        "a colleague |"
    ) in yours
    assert yours.index("!150") < yours.index("!42")  # newest first
    others = section[section.index("### Merged for others") :]
    assert "| Merged | Request | Title | Commits |" in others
    assert f"| 2026-05-11 | [!151]({GITLAB_APP}/-/merge_requests/151) | Theirs | 2 |" in others
    assert "!152" not in text and COLLEAGUE.name not in text and COLLEAGUE.email not in text


def test_a_github_pull_request_appears_with_its_link(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "fix/typo", (4, ME))
    repo.github_pr_merge("fix/typo", number=12, title="Fix a typo", at=at(7))
    hosted(repo, tmp_path, "git@github.com:alexgh/app.git")
    text = render_markdown(build(repo.path))
    assert (
        "| 2026-05-07 | [#12](https://github.com/alexgh/app/pull/12) | Fix a typo | 1 of 1 | you |"
        in text
    )
    assert (
        "**1 merge request or pull request** of yours was merged into a mainline, by you." in text
    )


def test_merges_change_no_number_the_report_had(make_repo, tmp_path) -> None:
    report = build(gitlab_app(make_repo, tmp_path).path)
    assert (report.total, report.active_days, len(report.active_repos)) == (3, 3, 1)
    assert summary_paragraph(report).startswith(
        "Between 1 April 2026 and 30 June 2026 you authored **3 commits** on **3 active days** "
        "across **1 repository** — most in app (3)."
    )
    assert summary_paragraph(report).endswith(
        " 2 merge requests and pull requests of yours were merged, and you merged 1 for colleagues "
        "(see *Delivered*)."
    )


def test_your_merge_commits_are_accounted_for_in_caveats(make_repo, tmp_path) -> None:
    repo = gitlab_app(make_repo, tmp_path)
    repo.switch("feature/report")
    repo.merge("main", message_text="Merge branch 'main' into feature/report\n", at=at(20))
    repo.switch("main")
    branch(repo, "feature/by-hand", (21, ME))
    repo.merge("feature/by-hand", message_text="Merge branch 'feature/by-hand'\n", at=at(22))
    lines = caveats(build(repo.path))
    assert (
        "4 merge commits you made are not counted as commits: 2 listed under *Delivered*, 1 "
        "brought a long-lived branch into another branch (a sync merge or back-merge, ignored), 1 "
        "merged a branch with no merge request or pull request (not listed)."
    ) in lines


def test_fast_forward_merges_are_disclosed_as_undetectable_without_an_api(make_repo, tmp_path):
    assert FAST_FORWARD in caveats(build(gitlab_app(make_repo, tmp_path).path))  # all merged
    repo = started(make_repo, "direct")
    repo.commit("fix: pushed straight to main", at=at(3), files={"direct.txt": "x"})
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=1, title="X", at=at(6))
    hosted(repo, tmp_path, "git@gitlab.com:acme/platform/direct.git")
    lines = caveats(build(repo.path))
    assert (
        "1 commit of yours reached a mainline with no merge request or pull request merge commit "
        "— a fast-forward or rebase merge, a squash not made on GitHub's website, a merge made by "
        "hand, or a push straight to it. It is counted as a commit; which request delivered it "
        "cannot be seen in git alone (the GitLab and GitHub APIs can; a future version may ask "
        "them)."
    ) in lines


def test_how_deliveries_are_chosen_is_always_said(make_repo) -> None:
    repo = make_repo("app")
    repo.commit("feat: alone", at=at(4))
    text = render_markdown(build(repo.path))
    assert (
        "No merge request or pull request of yours was merged into a mainline in this period, and "
        "you merged none for colleagues."
    ) in text
    assert (
        "Deliveries are merge requests (GitLab: `See merge request …!N`) and pull requests "
        "(GitHub: `Merge pull request #N`) merged into a mainline — a remote's default branch — "
        "or, for a merge request, into another long-lived branch (`main`, `master`, `develop`, "
        "`release/…`) whose merge commit has since reached a mainline, dated by the merge "
        "commit's author date"
    ) in text
    assert "Back-merges (a long-lived branch merged into another) and sync merges are never" in text


def test_deliveries_without_a_link_are_counted(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=9, title="X", at=at(6), project="acme/elsewhere")
    hosted(repo, tmp_path, "git@gitlab.com:acme/platform/app.git")
    report = build(repo.path)
    assert "| 2026-05-06 | !9 | X | 1 of 1 | you |" in render_markdown(report)
    assert (
        "1 merge request or pull request has no link, only its number: no remote is the GitLab "
        "project its merge commit names, or not exactly one GitHub project holds it — app (1)."
    ) in caveats(report)


def test_a_title_can_never_become_a_link(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=10, title="[click](javascript:alert(1)) <b>", at=at(6))
    hosted(repo, tmp_path, "git@gitlab.com:acme/platform/app.git")
    text = render_markdown(build(repo.path))
    assert "| \\[click](javascript:alert(1)) \\<b> | 1 of 1 | you |" in text


def test_a_graph_git_cannot_read_is_named(make_repo, tmp_path) -> None:
    repo = gitlab_app(make_repo, tmp_path)
    report = build_report(roots=[], paths=[repo.path], identities=MINE, window=REVIEW_PERIOD,
                          git=GraphFails(), generated_on=date(2026, 10, 8))  # fmt: skip
    assert (
        "Merged commits could not be read in app (bad object 0123abcd): its merge requests and "
        "pull requests are not listed."
    ) in caveats(report)


def test_build_report_passes_tickets_of_through(make_repo, tmp_path) -> None:
    report = build(gitlab_app(make_repo, tmp_path).path, tickets_of=lambda text: {"PROJ-1"})
    assert all(d.tickets == {"PROJ-1"} for rc in report.collection.repos for d in rc.deliveries)


def test_the_cli_report_has_the_delivered_section(make_repo, tmp_path) -> None:
    gitlab_app(make_repo, tmp_path)
    args = ["report", "--repo", str(tmp_path / "app"), "--author-email", ME.email, *PERIOD_ARGS]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "## Delivered" in result.stdout and "/-/merge_requests/150)" in result.stdout


def test_merging_for_colleagues_alone_is_reported(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/theirs", (4, COLLEAGUE))
    repo.gitlab_mr_merge("feature/theirs", number=3, title="Theirs", at=at(6))
    hosted(repo, tmp_path, "git@gitlab.com:acme/platform/app.git")
    report = build(repo.path)
    assert report.total == 0
    assert summary_paragraph(report).endswith(
        "in the 1 repository scanned. You merged 1 merge request or pull request for colleagues "
        "(see *Delivered*)."
    )
    text = render_markdown(report)
    assert "You merged **1 merge request or pull request** for colleagues." in text
    assert "### Yours" not in text and "### Merged for others" in text


def test_pull_request_numbers_git_cannot_confirm_are_disclosed(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "fix/typo", (4, ME))
    repo.github_pr_merge("fix/typo", number=12, title="Fix a typo", at=at(7))
    hosted(repo, tmp_path, "git@github.com:alexgh/app.git")
    assert (
        "1 pull request is linked to the only GitHub project whose branches hold its merge — app "
        "(1). In a fork synced from its upstream, a pull request's number may belong to the "
        "upstream project instead; only the GitHub API can tell."
    ) in caveats(build(repo.path))
