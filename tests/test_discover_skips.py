"""sijl.discover: every path that cannot be scanned is skipped with a reason a reader
can act on — and a broken repository is never mistaken for the repository around it."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from sijl.discover import BARE_REFUSED, DUBIOUS, discover, skip_reason
from sijl.git import GitError, GitRunner
from tests.gitfactory import reason_from_git, run_git

AT = "2026-05-04T10:00:00+02:00"


def names(found) -> list[str]:
    return sorted(repo.name for repo in found.repos)


def test_a_broken_git_folder_is_never_read_as_the_repository_around_it(make_repo) -> None:
    # An empty .git folder (an interrupted clone) inside a folder of another repository: without a
    # ceiling git walks up and opens the outer repository, which would be counted in its place.
    outer = make_repo("outer")
    outer.commit("feat: outer work", at=AT)
    hollow = outer.path / "inside" / "hollow"
    (hollow / ".git").mkdir(parents=True)
    walked = discover([outer.path / "inside"], [], GitRunner())
    named = discover([], [hollow], GitRunner())
    for found in (walked, named):
        assert found.repos == ()
        assert [s.path for s in found.skipped] == [hollow]


def test_a_plain_folder_named_inside_a_repository_still_means_that_repository(make_repo) -> None:
    outer = make_repo("outer")
    outer.commit("feat: outer work", at=AT, files={"src/app.py": "print()\n"})
    assert names(discover([], [outer.path / "src"], GitRunner())) == ["outer"]


def test_a_repository_reached_as_a_root_is_probed_where_it_stands(make_repo) -> None:
    repo = make_repo("app")
    repo.commit("feat: a", at=AT)
    [found] = discover([repo.path], [], GitRunner()).repos
    assert found.path == repo.path.resolve() and found.common_dir == (repo.path / ".git").resolve()


# git's own words (git 2.43, LC_ALL=C), whether the path looked like a repository, the reason given.
GIT_SAYS = [
    (
        "fatal: detected dubious ownership in repository at '/x/app'\n"
        "To add an exception for this directory, call:\n\n"
        "\tgit config --global --add safe.directory /x/app\n",
        True,
        DUBIOUS,
    ),
    (
        "fatal: cannot use bare repository '/x/m.git' (safe.bareRepository is 'explicit')\n",
        True,
        BARE_REFUSED,
    ),
    (
        "fatal: invalid gitfile format: /x/app/.git\n",
        True,
        "corrupt: its .git file does not point to a repository",
    ),
    (
        "fatal: not a git repository: /gone/.git/worktrees/wt\n",
        True,
        "broken link: its .git file points to /gone/.git/worktrees/wt, which is not a repository "
        "(moved or deleted)",
    ),
    (
        "fatal: not a git repository (or any of the parent directories): .git\n",
        True,
        "corrupt or incomplete: git cannot open it as a repository",
    ),
    (
        "fatal: not a git repository (or any of the parent directories): .git\n",
        False,
        "not a git repository",
    ),
    (
        "fatal: bad config line 6 in file .git/config\n",
        True,
        "cannot be opened: bad config line 6 in file .git/config",
    ),
    (
        "fatal: unknown repository extension found:\n\tfrobnicate\n",
        True,
        "cannot be opened: unknown repository extension found: frobnicate",
    ),
]


@pytest.mark.parametrize(("stderr", "looked_like_repo", "reason"), GIT_SAYS)
def test_git_s_words_become_a_reason_a_reader_can_act_on(stderr, looked_like_repo, reason) -> None:
    exc = GitError(("rev-parse",), 128, stderr)
    assert skip_reason(exc, looked_like_repo=looked_like_repo) == reason


def test_the_reason_never_tells_anyone_to_trust_the_repository() -> None:
    assert "--add safe.directory" not in DUBIOUS and "never overrides" in DUBIOUS


def test_corrupt_and_broken_repositories_are_skipped_with_their_reason(make_repo, tmp_path) -> None:
    make_repo("good").commit("feat: fine", at=AT)
    (tmp_path / "garbage").mkdir()
    (tmp_path / "garbage" / ".git").write_text("nonsense\n")
    gone = tmp_path / "deleted" / ".git" / "worktrees" / "orphan"
    (tmp_path / "orphan").mkdir()
    (tmp_path / "orphan" / ".git").write_text(f"gitdir: {gone}\n")  # its repository was deleted
    (tmp_path / "hollow" / ".git").mkdir(parents=True)
    badcfg = make_repo("badcfg")
    with (badcfg.path / ".git" / "config").open("a") as config:
        config.write("[core\n")
    newer = make_repo("newer")
    newer.git("config", "core.repositoryformatversion", "1")
    newer.git("config", "extensions.frobnicate", "true")

    found = discover([tmp_path], [], GitRunner())

    assert names(found) == ["good"]
    reasons = {s.path.name: s.reason for s in found.skipped}
    assert sorted(reasons) == ["badcfg", "garbage", "hollow", "newer", "orphan"]  # each listed
    assert reason_from_git(reasons["garbage"], "corrupt: its .git file does not point to")
    assert reason_from_git(reasons["orphan"], "broken link: its .git file points to ", "corrupt")
    assert reason_from_git(reasons["hollow"], "corrupt or incomplete")
    assert reasons["badcfg"].startswith("cannot be opened: ")  # git's words follow
    assert reasons["newer"].startswith("cannot be opened: ")


def _ownership_hook_works(path) -> bool:
    """git's own test switch makes it treat every repository as someone else's — the only way to
    reproduce dubious ownership without root. Older or patched gits may not have it."""
    env = {**os.environ, "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
    probe = subprocess.run(["git", "-C", str(path), "rev-parse"], env=env, capture_output=True)
    return b"dubious ownership" in probe.stderr


def test_a_repository_owned_by_someone_else_is_skipped_and_never_trusted(make_repo, tmp_path):
    make_repo("mine").commit("feat: mine", at=AT)
    theirs = make_repo("theirs")
    theirs.commit("feat: theirs", at=AT)
    if not _ownership_hook_works(theirs.path):
        pytest.skip("this git has no GIT_TEST_ASSUME_DIFFERENT_OWNER switch")
    argvs: list[list[str]] = []

    def runner(argv, **kwargs):
        argvs.append(argv)
        if str(theirs.path) in argv:  # only `theirs` belongs to another user
            kwargs["env"] = {**kwargs["env"], "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
        return subprocess.run(argv, **kwargs)

    found = discover([tmp_path], [], GitRunner(runner=runner))

    assert names(found) == ["mine"]
    assert [(s.path, s.reason) for s in found.skipped] == [(theirs.path, DUBIOUS)]
    assert not any("safe.directory" in arg for argv in argvs for arg in argv)


def test_a_bare_repository_git_is_told_not_to_open_is_skipped_not_overridden(
    hermetic_git, make_repo, tmp_path
) -> None:
    make_repo("app").commit("feat: a", at=AT)
    run_git(tmp_path, "init", "-q", "--bare", "-b", "main", "mirror.git")
    hermetic_git.write_text("[safe]\n\tbareRepository = explicit\n")  # the user's own choice
    found = discover([tmp_path], [], GitRunner())
    assert names(found) == ["app"]
    assert [(s.path.name, s.reason) for s in found.skipped] == [("mirror.git", BARE_REFUSED)]


def test_each_skip_is_listed_once_in_path_order_whatever_the_order_of_roots(make_repo, tmp_path):
    make_repo("b/app").commit("feat: a", at=AT)
    (tmp_path / "a" / "notes").mkdir(parents=True)
    (tmp_path / "b" / "hollow" / ".git").mkdir(parents=True)
    (tmp_path / "b" / ".cache").mkdir()
    (tmp_path / "c").mkdir()
    nested = [tmp_path / "b", tmp_path / "a", tmp_path]  # overlapping roots reach folders twice
    one = discover(nested, [tmp_path / "missing"], GitRunner())
    two = discover(nested[::-1], [tmp_path / "missing"], GitRunner())
    assert one == two
    reasons = {s.path.relative_to(tmp_path).as_posix(): s.reason for s in one.skipped}
    assert list(reasons) == ["a", "b/hollow", "c", "missing"]  # once each, in path order
    assert reasons["a"] == reasons["c"] == "no git repository inside"
    assert reasons["missing"] == "does not exist"
    assert reason_from_git(reasons["b/hollow"], "corrupt")
    assert [s.path for s in one.pruned] == [tmp_path / "b" / ".cache"]


def hollow_inside(make_repo, tmp_path, middle: str):
    """`enc` is a repository; `enc/<middle>/proj` has an empty .git of its own (a broken clone)."""
    enc = make_repo("enc")
    enc.commit("feat: the enclosing repository", at=AT)
    proj = tmp_path / "enc" / middle / "proj"
    (proj / ".git").mkdir(parents=True)
    return proj


def test_a_colon_in_the_parent_folder_does_not_let_git_open_the_repository_around_it(
    make_repo, tmp_path
) -> None:
    # GIT_CEILING_DIRECTORIES is ":"-separated: "…/enc/a:b" becomes two useless entries, so git
    # walks up past it. The probe itself must notice that git opened `enc`, not `proj`.
    proj = hollow_inside(make_repo, tmp_path, "a:b")
    for found in (discover([], [proj], GitRunner()), discover([proj.parent], [], GitRunner())):
        assert found.repos == ()
        [skipped] = found.skipped
        assert skipped.path == proj
        assert skipped.reason == (
            f"opens the repository around it ({tmp_path / 'enc'}) — its own .git is broken or "
            "missing"
        )


def test_a_repo_named_through_a_link_or_dot_dot_is_probed_where_it_really_is(
    make_repo, tmp_path
) -> None:
    proj = hollow_inside(make_repo, tmp_path, "ab")
    (tmp_path / "link").symlink_to(proj)  # --repo link: its parent is tmp_path, proj's is enc/ab
    (tmp_path / "rootlink").symlink_to(proj.parent)
    named = [tmp_path / "link", proj / ".git" / ".."]  # the lexical parent of the second is .git
    for found in (
        discover([], named, GitRunner()),
        discover([tmp_path / "rootlink"], [], GitRunner()),
    ):
        assert found.repos == (), found  # never `enc`
        assert all(
            reason_from_git(s.reason, "corrupt", "opens the repository around")
            for s in found.skipped
        )


def test_a_pipe_instead_of_head_is_never_opened(make_repo, tmp_path) -> None:
    # A FIFO named HEAD would make git wait for a writer until the timeout, per folder.
    make_repo("good").commit("feat: a", at=AT)
    trap = tmp_path / "trap" / ".git"
    (trap / "objects").mkdir(parents=True)
    (trap / "refs").mkdir()
    os.mkfifo(trap / "HEAD")
    started = time.monotonic()
    found = discover([tmp_path], [], GitRunner())
    assert time.monotonic() - started < 5
    assert names(found) == ["good"]
    assert [(s.path, s.reason) for s in found.skipped] == [
        (tmp_path / "trap", "its HEAD is not a regular file (a pipe or a device?) — not opened")
    ]


def test_a_probe_that_hangs_ends_at_the_short_probe_timeout_and_says_so(tmp_path) -> None:
    # Behind a .git file the git dir is git's to open, so its HEAD is not checked first: the
    # probe's own short timeout ends the wait, and the reason says what happened.
    gd = tmp_path / "gd"
    (gd / "objects").mkdir(parents=True)
    (gd / "refs").mkdir()
    os.mkfifo(gd / "HEAD")
    (tmp_path / "root" / "wt").mkdir(parents=True)
    (tmp_path / "root" / "wt" / ".git").write_text(f"gitdir: {gd}\n")
    started = time.monotonic()
    found = discover([tmp_path / "root"], [], GitRunner(probe_timeout=1))
    assert time.monotonic() - started < 5
    assert [(s.path, s.reason) for s in found.skipped] == [
        (
            tmp_path / "root" / "wt",
            "git gave no answer (timed out after 1 s) — a pipe, a device or a hung disk? "
            "Not scanned",
        )
    ]


def test_discovery_probes_get_10_seconds_and_history_reads_keep_120() -> None:
    timeouts = []

    def run(args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, b"", b"")

    git = GitRunner(runner=run)
    git.run_here(Path("/src/app"), "rev-parse")
    git.run_quick(Path("/src/app"), "worktree", "list")
    git.run(Path("/src/app"), "log")
    assert timeouts == [10, 10, 120]
