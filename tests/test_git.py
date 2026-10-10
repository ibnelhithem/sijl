"""sijl.git: the only place git runs. A scrubbed environment, a version gate, and parsers
for the NUL-separated pass-1 log format that survives any commit message and for the commit graph
deliveries walk."""

import contextlib
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from sijl.git import (
    GRAPH_FORMAT,
    PASS1_FORMAT,
    GitError,
    GitRunner,
    parse_graph_z,
    parse_log_z,
    parse_worktrees_z,
    safe_env,
)
from sijl.models import GraphNode
from tests.gitfactory import COLLEAGUE, ME, ME_GITHUB, run_git

F = "\x1f"


def record(**overrides) -> bytes:
    """One pass-1 record exactly as `git log -z --format=PASS1_FORMAT` prints it."""
    fields = {
        "sha": "a" * 40,
        "parents": "b" * 40,
        "tree": "c" * 40,
        "an": "Alex Example",
        "ae": "alex.example@acme.test",
        "aN": "Alex Example",
        "aE": "alex.example@acme.test",
        "aI": "2026-05-04T10:00:00+02:00",
        "cn": "Alex Example",
        "ce": "alex.example@acme.test",
        "cI": "2026-05-04T10:00:00+02:00",
        "trailers": "",
        "s": "feat: subject",
        "B": "feat: subject\n",
    } | overrides
    return F.join(fields.values()).encode() + b"\x00"


def test_a_record_becomes_a_commit_with_aware_dates() -> None:
    [commit] = parse_log_z(record())
    assert commit.sha == "a" * 40 and commit.parents == ("b" * 40,)
    assert commit.author_email == "alex.example@acme.test"
    assert commit.author_date.utcoffset() == timedelta(hours=2)
    assert commit.subject == "feat: subject" and not commit.is_merge


def test_both_utc_spellings_parse_to_the_same_instant() -> None:
    # git 2.43 prints "+00:00" for UTC; newer git prints "Z" (seen on the CI runner).
    [plus] = parse_log_z(record(aI="2026-06-01T08:00:00+00:00"))
    [zulu] = parse_log_z(record(aI="2026-06-01T08:00:00Z"))
    assert plus.author_date == zulu.author_date


def test_merges_root_commits_and_several_records() -> None:
    out = record(parents=f"{'1' * 40} {'2' * 40}") + record(sha="d" * 40, parents="")
    merge, root = parse_log_z(out)
    assert merge.is_merge and root.parents == ()


def test_a_body_with_separators_and_newlines_is_kept_whole() -> None:
    body = f"subject\n\nline one\nhas a {F} unit separator\n"
    [commit] = parse_log_z(record(B=body))
    assert commit.body == body


def test_trailers_are_split_and_their_keys_normalised() -> None:
    trailers = f"Co-Authored-By: {COLLEAGUE.name} <{COLLEAGUE.email}>\x1dRefs: ABC-12"
    [commit] = parse_log_z(record(trailers=trailers))
    assert commit.trailers == (
        ("co-authored-by", f"{COLLEAGUE.name} <{COLLEAGUE.email}>"),
        ("refs", "ABC-12"),
    )


def test_a_malformed_record_is_an_error_not_a_silent_drop() -> None:
    with pytest.raises(ValueError, match="fields"):
        parse_log_z(b"only" + F.encode() + b"two\x00")


def test_the_format_and_the_parser_agree_on_a_real_repository(make_repo) -> None:
    repo = make_repo()
    sha = repo.commit("fix: real", at="2026-05-04T10:00:00+02:00", body="Why.")
    out = GitRunner().run(repo.path, "log", "-z", f"--format={PASS1_FORMAT}", "HEAD")
    [commit] = parse_log_z(out)
    assert (commit.sha, commit.author_name, commit.subject) == (sha, ME.name, "fix: real")
    assert commit.body == "fix: real\n\nWhy.\n"


def node(sha: str = "a" * 40, parents: str = "b" * 40, ae: str = ME.email, aE: str = ME.email):
    """One record exactly as `git log -z --format=GRAPH_FORMAT` prints it."""
    return F.join((sha, parents, ae, aE)).encode() + b"\x00"


def test_graph_records_give_parents_and_both_author_emails() -> None:
    out = node(parents=f"{'1' * 40} {'2' * 40}", ae="Alex@ACME.test") + node(
        sha="d" * 40, parents=""
    )
    merge, root = parse_graph_z(out)
    assert merge == GraphNode("a" * 40, ("1" * 40, "2" * 40), "Alex@ACME.test", ME.email)
    assert root.parents == ()


