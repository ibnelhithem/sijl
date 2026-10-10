"""A conversation in one payload: a follow-up question carries the questions and answers
before it, after the cached digest, so every request of a session starts with the same bytes —
Sijl's instructions, then the digest with `cache_control` — and Claude reads the digest from
its cache. The history is shown in the preview like every other text, and redacted the same way;
the estimate says what a follow-up costs when the cache still holds the digest."""

import pytest

from sijl import ai
from sijl.redact import Redactor
from tests.gitfactory import COLLEAGUE, ME
from tests.test_ai_preview import DESTINATION, DIGEST, SYSTEM, payload, sections

FIRST, ANSWER = "What did I deliver in May?", "You shipped DEV-12 (abc12345)."
FOLLOW_UP = "And in June?"


def follow_up(**kwargs) -> ai.Payload:
    return payload(question=FOLLOW_UP, history=(ai.Turn(FIRST, ANSWER),), **kwargs)


def test_a_follow_up_keeps_the_first_request_as_its_prefix() -> None:
    first, second = payload(question=FIRST).params(), follow_up().params()
    assert second["system"] == first["system"] == SYSTEM
    assert second["messages"][0] == first["messages"][0]  # instructions, digest, first question
    assert second["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": DIGEST, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": FIRST},
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": ANSWER}]},
        {"role": "user", "content": [{"type": "text", "text": FOLLOW_UP}]},
    ]
    assert str(second).count("cache_control") == 1  # one breakpoint: the digest


def test_each_turn_is_appended_never_rewritten() -> None:
    turns = (ai.Turn(FIRST, ANSWER), ai.Turn(FOLLOW_UP, "Nothing: not in the data."))
    third = payload(question="And July?", history=turns).params()["messages"]
    assert third[:3] == follow_up().params()["messages"]
    assert [m["role"] for m in third] == ["user", "assistant", "user", "assistant", "user"]


def test_the_preview_shows_every_earlier_question_and_answer() -> None:
    sent = follow_up()
    shown = sections(ai.render_preview(sent, destination=DESTINATION))
    assert list(shown) == ["instructions", "evidence", "question 1", "answer 1", "question"]
    for name, text in [("question 1", FIRST), ("answer 1", ANSWER), ("question", FOLLOW_UP)]:
        assert shown[name] == (len(text.encode()), text + "\n")
    assert [name for name, _, _ in sent.texts()] == list(shown)


def test_the_history_is_redacted_like_the_question() -> None:
    redactor = Redactor(keep_emails=[ME.email], keep_names=[ME.name], names=[COLLEAGUE.name])
    sent = payload(
        question=f"Did {COLLEAGUE.name} review it?",
        history=(ai.Turn(f"What did {COLLEAGUE.name} and I do?", f"{COLLEAGUE.name} paired."),),
        redactor=redactor,
    )
    assert sent.history == (ai.Turn("What did [person 1] and I do?", "[person 1] paired."),)
    assert sent.question == "Did [person 1] review it?"


def test_redacting_an_already_redacted_digest_changes_no_byte() -> None:
    """The digest is redacted when it is built; build_payload() runs the same redactor over it
    again for every request of a session, which must leave the bytes — the cache key — alone."""
    redactor = Redactor(keep_emails=[ME.email], keep_names=[ME.name], names=[COLLEAGUE.name])
    once = redactor(f"- {COLLEAGUE.name} <{COLLEAGUE.email}> and sam@laptop helped {ME.name}")
    first = payload(digest=once, redactor=redactor)
    second = payload(digest=once, question=FOLLOW_UP, redactor=redactor,
                     history=(ai.Turn(first.question, ANSWER),))  # fmt: skip
    assert first.digest == second.digest == once
    assert first.params()["messages"][0] == second.params()["messages"][0]


def test_earlier_turns_are_input_the_cache_does_not_hold() -> None:
    sent = payload(system="s" * 300, digest="d" * 2_700, question="q" * 30,
                   history=(ai.Turn("a" * 30, "b" * 300),))  # fmt: skip
    estimate = ai.estimate(sent)
    assert (estimate.cached_tokens, estimate.input_tokens) == (1_000, 10 + 10 + 100)
    # read from the cache at $0.20/M instead of written at $5.00/M; the rest at $4.00/M
    assert estimate.warm_input_cost == pytest.approx((1_000 * 0.20 + 120 * 4.00) / 1e6)


def test_a_follow_up_estimate_says_what_a_warm_cache_costs() -> None:
    sent = payload(system="s" * 3_000, digest="d" * 297_000, question="q" * 30,
                   history=(ai.Turn("a" * 30, "b" * 300),))  # fmt: skip
    preview = ai.render_preview(sent, destination=DESTINATION)
    lines = preview.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("  input "))
    assert lines[at].startswith("  input   ~100,120 tokens: $0.50 (100,000 of them written")
    assert lines[at + 1] == (
        "          $0.02 instead if Claude's cache still holds them: it keeps them for 5 "
        "minutes after each use"
    )
    first = ai.render_preview(payload(digest="d" * 297_000), destination=DESTINATION)
    assert "cache still holds them" not in first  # nothing can be cached before the first
