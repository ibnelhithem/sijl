"""ai.Claude, the one door to the Anthropic API, tested with the real SDK over a fake HTTP
transport (`httpx2.MockTransport`): no socket is opened, and the fake answers only after the
test's consent step. It proves the request body on the wire carries exactly the previewed texts,
and that a refusal, a cut-off answer or an API error is never presented as a complete answer."""

import json

import anthropic
import httpx2
import pytest

from sijl import ai
from tests.test_ai_preview import DESTINATION, payload, sections, sent_texts


def sse(*events: dict) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def answer(
    *blocks: dict,
    stop_reason: str = "end_turn",
    model: str = ai.DEFAULT_MODEL,
    stop_details: dict | None = None,
    iterations: list | None = None,
) -> bytes:
    """A streamed Messages API response: message_start, each block, message_delta, stop."""
    usage = {"input_tokens": 12, "output_tokens": 1, "cache_creation_input_tokens": 1_000}
    events: list[dict] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test", "type": "message", "role": "assistant", "model": model,
                "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {**usage, "cache_read_input_tokens": 0},
            },
        }
    ]  # fmt: skip
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block["text"]}
            events += [
                {"type": "content_block_start", "index": index, "content_block": start},
                {"type": "content_block_delta", "index": index, "delta": delta},
            ]
        else:
            events.append({"type": "content_block_start", "index": index, "content_block": block})
        events.append({"type": "content_block_stop", "index": index})
    delta = {"stop_reason": stop_reason, "stop_sequence": None, "stop_details": stop_details}
    final_usage = {"output_tokens": 40, "iterations": iterations}
    events += [
        {"type": "message_delta", "delta": delta, "usage": final_usage},
        {"type": "message_stop"},
    ]
    return sse(*events)


def text(value: str) -> dict:
    return {"type": "text", "text": value}


class FakeApi:
    """The Anthropic API at the HTTP layer: records each request, answers with a canned body.
    `network.use()` makes any request before the test's consent fail."""

    def __init__(self, network, body: bytes, status: int = 200) -> None:
        self.network, self.body, self.status = network, body, status
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.network.use(f"{request.method} {request.url.path}")
        self.requests.append(request)
        kind = "text/event-stream" if self.status == 200 else "application/json"
        return httpx2.Response(self.status, headers={"content-type": kind}, content=self.body)

    def claude(self) -> ai.Claude:
        transport = httpx2.MockTransport(self.handle)
        client = anthropic.Anthropic(
            api_key="sk-test-not-a-key",
            http_client=httpx2.Client(transport=transport),
            max_retries=0,
        )
        return ai.Claude(client)


def send(network, body: bytes, sent: ai.Payload | None = None, status: int = 200):
    network.consent()
    api = FakeApi(network, body, status)
    reply = api.claude().send(sent or payload())
    return reply, api


def test_the_bytes_on_the_wire_are_the_previewed_ones(network) -> None:
    sent = payload()
    _, api = send(network, answer(text("You shipped DEV-12.")), sent)
    (request,) = api.requests
    assert request.url.path == "/v1/messages"
    body = json.loads(request.content)
    params = sent.params()
    assert body["messages"] == params["messages"] and body["system"] == params["system"]
    assert set(body) == {*params} - {"betas"} | {"stream"}  # nothing else is added
    assert ai.FALLBACK_BETA in request.headers["anthropic-beta"]
    shown = sections(ai.render_preview(sent, destination=DESTINATION))
    for name, value in sent_texts(body).items():
        assert shown[name][1].removesuffix("\n") == value.removesuffix("\n")


def test_a_complete_answer_is_labelled_as_ai_written(network) -> None:
    reply, _ = send(network, answer(text("You shipped "), text("DEV-12.")))
    assert reply.complete and reply.problem == ""
    assert reply.text == "You shipped DEV-12."
    assert ai.render_reply(reply) == (
        "*AI-written by claude-opus-5-5 — check it against your evidence.*\n\nYou shipped DEV-12.\n"
    )


def test_a_refusal_is_never_presented_as_an_answer(network) -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": None}
    reply, _ = send(
        network, answer(text("Here is what"), stop_reason="refusal", stop_details=details)
    )
    assert not reply.complete
    assert reply.problem.startswith("Claude declined to answer (category: cyber)")
    with pytest.raises(ValueError, match="not a complete answer"):
        ai.render_reply(reply)


