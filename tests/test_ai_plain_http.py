"""Sijl sends only over https, or over plain http to this machine (a local gateway). The
decision is made on httpx's own parse of the client's base URL — the parser the SDK connects
with — never on the raw string: the SDK's own https check lets `http://[::1]:x@host/` through,
and names that merely start like a loopback address are someone else's host. AI is then off
with the reason, and no AI command builds a preview."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl import ai
from sijl.cli import app

KEY = {"ANTHROPIC_API_KEY": "sk-test-not-a-key"}

REFUSED = [
    "http://h.test/",
    "http://[::1]:x@h.test/",  # the SDK reads "http://[::1]" as local; httpx: h.test
    "http://127.0.0.1@h.test/",  # 127.0.0.1 is the user name
    "http://localhost.evil.test/",
    "http://127.0.0.1.evil.test/",
    "http://0x7f000001/",  # a resolver may read hex or decimal as 127.0.0.1; httpx does not
    "http://2130706433/",
    "http://127.1/",
    "http://l" + chr(0x043E) + "calhost/",  # a look-alike: Cyrillic o
    "http://localhost./",
]
ALLOWED = [
    ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
    ("http://[::1]:8080/", "http://[::1]:8080"),
    ("http://localhost:8080/", "http://localhost:8080"),
    ("http://LOCALHOST:8080/", "http://localhost:8080"),
    ("http://127.9.9.9/", "http://127.9.9.9"),
    ("https://h.test/", "https://h.test"),
]


@pytest.mark.parametrize("raw", REFUSED)
def test_plain_http_to_another_host_turns_ai_off(monkeypatch, raw) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", raw)
    state, claude = ai.connect(enabled=True)
    assert claude is None and not state.on
    assert state.reason == ai.PLAIN_HTTP


@pytest.mark.parametrize(("raw", "shown"), ALLOWED)
def test_https_and_plain_http_to_this_machine_are_allowed(monkeypatch, raw, shown) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", raw)
    state, claude = ai.connect(enabled=True)
    assert state.on and claude is not None and state.destination == shown
    claude.close()


def test_a_profile_that_fools_the_sdks_https_check_is_refused(monkeypatch) -> None:
    """The SDK accepts this profile address as loopback; httpx would connect to h.test."""
    base = Path.home() / ".config" / "anthropic"
    for folder in ("configs", "credentials"):
        (base / folder).mkdir(parents=True, exist_ok=True)
    config = {"authentication": {"type": "user_oauth"}, "base_url": "http://[::1]:x@h.test/"}
    (base / "configs" / "p.json").write_text(json.dumps(config))
    (base / "configs" / "p.json").chmod(0o644)
    (base / "credentials" / "p.json").write_text('{"access_token": "not-a-token"}')
    (base / "credentials" / "p.json").chmod(0o600)
    monkeypatch.setenv("ANTHROPIC_PROFILE", "p")
    state, claude = ai.connect(enabled=True)
    assert claude is None and state.reason == ai.PLAIN_HTTP


def test_ai_status_says_why_it_is_off() -> None:
    env = {**KEY, "ANTHROPIC_BASE_URL": "http://localhost.evil.test/"}
    result = CliRunner().invoke(app, ["ai", "status"], env=env)
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"AI: off — {ai.PLAIN_HTTP}")


def test_an_ai_command_refuses_before_any_preview(network) -> None:
    env = {**KEY, "ANTHROPIC_BASE_URL": "http://[::1]:x@h.test/"}
    result = CliRunner().invoke(app, ["ai", "check", "--yes"], env=env)
    assert result.exit_code == 4
    assert ai.PLAIN_HTTP in result.stderr
    assert "What Sijl will send" not in result.stderr and result.stdout == ""
    assert network.used == [] and network.attempts == []
