"""Terminal-escape injection: a folder name, a commit subject or a git error
can carry ESC/OSC/CSI sequences, other C0 and C1 controls, DEL or bidi overrides that rewrite
the reader's terminal. Every text Sijl prints goes through `text.printable()`, which shows
them as visible escapes; JSON stays exact and escapes them too."""

import json
import os
import re
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from sijl.cli import app
from sijl.git import GitError, GitRunner
from sijl.text import json_text, printable
from tests.filesystem import require_raw_byte_names
from tests.gitfactory import ME, PERIOD_ARGS

runner = CliRunner()

TITLE = "\x1b]0;pwned\x07"  # OSC: sets the terminal's title, ended by BEL
CLEAR = "\x1b[2J"  # CSI: clears the screen
UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f\x80-\x9f‪-‮⁦-⁩]")


def assert_terminal_safe(text: str) -> None:
    """No raw control a terminal acts on: C0 but \\n and \\t, DEL, C1, bidi overrides."""
    found = UNSAFE.findall(text)
    assert not found, f"raw controls reach the terminal: {found!r}"


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("\x1b[2J", "\\x1b[2J"),
        ("a\rb\nc\x00d", "a\\x0db\\x0ac\\x00d"),  # CR and LF too: these fields are one line
        ("\x7f", "\\x7f"),
        ("\x9b31m", "\\x9b31m"),  # C1 CSI, one character
        ("abc‮exe.txt", "abc\\u202eexe.txt"),  # right-to-left override
        ("⁦x⁩", "\\u2066x\\u2069"),  # isolates
    ],
)
def test_controls_become_visible_escapes(raw: str, shown: str) -> None:
    assert printable(raw) == shown


def test_ordinary_text_is_unchanged() -> None:
    text = "fix(ä): Ünïcode — 日本語 ✓ tab\tstays"
    assert printable(text) == text


def test_json_text_is_exact_and_escapes_what_json_leaves_raw() -> None:
    value = {"name": f"x{TITLE}\x7f\x9b‮é"}
    out = json_text(value)
    assert_terminal_safe(out)
    assert "é" in out  # readable where it is harmless
    assert json.loads(out) == value


class HostileGit(GitRunner):
    """git 2.43 already shows controls in a .git file's target as "?", but not every message is
    cleaned: here git's error for one folder quotes ESC, as a crafted repository could make it."""

    def run_here(self, cwd, *args):
        if cwd.name == "orphan":
            raise GitError(args, 128, "fatal: not a git repository: /gone/\x1b[31mred\n")
        return super().run_here(cwd, *args)


def evil_tree(make_repo, tmp_path, monkeypatch):
    """A repository whose folder name carries an OSC title change and whose commit subject clears
    the screen, a plain folder named with CSI, and a git error quoting ESC."""
    repo = make_repo(f"evil{TITLE}\u202ename")
    repo.commit(
        f"feat: subject {CLEAR}\x9b31m with a \u202eoverride", at="2026-05-04T10:00:00+02:00"
    )
    (tmp_path / f"clear{CLEAR}" / "empty").mkdir(parents=True)
    (tmp_path / "orphan").mkdir()
    (tmp_path / "orphan" / ".git").write_text("gitdir: /gone\n")
    monkeypatch.setattr("sijl.cli.GitRunner", HostileGit)
    return repo


