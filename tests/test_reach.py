"""sijl.collect: which refs are read, and how far each of your commits travelled —
mainline, pushed, local or worktree. Work on no remote branch is kept apart from the totals."""

from pathlib import Path

import pytest

from sijl.collect import collect
from sijl.discover import discover
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.models import Reach
from tests.gitfactory import ME, REVIEW_PERIOD, RepoBuilder, identity_env

MINE = IdentitySet.from_emails([ME.email])


def at(day: int) -> str:
    return f"2026-05-{day:02d}T10:00:00+02:00"


def run(*paths: Path, include_worktrees: bool = False):
    git = GitRunner()
    repos = discover([], list(paths), git).repos
    return collect(repos, MINE, REVIEW_PERIOD, git, include_worktrees=include_worktrees)


def counted(result) -> set[str]:
    return {c.subject for c in result.commits}


def unpushed(result) -> set[str]:
    return {c.subject for c in result.local_only}


def reach(result) -> dict[str, Reach]:
    return {
        c.subject: rc.reach[c.sha] for rc in result.repos for c in (*rc.commits, *rc.local_only)
    }


def everywhere(make_repo, tmp_path: Path) -> RepoBuilder:
    """One repository with every shape: main pushed, a branch pushed and then deleted locally, a
    branch never pushed, a stash entry carrying a ticket key, and a detached linked worktree."""
    remote = tmp_path / "remotes" / "app.git"
    repo = make_repo("app")
    repo.commit("feat: on main, pushed", at=at(4))
    repo.publish(remote, "main")
    repo.branch("feature/pushed")
    repo.switch("feature/pushed")
    repo.commit("feat: pushed branch, deleted locally", at=at(5))
    repo.publish(remote, "feature/pushed")
    repo.switch("main")
    repo.git("branch", "-D", "feature/pushed")
    repo.branch("feature/local")
    repo.switch("feature/local")
    repo.commit("feat: never pushed", at=at(6))
    repo.switch("main")
    repo.stash("WIP PROJ-7 half done", files={"history.txt": "changed\n"}, at=at(7))
    scratch = repo.add_worktree(tmp_path / "scratch")
    scratch.commit("feat: experiment in a detached worktree", at=at(8))
    return repo


def test_every_commit_is_marked_by_how_far_it_travelled(make_repo, tmp_path) -> None:
    result = run(everywhere(make_repo, tmp_path).path)
    assert reach(result) == {
        "feat: on main, pushed": Reach.MAINLINE,  # no origin/HEAD: origin/main is the fallback
        "feat: pushed branch, deleted locally": Reach.PUSHED,
        "feat: never pushed": Reach.LOCAL,
    }
    assert counted(result) == {"feat: on main, pushed", "feat: pushed branch, deleted locally"}
    assert unpushed(result) == {"feat: never pushed"}


def test_stash_entries_and_detached_worktrees_never_appear_by_default(make_repo, tmp_path) -> None:
    result = run(everywhere(make_repo, tmp_path).path)
    seen = counted(result) | unpushed(result)
    assert not any("PROJ-7" in s or "experiment" in s for s in seen)


def test_include_worktrees_adds_detached_worktree_work_as_worktree_only(make_repo, tmp_path):
    result = run(everywhere(make_repo, tmp_path).path, include_worktrees=True)
    assert reach(result)["feat: experiment in a detached worktree"] == Reach.WORKTREE
    assert "feat: experiment in a detached worktree" in unpushed(result)
    assert result.include_worktrees


@pytest.mark.parametrize(
    "ref", ["refs/original/refs/heads/main", "refs/prefetch/remotes/origin/main", "refs/keep/x"]
)
def test_refs_outside_branches_tags_and_remotes_are_never_read(make_repo, ref: str) -> None:
    repo = make_repo("app")
    repo.commit("feat: on main", at=at(4))
    repo.branch("tmp")
    repo.switch("tmp")
    hidden = repo.commit("feat: only under a hidden ref", at=at(5))
    repo.switch("main")
    repo.git("update-ref", ref, hidden)
    repo.git("branch", "-D", "tmp")
    assert counted(run(repo.path)) | unpushed(run(repo.path)) == {"feat: on main"}


def test_notes_never_appear(make_repo) -> None:
    repo = make_repo("app")
    repo.commit("feat: on main", at=at(4))
    repo.git("notes", "add", "-m", "PROJ-9 a note", "HEAD", env=identity_env(ME, at(5), ME, at(5)))
    result = run(repo.path)
    assert counted(result) | unpushed(result) == {"feat: on main"}


def test_a_commit_only_a_tag_holds_is_local(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: on main", at=at(4))
    repo.publish(tmp_path / "remotes" / "app.git", "main")
    repo.branch("release")
    repo.switch("release")
    repo.commit("chore: release build", at=at(5))
    repo.git("tag", "-a", "-m", "v1", "v1", env=identity_env(ME, at(5), ME, at(5)))
    repo.git("tag", "a-tree", "HEAD^{tree}")
    repo.switch("main")
    repo.git("branch", "-D", "release")
    result = run(repo.path)
    assert reach(result)["chore: release build"] == Reach.LOCAL
    assert unpushed(result) == {"chore: release build"}
    assert result.tags_ignored == 1  # a-tree points at a tree, not a commit


def test_a_detached_main_checkout_is_read(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: on main", at=at(4))
    repo.publish(tmp_path / "remotes" / "app.git", "main")
    repo.git("switch", "-q", "--detach")
    repo.commit("feat: on a detached HEAD", at=at(5))
    assert unpushed(run(repo.path)) == {"feat: on a detached HEAD"}


def test_mainline_is_the_remote_default_branch(make_repo, tmp_path) -> None:
    upstream = make_repo("upstream")
    upstream.commit("feat: base", at=at(4))
    upstream.branch("develop")
    upstream.switch("develop")
    upstream.commit("feat: on develop", at=at(5))
    upstream.switch("main")
    upstream.commit("feat: only on main", at=at(6))
    work = upstream.clone(tmp_path / "work")
    work.git("remote", "set-head", "origin", "develop")
    assert reach(run(work.path)) == {
        "feat: base": Reach.MAINLINE,
        "feat: on develop": Reach.MAINLINE,
        "feat: only on main": Reach.PUSHED,
    }


def test_a_repository_without_a_remote_counts_everything(make_repo) -> None:
    repo = make_repo("solo")
    repo.commit("feat: a local project", at=at(4))
    result = run(repo.path)
    assert counted(result) == {"feat: a local project"}
    assert unpushed(result) == set()
    assert reach(result) == {"feat: a local project": Reach.LOCAL}
    assert [rc.repo.name for rc in result.repos_without_remote] == ["solo"]


def test_a_commit_in_two_clones_keeps_its_best_reached_copy(make_repo, tmp_path) -> None:
    app = make_repo("app")
    app.commit("feat: pushed", at=at(4))
    app.publish(tmp_path / "remotes" / "app.git", "main")
    app.commit("feat: local here, on the copy's remote", at=at(5))
    copy = app.clone(tmp_path / "app-copy")  # its origin is app, so origin/main has the commit
    result = run(app.path, copy.path)
    assert unpushed(result) == set()
    owners = [
        rc.repo.name
        for rc in result.repos
        for c in rc.commits
        if c.subject == "feat: local here, on the copy's remote"
    ]
    assert owners == ["app-copy"]
    assert result.seen_in_other_clones == 2
