"""The test repo builder itself: pinned identities and dates, deterministic SHAs, and immunity
to a hostile git config. Every accuracy test in the suite stands on these guarantees."""

from datetime import datetime, timedelta
from pathlib import Path

from tests.gitfactory import COLLEAGUE, GITHUB_WEB, ME, ME_GITHUB, RepoBuilder


def _scenario(repo: RepoBuilder) -> list[str]:
    """A small history: a commit, a colleague's branch commit, and a merge of that branch."""
    shas = [repo.commit("feat: first", at="2026-04-07T09:00:00+02:00")]
    repo.branch("feature/x")
    repo.switch("feature/x")
    shas.append(repo.commit("fix: on a branch", at="2026-04-08T10:00:00+02:00", who=COLLEAGUE))
    repo.switch("main")
    shas.append(
        repo.merge(
            "feature/x",
            message_text="Merge branch 'feature/x' into 'main'\n",
            at="2026-04-09T11:00:00+02:00",
        )
    )
    return shas


def test_the_same_scenario_builds_identical_shas(make_repo) -> None:
    assert _scenario(make_repo("first")) == _scenario(make_repo("second"))


def test_author_committer_and_both_dates_are_pinned(make_repo) -> None:
    repo = make_repo()
    sha = repo.commit(
        "docs: written by me, committed by a colleague",
        at="2026-05-04T10:00:00+02:00",
        committer=COLLEAGUE,
        committed_at="2026-06-01T08:00:00+00:00",
    )
    name, email, authored, c_name, c_email, committed = (
        repo.git("show", "-s", "--format=%an|%ae|%aI|%cn|%ce|%cI", sha).strip().split("|")
    )
    assert (name, email, c_name, c_email) == (ME.name, ME.email, COLLEAGUE.name, COLLEAGUE.email)
    # Compare instants and offsets, never git's text: git 2.43 prints a UTC date as
    # "+00:00" in %aI/%cI, newer git (the CI runner's) prints "Z" for the same commit.
    authored_at = datetime.fromisoformat(authored)
    assert authored_at == datetime.fromisoformat("2026-05-04T10:00:00+02:00")
    assert authored_at.utcoffset() == timedelta(hours=2)  # the author's own timezone survives
    assert datetime.fromisoformat(committed) == datetime.fromisoformat("2026-06-01T08:00:00+00:00")


def test_body_and_trailers_are_stored_verbatim(make_repo) -> None:
    repo = make_repo()
    sha = repo.commit(
        "feat(api): add the thing (ABC-12)",
        at="2026-05-04T10:00:00+02:00",
        body="Why it matters.",
        trailers=[f"Co-authored-by: {COLLEAGUE.name} <{COLLEAGUE.email}>"],
    )
    assert repo.git("log", "-1", "--format=%B", sha) == (
        "feat(api): add the thing (ABC-12)\n\nWhy it matters.\n\n"
        f"Co-authored-by: {COLLEAGUE.name} <{COLLEAGUE.email}>\n\n"
    )
    trailer = repo.git("log", "-1", "--format=%(trailers:key=Co-authored-by,valueonly)", sha)
    assert trailer.strip() == f"{COLLEAGUE.name} <{COLLEAGUE.email}>"


def test_files_are_written_and_deleted(make_repo) -> None:
    repo = make_repo()
    repo.commit("feat: add", at="2026-05-04T10:00:00+02:00", files={"src/a.py": "x = 1\n"})
    repo.commit("refactor: remove", at="2026-05-05T10:00:00+02:00", files={"src/a.py": None})
    assert repo.git("ls-files").split() == []
    stat = repo.git("log", "--format=", "--numstat", "-1")
    assert stat.split() == ["0", "1", "src/a.py"]


def test_a_hostile_global_config_changes_nothing(make_repo, hermetic_git: Path) -> None:
    clean = _scenario(make_repo("clean"))

    hooks = hermetic_git.parent / "hooks"
    hooks.mkdir()
    for hook in ("pre-commit", "commit-msg", "pre-merge-commit"):
        (hooks / hook).write_text("#!/bin/sh\nexit 1\n")
        (hooks / hook).chmod(0o755)
    hermetic_git.write_text(
        "[commit]\n\tgpgsign = true\n"
        f"[core]\n\thooksPath = {hooks}\n\tautocrlf = true\n"
        "[merge]\n\tlog = true\n\tff = only\n"
        "[user]\n\tuseConfigOnly = true\n"
        "[init]\n\tdefaultBranch = trunk\n"
    )

    assert _scenario(make_repo("hostile")) == clean


def test_publish_leaves_remote_tracking_refs_like_a_real_push(make_repo, tmp_path: Path) -> None:
    repo = make_repo()
    sha = repo.commit("feat: pushed", at="2026-05-04T10:00:00+02:00")
    repo.publish(tmp_path / "remote.git")
    assert repo.git("rev-parse", "refs/remotes/origin/main").strip() == sha
    local_only = repo.git("rev-list", "HEAD", "--not", "--remotes")
    assert local_only == ""


