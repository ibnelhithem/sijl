"""The guard every test runs under (tests/conftest.py): no test can open an internet connection
or look up a host name, and no test can see the developer's Claude credentials — an
ANTHROPIC_* variable or an `ant auth login` profile — so nothing a test does can reach Claude.
Local sockets (socketpair, Unix sockets) still work."""

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import NetworkBlocked


def test_a_test_cannot_open_an_internet_connection(network) -> None:
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("127.0.0.1", 9), timeout=1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkBlocked):
            sock.connect(("127.0.0.1", 9))
        with pytest.raises(NetworkBlocked):
            sock.connect_ex(("127.0.0.1", 9))
    assert len(network.attempts) == 3


def test_a_test_cannot_look_up_a_host_name(network) -> None:
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("api.anthropic.com", 443)
    assert network.attempts == ["getaddrinfo api.anthropic.com:443"]


def test_local_socket_pairs_still_work() -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"ping")
        assert right.recv(4) == b"ping"


def test_no_claude_credentials_are_visible_to_a_test(tmp_path_factory) -> None:
    assert not [name for name in os.environ if name.startswith("ANTHROPIC_")]
    # `ant auth login` profiles live in ~/.config/anthropic: HOME is a throwaway folder.
    assert Path.home().is_relative_to(tmp_path_factory.getbasetemp())
    assert not (Path.home() / ".config" / "anthropic").exists()


def test_consent_opens_the_fake_network_only(network) -> None:
    """`use()` stands for "the request leaves the machine now": fakes call it, and it fails
    until a test's consent step has called `consent()`. Real sockets stay blocked even then."""
    with pytest.raises(NetworkBlocked, match="before consent"):
        network.use("send")
    network.consent()
    network.use("send")
    assert network.used == ["send"]
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


@pytest.mark.parametrize(
    ("lookup", "arguments"),
    [
        ("gethostbyname", ("localhost",)),
        ("gethostbyname_ex", ("localhost",)),
        ("gethostbyaddr", ("127.0.0.1",)),
        ("getnameinfo", (("127.0.0.1", 80), 0)),
    ],
)
def test_every_name_lookup_is_blocked(network, lookup, arguments) -> None:
    """Every lookup is refused: gethostbyname() would otherwise resolve through /etc/hosts
    unnoticed."""
    with pytest.raises(NetworkBlocked):
        getattr(socket, lookup)(*arguments)  # looked up now: the fixture patched the module
    assert network.attempts


def test_udp_without_connect_is_blocked(network) -> None:
    """sendto() on an unconnected UDP socket never calls connect(): it is refused too."""
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rx,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx,
    ):
        rx.bind(("127.0.0.1", 0))
        with pytest.raises(NetworkBlocked):
            tx.sendto(b"payload-before-consent", rx.getsockname())
        with pytest.raises(NetworkBlocked):
            tx.sendmsg([b"payload-before-consent"], [], 0, rx.getsockname())


def test_a_python_subprocess_cannot_reach_the_network_either() -> None:
    """A child process must be guarded too. Every Python a test starts imports the
    guard first (an injected sitecustomize), so even a local listener is out of reach."""
    code = (
        "import socket\n"
        "rx = socket.socket(); rx.bind(('127.0.0.1', 0)); rx.listen()\n"
        "socket.create_connection(rx.getsockname()); print('connected')\n"
    )
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert child.stdout == ""
    assert "a test subprocess tried to reach the network" in child.stderr


def test_subprocesses_get_no_proxy_settings(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")
    monkeypatch.setenv("all_proxy", "socks5://proxy.example.test:1080")
    code = "import os; print(sorted(k for k in os.environ if k.lower().endswith('_proxy')))"
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert child.stdout == "[]\n", child.stderr


def test_only_known_local_programs_may_be_started(network) -> None:
    with pytest.raises(NetworkBlocked, match="subprocess"):
        subprocess.run(["true"], check=False)
    assert subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    # macOS has no /proc, so a killed-process check asks ps. It must stay allowed.
    assert subprocess.run(["ps", "-p", "1", "-o", "pid="], capture_output=True).returncode == 0


@pytest.mark.allow_subprocess
def test_a_test_can_opt_in_to_another_local_program() -> None:
    assert subprocess.run(["true"], check=False).returncode == 0