@pytest.mark.parametrize("out", [node()[:-1] + F.encode() + b"x\x00", node(sha="not-a-sha")])
def test_a_graph_record_that_is_not_one_is_an_error(out: bytes) -> None:
    with pytest.raises(ValueError, match="graph record"):
        parse_graph_z(out)


def test_the_graph_format_and_its_parser_agree_on_a_real_repository(make_repo) -> None:
    repo = make_repo()
    first = repo.commit("fix: real", at="2026-05-04T10:00:00+02:00", who=ME_GITHUB)
    second = repo.commit("fix: again", at="2026-05-05T10:00:00+02:00")
    out = GitRunner().run(repo.path, "log", "-z", f"--format={GRAPH_FORMAT}", "HEAD")
    assert parse_graph_z(out) == [
        GraphNode(second, (first,), ME.email, ME.email),
        GraphNode(first, (), ME_GITHUB.email, ME_GITHUB.email),
    ]


def test_the_environment_drops_git_variables_that_redirect_commands() -> None:
    env = safe_env({"PATH": "/bin", "GIT_DIR": "/elsewhere", "GIT_WORK_TREE": "/x",
                    "GIT_CONFIG_PARAMETERS": "'core.fsmonitor=evil'",
                    "GIT_CONFIG_GLOBAL": "/Users/me/.gitconfig"})  # fmt: skip
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
    assert "GIT_CONFIG_PARAMETERS" not in env
    assert env["GIT_CONFIG_GLOBAL"] == "/Users/me/.gitconfig"  # where config lives is the user's
    assert env["GIT_OPTIONAL_LOCKS"] == "0" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["PATH"] == "/bin"


def test_an_inherited_git_dir_cannot_redirect_a_command(make_repo, monkeypatch) -> None:
    repo = make_repo()
    sha = repo.commit("feat: here", at="2026-05-04T10:00:00+02:00")
    monkeypatch.setenv("GIT_DIR", "/nonexistent")
    assert GitRunner().run(repo.path, "rev-parse", "HEAD").decode().strip() == sha


def test_a_partial_clone_is_never_asked_to_fetch_missing_objects() -> None:
    env = safe_env({})
    assert env["GIT_NO_LAZY_FETCH"] == "1"  # honoured from git 2.44 (and its security backports)
    assert env["GIT_ALLOW_PROTOCOL"] == "none"  # and no transport exists to fetch with


def test_no_upload_pack_runs_even_where_git_would_fetch(make_repo, tmp_path, monkeypatch) -> None:
    """A git without GIT_NO_LAZY_FETCH still fetches a partial clone's missing blob — through the
    upload-pack program the repository's own config names. No transport may be allowed to run."""
    origin = make_repo("origin")
    origin.commit("chore: start", at="2026-05-03T10:00:00+02:00", files={"a.txt": "1\n"})
    origin.commit("feat: change", at="2026-05-04T10:00:00+02:00", files={"a.txt": "2\n"})
    origin.git("config", "uploadpack.allowfilter", "true")
    victim = tmp_path / "victim"
    run_git(tmp_path, "clone", "-q", "--filter=blob:none", f"file://{origin.path}", str(victim))
    marker, script = tmp_path / "ran", tmp_path / "upload-pack.sh"
    script.write_text(f'#!/bin/sh\ntouch {marker}\nexec git-upload-pack "$@"\n')
    script.chmod(0o755)
    run_git(victim, "config", "remote.origin.uploadpack", str(script))
    without_guard = {k: v for k, v in safe_env().items() if k != "GIT_NO_LAZY_FETCH"}
    monkeypatch.setattr("sijl.git.safe_env", lambda base=None: dict(without_guard))
    with contextlib.suppress(GitError):  # failing is fine; running the repository's program is not
        GitRunner().run(victim, "diff-tree", "-p", "--root", "HEAD~1")  # needs a missing blob
    assert not marker.exists()


def test_replace_refs_never_rewrite_what_is_read(make_repo) -> None:
    repo = make_repo("app")
    original = repo.commit("feat: the real commit", at="2026-05-04T10:00:00+02:00")
    repo.git("switch", "-q", "--orphan", "fake")
    fake = repo.commit("feat: a replacement", at="2026-05-05T10:00:00+02:00")
    repo.switch("main")
    repo.git("branch", "-D", "fake")
    repo.git("replace", original, fake)
    out = GitRunner().run(repo.path, "log", "--format=%s", "main")
    assert out.decode().splitlines() == ["feat: the real commit"]


