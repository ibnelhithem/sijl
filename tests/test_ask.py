"""sijl.ask: a conversation about one period. Every question goes out as one payload —
Sijl's rules, then the digest with `cache_control`, then the conversation so far and the
question — through the command's one Session (one yes); a follow-up starts with the bytes of
the first request, so Claude reads the digest from its cache; every complete answer is labelled
as AI-written and its ids checked against the digest. Fake clients only: the real SDK runs over
`httpx2.MockTransport`, and nothing is sent before the test's consent."""

import json

import anthropic
import httpx2

from sijl import ai, ask, citations, digest
from sijl.redact import Redactor
from tests.gitfactory import COLLEAGUE, ME
from tests.test_ai_client import sse
from tests.test_citations import evidence

HEADER = "Claude's answer — AI-written by claude-opus-5-5, not verified by Sijl:"
SECTION = "───── Evidence Sijl checked: from your digest, not written by AI ─────"
NONE = "No id was cited as [[ID]] and found in your digest."
SUMMARY = (
    "Evidence check (by Sijl, not AI): Sijl verified {} (listed under Evidence Sijl "
    "checked). Treat any other id-like text in the answer as unverified."
)


class Fake:
    """Stands in for ai.Claude: answers each payload with the next reply text."""

    destination = "https://api.anthropic.com"

    def __init__(self, network, *texts: str, stop_reason: str = "end_turn") -> None:
        self.network, self.texts, self.stop_reason = network, list(texts), stop_reason
        self.sent: list[ai.Payload] = []

    def send(self, payload: ai.Payload) -> ai.Reply:
        self.network.use("send to Claude")
        self.sent.append(payload)
        text = self.texts.pop(0)
        return ai.Reply(text, self.stop_reason, payload.model, payload.model, payload.max_tokens)


def conversation(sender, *, redactor: Redactor | None = None, confirm=None) -> ask.Conversation:
    session = ai.Session(sender=sender, show=lambda text: None, confirm=confirm)
    return ask.Conversation(
        session=session,
        evidence=evidence(),
        model=ai.DEFAULT_MODEL,
        redactor=redactor,
        max_tokens=16_000,
        about="2 repositories",
    )


def test_the_instructions_are_the_digests_and_the_rules_of_a_conversation() -> None:
    assert ask.SYSTEM.startswith(digest.PROMPT)
    assert 'say "not in the data"' in ask.SYSTEM  # from the digest's instructions
    assert "Only the digest is evidence" in ask.SYSTEM
    assert "Cite every id you use as [[ID]]" in ask.SYSTEM


def test_a_question_is_one_payload_with_the_digest_first(network) -> None:
    network.consent()
    fake = Fake(network, "You shipped webapp!42.")
    result = conversation(fake).ask("What did I deliver?")
    (sent,) = fake.sent
    assert (sent.system, sent.digest, sent.question) == (ask.SYSTEM, evidence().text,
                                                         "What did I deliver?")  # fmt: skip
    assert (sent.max_tokens, sent.effort, sent.about) == (16_000, "high", "2 repositories")
    assert sent.history == ()
    assert result is not None and result.reading is not None and result.ok


def test_follow_ups_carry_the_conversation_and_one_yes_covers_them(network) -> None:
    asked: list[str] = []

    def confirm(question: str) -> bool:
        asked.append(question)
        network.consent()
        return True

    fake = Fake(network, "First answer.", "Second answer.", "Third.")
    chat = conversation(fake, confirm=confirm)
    for question in ("One?", "Two?", "Three?"):
        assert chat.ask(question) is not None
    assert len(asked) == 1
    assert [p.history for p in fake.sent] == [
        (),
        (ai.Turn("One?", "First answer."),),
        (ai.Turn("One?", "First answer."), ai.Turn("Two?", "Second answer.")),
    ]
    first = fake.sent[0].params()
    assert all(p.params()["messages"][0] == first["messages"][0] for p in fake.sent)
    assert chat.turns == (*fake.sent[2].history, ai.Turn("Three?", "Third."))


