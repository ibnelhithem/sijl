"""ai.Session, the consent gate: nothing reaches Claude before the preview and the user's yes.
Every send shows the full preview first; the first one asks — one confirmation per command —
unless the user said yes for the whole command (--yes, [ai] confirm = false). The client is
built once, before the preview (building it reads only local files), and the request goes out
through exactly that client, so the destination shown is the one used. The network guard
(tests/conftest.py) refuses any real connection, and the fakes refuse to "send" before the
test's consent step."""

import json
from pathlib import Path

import httpx2

from sijl import ai
from tests.test_ai_client import FakeApi, answer, text
from tests.test_ai_preview import DESTINATION, payload


class FakeClaude:
    """Stands in for ai.Claude: records each payload; `network.use()` refuses before consent."""

    destination = DESTINATION

    def __init__(self, network) -> None:
        self.network = network
        self.sent: list[ai.Payload] = []

    def send(self, sent: ai.Payload) -> ai.Reply:
        self.network.use("send to Claude")
        self.sent.append(sent)
        return ai.Reply("You shipped DEV-12.", "end_turn", sent.model, sent.model, sent.max_tokens)


def test_nothing_is_sent_before_the_yes(network) -> None:
    shown: list[str] = []
    fake = FakeClaude(network)

    def confirm(question: str) -> bool:
        assert question == "Send this to Claude?"
        assert len(shown) == 1 and "What Sijl will send to Claude" in shown[0]
        assert (fake.sent, network.used, network.attempts) == ([], [], [])
        network.consent()  # the user's yes
        return True

    reply = ai.Session(sender=fake, show=shown.append, confirm=confirm).send(payload())
    assert reply is not None and reply.complete
    assert network.used == ["send to Claude"]


def test_no_means_nothing_is_sent(network) -> None:
    shown: list[str] = []
    fake = FakeClaude(network)
    session = ai.Session(sender=fake, show=shown.append, confirm=lambda q: False)
    assert session.send(payload()) is None
    assert len(shown) == 1  # the preview was still shown
    assert (fake.sent, network.used, network.attempts) == ([], [], [])


def test_one_yes_covers_the_rest_of_the_command(network) -> None:
    asked: list[str] = []
    shown: list[str] = []
    fake = FakeClaude(network)

    def confirm(question: str) -> bool:
        asked.append(question)
        network.consent()
        return True

    session = ai.Session(sender=fake, show=shown.append, confirm=confirm)
    session.send(payload(question="What did I deliver in May?"))
    session.send(payload(question="And in June?"))
    assert len(asked) == 1
    assert len(shown) == 2 and "And in June?" in shown[1]  # every send is previewed
    assert [p.question for p in fake.sent] == ["What did I deliver in May?", "And in June?"]


def test_yes_for_the_command_skips_the_question_but_not_the_preview(network) -> None:
    network.consent()  # --yes: the user said yes before the command started
    shown: list[str] = []
    session = ai.Session(sender=FakeClaude(network), show=shown.append, confirm=None)
    assert session.send(payload()) is not None
    assert "What Sijl will send to Claude" in shown[0]
    assert shown[1] == (
        "Sending without asking: you said yes for this command (--yes, SIJL_AI_CONFIRM=false "
        "or ai.confirm = false)."
    )


def test_what_is_sent_is_what_was_previewed(network) -> None:
    shown: list[str] = []
    fake = FakeClaude(network)

    def confirm(question: str) -> bool:
        network.consent()
        return True

    ai.Session(sender=fake, show=shown.append, confirm=confirm).send(payload())
    (sent,) = fake.sent
    assert shown[0] == ai.render_preview(sent, destination=fake.destination)


def test_the_real_sdk_sends_only_after_the_yes_to_the_destination_shown(network) -> None:
    api = FakeApi(network, answer(text("You shipped DEV-12.")))
    claude = api.claude()
    shown: list[str] = []

    def confirm(question: str) -> bool:
        assert api.requests == []
        network.consent()
        return True

    reply = ai.Session(sender=claude, show=shown.append, confirm=confirm).send(payload())
    assert reply is not None and reply.text == "You shipped DEV-12."
    (request,) = api.requests
    shown_to = next(line for line in shown[0].splitlines() if line.startswith("To: "))
    assert shown_to == f"To: {request.url.scheme}://{request.url.host}"
    assert network.attempts == []


def profile(name: str, host: str) -> None:
    """An `ant auth login` profile pointing at `host`, with a token that needs no refresh."""
    base = Path.home() / ".config" / "anthropic"
    for folder in ("configs", "credentials"):
        (base / folder).mkdir(parents=True, exist_ok=True)
    config = base / "configs" / f"{name}.json"
    config.write_text(json.dumps({"authentication": {"type": "user_oauth"}, "base_url": host}))
    config.chmod(0o644)
    secret = base / "credentials" / f"{name}.json"
    secret.write_text(json.dumps({"access_token": "not-a-token", "expires_at": 9999999999}))
    secret.chmod(0o600)


def test_switching_profile_while_the_question_waits_changes_nothing(network) -> None:
    """The preview must name the endpoint of the client that sends: when status() built one
    client and a second one was built after the yes, it could reach another. So the one client
    is built before the preview and used for the request."""
    profile("a", "https://a.example.test")
    profile("b", "https://b.example.test")
    pointer = Path.home() / ".config" / "anthropic" / "active_config"
    pointer.write_text("a")
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        network.use(f"POST {request.url.host}")
        requests.append(request)
        body = answer(text("OK"))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = httpx2.Client(transport=httpx2.MockTransport(handle))
    state, claude = ai.connect(enabled=True, http_client=transport)
    assert state.on and state.destination == "https://a.example.test" == claude.destination
    shown: list[str] = []

    def confirm(question: str) -> bool:
        pointer.write_text("b")  # `ant profile activate b` in another terminal
        network.consent()
        return True

    ai.Session(sender=claude, show=shown.append, confirm=confirm).send(payload())
    assert "To: https://a.example.test\n" in shown[0]
    assert [r.url.host for r in requests] == ["a.example.test"]
    claude.close()
