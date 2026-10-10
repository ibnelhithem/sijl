"""sijl.discover: every repository under the roots, exactly once, and every skip explained.
Depth, ownership, symbolic links and `sijl repos` have their own tests (test_discover_walk.py,
test_discover_skips.py, test_cli_repos.py)."""

import os
from pathlib import Path

import pytest

from sijl.discover import discover
from sijl.git import GitRunner
from tests.gitfactory import RepoBuilder, reason_from_git, run_git


def names(found) -> list[str]:
    return sorted(repo.name for repo in found.repos)


def test_repositories_are_found_and_plain_folders_reported(make_repo, tmp_path: Path) -> None:
    root = tmp_path
    make_repo("web-tests").commit("feat: a", at="2026-05-04T10:00:00+02:00")
    make_repo("group/api-tests").commit("feat: b", at="2026-05-04T10:00:00+02:00")
    (root / "notes" / "drafts").mkdir(parents=True)
    found = discover([root], [], GitRunner())
    assert names(found) == ["api-tests", "web-tests"]
    assert [(s.path.name, s.reason) for s in found.skipped] == [
        ("notes", "no git repository inside")
    ]


def test_a_linked_worktree_is_the_same_repository(make_repo, tmp_path: Path) -> None:
    repo = make_repo("main-repo")
    repo.commit("feat: a", at="2026-05-04T10:00:00+02:00")
    repo.add_worktree(tmp_path / "elsewhere" / "wt-feature", branch="feature/x")
    found = discover([tmp_path], [], GitRunner())
    assert names(found) == ["main-repo"]
    assert found.repos[0].path == repo.path.resolve()


def test_two_clones_are_two_repositories(make_repo, tmp_path: Path) -> None:
    original = make_repo("app")
    original.commit("feat: a", at="2026-05-04T10:00:00+02:00")
    original.clone(tmp_path / "app-copy")
    assert names(discover([tmp_path], [], GitRunner())) == ["app", "app-copy"]


def test_bare_repositories_are_found(tmp_path: Path) -> None:
    (tmp_path / "mirror.git").mkdir()
    run_git(tmp_path / "mirror.git", "init", "-q", "--bare", "-b", "main")
    [repo] = discover([tmp_path], [], GitRunner()).repos
    assert (repo.name, repo.bare) == ("mirror", True)


def test_dependency_and_hidden_folders_are_not_searched(make_repo, tmp_path: Path) -> None:
    make_repo("node_modules/left-pad")
    make_repo(".cache/scratch")
    found = discover([tmp_path], [], GitRunner())
    assert found.repos == ()


def test_a_repository_is_not_searched_inside(make_repo, tmp_path: Path) -> None:
    outer = make_repo("outer")
    RepoBuilder(outer.path / "vendor" / "inner")  # a nested repository, e.g. an unregistered copy
    assert names(discover([tmp_path], [], GitRunner())) == ["outer"]


def test_explicit_paths_are_probed_and_explained(make_repo, tmp_path: Path) -> None:
    repo = make_repo("chosen")
    (tmp_path / "plain").mkdir()
    found = discover([], [repo.path, tmp_path / "plain", tmp_path / "missing"], GitRunner())
    assert names(found) == ["chosen"]
    reasons = {s.path.name: s.reason for s in found.skipped}
    assert reasons["missing"] == "does not exist"
    assert reason_from_git(reasons["plain"], "not a git repository")  # from git's words


def test_the_same_repository_given_twice_is_listed_once(make_repo) -> None:
    repo = make_repo("twice")
    found = discover([repo.path.parent], [repo.path], GitRunner())
    assert names(found) == ["twice"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any folder")
def test_an_unreadable_folder_is_a_caveat_not_a_crash(make_repo, tmp_path: Path) -> None:
    make_repo("fine")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        found = discover([tmp_path], [], GitRunner())
    finally:
        locked.chmod(0o755)
    assert names(found) == ["fine"]
    assert [(s.path, s.reason) for s in found.skipped] == [
        (locked, "cannot be read (Permission denied)")
    ]


def test_the_depth_limit_is_disclosed(make_repo, tmp_path: Path) -> None:
    make_repo("a/b/c/d/deep-repo")
    found = discover([tmp_path], [], GitRunner())
    assert found.repos == ()
    [skipped] = found.skipped
    assert skipped.path == tmp_path / "a" / "b" / "c" / "d"
    assert "not searched deeper than 4 levels" in skipped.reason