@pytest.mark.parametrize(
    ("stop_reason", "words"),
    [
        ("max_tokens", "cut off at the output limit (64,000 tokens)"),
        ("model_context_window_exceeded", "larger than the model's context window"),
        ("pause_turn", "stopped before finishing (pause_turn)"),
    ],
)
def test_a_cut_off_answer_is_never_presented_as_complete(network, stop_reason, words) -> None:
    reply, _ = send(network, answer(text("You shipped DEV-1"), stop_reason=stop_reason))
    assert not reply.complete
    assert words in reply.problem and "not shown as an answer" in reply.problem
    with pytest.raises(ValueError):
        ai.render_reply(reply)


def test_an_empty_answer_is_not_an_answer(network) -> None:
    reply, _ = send(network, answer(text("  ")))
    assert not reply.complete and "no text" in reply.problem


def test_a_fallback_answer_keeps_only_the_fallback_models_text(network) -> None:
    """After a mid-stream decline the declined model's partial text comes first: it is dropped,
    and the label names the model that answered."""
    boundary = {
        "type": "fallback",
        "from": {"model": "claude-opus-5-5"},
        "to": {"model": "claude-opus-5"},
        "trigger": {"category": "cyber"},
    }
    iterations = [{"type": "fallback_message", "model": "claude-opus-5"}]
    reply, _ = send(
        network,
        answer(text("partial"), boundary, text("You shipped DEV-12."), iterations=iterations),
    )
    assert reply.complete and reply.fell_back
    assert (reply.text, reply.model) == ("You shipped DEV-12.", "claude-opus-5")
    assert ai.render_reply(reply).startswith(
        "*AI-written by claude-opus-5 (Anthropic's fallback after claude-opus-5-5 declined)"
    )


@pytest.mark.parametrize(
    ("status", "words"),
    [
        (401, "did not accept the credentials (401)"),
        (403, "not allowed (403)"),
        (404, "check ai.model"),
        (429, "rate-limiting"),
        (400, "refused the request (400)"),
        (500, "error (500)"),
        (529, "overloaded (529)"),
    ],
)
def test_api_errors_are_explained_in_plain_words(network, status, words) -> None:
    error = {"type": "error", "error": {"type": "x", "message": "the API's own words"}}
    with pytest.raises(ai.AiError) as raised:
        send(network, json.dumps(error).encode(), status=status)
    assert words in str(raised.value) and "the API's own words" in str(raised.value)


def test_a_dropped_connection_is_explained(network) -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    network.consent()
    client = anthropic.Anthropic(
        api_key="sk-test-not-a-key",
        http_client=httpx2.Client(transport=httpx2.MockTransport(fail)),
        max_retries=0,
    )
    with pytest.raises(ai.AiError, match="could not reach"):
        ai.Claude(client).send(payload())


def test_the_real_usage_and_cost_are_reported_after_the_answer(network) -> None:
    reply, _ = send(network, answer(text("OK")))
    assert (reply.input_tokens, reply.output_tokens, reply.cache_write_tokens) == (12, 40, 1_000)
    line = ai.render_usage(reply)
    assert line.startswith("Claude used 1,012 input tokens (1,000 written to its cache")
    assert "40 output tokens" in line
    # 12 x $4 + 1,000 x $5 + 40 x $20 per million = $0.005848
    assert line.endswith("about $0.0058 at the local prices for claude-opus-5-5.")


# A reply that breaks while it is read is an incomplete answer —
# explained, exit 4 with the Tier 1 route — never a traceback, never shown as complete.


class BreaksAfter(httpx2.SyncByteStream):
    """A response body that delivers `first`, then fails the way a network or gateway does."""

    def __init__(self, first: bytes, failure: Exception) -> None:
        self.first, self.failure = first, failure

    def __iter__(self):
        yield self.first
        raise self.failure


STARTED = answer(text("You shipped DEV-1")).split(b"event: message_delta")[0]
EVENT_STREAM = {"content-type": "text/event-stream"}
BROKEN_JSON = b"event: content_block_delta\ndata: {nope\n\n"