def test_an_incomplete_answer_is_not_part_of_the_conversation(network) -> None:
    network.consent()
    fake = Fake(network, "Here is what", stop_reason="max_tokens")
    chat = conversation(fake)
    result = chat.ask("What did I deliver?")
    assert result is not None and not result.reply.complete and result.reading is None
    assert chat.turns == ()


def test_a_no_sends_nothing_and_records_nothing(network) -> None:
    fake = Fake(network, "unused")
    chat = conversation(fake, confirm=lambda question: False)
    assert chat.ask("What did I deliver?") is None
    assert fake.sent == [] and chat.turns == () and network.used == []


def test_questions_are_redacted_with_the_digests_labels(network) -> None:
    network.consent()
    redactor = Redactor(keep_emails=[ME.email], keep_names=[ME.name], names=[COLLEAGUE.name])
    fake = Fake(network, "not in the data")
    conversation(fake, redactor=redactor).ask(f"What did {COLLEAGUE.name} review?")
    assert fake.sent[0].question == "What did [person 1] review?"


def test_not_in_the_data_is_passed_through_and_nothing_is_added(network) -> None:
    """The system prompt asks for "not in the data" when the digest holds no evidence; Sijl
    shows that answer as it came — quoted, AI-written — and adds no evidence of its own; its
    summary claims only that it verified no id."""
    network.consent()
    result = conversation(Fake(network, "not in the data")).ask("Who reviewed my code in 2019?")
    assert result is not None and result.ok
    assert result.render() == (
        f"{HEADER}\n```\nnot in the data\n```\n\n{SECTION}\n{NONE}\n\n{SUMMARY.format('0 ids')}\n"
    )


def test_cited_ids_are_verified_and_their_digest_lines_listed(network) -> None:
    """Positive verification: each [[ID]] the digest holds is shown as [ID], and listed
    after the answer with the digest's own line for it — Sijl's words, from the data."""
    network.consent()
    text = "You shipped [[webapp!42]] ([[1a2b3c4d]]) for [[PROJ-12]]; again [[webapp!42]]."
    result = conversation(Fake(network, text)).ask("What did I deliver?")
    assert result is not None and result.ok
    assert result.render() == (
        f"{HEADER}\n```\nYou shipped [webapp!42] ([1a2b3c4d]) for [PROJ-12]; again "
        "[webapp!42].\n```\n\n"
        f"{SECTION}\n"
        "- `webapp!42`: ### webapp!42 — 2026-05-06 \\[webapp]; tickets: PROJ-12; title: Billing\n"
        "- `1a2b3c4d`: - 1a2b3c4d 2026-05-04 feat(PROJ-12): billing flow\n"
        "- `PROJ-12`: ### webapp!42 — 2026-05-06 \\[webapp]; tickets: PROJ-12; title: Billing\n\n"
        f"{SUMMARY.format('3 ids')}\n"
    )


def test_on_a_terminal_the_answer_is_quoted_with_a_bar(network) -> None:
    network.consent()
    result = conversation(Fake(network, "One.\n\nTwo.")).ask("Q?")
    assert result is not None
    assert result.render(terminal=True).startswith(f"{HEADER}\n│ One.\n│\n│ Two.\n\n")


def test_unknown_ids_are_marked_in_the_answer_and_named_under_it(network) -> None:
    network.consent()
    text = "You shipped [[webapp!42]] and [[webapp!999]], and webapp!998."
    result = conversation(Fake(network, text)).ask("What did I deliver?")
    assert result is not None and not result.ok
    assert result.render() == (
        f"{HEADER}\n```\nYou shipped [webapp!42] and [[webapp!999]]{citations.MARK}, and "
        f"webapp!998{citations.MARK}.\n```\n\n"
        f"{SECTION}\n"
        "- `webapp!42`: ### webapp!42 — 2026-05-06 \\[webapp]; tickets: PROJ-12; title: "
        "Billing\n\n"
        f"{SUMMARY.format('1 id')}\n"
        "- NOT in your evidence, marked in the answer above: 1 [[…]] citation and 1 other "
        "id-like text.\n"
    )


