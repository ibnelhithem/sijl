"""The opt-in git hooks in scripts/hooks (`git config core.hooksPath scripts/hooks`): the only
checks that run before anything is public (ADR 0003).

- `pre-commit` scans the staged changes for credentials with Betterleaks. CI has no scanner, so
  these tests put a stand-in named `betterleaks` on PATH: it records how the hook called it and
  exits as told.
- `pre-push` runs the publication guard on every commit the remote does not have yet and on the
  name of each ref pushed, with a canary list — never the real one.

Without the scanner or the list a hook warns loudly and lets the commit or push go: never silent.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.gitfactory import RepoBuilder, Who, identity_env

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "scripts" / "hooks"
CANARY = "zqcanary"  # a made-up word; nothing real
AT = "2026-05-04T10:00:00+02:00"
PLAIN = Who("Alex Example", "alex@example.test")
FAKE_SCANNER = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_ARGS"
exit "$FAKE_EXIT"
"""


def _env(**extra: str) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name != "PUBLICATION_GUARD_LIST"}
    return {**env, **identity_env(PLAIN, AT, PLAIN, AT), **extra}


def _git(cwd: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """git with the hooks under test, and nothing else from this machine."""
    command = ["git", "-c", f"core.hooksPath={HOOKS}", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def _without_a_scanner(path: str) -> str:
    return os.pathsep.join(
        folder for folder in path.split(os.pathsep) if not (Path(folder) / "betterleaks").exists()
    )


def test_both_hooks_are_executable_in_git() -> None:
    # The executable bit on disk is not enough: a fresh clone gets the mode git recorded.
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-s", "scripts/hooks"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    modes = {line.split("\t")[1]: line.split()[0] for line in listed.splitlines()}
    assert modes == {"scripts/hooks/pre-commit": "100755", "scripts/hooks/pre-push": "100755"}


@pytest.mark.parametrize("hook", ["pre-commit", "pre-push"])
def test_the_hooks_are_posix_shell(hook: str) -> None:
    text = (HOOKS / hook).read_text()
    assert text.startswith("#!/bin/sh\n")  # macOS's sh is bash 3.2; Debian's is dash
    assert subprocess.run(["sh", "-n", str(HOOKS / hook)], check=False).returncode == 0
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for bashism in (r"\[\[", r"(?m)^\s*(function|local|declare)\s", "<<<", r"\$'", "pipefail"):
        assert not re.search(bashism, code), bashism


# ------------------------------------------------------------------------------- pre-commit


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    repo = RepoBuilder(tmp_path / "repo")
    repo.commit("chore: start", at=AT, who=PLAIN)
    (repo.path / "config.txt").write_text("a change\n")
    repo.git("add", "config.txt")
    return repo.path


def _commit_with_scanner(tmp_path: Path, repo: Path, exit_code: int) -> tuple[int, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scanner = bin_dir / "betterleaks"
    scanner.write_text(FAKE_SCANNER)
    scanner.chmod(0o755)
    calls = tmp_path / "args.txt"
    env = _env(
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        FAKE_ARGS=str(calls),
        FAKE_EXIT=str(exit_code),
    )
    result = _git(repo, "commit", "-q", "-m", "feat: change", env=env)
    return result.returncode, calls.read_text().splitlines()


def test_pre_commit_stops_a_commit_when_the_scanner_finds_a_credential(
    tmp_path: Path, staged: Path
) -> None:
    head = RepoBuilder.existing(staged).head()
    code, _ = _commit_with_scanner(tmp_path, staged, exit_code=1)
    assert code != 0
    assert RepoBuilder.existing(staged).head() == head


def test_pre_commit_scans_the_staged_changes_redacted_and_offline(
    tmp_path: Path, staged: Path
) -> None:
    code, args = _commit_with_scanner(tmp_path, staged, exit_code=0)
    assert code == 0
    assert args[:3] == ["git", "--pre-commit", "--staged"]
    assert "--redact" in args
    assert args[args.index("--report-template") + 1].endswith("scripts/secret-scan.tmpl")
    assert not any(arg.startswith("--validation") for arg in args)  # it sends secrets out


def test_pre_commit_without_the_scanner_warns_and_lets_the_commit_go(staged: Path) -> None:
    head = RepoBuilder.existing(staged).head()
    env = _env(PATH=_without_a_scanner(os.environ["PATH"]))
    result = _git(staged, "commit", "-q", "-m", "feat: change", env=env)
    assert result.returncode == 0
    assert "NOT scanned" in result.stderr
    assert RepoBuilder.existing(staged).head() != head


# --------------------------------------------------------------------------------- pre-push


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    return bare


@pytest.fixture
def canary_list(tmp_path: Path) -> Path:
    listing = tmp_path / "canary-list.txt"
    listing.write_text(f"{CANARY}\n")
    return listing


def _repo(tmp_path: Path, remote: Path) -> RepoBuilder:
    repo = RepoBuilder(tmp_path / "repo")
    repo.commit("chore: start", at=AT, who=PLAIN)
    repo.git("remote", "add", "origin", str(remote))
    return repo


def _push(repo: RepoBuilder, refspec: str, listing: Path | None) -> subprocess.CompletedProcess:
    extra = {"PUBLICATION_GUARD_LIST": str(listing)} if listing else {}
    return _git(repo.path, "push", "-q", "origin", refspec, env=_env(**extra))


def test_pre_push_lets_clean_commits_go(tmp_path: Path, remote: Path, canary_list: Path) -> None:
    repo = _repo(tmp_path, remote)
    result = _push(repo, "HEAD:refs/heads/main", canary_list)
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr


@pytest.mark.parametrize(
    ("commit", "place"),
    [
        ({"subject": f"fix the {CANARY} parser"}, "message:1"),
        ({"who": Who("Alex", f"alex@{CANARY}.example")}, "author email:1"),
        ({"files": {"notes.md": f"fine\n{CANARY}\n"}}, "notes.md:2"),
    ],
    ids=["message", "author-email", "file"],
)
def test_pre_push_stops_a_listed_word_in_a_new_commit_without_printing_it(
    tmp_path: Path, remote: Path, canary_list: Path, commit: dict, place: str
) -> None:
    repo = _repo(tmp_path, remote)
    commit.setdefault("subject", "feat: change")
    commit.setdefault("files", {"change.txt": "a change\n"})
    sha = repo.commit(commit.pop("subject"), at=AT, **{"who": PLAIN, **commit})
    result = _push(repo, "HEAD:refs/heads/main", canary_list)
    assert result.returncode != 0
    output = result.stdout + result.stderr  # git shows a hook's output on stderr
    assert f"commit {sha[:12]}, {place}: list entry 1" in output
    assert CANARY not in output.lower()
    sent = ["git", "-C", str(remote), "rev-parse", "-q", "--verify", "main"]
    assert subprocess.run(sent, capture_output=True, check=False).returncode != 0


def test_pre_push_stops_a_listed_word_in_the_branch_name(
    tmp_path: Path, remote: Path, canary_list: Path
) -> None:
    repo = _repo(tmp_path, remote)
    result = _push(repo, f"HEAD:refs/heads/{CANARY}-topic", canary_list)
    assert result.returncode != 0
    assert "the name of the pushed ref:1: list entry 1" in result.stdout + result.stderr


def test_pre_push_does_not_check_again_what_the_remote_already_has(
    tmp_path: Path, remote: Path, canary_list: Path
) -> None:
    repo = _repo(tmp_path, remote)
    repo.commit(f"feat: {CANARY}", at=AT, who=PLAIN, files={"old.md": f"{CANARY}\n"})
    repo.git("push", "-q", "origin", "HEAD:refs/heads/main")  # gitfactory runs git without hooks
    repo.commit("feat: clean", at=AT, who=PLAIN, files={"new.md": "fine\n"})
    result = _push(repo, "HEAD:refs/heads/main", canary_list)
    assert result.returncode == 0, result.stdout + result.stderr


def test_pre_push_without_the_list_warns_and_lets_the_push_go(tmp_path: Path, remote: Path) -> None:
    repo = _repo(tmp_path, remote)
    repo.commit(f"feat: {CANARY}", at=AT, who=PLAIN)
    result = _push(repo, "HEAD:refs/heads/main", None)
    assert result.returncode == 0
    assert "NOT checked" in result.stderr


def test_pre_push_reads_the_list_from_git_config_too(
    tmp_path: Path, remote: Path, canary_list: Path
) -> None:
    repo = _repo(tmp_path, remote)
    repo.git("config", "publicationGuard.list", str(canary_list))
    repo.commit(f"feat: {CANARY}", at=AT, who=PLAIN)
    result = _push(repo, "HEAD:refs/heads/main", None)
    assert result.returncode != 0
    assert "message:1: list entry 1" in result.stdout + result.stderr
