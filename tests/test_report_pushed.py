"""The report and the CLI: work on no remote branch has its own section and stays out of the
totals; Caveats say what was left out and which repositories cannot be checked as pushed."""

from datetime import date

from typer.testing import CliRunner

from sijl.cli import app
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.report import build_report, render_markdown
from tests.gitfactory import ME, PERIOD_ARGS, REVIEW_PERIOD
from tests.test_config import config_file
from tests.test_reach import at, everywhere

runner = CliRunner()


def build(*paths, include_worktrees: bool = False):
    return build_report(
        roots=[],
        paths=list(paths),
        identities=IdentitySet.from_emails([ME.email]),
        window=REVIEW_PERIOD,
        git=GitRunner(),
        generated_on=date(2026, 10, 7),
        include_worktrees=include_worktrees,
    )


def section(text: str, title: str) -> str:
    start = text.index(f"## {title}")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else None]


def test_unpushed_work_has_its_own_section_outside_the_totals(make_repo, tmp_path) -> None:
    report = build(everywhere(make_repo, tmp_path).path)
    assert report.total == 2
    text = render_markdown(report)
    order = ["## Summary", "## Per repository", "## Not pushed yet", "## Caveats"]
    positions = [text.index(line) for line in order]
    assert positions == sorted(positions)
    assert "feat: never pushed" in section(text, "Not pushed yet")
    assert "feat: never pushed" not in section(text, "Per repository")
    assert "1 more commit is not pushed yet" in section(text, "Summary")
    assert "1 commit on no remote branch" in section(text, "Caveats")


def test_worktree_only_work_is_marked_when_included(make_repo, tmp_path) -> None:
    text = render_markdown(build(everywhere(make_repo, tmp_path).path, include_worktrees=True))
    unpushed = section(text, "Not pushed yet")
    assert "| worktree only | feat: experiment in a detached worktree |" in unpushed
    assert "| local branch | feat: never pushed |" in unpushed


def test_caveats_explain_worktrees_either_way(make_repo, tmp_path) -> None:
    repo = everywhere(make_repo, tmp_path)
    assert "--include-worktrees" in section(render_markdown(build(repo.path)), "Caveats")
    included = section(render_markdown(build(repo.path, include_worktrees=True)), "Caveats")
    assert "worktree only" in included


def test_caveats_name_repositories_that_cannot_be_checked_as_pushed(make_repo) -> None:
    solo = make_repo("solo")
    solo.commit("feat: a local project", at=at(4))
    report = build(solo.path)
    assert report.total == 1
    text = render_markdown(report)
    caveats = section(text, "Caveats")
    assert "No remote-tracking branches in solo" in caveats
    assert "## Not pushed yet" not in text


def test_the_include_worktrees_flag(make_repo, tmp_path) -> None:
    repo = everywhere(make_repo, tmp_path)
    args = ["report", "--repo", str(repo.path), "--author-email", ME.email, *PERIOD_ARGS]
    assert "experiment" not in runner.invoke(app, args).stdout
    result = runner.invoke(app, [*args, "--include-worktrees"])
    assert result.exit_code == 0, result.output
    assert "feat: experiment in a detached worktree" in result.stdout


def test_the_include_worktrees_setting(make_repo, tmp_path) -> None:
    repo = everywhere(make_repo, tmp_path)
    config_file("[scan]\ninclude_worktrees = true\n")
    args = ["report", "--repo", str(repo.path), "--author-email", ME.email, *PERIOD_ARGS]
    assert "feat: experiment in a detached worktree" in runner.invoke(app, args).stdout
    without = runner.invoke(app, [*args, "--no-include-worktrees"]).stdout
    assert "experiment" not in without
