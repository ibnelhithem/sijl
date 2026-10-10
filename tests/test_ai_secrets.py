"""Secrets in addresses are never printed. A user name, password,
query string or fragment written into ANTHROPIC_BASE_URL or a profile's base_url can carry a
key; the SDK quotes the address in its own error messages. `ai.scrub()` drops those parts from
every text Sijl prints — `Status`, every `AiError`, the preview — and the paths keep the
host and path, which say where a request goes."""

import json
import re
from pathlib import Path

import anthropic
import httpx2
import pytest
from typer.testing import CliRunner

from sijl import ai
from sijl.cli import app
from tests.test_ai_preview import payload

SECRETS = ("s3cretPW", "s3cretQS", "s3cretFR")


def clean(text: str) -> bool:
    return not any(secret in text for secret in SECRETS)


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("https://me:s3cretPW@gateway.corp.test/v1", "https://gateway.corp.test/…"),
        ("https://gateway.corp.test/?key=s3cretQS", "https://gateway.corp.test"),
        ("https://gateway.corp.test/p#s3cretFR", "https://gateway.corp.test/…"),
        ("got 'http://me:s3cretPW@evil.test:8080/a?b=s3cretQS'", "got 'http://evil.test:8080/…'"),
        ("http://[::1]:9/x and https://u:s3cretPW@h.test", "http://[::1]:9/… and https://h.test"),
        ("https://me:p/s3cretPW@gateway.corp.test/", "https://gateway.corp.test"),
        ("https://gw.test/t/s3cretPATH/v1/messages", "https://gw.test/…"),
        ("no address here", "no address here"),
    ],
)
def test_scrub_keeps_scheme_host_and_port_only(text, shown) -> None:
    """A gateway token can live in the path: a non-empty path is shown as `/…`."""
    assert ai.scrub(text) == shown


