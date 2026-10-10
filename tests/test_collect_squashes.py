"""sijl.collect, what squash merges need: your one-parent commits that GitHub committed
on each remote default branch's first-parent chain, dated on the period's first day or later —
squash merges and web edits, told apart later by squash.py. Pass 1 already read them: no extra git
call."""

from pathlib import Path

from sijl.collect import collect
from sijl.discover import discover
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from tests.gitfactory import COLLEAGUE, GITHUB_WEB, ME, ME_GITHUB, REVIEW_PERIOD, RepoBuilder
from tests.test_collect import RecordingGit

MINE = IdentitySet.from_emails([ME.email, ME_GITHUB.email])


def feature(repo: RepoBuilder, branch: str, at: str, start: str = "main") -> None:
    repo.branch(branch, start)
    repo.switch(branch)
    repo.commit(f"feat: {branch}", at=at, files={f"{branch}.txt": at})
    repo.switch("main")


def history(make_repo, tmp_path: Path) -> tuple[RepoBuilder, dict[str, str]]:
    repo = make_repo("app")
    repo.commit("chore: start", at="2026-03-20T09:00:00+01:00", who=COLLEAGUE)
    shas: dict[str, str] = {}
    feature(repo, "fix/old", "2026-03-29T09:00:00+02:00")
    shas["before"] = repo.github_squash("fix/old", number=1, title="Old",
                                        at="2026-03-30T09:00:00+02:00")  # fmt: skip
    feature(repo, "fix/a", "2026-04-10T09:00:00+02:00")
    shas["inside"] = repo.github_squash("fix/a", number=2, title="A", at="2026-04-11T09:00:00Z")
    feature(repo, "fix/theirs", "2026-04-12T09:00:00+02:00")
    repo.github_squash("fix/theirs", number=3, title="Theirs", at="2026-04-13T09:00:00Z",
                       who=COLLEAGUE)  # fmt: skip
    shas["edit"] = repo.commit("Update README.md", at="2026-04-14T09:00:00Z", who=ME_GITHUB,
                               committer=GITHUB_WEB, files={"README.md": "x"})  # fmt: skip
    feature(repo, "fix/c", "2026-06-20T09:00:00+02:00")
    shas["after"] = repo.github_squash("fix/c", number=4, title="C", at="2026-07-02T09:00:00Z")
    # A pull request squashed into another branch: on that branch, not on the mainline's line.
    feature(repo, "feature/base", "2026-05-01T09:00:00+02:00")
    feature(repo, "feature/top", "2026-05-02T09:00:00+02:00", start="feature/base")
    repo.switch("feature/base")
    shas["elsewhere"] = repo.github_squash("feature/top", number=5, title="Top",
                                           at="2026-05-03T09:00:00Z")  # fmt: skip
    repo.switch("main")
    repo.publish(tmp_path / "remotes" / "app.git", "--all")
    return repo, shas


def test_your_github_commits_on_a_mainline_are_kept_from_the_period_on(make_repo, tmp_path):
    repo, shas = history(make_repo, tmp_path)
    git = GitRunner()
    [rc] = collect(discover([], [repo.path], git).repos, MINE, REVIEW_PERIOD, git).repos
    # Newest first; after the period too (a branch written in it may be merged after it) and in
    # the lookback before it (a branch written there was merged there); not a colleague's, not
    # one off the mainline's own line.
    assert [c.sha for c in rc.github_commits] == [
        shas["after"],
        shas["edit"],
        shas["inside"],
        shas["before"],
    ]


def test_finding_them_costs_no_git_call(make_repo, tmp_path) -> None:
    repo, _ = history(make_repo, tmp_path)
    git = RecordingGit()
    collect(discover([], [repo.path], git).repos, MINE, REVIEW_PERIOD, git)
    assert [c[0] for c in git.calls if c[0] == "log"] == ["log"]  # pass 1 only


def test_your_commits_in_the_lookback_are_read_for_linking_but_never_counted(make_repo, tmp_path):
    repo, shas = history(make_repo, tmp_path)
    git = GitRunner()
    [rc] = collect(discover([], [repo.path], git).repos, MINE, REVIEW_PERIOD, git).repos
    before = {c.sha for c in rc.lookback}
    assert {shas["before"]} <= {c.sha for c in rc.github_commits}  # 30 March: in the lookback
    assert before and before.isdisjoint({c.sha for c in (*rc.commits, *rc.local_only)})
    assert all(sha in rc.reach for sha in before)  # their reach, in the same rev-list calls


def test_the_lookback_moves_only_git_s_prefilter(make_repo, tmp_path) -> None:
    repo, _ = history(make_repo, tmp_path)
    git = RecordingGit()
    collect(discover([], [repo.path], git).repos, MINE, REVIEW_PERIOD, git)
    [log] = [c for c in git.calls if c[0] == "log"]
    assert "--since=2025-12-30T00:00:00" in log  # 90 days, then git's two days of slack