@pytest.mark.parametrize(
    ("response", "cause"),
    [
        (lambda r: httpx2.Response(200, headers=EVENT_STREAM, stream=BreaksAfter(
            STARTED, httpx2.ReadError("connection reset"))), "ReadError: connection reset"),
        (lambda r: httpx2.Response(200, headers=EVENT_STREAM, stream=BreaksAfter(
            STARTED, httpx2.ReadTimeout("timed out"))), "ReadTimeout: timed out"),
        (lambda r: httpx2.Response(200, headers=EVENT_STREAM, stream=BreaksAfter(
            STARTED, httpx2.RemoteProtocolError("peer closed"))), "RemoteProtocolError"),
        (lambda r: httpx2.Response(200, headers={"content-type": "text/html"},
                                   content=b"<html>Wi-Fi login</html>"), "AssertionError"),
        (lambda r: httpx2.Response(200, headers=EVENT_STREAM,
                                   content=STARTED + BROKEN_JSON),
         "JSONDecodeError"),
    ],
    ids=["reset", "timeout", "protocol", "captive-portal-html", "broken-json"],
)  # fmt: skip
def test_a_reply_that_breaks_while_read_is_an_incomplete_answer(network, response, cause) -> None:
    network.consent()
    client = anthropic.Anthropic(
        api_key="sk-test-not-a-key",
        http_client=httpx2.Client(transport=httpx2.MockTransport(response)),
        max_retries=0,
    )
    with pytest.raises(ai.AiError) as raised:
        ai.Claude(client).send(payload())
    message = str(raised.value)
    assert message.startswith("the answer is incomplete: the reply could not be read (")
    assert cause in message


def test_the_usage_line_adds_every_attempt_at_its_own_prices(network) -> None:
    """A mid-stream decline bills the
    declined attempt too; top-level usage covers only the attempt that answered."""
    iterations = [
        {"type": "message", "input_tokens": 0, "output_tokens": 50_000,
         "cache_creation_input_tokens": 23_050, "cache_read_input_tokens": 0},
        {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 10,
         "output_tokens": 64_000, "cache_creation_input_tokens": 23_050,
         "cache_read_input_tokens": 0},
    ]  # fmt: skip
    boundary = {"type": "fallback", "from": {"model": "claude-opus-5-5"},
                "to": {"model": "claude-opus-5"}}  # fmt: skip
    body = answer(text("partial"), boundary, text("answer"), iterations=iterations)
    reply, _ = send(network, body)
    assert [a.model for a in reply.attempts] == ["claude-opus-5-5", "claude-opus-5"]
    line = ai.render_usage(reply)
    assert line.startswith("Claude used 46,110 input tokens (46,100 written to its cache)")
    assert "114,000 output tokens" in line
    assert "over 2 attempts (claude-opus-5-5 declined, claude-opus-5 answered)" in line
    # 50,000 x $20 + 23,050 x $5 (opus-5-5) + 10 x $5 + 64,000 x $25 + 23,050 x $6.25 (opus-5)
    assert line.endswith("about $2.8594 at the local prices.")


def test_a_usage_line_with_an_unpriced_model_names_it_instead_of_a_number(network) -> None:
    iterations = [
        {"type": "message", "input_tokens": 5, "output_tokens": 7,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        {"type": "fallback_message", "model": "claude-future-9", "input_tokens": 5,
         "output_tokens": 7, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    ]  # fmt: skip
    reply, _ = send(network, answer(text("answer"), iterations=iterations))
    line = ai.render_usage(reply)
    assert "the cost cannot be computed: no local price for claude-future-9" in line
    assert "$" not in line


def test_the_sdk_does_not_tell_anthropic_about_this_machine(network, monkeypatch) -> None:
    """Disclosure: the SDK adds X-Stainless-OS, -Arch, -Runtime and -Runtime-Version
    headers to every request. The client Sijl builds removes them; the SDK's name and
    version (User-Agent) stay."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    api = FakeApi(network, answer(text("OK")))
    _, claude = ai.connect(
        enabled=True, http_client=httpx2.Client(transport=httpx2.MockTransport(api.handle))
    )
    network.consent()
    claude.send(payload())
    headers = {name.lower() for name in api.requests[0].headers}
    for removed in ai.PLATFORM_HEADERS:
        assert removed.lower() not in headers
    assert "user-agent" in headers
    claude.close()
