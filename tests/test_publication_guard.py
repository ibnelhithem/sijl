"""The publication guard: no tracked file — and no file of a built wheel or sdist — may hold a word
from the maintainer's private list of words that must never be published.

The matcher lives in `scripts/publication_guard.py` (how a text is cut into candidates, and why a
failure names the entry's number, never the word), shared with the `guard` workflow and the
`pre-push` hook. The list is the file `PUBLICATION_GUARD_LIST` names; the release workflow writes
it from a GitHub Actions secret, and a maintainer keeps their own copy outside the repository.
Without the variable the checks against the list are skipped, with the reason; the tests below
that prove the matcher itself always run, with a harmless canary. To check a built distribution
as well, point `PUBLICATION_GUARD_DIST` at the folder that holds the wheel and the sdist
(`scripts/release-check.sh` and the release workflow do).
"""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from tests.gitfactory import RepoBuilder, Who

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publication_guard.py"
# Read at import, before any fixture changes the environment.
LIST = os.environ.get("PUBLICATION_GUARD_LIST", "")
DIST = os.environ.get("PUBLICATION_GUARD_DIST", "")


def _load_guard():
    spec = importlib.util.spec_from_file_location("publication_guard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()
read_list, hits, tracked_files = guard.read_list, guard.hits, guard.tracked_files
tree, distribution, commits = guard.tree, guard.distribution, guard.commits


def _private_list() -> dict[str, int]:
    """The maintainer's list, or a skip that says why there is none."""
    if not LIST:
        pytest.skip("PUBLICATION_GUARD_LIST is not set: the private word list was not checked")
    path = Path(LIST).expanduser()
    if not path.is_file():
        pytest.skip(f"PUBLICATION_GUARD_LIST names no file ({path}): the list was not checked")
    words = read_list(path)
    assert words, f"{path} holds no word"
    assert ROOT not in path.resolve().parents, "the list must live outside the repository"
    return words


def test_no_tracked_file_holds_a_forbidden_word() -> None:
    forbidden = _private_list()
    files = tracked_files()
    assert len(files) > 50 and Path(__file__).resolve() in files  # the walk saw the project
    assert hits(tree(ROOT), forbidden) == []


def test_no_built_file_holds_a_forbidden_word() -> None:
    forbidden = _private_list()
    if not DIST:
        pytest.skip("PUBLICATION_GUARD_DIST names no folder of built files")
    built = sorted(Path(DIST).glob("*.whl")) + sorted(Path(DIST).glob("*.tar.gz"))
    assert built, f"no wheel or sdist in {DIST}"
    assert hits((pair for path in built for pair in distribution(path)), forbidden) == []


# ------------------------------------------------------------- the scanner, with a canary word

CANARY = "zqcanary"  # a made-up word the tests forbid; nothing real
FORBIDDEN = {CANARY: 1}


def test_a_list_file_is_read_as_one_word_per_line(tmp_path: Path) -> None:
    listing = tmp_path / "forbidden.txt"
    listing.write_text(f"# words never to publish\n\n{CANARY.upper()}\n  x-y  # a compound\nx-y\n")
    assert read_list(listing) == {CANARY: 3, "x-y": 4}


@pytest.mark.parametrize(
    ("written", "forbidden"),
    [
        (f"a line with {CANARY.title()} in it", CANARY),  # a word, in any case
        (f"x = '{CANARY}'", CANARY),
        (f"see {CANARY}-repo/src", f"{CANARY}-repo"),  # a hyphenated name
        (f"git@host:{CANARY}_x.git", f"{CANARY}_x"),
        (f"mail {CANARY}.dev@{CANARY}corp.example", f"{CANARY}.dev@{CANARY}corp.example"),
        (f"mail {CANARY}.dev@{CANARY}corp.example", f"{CANARY}.dev"),  # the address's user
        (f"login {CANARY}.dev1 on the forge", f"{CANARY}.dev1"),
        (f"cd /srv/{CANARY}/repos", f"/{CANARY}/"),  # a folder of a path
        (f"roots = ['~/{CANARY}/code']", f"~/{CANARY}/"),
    ],
)
def test_a_forbidden_word_written_anywhere_is_found(
    tmp_path: Path, written: str, forbidden: str
) -> None:
    (tmp_path / "notes.md").write_text(f"before\n{written}\nafter\n")
    assert hits(tree(tmp_path), {forbidden: 7}) == ["notes.md:2: list entry 7"]


def test_a_failure_never_repeats_the_word(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text(f"{CANARY}\n")
    (found,) = hits(tree(tmp_path), FORBIDDEN)
    assert CANARY not in found


def test_a_forbidden_word_in_a_file_name_is_found(tmp_path: Path) -> None:
    (tmp_path / f"{CANARY}-notes.txt").write_text("nothing here\n")
    assert hits(tree(tmp_path), FORBIDDEN) == [f"{CANARY}-notes.txt (its path):1: list entry 1"]


def test_a_word_that_only_contains_a_forbidden_one_is_not_found(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text(f"{CANARY}x x{CANARY} {CANARY}2\n")
    assert hits(tree(tmp_path), FORBIDDEN) == []


def test_a_forbidden_word_inside_a_wheel_or_an_sdist_is_found(tmp_path: Path) -> None:
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("pkg-1.0.0.dist-info/METADATA", f"Name: pkg\nAuthor: {CANARY}\n")
    sdist = tmp_path / "pkg-1.0.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        data = b"ok\n"
        member = tarfile.TarInfo(f"pkg-1.0.0/{CANARY}/README.md")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    found = hits((pair for path in (wheel, sdist) for pair in distribution(path)), FORBIDDEN)
    assert found == [
        f"{wheel.name}:pkg-1.0.0.dist-info/METADATA:2: list entry 1",
        f"{sdist.name}:pkg-1.0.0/{CANARY}/README.md (its path):1: list entry 1",
    ]


# ------------------------------------- text that is not a file: commits, a pull request's text

AT = "2026-05-04T10:00:00+02:00"
PLAIN = Who("Alex Example", "alex@example.test")


def _one_more_commit(tmp_path: Path, subject: str = "feat: change", **commit) -> tuple[Path, str]:
    """A repository with a clean first commit, then one commit made of `commit`."""
    repo = RepoBuilder(tmp_path / "repo")
    repo.commit("chore: start", at=AT, who=PLAIN)
    commit.setdefault("who", PLAIN)
    commit.setdefault("committer", PLAIN)
    commit.setdefault("files", {"change.txt": "a change\n"})
    return repo.path, repo.commit(subject, at=AT, **commit)


@pytest.mark.parametrize(
    ("commit", "place"),
    [
        ({"subject": f"fix the {CANARY} parser"}, "message:1"),
        ({"body": f"Why:\n\nsee {CANARY}"}, "message:5"),
        ({"who": Who(f"{CANARY} Person", "alex@example.test")}, "author name:1"),
        ({"who": Who("Alex", f"{CANARY}@example.test")}, "author email:1"),
        ({"committer": Who(f"Sam {CANARY}", "sam@example.test")}, "committer name:1"),
        ({"committer": Who("Sam", f"sam@{CANARY}.example")}, "committer email:1"),
        ({"files": {"notes.md": f"fine\n{CANARY}\n"}}, "notes.md:2"),
        ({"files": {f"docs/{CANARY}.md": "fine\n"}}, f"docs/{CANARY}.md (its path):1"),
    ],
    ids=[
        "subject",
        "body",
        "author",
        "author-email",
        "committer",
        "committer-email",
        "file",
        "path",
    ],
)
def test_a_forbidden_word_anywhere_in_a_commit_is_found(
    tmp_path: Path, commit: dict, place: str
) -> None:
    repo, sha = _one_more_commit(tmp_path, **commit)
    found = hits(commits(repo, ["HEAD~1..HEAD"]), FORBIDDEN)
    assert found == [f"commit {sha[:12]}, {place}: list entry 1"]


def test_only_the_commits_asked_for_are_checked_and_a_deleted_file_is_not(tmp_path: Path) -> None:
    repo = RepoBuilder(tmp_path / "repo")
    first = repo.commit(f"feat: {CANARY}", at=AT, who=PLAIN, files={"old.md": f"{CANARY}\n"})
    repo.commit("chore: remove it", at=AT, who=PLAIN, files={"old.md": None})
    assert hits(commits(repo.path, [f"{first}..HEAD"]), FORBIDDEN) == []
    assert (
        hits(commits(repo.path, ["HEAD"]), FORBIDDEN)
        == [
            f"commit {first[:12]}, message:1: list entry 1",  # a first commit is checked in full
            f"commit {first[:12]}, old.md:1: list entry 1",
        ]
    )


def test_commits_already_on_the_remote_are_not_checked_again(tmp_path: Path) -> None:
    # The range the pre-push hook asks for: everything not yet on that remote's branches.
    repo = RepoBuilder(tmp_path / "repo")
    repo.commit(f"feat: {CANARY}", at=AT, who=PLAIN)
    repo.publish(tmp_path / "remote.git", "HEAD:refs/heads/main")
    repo.commit("feat: clean", at=AT, who=PLAIN, files={"new.md": "fine\n"})
    assert hits(commits(repo.path, ["HEAD", "--not", "--remotes=origin"]), FORBIDDEN) == []
    assert hits(commits(repo.path, ["HEAD"]), FORBIDDEN) != []


@pytest.fixture
def canary_list(tmp_path: Path) -> Path:
    listing = tmp_path / "canary-list.txt"
    listing.write_text(f"{CANARY}\n")
    return listing


def _command(
    cwd: Path, *args: str, listing: Path | None = None, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    """scripts/publication_guard.py as CI and the hook run it; never with the real list."""
    env = {name: value for name, value in os.environ.items() if name != "PUBLICATION_GUARD_LIST"}
    if listing is not None:
        env["PUBLICATION_GUARD_LIST"] = str(listing)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_command_reports_pull_request_text_by_place_never_by_word(
    tmp_path: Path, canary_list: Path
) -> None:
    repo, _ = _one_more_commit(tmp_path)
    body = f"Summary\n\nfixes the {CANARY.upper()} case\n"
    result = _command(repo, "text", "pull request body", listing=canary_list, stdin=body)
    assert result.returncode == 1
    assert result.stdout == "pull request body:3: list entry 1\n"
    assert CANARY not in (result.stdout + result.stderr).lower()


def test_the_command_passes_clean_commits_and_says_so(tmp_path: Path, canary_list: Path) -> None:
    repo, _ = _one_more_commit(tmp_path)
    result = _command(repo, "--list", str(canary_list), "commits", "HEAD")
    assert (result.returncode, result.stdout) == (0, "")
    assert "no listed word in" in result.stderr  # a pass is said, never silent


def test_without_a_list_the_command_fails_and_says_why(tmp_path: Path) -> None:
    repo, _ = _one_more_commit(tmp_path)
    result = _command(repo, "tree")
    assert result.returncode == 2
    assert "no word list" in result.stderr


def test_the_command_refuses_an_option_git_would_act_on(tmp_path: Path, canary_list: Path) -> None:
    repo, _ = _one_more_commit(tmp_path)
    result = _command(repo, "commits", "HEAD", "--output=out.txt", listing=canary_list)
    assert result.returncode == 2
    assert not (repo / "out.txt").exists()