def test_a_failing_command_raises_with_git_s_message(tmp_path) -> None:
    with pytest.raises(GitError) as raised:
        GitRunner().run(tmp_path, "rev-parse", "HEAD")
    assert raised.value.returncode == 128 and raised.value.stderr.startswith("fatal: ")
    assert raised.value.stderr.strip() in str(raised.value)  # git's own words, whatever they are


def test_versions_are_parsed_and_old_git_is_refused() -> None:
    def fake(version: str):
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, version.encode(), b"")

        return GitRunner(runner=run)

    assert fake("git version 2.43.0\n").version() == (2, 43, 0)
    assert fake("git version 2.39.3 (Apple Git-145)\n").version() == (2, 39, 3)
    with pytest.raises(GitError, match=r"2\.36"):
        fake("git version 2.30.1\n").require_version()


def test_a_hung_git_becomes_a_git_error_not_a_crash() -> None:
    def hangs(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    with pytest.raises(GitError, match="timed out after 5 s"):
        GitRunner(runner=hangs, timeout=5).run(Path("."), "log")


def test_version_goes_through_the_same_protected_spawn() -> None:
    seen = {}

    def run(args, **kwargs):
        seen.update(args=args, kwargs=kwargs)
        return subprocess.CompletedProcess(args, 0, b"git version 2.43.0\n", b"")

    GitRunner(runner=run, timeout=7).version()
    assert seen["kwargs"]["timeout"] == 7
    assert (
        "core.fsmonitor=false" in seen["args"]
        and seen["kwargs"]["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    )


def test_worktree_records_are_split_on_empty_fields() -> None:
    # `git worktree list --porcelain -z`: "key value" or a bare "key" per field, NUL after each,
    # an empty field after each record. The main worktree (or the bare repository) comes first.
    out = (
        b"worktree /src/app\0HEAD " + b"a" * 40 + b"\0branch refs/heads/main\0\0"
        b"worktree /wt/x\0HEAD " + b"b" * 40 + b"\0detached\0locked on a usb disk\0\0"
        b"worktree /wt/gone\0HEAD " + b"c" * 40 + b"\0detached\0prunable gitdir file points "
        b"to non-existent location\0\0"
    )
    main, locked, gone = parse_worktrees_z(out)
    assert main == {"worktree": "/src/app", "HEAD": "a" * 40, "branch": "refs/heads/main"}
    assert locked["detached"] == "" and locked["locked"] == "on a usb disk"
    assert "prunable" in gone and gone["worktree"] == "/wt/gone"


def test_a_bare_repository_lists_itself_first(tmp_path) -> None:
    run_git(tmp_path, "init", "-q", "--bare", "-b", "main", "mirror.git")
    out = GitRunner().run(tmp_path / "mirror.git", "worktree", "list", "--porcelain", "-z")
    assert parse_worktrees_z(out) == [{"worktree": str(tmp_path / "mirror.git"), "bare": ""}]


def test_a_ceiling_stops_git_searching_above_a_folder(make_repo) -> None:
    outer = make_repo("outer")
    (outer.path / "sub").mkdir()
    git = GitRunner()
    toplevel = git.run(outer.path / "sub", "rev-parse", "--show-toplevel").decode().strip()
    assert toplevel == str(outer.path)  # without a ceiling git walks up to the enclosing repo
    with pytest.raises(GitError):  # the ceiling: git gives up instead of opening `outer`
        git.run_here(outer.path / "sub", "rev-parse", "--show-toplevel")


def test_the_ceiling_is_set_only_on_the_call_that_asks_for_it() -> None:
    envs = []

    def run(args, **kwargs):
        envs.append(kwargs["env"])
        return subprocess.CompletedProcess(args, 0, b"", b"")

    git = GitRunner(runner=run)
    git.run(Path("/src/app"), "rev-parse", "--git-common-dir")
    git.run_here(Path("/src/app"), "rev-parse", "--git-common-dir")
    git.run(Path("/src/app"), "rev-parse", "--git-common-dir")
    assert [env.get("GIT_CEILING_DIRECTORIES") for env in envs] == [None, "/src", None]
    assert all(env["GIT_OPTIONAL_LOCKS"] == "0" for env in envs)  # the rest is unchanged
