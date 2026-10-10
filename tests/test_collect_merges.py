"""sijl.collect, the merges deliveries need: every merge on each remote's default branch
— its first-parent chain, anyone's — dated in the period or later, the commit just below the
oldest, the mainlines' names, and your own merge commits wherever they are. Pass 1 already read
them all: no extra git call."""

from pathlib import Path

from sijl.collect import Mainline, collect
from sijl.discover import discover
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from tests.gitfactory import COLLEAGUE, ME, REVIEW_PERIOD, RepoBuilder
from tests.test_collect import RecordingGit

MINE = IdentitySet.from_emails([ME.email])


def feature(repo: RepoBuilder, branch: str, at: str, who=ME) -> None:
    repo.branch(branch, "main")
    repo.switch(branch)
    repo.commit(f"feat: {branch}", at=at, who=who, files={f"{branch}.txt": at})  # no conflicts
    repo.switch("main")


def history(make_repo, tmp_path: Path) -> tuple[RepoBuilder, dict[str, str]]:
    """main: a merge two days before the period (read by pass 1's slack), one inside, one after;
    a sync merge on the feature branch; a direct commit between."""
    repo = make_repo("app")
    repo.commit("chore: start", at="2026-03-20T09:00:00+01:00")
    feature(repo, "feature/old", "2026-03-30T09:00:00+02:00")
    shas = {"old": repo.gitlab_mr_merge("feature/old", number=1, title="Old", who=COLLEAGUE,
                                        at="2026-03-31T09:00:00+02:00")}  # fmt: skip
    feature(repo, "feature/b", "2026-04-10T09:00:00+02:00")
    shas["direct"] = repo.commit(
        "fix: straight onto main",
        at="2026-04-10T12:00:00+02:00",
        who=COLLEAGUE,
        files={"direct.txt": "fix"},
    )
    repo.switch("feature/b")
    shas["sync"] = repo.merge(
        "main", message_text="Merge branch 'main' into feature/b\n", at="2026-04-11T09:00:00+02:00"
    )
    repo.switch("main")
    shas["inside"] = repo.gitlab_mr_merge(
        "feature/b", number=2, title="B", at="2026-04-12T09:00:00+02:00", who=COLLEAGUE
    )
    feature(repo, "feature/c", "2026-07-02T09:00:00+02:00")
    shas["after"] = repo.gitlab_mr_merge("feature/c", number=3, title="C",
                                         at="2026-07-03T09:00:00+02:00")  # fmt: skip
    repo.publish(tmp_path / "remotes" / "app.git", "main")
    return repo, shas


def collected(*paths: Path, git: GitRunner | None = None):
    git = git or GitRunner()
    return collect(discover([], list(paths), git).repos, MINE, REVIEW_PERIOD, git)


def test_the_mainline_merges_from_the_period_on_newest_first(make_repo, tmp_path) -> None:
    repo, shas = history(make_repo, tmp_path)
    [rc] = collected(repo.path).repos
    [mainline] = rc.mainlines
    assert [m.sha for m in mainline.merges] == [shas["after"], shas["inside"]]
    # The first parent of the oldest: what is below it was on the mainline before the period.
    assert mainline.base == shas["direct"]
    assert rc.mainline_names == ("main",)


def test_your_merges_are_kept_wherever_they_are_but_only_inside_the_period(make_repo, tmp_path):
    repo, shas = history(make_repo, tmp_path)
    result = collected(repo.path)
    [rc] = result.repos
    assert [m.sha for m in rc.merges] == [shas["sync"]]  # yours: the sync merge on feature/b
    assert result.merges_left_out == 1
    assert all(not c.is_merge for c in rc.commits)  # merges are still never commits


def test_reading_the_mainline_merges_costs_no_git_call(make_repo, tmp_path) -> None:
    repo, _ = history(make_repo, tmp_path)
    git = RecordingGit()
    collected(repo.path, git=git)
    assert [call[0] for call in git.calls].count("log") == 1  # pass 1 alone


def test_without_a_remote_nothing_is_a_mainline(make_repo) -> None:
    repo = make_repo("local")
    repo.commit("chore: start", at="2026-04-01T09:00:00+02:00")
    feature(repo, "feature/x", "2026-04-02T09:00:00+02:00")
    sha = repo.gitlab_mr_merge("feature/x", number=4, title="X", at="2026-04-03T09:00:00+02:00")
    [rc] = collected(repo.path).repos
    assert (rc.mainlines, rc.mainline_names) == ((), ())
    assert [m.sha for m in rc.merges] == [sha]


def test_a_remote_default_branch_named_otherwise_is_the_mainline(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("chore: start", at="2026-04-01T09:00:00+02:00")
    feature(repo, "feature/x", "2026-04-02T09:00:00+02:00")
    sha = repo.gitlab_mr_merge("feature/x", number=5, title="X", at="2026-04-03T09:00:00+02:00",
                               into="trunk")  # fmt: skip
    repo.publish(tmp_path / "remotes" / "app.git", "main:trunk")
    repo.git("remote", "set-head", "origin", "trunk")
    [rc] = collected(repo.path).repos
    [mainline] = rc.mainlines
    assert isinstance(mainline, Mainline) and [m.sha for m in mainline.merges] == [sha]
    assert rc.mainline_names == ("trunk",)


def test_a_merge_in_two_clones_is_one_of_your_merges(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("chore: start", at="2026-04-01T09:00:00+02:00")
    feature(repo, "feature/x", "2026-04-02T09:00:00+02:00")
    repo.gitlab_mr_merge("feature/x", number=6, title="X", at="2026-04-03T09:00:00+02:00")
    repo.clone(tmp_path / "app-copy")
    result = collected(repo.path, tmp_path / "app-copy")
    assert result.merges_left_out == 1
    assert [len(rc.merges) for rc in result.repos] == [1, 1]  # each clone keeps its own


def test_merges_off_the_mainline_from_the_period_on_are_kept_too(make_repo, tmp_path) -> None:
    repo, shas = history(make_repo, tmp_path)
    [rc] = collected(repo.path).repos
    assert [m.sha for m in rc.side_merges] == [shas["sync"]]  # anyone's; not the mainline's
