"""`sijl ask` cleans up and exits right in the corners: the
private file of a long request is removed even when the command is killed with SIGHUP, SIGTERM
or SIGQUIT — or interrupted with Ctrl-C — while the question waits, or meets a closed pipe while
it writes the answer; a signal the command was started ignoring stays ignored
(`nohup`); an unusable temporary folder is
a plain error with the Tier 1 route (exit 4), never a traceback; and once an answer cited an id
not in the evidence, a later failure still exits 5."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl import ai, cli
from sijl.cli import app
from tests.gitfactory import ME, RepoBuilder
from tests.test_cli_ask import KEY, QUESTION, FakeClaude, args, claude, repos  # noqa: F401
from tests.test_deliveries import at

runner = CliRunner()

# Runs `sijl ask` with a client that cannot send and a tiny preview limit, so the request is
# saved to a file and the command waits at "Send this to Claude?" for an answer that never comes.
DRIVER = """
import sys
from sijl import ai, cli

ai.PREVIEW_MAX_CHARS = 100

class Fake:
    destination = "https://api.anthropic.com"
    def close(self):
        pass
    def send(self, payload):
        raise ai.AiError("this test never sends")

cli._connect = lambda loaded: (ai.Status(True, "test credentials", Fake.destination), Fake())
cli.app(prog_name="sijl", args=sys.argv[1:])
"""

# The same, but Claude answers — a long answer — and SIGPIPE kills, as it does a program that
# did not ask Python to ignore it (Python's own default): a closed stdout is then deadly.
ANSWERING = DRIVER.replace(
    "import sys\n", "import signal\nimport sys\nsignal.signal(signal.SIGPIPE, signal.SIG_DFL)\n"
).replace(
    'raise ai.AiError("this test never sends")',
    'return ai.Reply("Fine. " * 200_000, "end_turn", payload.model, payload.model, 16_000)',
)


def _no_core_dump() -> None:  # a SIGQUIT the command does not catch would dump core
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _waiting(tmp_path: Path, driver: str, stdout: int) -> tuple[subprocess.Popen, Path]:
    """`ask` started on a one-commit repository, once it saved the request and asks."""
    repo = RepoBuilder(tmp_path / "repo")
    repo.commit("feat: a thing", at=at(4), who=ME, files={"a.txt": "x"})
    folder = tmp_path / "tmp"
    folder.mkdir()
    env = {**os.environ, "TMPDIR": str(folder)}
    period = ["--since", "2026-04-01", "--until", "2026-06-30"]
    command = [sys.executable, "-c", driver, "ask", QUESTION, "--repo", str(repo.path),
               "--author-email", ME.email, *period]  # fmt: skip
    child = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=stdout,
                             stderr=subprocess.PIPE, preexec_fn=_no_core_dump)  # fmt: skip
    deadline = time.monotonic() + 60
    while not list(folder.glob("sijl-ask-*/request-1.txt")):
        if child.poll() is not None or time.monotonic() > deadline:
            child.kill()
            raise AssertionError(child.stderr.read().decode() if child.stderr else "never saved")
        time.sleep(0.05)
    time.sleep(0.5)  # the question is asked right after the file is written
    return child, folder


@pytest.mark.allow_subprocess
@pytest.mark.parametrize(
    "signum", [signal.SIGHUP, signal.SIGTERM, signal.SIGQUIT, signal.SIGINT], ids=lambda s: s.name
)
def test_the_saved_request_is_removed_when_the_command_is_killed(tmp_path, signum) -> None:
    child, folder = _waiting(tmp_path, DRIVER, subprocess.DEVNULL)
    try:
        child.send_signal(signum)
        child.wait(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
        if child.stdin:
            child.stdin.close()
    assert list(folder.iterdir()) == []
    assert child.returncode in (1, 128 + signum)  # Ctrl-C: Typer's "Aborted!" (1)


@pytest.mark.allow_subprocess
def test_the_saved_request_is_removed_when_stdout_closes_while_the_answer_is_written(
    tmp_path,
) -> None:
    """`sijl ask … | head -1`: the reader is gone before the answer is written."""
    child, folder = _waiting(tmp_path, ANSWERING, subprocess.PIPE)
    try:
        assert child.stdout is not None and child.stdin is not None
        child.stdout.close()  # nobody reads the answer
        child.stdin.write(b"y\n")
        child.stdin.close()
        child.wait(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
    assert list(folder.iterdir()) == []
    assert child.returncode == 128 + signal.SIGPIPE


def test_a_signal_ignored_when_the_command_started_stays_ignored(monkeypatch, tmp_path) -> None:
    """Under `nohup` a hangup must not end the command: only a signal that would kill it is
    caught to remove the file first."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    before = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        files = cli._PreviewFiles()
        files("x")
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        assert signal.getsignal(signal.SIGQUIT) not in (signal.SIG_DFL, signal.SIG_IGN, None)
        files.remove()
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGHUP, before)


def test_an_unusable_temporary_folder_is_a_plain_error_with_the_route(
    claude,  # noqa: F811
    repos,  # noqa: F811
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "missing"))
    monkeypatch.setattr(ai, "PREVIEW_MAX_CHARS", 1_000)
    result = runner.invoke(app, args(repos, QUESTION), env=KEY, input="y\n")
    assert result.exit_code == cli.EXIT_AI
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "could not save the request for you to read" in result.stderr
    assert "nothing was sent" in result.stderr
    assert f"answer: {QUESTION}" in result.stderr  # the Tier 1 route
    assert claude.sent == []


def test_an_unverified_answer_then_an_error_still_exits_5(repos, network, monkeypatch) -> None:  # noqa: F811
    network.consent()

    class Then(FakeClaude):
        def send(self, payload: ai.Payload) -> ai.Reply:
            if self.sent:
                raise ai.AiError("the API is overloaded (529): try later")
            return super().send(payload)

    then = Then(network)
    then.replies = [("You also did PROJ-99.", "end_turn", 0, 0)]
    on = ai.Status(True, "Claude credentials found: ANTHROPIC_API_KEY", then.destination)
    monkeypatch.setattr(cli, "_connect", lambda loaded: (on, then))
    result = runner.invoke(app, args(repos, QUESTION, "-i", "--yes"), env=KEY, input="More?\n")
    assert result.exit_code == cli.EXIT_UNVERIFIED, result.output
    assert "overloaded" in result.stderr
    assert "An earlier answer cites ids that are NOT in your evidence" in result.stderr


def test_handlers_are_restored_after_the_command(
    claude,  # noqa: F811
    repos,  # noqa: F811
    monkeypatch,
    tmp_path,
) -> None:
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    monkeypatch.setattr(ai, "PREVIEW_MAX_CHARS", 1_000)
    guarded = (signal.SIGHUP, signal.SIGTERM, signal.SIGQUIT, signal.SIGPIPE)
    before = {s: signal.getsignal(s) for s in guarded}
    result = runner.invoke(app, args(repos, QUESTION), env=KEY, input="n\n")
    assert result.exit_code == 0, result.output
    assert {s: signal.getsignal(s) for s in before} == before
    assert list(folder.iterdir()) == []


def test_a_signal_handler_is_installed_while_the_file_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    files = cli._PreviewFiles()
    files("x")
    try:
        assert signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, signal.SIG_IGN, None)
        with pytest.raises(SystemExit) as stopped:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert stopped.value.code == 128 + signal.SIGTERM
    finally:
        files.remove()
    assert not list(Path(tmp_path).glob("sijl-ask-*"))
