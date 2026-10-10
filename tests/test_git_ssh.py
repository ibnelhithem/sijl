"""sijl.git, ssh: `ssh -G` says which host an ssh alias stands for, without connecting. The
host comes from a repository's remote URL — hostile input — so it must never reach ssh's argv
as an option, and ssh runs with stdin closed, a minimal environment and a timeout."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from sijl import git as git_module
from sijl.git import SshError, SshRunner, parse_ssh_config, run_in_own_group, ssh_env

needs_ssh = pytest.mark.skipif(shutil.which("ssh") is None, reason="ssh is not installed")


class Spy:
    """Stands in for subprocess.run: records each call and answers like `ssh -G` would."""

    def __init__(self, stdout: bytes = b"hostname github.com\nport 22\n", returncode: int = 0):
        self.calls: list[tuple[list[str], dict]] = []
        self.stdout, self.returncode = stdout, returncode

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, b"ssh: oops\n")


def ssh_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "ssh_config"
    path.write_text(text)
    return path


@needs_ssh
def test_an_alias_resolves_to_its_host_name(tmp_path) -> None:
    config = ssh_config(tmp_path, "Host github-alt\n  HostName github.com\n  User git\n")
    assert SshRunner(config_file=config).hostname("github-alt") == "github.com"


@needs_ssh
def test_a_host_that_is_no_alias_is_itself(tmp_path) -> None:
    config = ssh_config(tmp_path, "Host github-alt\n  HostName github.com\n")
    assert SshRunner(config_file=config).hostname("Git.Acme.test") == "git.acme.test"


def test_the_command_line_ends_options_before_the_host(tmp_path) -> None:
    spy = Spy()
    config = tmp_path / "cfg"
    SshRunner(config_file=config, runner=spy).hostname("github-alt")
    [(argv, kwargs)] = spy.calls
    assert argv == [
        "ssh", "-G", "-T", "-o", "CanonicalizeHostname=no", "-F", str(config), "--",
        "github-alt",
    ]  # fmt: skip
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] == 5.0
    assert kwargs["cwd"] == os.path.abspath(os.sep)
    assert set(kwargs["env"]) <= {"PATH", "HOME", "LC_ALL"}


def test_without_a_config_file_ssh_reads_its_own(monkeypatch) -> None:
    monkeypatch.setattr(git_module, "SSH_CONFIG", None)  # what a normal run uses
    spy = Spy()
    SshRunner(runner=spy).hostname("github-alt")
    assert "-F" not in spy.calls[0][0]


def test_the_hermetic_fixture_points_ssh_at_an_empty_config() -> None:
    # ssh finds ~/.ssh/config through the password database, not $HOME: without this, a test
    # would read the developer's own ssh config.
    assert git_module.SSH_CONFIG is not None and git_module.SSH_CONFIG.read_text() == ""
    spy = Spy()
    SshRunner(runner=spy).hostname("github-alt")
    argv = spy.calls[0][0]
    assert argv[argv.index("-F") + 1] == str(git_module.SSH_CONFIG)


@pytest.mark.parametrize(
    "host",
    [
        "-oProxyCommand=touch pwned",
        "-F/tmp/evil_config",
        "-Fevil",
        "-G",
        "--",
        "a b",
        "",
        "github.com\n-oProxyCommand=x",
        "host%h",
        "user@github.com",
        "[::1]",
    ],
)
def test_a_hostile_host_never_reaches_ssh(host: str) -> None:
    spy = Spy()
    with pytest.raises(SshError, match="not a host name"):
        SshRunner(runner=spy).hostname(host)
    assert spy.calls == []


@needs_ssh
def test_match_exec_runs_under_ssh_g_so_a_host_can_never_choose_the_config(tmp_path) -> None:
    marker = tmp_path / "marker"
    evil = ssh_config(tmp_path, f'Match exec "touch {marker}"\n  User x\n')
    # The threat is real: ssh -G evaluates Match exec, so a config file of the attacker's runs.
    subprocess.run(["ssh", "-G", "-F", str(evil), "--", "example.test"], capture_output=True,
                   stdin=subprocess.DEVNULL, check=False, timeout=10)  # fmt: skip
    assert marker.exists()
    marker.unlink()
    for host in (f"-F{evil}", f"-oProxyCommand=touch {marker}", f"-Fevil -- {marker}"):
        with pytest.raises(SshError):
            SshRunner().hostname(host)
    assert not marker.exists()


def test_the_environment_is_minimal() -> None:
    env = ssh_env(
        {
            "PATH": "/usr/bin",
            "HOME": "/Users/alex",
            "SSH_AUTH_SOCK": "/tmp/agent",
            "GIT_SSH_COMMAND": "ssh -i key",
            "AWS_SECRET_ACCESS_KEY": "x",
            "LD_PRELOAD": "/tmp/evil.so",
        }
    )
    assert env == {"PATH": "/usr/bin", "HOME": "/Users/alex", "LC_ALL": "C"}


def test_a_timeout_is_an_ssh_error() -> None:
    def hangs(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with pytest.raises(SshError, match="timed out after 5 s"):
        SshRunner(runner=hangs).hostname("github-alt")


def test_missing_ssh_is_an_ssh_error() -> None:
    def missing(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    with pytest.raises(SshError, match="ssh is not installed"):
        SshRunner(runner=missing).hostname("github-alt")


def test_ssh_s_own_error_is_kept() -> None:
    with pytest.raises(SshError, match="ssh: oops"):
        SshRunner(runner=Spy(returncode=255)).hostname("github-alt")


@pytest.mark.parametrize("stdout", [b"", b"port 22\n", b"hostname -oProxyCommand=x\n"])
def test_an_unusable_answer_is_an_ssh_error(stdout: bytes) -> None:
    with pytest.raises(SshError, match="no usable host name"):
        SshRunner(runner=Spy(stdout=stdout)).hostname("github-alt")


def test_parse_ssh_config_keeps_the_first_value_of_each_key() -> None:
    out = b"user git\nhostname github.com\nport 22\nidentityfile ~/.ssh/a\nidentityfile ~/.ssh/b\n"
    parsed = parse_ssh_config(out)
    assert parsed["hostname"] == "github.com" and parsed["identityfile"] == "~/.ssh/a"


def test_a_shorter_timeout_can_be_asked_for_but_never_a_longer_one() -> None:
    spy = Spy()
    runner = SshRunner(runner=spy)
    runner.hostname("a", timeout=2.0)
    runner.hostname("b", timeout=60.0)
    assert [kwargs["timeout"] for _, kwargs in spy.calls] == [2.0, 5.0]


def alive(pid: int) -> bool:
    """Is `pid` a running process (a zombie waiting to be reaped is not)? Linux says in /proc;
    macOS has no /proc, and there `ps` names a zombie's state "Z"."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    if not Path("/proc/self").exists():
        ps = ["ps", "-o", "stat=", "-p", str(pid)]
        state = subprocess.run(ps, capture_output=True, text=True, check=False).stdout.strip()
        return bool(state) and not state.startswith("Z")
    status = Path(f"/proc/{pid}/status")
    try:
        return "\tZ" not in next(
            x for x in status.read_text().splitlines() if x.startswith("State")
        )
    except (OSError, StopIteration):
        return True