def _feature(repo: RepoBuilder, branch: str = "feature/x") -> None:
    """main with one commit, and `branch` with a colleague's commit on it; back on main."""
    repo.commit("feat: base", at="2026-05-04T09:00:00+02:00")
    repo.branch(branch)
    repo.switch(branch)
    repo.commit("feat: on the branch", at="2026-05-05T09:00:00+02:00", who=COLLEAGUE)
    repo.switch("main")


def test_a_gitlab_merge_request_merge_looks_like_gitlabs(make_repo) -> None:
    repo = make_repo()
    _feature(repo)
    sha = repo.gitlab_mr_merge(
        "feature/x", number=42, title="Add the x report", at="2026-05-06T10:00:00+02:00"
    )
    assert repo.git("log", "-1", "--format=%B", sha) == (
        "Merge branch 'feature/x' into 'main'\n\nAdd the x report\n\n"
        "See merge request acme/platform/app!42\n\n"
    )
    who = repo.git("show", "-s", "--format=%ae|%ce|%aI|%cI|%P", sha).strip().split("|")
    assert who[:2] == [ME.email, ME.email]  # GitLab's merge button: the merger writes the commit
    assert datetime.fromisoformat(who[2]) == datetime.fromisoformat(who[3])
    assert len(who[4].split()) == 2


def test_a_github_pull_request_merge_looks_like_githubs(make_repo) -> None:
    repo = make_repo()
    _feature(repo, "fix/typo")
    sha = repo.github_pr_merge(
        "fix/typo", number=12, title="Fix a typo", at="2026-05-06T10:00:00+02:00"
    )
    assert repo.git("log", "-1", "--format=%B", sha) == (
        "Merge pull request #12 from alexgh/fix/typo\n\nFix a typo\n\n"
    )
    who = repo.git("show", "-s", "--format=%ae|%ce|%P", sha).strip().split("|")
    # GitHub's web merge: authored by the merger's GitHub identity, committed by GitHub itself.
    assert who[:2] == [ME_GITHUB.email, GITHUB_WEB.email]
    assert len(who[2].split()) == 2


def _two_commits(repo: RepoBuilder, branch: str = "fix/typo") -> list[str]:
    """main with one commit, and `branch` with two of yours on it; back on main."""
    repo.commit("feat: base", at="2026-05-04T09:00:00+02:00")
    repo.branch(branch)
    repo.switch(branch)
    shas = [
        repo.commit(
            "fix: one", at="2026-05-05T09:00:00+02:00", body="Why one.", files={"one.txt": "1"}
        ),
        repo.commit("fix: two", at="2026-05-05T10:00:00+02:00", files={"two.txt": "2"}),
    ]
    repo.switch("main")
    return shas


def test_a_github_squash_looks_like_githubs(make_repo) -> None:
    repo = make_repo()
    one, two = _two_commits(repo)
    base = repo.head()
    sha = repo.github_squash(
        "fix/typo", number=12, title="Fix a typo", at="2026-05-06T10:00:00+02:00"
    )
    # GitHub's default description lists each branch commit's message as a "* " item.
    assert repo.git("log", "-1", "--format=%B", sha) == (
        "Fix a typo (#12)\n\n* fix: one\n\nWhy one.\n\n* fix: two\n\n"
    )
    who = repo.git("show", "-s", "--format=%ae|%ce|%aI|%cI|%P|%T", sha).strip().split("|")
    # Authored by the pull request's author (their GitHub identity), committed by GitHub, one
    # instant; one parent; and the branch's whole change: the tree of the branch tip.
    assert who[:2] == [ME_GITHUB.email, GITHUB_WEB.email]
    assert datetime.fromisoformat(who[2]) == datetime.fromisoformat(who[3])
    assert who[4] == base
    assert who[5] == repo.git("rev-parse", f"{two}^{{tree}}").strip()
    assert one != sha and two != sha


def test_a_one_commit_squash_keeps_that_commits_body_and_any_body_can_be_written(make_repo):
    repo = make_repo()
    _two_commits(repo)
    repo.branch("docs/one")
    repo.switch("docs/one")
    repo.commit("docs: one", at="2026-05-07T09:00:00+02:00", body="Because.", files={"d": "x"})
    repo.switch("main")
    sha = repo.github_squash("docs/one", number=13, title="docs: one", at="2026-05-08T10:00:00Z")
    assert repo.git("log", "-1", "--format=%B", sha) == "docs: one (#13)\n\nBecause.\n\n"
    sha = repo.github_squash("fix/typo", number=14, title="Typo", at="2026-05-09T10:00:00Z",
                             body="Edited by hand.")  # fmt: skip
    assert repo.git("log", "-1", "--format=%B", sha) == "Typo (#14)\n\nEdited by hand.\n\n"


def test_a_github_title_merge_is_a_merge_commit_titled_like_a_squash(make_repo) -> None:
    repo = make_repo()
    _two_commits(repo)
    sha = repo.github_title_merge("fix/typo", number=15, title="Fix a typo",
                                  at="2026-05-06T10:00:00+02:00")  # fmt: skip
    assert repo.git("log", "-1", "--format=%B", sha) == "Fix a typo (#15)\n\n"
    who = repo.git("show", "-s", "--format=%ae|%ce|%P", sha).strip().split("|")
    assert who[:2] == [ME_GITHUB.email, GITHUB_WEB.email]
    assert len(who[2].split()) == 2