def test_scrub_removes_the_configured_secrets_wherever_they_appear(monkeypatch) -> None:
    """An address the URL pattern cannot take whole (a space in the password) still loses
    every secret part of ANTHROPIC_BASE_URL, written raw or percent-encoded."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://me:s3cretPW x@gateway.corp.test/?k=s3cretQS")
    text = "bad https://me:s3cretPW x@gateway.corp.test/ or me:s3cretPW%20x@ and k=s3cretQS"
    assert clean(ai.scrub(text))


def test_status_never_shows_a_query_string(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.corp.test/?key=s3cretQS")
    assert ai.status(enabled=True).destination == "https://gateway.corp.test"


def test_the_sdks_own_error_text_is_scrubbed(monkeypatch) -> None:
    """The SDK refuses a non-https profile address and quotes it, password included."""
    base = Path.home() / ".config" / "anthropic"
    (base / "configs").mkdir(parents=True)
    config = {"authentication": {"type": "user_oauth"}, "base_url": "http://me:s3cretPW@evil.test"}
    (base / "configs" / "bad.json").write_text(json.dumps(config))
    (base / "configs" / "bad.json").chmod(0o644)  # the SDK refuses a file others can write
    monkeypatch.setenv("ANTHROPIC_PROFILE", "bad")
    state = ai.status(enabled=True)
    assert not state.on and "https" in state.reason and clean(state.reason)
    state, claude = ai.connect(enabled=True)
    assert claude is None and clean(state.reason)


def test_a_connection_error_never_shows_the_password(network) -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    network.consent()
    client = anthropic.Anthropic(
        api_key="sk-test-not-a-key",
        base_url="http://me:s3cretPW@127.0.0.1:9/?key=s3cretQS",
        http_client=httpx2.Client(transport=httpx2.MockTransport(fail)),
        max_retries=0,
    )
    with pytest.raises(ai.AiError, match=re.escape("could not reach http://127.0.0.1:9")) as raised:
        ai.Claude(client).send(payload())
    assert clean(str(raised.value))


def test_an_api_message_that_quotes_an_address_is_scrubbed(network) -> None:
    error = {"type": "error", "error": {"message": "see https://u:s3cretPW@x.test/?t=s3cretQS"}}

    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json=error)

    network.consent()
    client = anthropic.Anthropic(
        api_key="sk-test-not-a-key",
        http_client=httpx2.Client(transport=httpx2.MockTransport(answer)),
        max_retries=0,
    )
    with pytest.raises(ai.AiError) as raised:
        ai.Claude(client).send(payload())
    assert "see https://x.test" in str(raised.value) and clean(str(raised.value))


def test_every_ai_error_and_status_is_scrubbed_by_construction() -> None:
    assert clean(str(ai.AiError("x https://u:s3cretPW@h.test/?q=s3cretQS")))
    state = ai.Status(False, "x http://u:s3cretPW@h.test", "https://h.test/?k=s3cretQS")
    assert clean(state.reason) and clean(state.destination)


def test_the_preview_never_shows_a_secret_address() -> None:
    preview = ai.render_preview(payload(), destination="https://u:s3cretPW@h.test/?k=s3cretQS")
    assert "To: https://h.test\n" in preview and clean(preview)


def test_ai_status_prints_no_secret(monkeypatch) -> None:
    env = {
        "ANTHROPIC_API_KEY": "sk-test-not-a-key",
        "ANTHROPIC_BASE_URL": "https://me:s3cretPW@gateway.corp.test/?key=s3cretQS#s3cretFR",
    }
    result = CliRunner().invoke(app, ["ai", "status"], env=env)
    assert result.exit_code == 0, result.output
    assert "https://gateway.corp.test" in result.stdout
    assert clean(result.stdout) and clean(result.stderr)


# Parser-differential cases: what is shown comes from httpx's own parse
# (the parser the SDK sends with), and a textual scrub runs over every output as well, so a URL
# parsed differently — or not at all — still never shows a secret.
HOSTILE = [
    # (ANTHROPIC_BASE_URL, the destination shown or None when unreadable, secrets)
    ("https://usr:s3cretA@h.test\\@evil.test/", "https://evil.test", ["s3cretA"]),
    ("https://usr:s3cretB@evil.test@h.test/", "https://h.test", ["s3cretB", "evil"]),
    ("https://h.test#@evil.test/", "https://h.test", ["evil"]),
    ("https://h.test?@evil.test/", "https://h.test", ["evil"]),
    ("https://usr%3As3cretC%40x@h.test/", "https://h.test", ["s3cretC"]),
    ("https://[::1]:s3cretD@h.test/", "https://h.test", ["s3cretD"]),
    ("https://usr:s3cretE@bücher.example/", "https://bücher.example", ["s3cretE"]),
    ("https://usr:s3cret F@h.test/", "https://h.test", ["s3cret"]),
    ("http://usr:s3cretG\n@h.test/", None, ["s3cretG"]),
    ("http://usr:s3cretH@", None, ["s3cretH"]),
    ("http://usr:s3cretI/x@h.test/", None, ["s3cretI"]),
    ("https://h.test/v1?key=s3cretJ#s3cretK", "https://h.test/…", ["s3cretJ", "s3cretK"]),
    ("https://gw.test/t/s3cretPATH/", "https://gw.test/…", ["s3cretPATH"]),  # a token in the path
    ("https://gw.test:8443/", "https://gw.test:8443", ["s3cret"]),
]


def hidden(text: str, secrets: list[str]) -> bool:
    return not any(secret in text for secret in secrets)


@pytest.mark.parametrize(("raw", "shown", "secrets"), HOSTILE)
def test_status_shows_what_httpx_parsed_and_no_secret(monkeypatch, raw, shown, secrets) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", raw)
    state = ai.status(enabled=True)
    assert hidden(state.reason, secrets) and hidden(state.destination, secrets)
    if shown is None:
        assert not state.on
        assert state.reason.startswith("the base URL (ANTHROPIC_BASE_URL or your Anthropic")
        assert state.reason.endswith("could not be read")
        return
    assert state.on and state.destination == shown
    parsed = httpx2.URL(raw)  # what the SDK connects to: scheme, host and port, nothing else
    port = f":{parsed.port}" if parsed.port else ""
    assert state.destination.removesuffix("/…") == f"{parsed.scheme}://{parsed.host}{port}"
    preview = ai.render_preview(payload(), destination=state.destination)
    assert f"To: {shown}\n" in preview and hidden(preview, secrets)


@pytest.mark.parametrize(("raw", "shown", "secrets"), HOSTILE)
def test_ai_status_prints_no_secret_for_any_hostile_url(raw, shown, secrets) -> None:
    env = {"ANTHROPIC_API_KEY": "sk-test-not-a-key", "ANTHROPIC_BASE_URL": raw}
    result = CliRunner().invoke(app, ["ai", "status"], env=env)
    assert result.exit_code == 0, result.output
    assert hidden(result.stdout, secrets) and hidden(result.stderr, secrets)


@pytest.mark.parametrize(("raw", "shown", "secrets"), [h for h in HOSTILE if h[1]])
def test_connection_errors_show_no_secret_for_any_hostile_url(network, raw, shown, secrets):
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot connect to {request.url}", request=request)

    network.consent()
    client = anthropic.Anthropic(
        api_key="sk-test-not-a-key",
        base_url=raw,
        http_client=httpx2.Client(transport=httpx2.MockTransport(fail)),
        max_retries=0,
    )
    with pytest.raises(ai.AiError) as raised:
        ai.Claude(client).send(payload())
    assert f"could not reach {shown}" in str(raised.value)
    assert hidden(str(raised.value), secrets)


@pytest.mark.parametrize(
    ("raw", "shown", "secrets"),
    [(raw.replace("https:", "http:", 1), shown, secrets) for raw, shown, secrets in HOSTILE],
)
def test_the_sdks_errors_about_a_profile_url_show_no_secret(monkeypatch, raw, shown, secrets):
    """A profile's base_url must be https; the SDK's refusal quotes the address. (The SDK lets
    `http://[::1]:…@host` through as if it were this computer — httpx connects to `host` —
    and Sijl refuses it instead: plain http to another host.)"""
    base = Path.home() / ".config" / "anthropic"
    (base / "configs").mkdir(parents=True)
    config = {"authentication": {"type": "user_oauth"}, "base_url": raw}
    (base / "configs" / "p.json").write_text(json.dumps(config))
    (base / "configs" / "p.json").chmod(0o644)
    monkeypatch.setenv("ANTHROPIC_PROFILE", "p")
    state = ai.status(enabled=True)
    assert hidden(state.reason, secrets) and hidden(state.destination, secrets)
    assert not state.on  # refused by the SDK, or by Sijl (plain http, not this machine)
