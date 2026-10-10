"""`sijl report --out`: Sijl never writes into a repository it reads, unless the
path is exactly given — absolute (or from `~`) on the command line, with no symbolic link on the
way. A relative path resolved inside a scanned repository's work tree, or a link that leads into
one, is refused with exit 2 and the full path to give instead; a repository's git folder is never
written to at all. Real paths decide, so a link cannot smuggle the report in."""

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl.cli import app
from sijl.outfile import OutRefused, Place
from tests.gitfactory import ME, PERIOD_ARGS, RepoBuilder

runner = CliRunner()


def app_repo(make_repo) -> RepoBuilder:
    repo = make_repo("repos/app")
    repo.commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    return repo


def report(tmp_path: Path, out: str | Path):
    args = ["report", "--root", str(tmp_path / "repos"), "--author-email", ME.email, *PERIOD_ARGS]
    return runner.invoke(app, [*args, "--out", str(out)])


def test_a_relative_path_inside_a_scanned_repository_is_refused(
    make_repo, tmp_path, monkeypatch
) -> None:
    repo = app_repo(make_repo)
    monkeypatch.chdir(repo.path)
    result = report(tmp_path, "review.md")
    assert result.exit_code == 2
    assert result.stdout == "" and not (repo.path / "review.md").exists()
    assert "--out review.md would write into the scanned repository app" in result.stderr
    assert f"--out {repo.path / 'review.md'}" in result.stderr  # the exact path to give instead


def test_the_exact_path_inside_a_work_tree_is_written(make_repo, tmp_path) -> None:
    repo = app_repo(make_repo)
    target = repo.path / "reports" / "review.md"
    target.parent.mkdir()
    result = report(tmp_path, target)
    assert result.exit_code == 0, result.output
    assert target.read_text().startswith("# Work summary")


def test_a_git_folder_is_never_written_even_by_its_exact_path(make_repo, tmp_path) -> None:
    repo = app_repo(make_repo)
    result = report(tmp_path, repo.path / ".git" / "review.md")
    assert result.exit_code == 2
    assert "is in git's own files of the scanned repository app" in result.stderr
    assert not (repo.path / ".git" / "review.md").exists()


def test_a_link_into_a_scanned_repository_is_refused(make_repo, tmp_path) -> None:
    repo = app_repo(make_repo)
    (repo.path / "docs").mkdir()
    link = tmp_path / "reports"
    link.symlink_to(repo.path / "docs", target_is_directory=True)
    result = report(tmp_path, link / "review.md")  # absolute, but a link leads into the repository
    assert result.exit_code == 2
    assert "would write into the scanned repository app" in result.stderr
    assert not (repo.path / "docs" / "review.md").exists()


def test_a_linked_worktree_is_a_work_tree_of_its_repository(make_repo, tmp_path, monkeypatch):
    repo = app_repo(make_repo)
    worktree = repo.add_worktree(tmp_path / "elsewhere" / "app-wt", branch="side")
    monkeypatch.chdir(worktree.path)
    result = report(tmp_path, "review.md")
    assert result.exit_code == 2
    assert "would write into the scanned repository app" in result.stderr
    gitfile = worktree.path / ".git"  # a file pointing at its git dir: overwriting it cuts it off
    exact = report(tmp_path, gitfile)
    assert exact.exit_code == 2 and "is in git's own files" in exact.stderr
    assert gitfile.read_text().startswith("gitdir: ")


def test_a_relative_path_outside_every_repository_is_written(make_repo, tmp_path, monkeypatch):
    app_repo(make_repo)
    (tmp_path / "reports").mkdir()
    monkeypatch.chdir(tmp_path / "reports")
    result = report(tmp_path, "review.md")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "reports" / "review.md").read_text().startswith("# Work summary")


def test_a_folder_that_cannot_be_written_is_a_usage_error(make_repo, tmp_path) -> None:
    app_repo(make_repo)
    (tmp_path / "taken").mkdir()
    result = report(tmp_path, tmp_path / "taken")  # a folder, not a file
    assert result.exit_code == 2
    assert f"sijl: cannot write {tmp_path / 'taken'}" in result.stderr