def gone(pid: int, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_a_timeout_kills_the_whole_process_group(tmp_path) -> None:
    # subprocess.run kills only ssh on a timeout; the `sh -c` a
    # `Match exec` line started, and its children, kept running.
    pids = tmp_path / "pids"
    script = f"sleep 30 & echo $$ $! > {pids}; wait"
    with pytest.raises(subprocess.TimeoutExpired):
        run_in_own_group(["sh", "-c", script], stdin=subprocess.DEVNULL, capture_output=True,
                         env=ssh_env(), cwd=str(tmp_path), timeout=0.5)  # fmt: skip
    shell, sleeper = map(int, pids.read_text().split())
    assert gone(shell) and gone(sleeper)


def test_run_in_own_group_returns_what_run_would() -> None:
    done = run_in_own_group(["sh", "-c", "echo out; echo err >&2; exit 3"],
                            stdin=subprocess.DEVNULL, capture_output=True, env=ssh_env(),
                            cwd=os.path.abspath(os.sep), timeout=5)  # fmt: skip
    assert (done.returncode, done.stdout, done.stderr) == (3, b"out\n", b"err\n")


@needs_ssh
def test_a_slow_match_exec_is_killed_with_ssh(tmp_path) -> None:
    pids = tmp_path / "pids"
    config = ssh_config(tmp_path, f'Match exec "sleep 30 & echo $! > {pids}; wait"\n  User x\n')
    started = time.monotonic()
    with pytest.raises(SshError, match="timed out after 1 s"):
        SshRunner(config_file=config, timeout=1.0).hostname("example.test")
    assert time.monotonic() - started < 5
    assert gone(int(pids.read_text()))
