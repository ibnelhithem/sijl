"""`sijl ai status` and `sijl ai check`. Status says whether AI is on and why; when
it is off, every AI command explains why and prints the Tier 1 route — never a bare error.
`ai check` sends one fixed line to Claude after the preview and the user's yes. A fake client
stands in for the SDK client `cli._connect` builds; the network guard refuses any real
connection, and the fake refuses to send before the user's yes."""

import pytest
from typer.testing import CliRunner

from sijl import ai, cli
from sijl.cli import app
from tests.test_config import config_file

runner = CliRunner()
KEY = {"ANTHROPIC_API_KEY": "sk-test-not-a-key"}
TIER1 = "sijl digest --with-prompt --out digest.txt"  # the digest, its prompt first


class FakeClaude:
    destination = "https://api.anthropic.com"

    def __init__(self, network, reply_text: str = "OK", stop_reason: str = "end_turn") -> None:
        self.network, self.reply_text, self.stop_reason = network, reply_text, stop_reason
        self.sent: list[ai.Payload] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def send(self, payload: ai.Payload) -> ai.Reply:
        self.network.use("send to Claude")
        self.sent.append(payload)
        return ai.Reply(
            self.reply_text, self.stop_reason, payload.model, payload.model, payload.max_tokens,
            input_tokens=30, output_tokens=5,
        )  # fmt: skip


@pytest.fixture
def claude(monkeypatch, network) -> FakeClaude:
    """The client `ai check` sends with. The real flag decides on or off from the environment;
    when on, the fake takes the SDK client's place, with its destination. The test's consent
    is the user's "y" (`cli._ask`); with --yes or confirm = false a test consents first."""
    fake = FakeClaude(network)
    real_connect, real_ask = cli._connect, cli._ask

    def connect(loaded):
        state, client = real_connect(loaded)
        if client is None:
            return state, None
        fake.destination = client.destination
        client.close()
        return state, fake

    def ask(question: str) -> bool:
        yes = real_ask(question)
        if yes:
            network.consent()
        return yes

    monkeypatch.setattr(cli, "_connect", connect)
    monkeypatch.setattr(cli, "_ask", ask)
    return fake


# ------------------------------------------------------------------------------------ status


def test_status_without_credentials_is_off_with_the_tier1_route(network) -> None:
    result = runner.invoke(app, ["ai", "status"])
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("AI: off — no Claude credentials found")
    assert TIER1 in result.stdout and "claude " in result.stdout
    assert result.stderr == "" and network.attempts == []


def test_the_tier1_route_is_the_digest_with_its_prompt() -> None:
    """Step 1 writes the digest with its prompt; step 2 asks Claude Code, with the question
    filled in when there is one."""
    assert ai.tier1_route() == (
        "You can still ask Claude about your work without Sijl sending anything:\n"
        "  1. sijl digest --with-prompt --out digest.txt    (with your usual period flags)\n"
        "  2. claude 'Read digest.txt and summarise what I delivered'    (Claude Code, in the "
        "same folder)\n"
    )
    asked = ai.tier1_route("what's left of PROJ-12?")
    assert "  2. claude 'Read digest.txt and answer: what'\"'\"'s left of PROJ-12?'" in asked


def test_the_tier1_route_carries_the_flags_it_is_given_quoted_for_the_shell() -> None:
    route = ai.tier1_route("q", ["--quarter", "2026Q3", "--root", "/srv/my code"])
    assert (
        "  1. sijl digest --with-prompt --out digest.txt --quarter 2026Q3 --root '/srv/my code'\n"
    ) in route
    assert "usual period flags" not in route
    assert "usual period flags" not in ai.tier1_route("q", [])  # the same settings, no flags


def test_status_with_credentials_is_on_and_says_how_it_will_behave(network) -> None:
    result = runner.invoke(app, ["ai", "status"], env=KEY)
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "AI: on — Claude credentials found: ANTHROPIC_API_KEY"
    assert "claude-opus-5-5" in lines[1] and "https://api.anthropic.com" in lines[1]
    assert "asks first" in result.stdout and "redacted" in result.stdout
    assert TIER1 not in result.stdout and network.attempts == []


@pytest.mark.parametrize(
    ("args", "env", "file", "reason"),
    [
        (["--no-ai"], KEY, "", "turned off by --no-ai"),
        ([], {**KEY, "SIJL_AI": "off"}, "", "turned off by SIJL_AI=off"),
        ([], KEY, "[ai]\nenabled = false\n", "turned off by ai.enabled = false in "),
    ],
    ids=["flag", "environment", "config-file"],
)
def test_turning_ai_off_says_where_it_was_turned_off(args, env, file, reason) -> None:
    if file:
        config_file(file)
    result = runner.invoke(app, ["ai", "status", *args], env=env)
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"AI: off — {reason}")
    assert TIER1 in result.stdout


# ------------------------------------------------------------------------------------- check


def test_check_without_credentials_prints_the_tier1_route(claude, network) -> None:
    result = runner.invoke(app, ["ai", "check"])
    assert result.exit_code == cli.EXIT_AI
    assert result.stdout == ""
    assert "AI is off: no Claude credentials found" in result.stderr
    assert TIER1 in result.stderr
    assert claude.sent == [] and network.used == []