def test_sijl_repos_shows_controls_escaped(make_repo, tmp_path, monkeypatch) -> None:
    evil_tree(make_repo, tmp_path, monkeypatch)
    result = runner.invoke(app, ["repos", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert_terminal_safe(result.stdout)
    assert "evil\\x1b]0;pwned\\x07\\u202ename" in result.stdout
    assert "clear\\x1b[2J" in result.stdout
    assert "/gone/\\x1b[31mred" in result.stdout  # from git's error message


def test_sijl_repos_json_round_trips_the_exact_name(make_repo, tmp_path, monkeypatch):
    evil_tree(make_repo, tmp_path, monkeypatch)
    result = runner.invoke(app, ["repos", "--json", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert_terminal_safe(result.stdout)
    [repo] = json.loads(result.stdout)["repositories"]
    assert repo["name"] == f"evil{TITLE}\u202ename"


def test_sijl_report_shows_controls_escaped(make_repo, tmp_path, monkeypatch) -> None:
    evil_tree(make_repo, tmp_path, monkeypatch)
    result = runner.invoke(
        app, ["report", "--root", str(tmp_path), "--author-email", ME.email, *PERIOD_ARGS]
    )
    assert result.exit_code == 0, result.output
    assert_terminal_safe(result.stdout)
    # Markdown escapes "[" too: it could start a link (test_report_markdown.py).
    assert "### evil\\x1b]0;pwned\\x07\\u202ename — 1 commit" in result.stdout
    assert "feat: subject \\x1b\\[2J\\x9b31m with a \\u202eoverride" in result.stdout
    assert "/gone/\\x1b\\[31mred" in result.stdout  # a Caveats line built from git's stderr


def test_errors_on_stderr_are_escaped_too(tmp_path) -> None:
    (tmp_path / f"clear{CLEAR}").mkdir()
    root = str(tmp_path / f"clear{CLEAR}")
    result = runner.invoke(
        app, ["report", "--root", root, "--author-email", ME.email, *PERIOD_ARGS]
    )
    assert result.exit_code == 3
    assert_terminal_safe(result.stderr)
    assert "clear\\x1b[2J" in result.stderr


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("r\udcff", "r\\xff"),  # a byte that was not UTF-8, as os.fsdecode keeps it
        ("\udc9b2J", "\\x9b2J"),  # a raw 8-bit CSI byte: never written back as 0x9b
        ("\ud800", "\\ud800"),  # any other lone surrogate
        ("a\u200eb‏c؜d", "a\\u200eb\\u200fc\\u061cd"),  # LRM, RLM, ALM
        ("zero​width﻿", "zero\\u200bwidth\\ufeff"),
        ("tag\U000e0041", "tag\\U000e0041"),  # invisible tag characters
        ("line\u2028para\u2029", "line\\u2028para\\u2029"),
    ],
)
def test_surrogates_and_invisible_format_characters_are_shown(raw: str, shown: str) -> None:
    assert printable(raw) == shown


def test_spaces_that_look_like_spaces_are_left_alone() -> None:
    assert printable("a\u00a0b\u3000c") == "a\u00a0b\u3000c"  # Zs: visible as space


def test_json_text_encodes_strictly_and_round_trips_a_non_utf8_name() -> None:
    value = {"name": "r\udcff\U000e0041\u2028\u200e"}
    out = json_text(value)
    out.encode("utf-8")  # strict: no lone surrogate left
    assert json.loads(out) == value
    assert os.fsencode(json.loads(out)["name"]).startswith(b"r\xff")


ODD_NAMES = (b".hid\xff", b"plain\x9b2J\x9b31mRED", b"c1-\xc2\x9b-name")


@pytest.mark.parametrize("encoding", ["utf-8:strict", "utf-8:surrogateescape"])
def test_names_that_are_not_utf8_never_crash_or_reach_the_terminal_raw(
    make_repo, tmp_path, encoding
) -> None:
    require_raw_byte_names(tmp_path)  # macOS cannot have such folders
    make_repo("good").commit("feat: a", at="2026-05-04T10:00:00+02:00")
    for name in ODD_NAMES:
        os.mkdir(os.fsencode(tmp_path) + b"/" + name)
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    out_file = tmp_path.parent / f"{tmp_path.name}-review.md"
    base = [sys.executable, "-m", "sijl"]
    commands = (
        ["repos", "--root", str(tmp_path)],
        ["repos", "--json", "--root", str(tmp_path)],
        ["report", "--root", str(tmp_path), "--author-email", ME.email, *PERIOD_ARGS],
        ["report", "--root", str(tmp_path), "--author-email", ME.email, *PERIOD_ARGS,
         "--out", str(out_file)],
    )  # fmt: skip
    for args in commands:
        done = subprocess.run([*base, *args], env=env, capture_output=True, check=False)
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
        for raw in (done.stdout, done.stderr, out_file.read_bytes() if out_file.exists() else b""):
            text = raw.decode("utf-8")  # strict: no stray 0x80-0xff byte on its own
            assert not re.findall("[\x80-\x9f]", text), "a C1 control reached the terminal"
            assert_terminal_safe(text)
    listing = subprocess.run([*base, *commands[0]], env=env, capture_output=True).stdout.decode()
    assert ".hid\\xff" in listing and "plain\\x9b2J\\x9b31mRED" in listing
    assert "c1-\\x9b-name" in listing
    names = subprocess.run([*base, *commands[1]], env=env, capture_output=True).stdout
    pruned = [os.fsencode(p["path"]) for p in json.loads(names)["pruned"]]
    assert pruned == [os.fsencode(tmp_path) + b"/.hid\xff"]  # the exact bytes come back