def test_the_check_reads_what_the_terminal_shows(network) -> None:
    """An invisible character inside an id is shown as its escape: the check reads and marks
    that shown text, so what it verifies is what the user reads."""
    network.consent()
    reply = "Fixed in dead\u200bbeef and [[dead\u200bbeef]]."
    result = conversation(Fake(network, reply)).ask("Which commit?")
    assert result is not None and not result.ok
    assert result.render() == (
        f"{HEADER}\n```\nFixed in dead\\u200bbeef{citations.MARK} and "
        f"[[dead\\u200bbeef]]{citations.MARK}.\n```\n\n{SECTION}\n{NONE}\n\n"
        f"{SUMMARY.format('0 ids')}\n"
        "- NOT in your evidence, marked in the answer above: 1 [[…]] citation and 1 other "
        "id-like text.\n"
    )


# ------------------------------------------------------------- the real SDK, over a fake API


def streamed(text: str, *, written: int, read: int) -> bytes:
    """A streamed answer whose usage reports `written` tokens written to the cache and `read`
    read from it, as the API reports them."""
    usage = {"input_tokens": 9, "output_tokens": 1, "cache_creation_input_tokens": written,
             "cache_read_input_tokens": read}  # fmt: skip
    message = {"id": "msg_test", "type": "message", "role": "assistant",
               "model": ai.DEFAULT_MODEL, "content": [], "stop_reason": None,
               "stop_sequence": None, "usage": usage}  # fmt: skip
    return sse(
        {"type": "message_start", "message": message},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "usage": {"output_tokens": 30},
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        },
        {"type": "message_stop"},
    )


def test_a_follow_up_reads_the_digest_from_claudes_cache(network) -> None:
    """Acceptance: follow-up questions reuse the cached digest. On the wire, the second request
    repeats the first one's instructions and first message byte for byte — the digest block
    with its cache marker — and the usage the API reports (cache reads) is what Sijl shows."""
    bodies = [
        streamed("You shipped webapp!42.", written=3_000, read=0),
        streamed("And PROJ-12.", written=0, read=3_000),
    ]
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        network.use("POST /v1/messages")
        requests.append(request)
        headers = {"content-type": "text/event-stream"}
        return httpx2.Response(200, headers=headers, content=bodies.pop(0))

    transport = httpx2.Client(transport=httpx2.MockTransport(handle))
    client = anthropic.Anthropic(api_key="sk-test-not-a-key", max_retries=0, http_client=transport)

    def confirm(question: str) -> bool:
        network.consent()
        return True

    chat = conversation(ai.Claude(client), confirm=confirm)
    first, second = chat.ask("What did I deliver?"), chat.ask("Which tickets?")
    one, two = (json.loads(request.content) for request in requests)
    assert two["system"] == one["system"] == ask.SYSTEM
    assert two["messages"][0] == one["messages"][0]
    assert one["messages"][0]["content"][0] == {
        "type": "text", "text": evidence().text, "cache_control": {"type": "ephemeral"}
    }  # fmt: skip
    assert two["messages"][1:] == [
        {"role": "assistant",
         "content": [{"type": "text", "text": "You shipped webapp!42."}]},
        {"role": "user", "content": [{"type": "text", "text": "Which tickets?"}]},
    ]  # fmt: skip
    assert first is not None and second is not None
    assert (first.reply.cache_write_tokens, second.reply.cache_read_tokens) == (3_000, 3_000)
    assert "(3,000 written to its cache)" in ai.render_usage(first.reply)
    assert "(3,000 read from its cache)" in ai.render_usage(second.reply)
    assert network.attempts == []