def test_check_shows_the_preview_and_a_no_sends_nothing(claude, network) -> None:
    result = runner.invoke(app, ["ai", "check"], env=KEY, input="n\n")
    assert result.exit_code == 0, result.output
    assert "What Sijl will send to Claude" in result.stderr
    assert "Reply with the single word OK" in result.stderr
    assert "Send this to Claude? [y/N]" in result.stderr
    assert result.stderr.rstrip().endswith("Nothing was sent to Claude.")
    assert result.stdout == ""
    assert claude.sent == [] and network.used == [] and not network.consented


def test_check_without_an_answer_sends_nothing(claude, network) -> None:
    """No terminal to answer (end of input) counts as no."""
    result = runner.invoke(app, ["ai", "check"], env=KEY, input="")
    assert result.exit_code == 0, result.output
    assert "Nothing was sent to Claude." in result.stderr
    assert claude.sent == [] and network.used == []


def test_check_sends_after_yes_and_labels_the_answer(claude, network) -> None:
    result = runner.invoke(app, ["ai", "check"], env=KEY, input="y\n")
    assert result.exit_code == 0, result.output
    assert (
        result.stdout == "*AI-written by claude-opus-5-5 — check it against your evidence.*\n\nOK\n"
    )
    assert "Claude used 30 input tokens and 5 output tokens" in result.stderr
    (sent,) = claude.sent
    assert (sent.model, sent.effort, sent.digest) == ("claude-opus-5-5", "low", "")
    assert network.used == ["send to Claude"]


def test_yes_sends_without_asking_but_still_previews(claude, network) -> None:
    network.consent()  # --yes is the user's yes, given before the command
    result = runner.invoke(app, ["ai", "check", "--yes"], env=KEY)
    assert result.exit_code == 0, result.output
    assert "What Sijl will send to Claude" in result.stderr
    assert "Send this to Claude?" not in result.stderr
    assert "Sending without asking" in result.stderr
    assert result.stdout.endswith("OK\n")


def test_the_configured_model_is_used(claude, network) -> None:
    network.consent()  # confirm = false
    config_file('[ai]\nmodel = "claude-sonnet-5-5"\nconfirm = false\n')
    result = runner.invoke(app, ["ai", "check"], env=KEY)
    assert result.exit_code == 0, result.output
    assert claude.sent[0].model == "claude-sonnet-5-5"


def test_an_incomplete_answer_is_an_error_with_the_tier1_route(claude, network) -> None:
    network.consent()
    claude.stop_reason = "refusal"
    result = runner.invoke(app, ["ai", "check", "--yes"], env=KEY)
    assert result.exit_code == cli.EXIT_AI
    assert result.stdout == ""
    assert "Claude declined to answer" in result.stderr and TIER1 in result.stderr
    assert "Claude used 30 input tokens and 5 output tokens" in result.stderr  # still billed


def test_an_api_error_is_explained_with_the_tier1_route(monkeypatch, network) -> None:
    class Failing:
        destination = "https://api.anthropic.com"

        def send(self, payload: ai.Payload) -> ai.Reply:
            raise ai.AiError("the API is rate-limiting these credentials (429): slow down")

        def close(self) -> None:
            pass

    on = ai.Status(True, "Claude credentials found: ANTHROPIC_API_KEY", Failing.destination)
    monkeypatch.setattr(cli, "_connect", lambda loaded: (on, Failing()))
    result = runner.invoke(app, ["ai", "check", "--yes"], env=KEY)
    assert result.exit_code == cli.EXIT_AI
    assert "rate-limiting" in result.stderr and TIER1 in result.stderr
    assert result.stdout == ""


def test_the_preview_names_the_client_that_sends(claude, network, monkeypatch) -> None:
    """One client, built before the preview; the To: line is its
    destination, and it is closed when the command ends."""
    env = {**KEY, "ANTHROPIC_BASE_URL": "https://gateway.example.test/anthropic"}
    result = runner.invoke(app, ["ai", "check"], env=env, input="y\n")
    assert result.exit_code == 0, result.output
    assert "To: https://gateway.example.test/…\n" in result.stderr  # never the path
    assert claude.destination == "https://gateway.example.test/…"
    assert "anthropic\n" not in result.stderr.split("To: ")[1].split("\n")[0]
    assert len(claude.sent) == 1 and claude.closed


@pytest.mark.parametrize(
    ("env", "file", "said"),
    [
        ({"SIJL_AI_CONFIRM": "false"}, "", "does not ask first (SIJL_AI_CONFIRM=false)"),
        ({}, "[ai]\nconfirm = false\n", "does not ask first (ai.confirm = false in "),
        ({}, "", "asks first"),
    ],
)
def test_status_says_where_asking_first_was_turned_off(env, file, said) -> None:
    if file:
        config_file(file)
    result = runner.invoke(app, ["ai", "status"], env={**KEY, **env})
    assert result.exit_code == 0, result.output
    assert said in result.stdout
