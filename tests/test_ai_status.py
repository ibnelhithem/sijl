"""ai.status(), the one feature flag: off when the user turns AI off, otherwise on exactly when
the Anthropic SDK resolves Claude credentials — an API key, an auth token, an `ant auth login`
profile or workload identity federation. An unset ANTHROPIC_API_KEY alone does not mean "no
credentials". Resolving them reads local files only: nothing is sent (the network guard in
tests/conftest.py would fail the test)."""

import json
import subprocess
import sys
from pathlib import Path

import anthropic
import pytest

from sijl import ai
from tests.conftest import NetworkBlocked


def profile(name: str = "default", *, pointer: bool = False) -> Path:
    """An `ant auth login` profile where the SDK looks for one: ~/.config/anthropic (HOME is a
    throwaway folder). Only the config half is needed to resolve; the token is read when a
    request is sent."""
    base = Path.home() / ".config" / "anthropic"
    config = base / "configs" / f"{name}.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"authentication": {"type": "user_oauth"}}))
    config.chmod(0o644)
    if pointer:
        (base / "active_config").write_text(name)
    return config


WIF = {
    "ANTHROPIC_FEDERATION_RULE_ID": "fdrl_test",
    "ANTHROPIC_ORGANIZATION_ID": "org_test",
    "ANTHROPIC_IDENTITY_TOKEN": "not-a-jwt",
}


@pytest.mark.parametrize(
    ("env", "profiles", "source"),
    [
        ({"ANTHROPIC_API_KEY": "sk-test-not-a-key"}, [], "ANTHROPIC_API_KEY"),
        ({"ANTHROPIC_AUTH_TOKEN": "not-a-token"}, [], "ANTHROPIC_AUTH_TOKEN"),
        ({}, ["default"], "the `ant auth login` profile 'default'"),
        ({"ANTHROPIC_PROFILE": "work"}, ["work"], "the `ant auth login` profile 'work'"),
        ({}, ["work*"], "the `ant auth login` profile 'work'"),  # * = active_config points at it
        (WIF, [], "workload identity federation"),
        (WIF, ["default"], "workload identity federation"),  # WIF beats a leftover profile
        ({"ANTHROPIC_API_KEY": "sk-test-not-a-key"}, ["default"], "ANTHROPIC_API_KEY"),
        ({"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "t"}, [], "ANTHROPIC_AUTH_TOKEN"),
    ],
    ids=[
        "api-key",
        "auth-token",
        "default-profile-no-key",
        "named-profile",
        "active-profile-pointer",
        "workload-identity",
        "workload-identity-over-profile",
        "api-key-shadows-profile",
        "empty-key-is-unset",
    ],
)
def test_ai_is_on_when_the_sdk_resolves_credentials(monkeypatch, env, profiles, source) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in profiles:
        profile(name.rstrip("*"), pointer=name.endswith("*"))
    state = ai.status(enabled=True)
    assert state.on
    assert state.reason == f"Claude credentials found: {source}"
    assert state.destination == "https://api.anthropic.com"


@pytest.mark.parametrize(
    "env",
    [{}, {"ANTHROPIC_API_KEY": ""}, {"ANTHROPIC_BASE_URL": "https://example.test"}],
    ids=["nothing", "empty-key", "only-a-base-url"],
)
def test_without_credentials_ai_is_off_with_the_reason(monkeypatch, env) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    state = ai.status(enabled=True)
    assert not state.on
    assert state.reason.startswith("no Claude credentials found")
    assert "ANTHROPIC_API_KEY" in state.reason and "ant auth login" in state.reason


def test_a_named_profile_that_does_not_exist_is_off_with_the_sdks_words(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_PROFILE", "missing")
    state = ai.status(enabled=True)
    assert not state.on
    assert state.reason.startswith("Claude credentials could not be read: ")
    assert "missing" in state.reason


def test_turning_ai_off_wins_over_credentials(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    state = ai.status(enabled=False, disabled_by="SIJL_AI=off")
    assert (state.on, state.reason) == (False, "turned off by SIJL_AI=off")


def test_a_profile_or_variable_can_point_requests_elsewhere(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.test/")
    assert ai.status(enabled=True).destination == "https://gateway.example.test"


def test_without_the_sdk_ai_is_off_and_nothing_breaks(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setitem(sys.modules, "anthropic", None)  # `import anthropic` now fails
    state = ai.status(enabled=True)
    assert not state.on
    assert state.reason.startswith("the Anthropic SDK (the anthropic package) could not be loaded")


def test_sijl_imports_the_sdk_only_when_ai_is_used() -> None:
    """Tier 1 commands never pay for importing the SDK, and never break when it is broken."""
    check = "import sys, sijl.cli, sijl.ai; print('anthropic' in sys.modules)"
    loaded = subprocess.run([sys.executable, "-c", check], capture_output=True, text=True)
    assert loaded.stdout == "False\n", loaded.stderr


def test_the_sdk_is_stopped_by_the_network_guard(network) -> None:
    """The guard sits below the SDK's HTTP stack. The address is the local discard port, so even
    a broken guard could not reach Anthropic, and the key is not a key."""
    client = anthropic.Anthropic(
        api_key="sk-test-not-a-key", base_url="http://127.0.0.1:9", max_retries=0
    )
    with pytest.raises(anthropic.APIConnectionError) as raised:
        client.messages.create(
            model="claude-opus-5-5", max_tokens=1, messages=[{"role": "user", "content": "x"}]
        )
    assert isinstance(raised.value.__cause__, NetworkBlocked)  # the guard, not the closed port
    assert network.attempts[0].startswith("create_connection ('127.0.0.1', 9)")


def test_a_broken_base_url_is_off_with_the_reason_never_a_traceback(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "::bad")
    state = ai.status(enabled=True)
    assert not state.on
    assert state.reason == ai.UNREADABLE_URL  # the URL itself is never echoed


def test_connect_reports_sdk_failures_as_off_never_a_traceback(monkeypatch) -> None:
    """connect() builds the command's one client; a profile the SDK rejects or a base URL it
    cannot parse is "off" with the reason, and no client is returned."""
    monkeypatch.setenv("ANTHROPIC_PROFILE", "missing")
    state, claude = ai.connect(enabled=True)
    assert claude is None and state.reason.startswith("Claude credentials could not be read")
    monkeypatch.delenv("ANTHROPIC_PROFILE")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "::bad")
    state, claude = ai.connect(enabled=True)
    assert claude is None and state.reason == ai.UNREADABLE_URL


def test_a_password_in_the_base_url_is_never_shown(monkeypatch) -> None:
    """The destination is printed by `ai status` and every preview: a user name or password
    written into ANTHROPIC_BASE_URL is dropped from what is shown (the SDK still uses it)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://me:s3cret@gateway.example.test:8443/v")
    assert ai.status(enabled=True).destination == "https://gateway.example.test:8443/…"