def test_a_link_the_repository_committed_is_never_written_through(make_repo, tmp_path) -> None:
    """An exact path inside a work tree once followed a symbolic link the
    repository itself committed — reports/review.md → another repository's hook — and overwrote
    it. Any link from the work tree down, the file itself included, makes the path not exact."""
    victim = RepoBuilder(tmp_path / "unscanned" / "lib")
    hook = victim.path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho original\n")
    repo = app_repo(make_repo)
    (repo.path / "reports").mkdir()
    (repo.path / "reports" / "review.md").symlink_to(hook)
    result = report(tmp_path, repo.path / "reports" / "review.md")
    assert result.exit_code == 2 and "would write into the scanned repository app" in result.stderr
    assert hook.read_text() == "#!/bin/sh\necho original\n"


def test_a_linked_folder_inside_a_work_tree_is_never_written_through(make_repo, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = app_repo(make_repo)
    (repo.path / "reports").symlink_to(outside, target_is_directory=True)
    result = report(tmp_path, repo.path / "reports" / "review.md")
    assert result.exit_code == 2 and "would write into the scanned repository app" in result.stderr
    assert list(outside.iterdir()) == []


def test_a_dangling_link_inside_a_work_tree_is_refused(make_repo, tmp_path) -> None:
    repo = app_repo(make_repo)
    (repo.path / "review.md").symlink_to(tmp_path / "nowhere" / "x.md")
    result = report(tmp_path, repo.path / "review.md")
    assert result.exit_code == 2 and not (tmp_path / "nowhere").exists()


def test_a_hard_link_is_replaced_never_written_through(make_repo, tmp_path) -> None:
    """The report is written to a new file and moved into place (os.replace): a hard link at the
    path is replaced, and the file it shared its contents with is left alone."""
    app_repo(make_repo)
    other = tmp_path / "other.txt"
    other.write_text("keep me\n")
    (tmp_path / "out").mkdir()
    target = tmp_path / "out" / "review.md"
    os.link(other, target)
    result = report(tmp_path, target)
    assert result.exit_code == 0, result.output
    assert other.read_text() == "keep me\n" and target.read_text().startswith("# Work summary")
    assert oct(target.stat().st_mode & 0o777) == "0o644"
    assert sorted(p.name for p in target.parent.iterdir()) == ["review.md"]  # no temporary left


def test_a_link_swapped_in_after_the_check_is_never_written_through(
    make_repo, tmp_path, monkeypatch
) -> None:
    """The path is checked before rendering and again when writing — on the very folder the file
    is written into, opened link by link without following any (O_NOFOLLOW)."""
    import sijl.cli as cli

    hooks = RepoBuilder(tmp_path / "unscanned" / "lib").path / ".git" / "hooks"
    repo = app_repo(make_repo)
    reports = repo.path / "reports"
    reports.mkdir()
    render = cli.render_report

    def render_then_swap(*args, **kwargs):
        rendered = render(*args, **kwargs)
        reports.rmdir()
        reports.symlink_to(hooks, target_is_directory=True)  # between the check and the write
        return rendered

    monkeypatch.setattr(cli, "render_report", render_then_swap)
    result = report(tmp_path, reports / "pre-commit")
    assert result.exit_code == 2, result.output
    assert not (hooks / "pre-commit").exists()


def test_git_s_own_files_are_known_by_identity_not_by_spelling(make_repo, tmp_path) -> None:
    """On a case-insensitive file system (macOS by default) `.GIT` is the
    git folder, and a name in another Unicode normalisation is the same folder, though the paths
    differ. Folders are compared by (device, inode): here a Place whose paths are spelt nothing
    like the repository's, but whose identities are its, still refuses its git folder."""
    from sijl.git import GitRunner
    from sijl.outfile import check, places

    repo = app_repo(make_repo)
    real = places([found_repo(repo.path)], GitRunner())[0]
    elsewhere = Place("app", (Path("/Nowhere/APP"),), (Path("/Nowhere/APP/.GIT"),),
                      real.tree_ids, real.git_ids)  # fmt: skip
    with pytest.raises(OutRefused, match="git's own files"):
        check(repo.path / ".git" / "config.md", [elsewhere], cwd=tmp_path)
    with pytest.raises(OutRefused, match="would write into"):
        check(Path("review.md"), [elsewhere], cwd=repo.path)


def found_repo(path: Path):
    from sijl.discover import discover
    from sijl.git import GitRunner

    return discover([], [path], GitRunner()).repos[0]
