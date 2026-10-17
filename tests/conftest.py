"""Shared fixtures: every test runs against a hermetic git environment, with the network blocked
and no Claude credentials in sight."""

import os
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.discovery_tree import DiscoveryTree, build_discovery_tree
from tests.gitfactory import RepoBuilder

# Variables that make Typer, Rich, click or pytest's children colour their output anyway.
_COLOUR_FORCING = (
    "FORCE_COLOR",
    "CLICOLOR_FORCE",
    "PY_COLORS",
    "TTY_COMPATIBLE",
    "TTY_INTERACTIVE",
    "COLORTERM",
    "GITHUB_ACTIONS",
)


@pytest.fixture(autouse=True)
def hermetic_git(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate git from the developer's machine.

    Gives every test an empty global config, no system config, a throwaway HOME, UTC, and none
    of the caller's GIT_*, SIJL_* or ANTHROPIC_* variables (an inherited GIT_DIR would
    redirect every git command; a SIJL_EMAILS would change every report; an
    ANTHROPIC_API_KEY is the developer's real Claude key). The throwaway HOME also hides the
    developer's `ant auth login` profiles, which the Anthropic SDK reads from
    ~/.config/anthropic — ANTHROPIC_CONFIG_DIR is not set instead, because the SDK treats a set
    one as an explicit profile choice and fails when it is empty.
    Returns the global config file, so a test can make it hostile on purpose.

    Output is plain text everywhere, CI included. Typer forces a terminal when GITHUB_ACTIONS,
    FORCE_COLOR or PY_COLORS is set — decided once, at import — and Rich then colours its usage
    errors, splitting an option such as --root with ANSI codes. Rich builds a console per message
    and reads the live environment: TERM=dumb leaves it no colour system, so it writes no escape
    codes even on a forced terminal; NO_COLOR covers other tools, and the variables that force
    colour (or a terminal) are removed for anything a test starts, such as `python -m sijl`.
    """
    for name in list(os.environ):
        if name.startswith(("GIT_", "SIJL_", "ANTHROPIC_")):
            monkeypatch.delenv(name)
    for name in _COLOUR_FORCING:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    home = tmp_path_factory.mktemp("home")
    global_config = home / ".gitconfig"
    global_config.write_text("")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("TZ", "UTC")
    ssh_config = home / ".ssh" / "config"
    ssh_config.parent.mkdir()
    ssh_config.write_text("")
    monkeypatch.setattr("sijl.git.SSH_CONFIG", ssh_config)
    return global_config


class NetworkBlocked(RuntimeError):
    """A test tried to reach the network, or a fake sent a request before consent."""


class NetworkGuard:
    """What the `network` fixture hands a test: every attempt at a real connection (refused),
    and the requests fakes made once the test's consent step allowed them."""

    def __init__(self) -> None:
        self.attempts: list[str] = []  # real connections and lookups, all refused
        self.used: list[str] = []  # requests a fake client made after consent
        self.consented = False

    def refuse(self, what: str) -> None:
        self.attempts.append(what)
        raise NetworkBlocked(f"a test tried to reach the network: {what}")

    def consent(self) -> None:
        """The test's "yes": from now on a fake client may "send"."""
        self.consented = True

    def use(self, what: str) -> None:
        """Called by fake clients at the moment a real one would send bytes: refused before
        consent, recorded after."""
        if not self.consented:
            raise NetworkBlocked(f"{what} before consent")
        self.used.append(what)


_INTERNET = (socket.AF_INET, socket.AF_INET6)
# Name lookups, forward and reverse: each may ask a DNS server.
_LOOKUPS = ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo")
# Programs tests may start without opting in: none of them is asked to reach the network, and
# each child gets no proxy settings and, for Python, the guard below. ps is here because macOS
# has no /proc, so a test asks ps whether a killed child is a zombie.
_LOCAL_PROGRAMS = frozenset({"git", "ssh", "sh", "ps"})
_PROXIES = ("http_proxy", "https_proxy", "ftp_proxy", "all_proxy")

# Imported first by every Python a test starts (it is put on PYTHONPATH as sitecustomize):
# the same refusals, in the child, where the fixture's patches do not reach.
_CHILD_GUARD = """
import socket

def _refuse(*args, **kwargs):
    raise RuntimeError("a test subprocess tried to reach the network")

def _guarded(method):
    def check(self, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _refuse()
        return method(self, *args, **kwargs)
    return check

for _name in ("connect", "connect_ex", "sendto", "sendmsg"):
    setattr(socket.socket, _name, _guarded(getattr(socket.socket, _name)))
for _name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr",
              "getnameinfo", "create_connection"):
    setattr(socket, _name, _refuse)
"""


@pytest.fixture(scope="session")
def child_guard(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A folder holding the subprocess guard as `sitecustomize.py`."""
    folder = tmp_path_factory.mktemp("child-guard")
    (folder / "sitecustomize.py").write_text(_CHILD_GUARD)
    return folder


def _program(args: object, shell: bool) -> str:
    if shell:
        return "sh"
    first = args if isinstance(args, str | bytes | os.PathLike) else next(iter(args))  # type: ignore[call-overload]
    return Path(os.fsdecode(first)).name


@pytest.fixture(autouse=True)
def network(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, child_guard: Path
) -> NetworkGuard:
    """No test reaches the network: tests use a fake client only.

    In the test's own process, internet sockets cannot connect (`connect`, `connect_ex`,
    `create_connection`) or send without connecting (`sendto`, `sendmsg` — UDP), and no host
    name or address can be looked up — for the whole test, consent included, so even a test
    that wrongly built a real Anthropic client after its "yes" could not send. Local sockets
    (socketpair, AF_UNIX) are left alone.

    Subprocesses: a test may start git, ssh, sh, ps and Python (`_LOCAL_PROGRAMS`); anything else
    needs `@pytest.mark.allow_subprocess`. Every child runs without proxy variables, and every
    Python child imports the same refusals first (`child_guard` on PYTHONPATH as
    sitecustomize; `python -I` or `-S` would skip it, and no test uses them).

    Fakes model the moment of sending with `network.use()`, which fails until the test's
    consent step calls `network.consent()`: that is how the tests prove nothing is sent before
    the user's yes."""
    guard = NetworkGuard()

    def guarded(method: Callable, what: str) -> Callable:
        def check(self: socket.socket, *args: object, **kwargs: object) -> object:
            if self.family in _INTERNET:
                guard.refuse(f"{what} {args[-1] if args else ''}".rstrip())
            return method(self, *args, **kwargs)

        return check

    def refuse(what: str) -> Callable:
        def blocked(*args: object, **kwargs: object) -> object:
            guard.refuse(f"{what} {':'.join(str(a) for a in args[:2])}".rstrip())

        return blocked

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        monkeypatch.setattr(socket.socket, name, guarded(getattr(socket.socket, name), name))
    for name in (*_LOOKUPS, "create_connection"):
        monkeypatch.setattr(socket, name, refuse(name))

    allowed = request.node.get_closest_marker("allow_subprocess") is not None
    popen = subprocess.Popen

    class GuardedPopen(popen):  # type: ignore[misc, valid-type]
        def __init__(self, args: object, *rest: object, **kwargs: object) -> None:
            program = _program(args, bool(kwargs.get("shell")))
            python = program.startswith("python") or str(args).find(sys.executable) >= 0
            if not (allowed or python or program in _LOCAL_PROGRAMS):
                guard.refuse(f"subprocess {program} (mark the test allow_subprocess if local)")
            env = dict(kwargs.get("env") or os.environ)  # type: ignore[arg-type]
            for name in list(env):
                if name.lower() in _PROXIES:
                    del env[name]
            env["PYTHONPATH"] = os.pathsep.join(
                part for part in (str(child_guard), env.get("PYTHONPATH", "")) if part
            )
            super().__init__(args, *rest, **{**kwargs, "env": env})

    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
    return guard


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[[str], RepoBuilder]:
    """Build a fresh repository under the test's tmp dir: `make_repo("web-tests")`."""

    def _make(name: str = "repo") -> RepoBuilder:
        return RepoBuilder(tmp_path / name)

    return _make


@pytest.fixture
def discovery_tree(tmp_path: Path) -> DiscoveryTree:
    """Every shape discovery must handle, under one root (see tests/discovery_tree.py)."""
    return build_discovery_tree(tmp_path)
