"""The payload and its preview: one `Payload` is both what the preview shows and what the
client sends, so the preview can never differ from the bytes sent. The preview shows every text
in full, where it will go, how it was redacted, and a *local* token and cost estimate — the
API's token counter would itself upload the text, so it is never used before consent."""

import re

import pytest

from sijl import ai
from sijl.redact import Redactor
from sijl.text import printable_block
from tests.gitfactory import COLLEAGUE, ME

SYSTEM = "Answer only from the evidence."
DIGEST = "## DEV-12 Caveats\n- `abc12345` feat(report): add caveats\n"
QUESTION = "What did I deliver in May?"
DESTINATION = "https://api.anthropic.com"


def payload(redactor: Redactor | None = None, **kwargs) -> ai.Payload:
    fields = {"model": ai.DEFAULT_MODEL, "system": SYSTEM, "digest": DIGEST, "question": QUESTION}
    return ai.build_payload(**{**fields, **kwargs}, redactor=redactor)


def sections(preview: str) -> dict[str, str]:
    """Each '───── <name> (N bytes) ─────' section of a preview → the text shown under it."""
    found = {}
    parts = re.split(r"^───── (.+) \((\d[\d,]*) bytes?(?:, [^)]*)?\) ─────\n", preview, flags=re.M)
    for name, size, body in zip(parts[1::3], parts[2::3], parts[3::3], strict=True):
        shown = body.split("───── end of what will be sent ─────")[0]
        found[name] = (int(size.replace(",", "")), shown)
    return found


def sent_texts(params: dict) -> dict[str, str]:
    """The texts in the request, by the name the preview gives them."""
    content = params["messages"][0]["content"]
    texts = {"question": content[-1]["text"]}
    if len(content) == 2:
        texts["evidence"] = content[0]["text"]
    if "system" in params:
        texts["instructions"] = params["system"]
    return texts


def test_the_request_shape_follows_the_design() -> None:
    params = payload().params()
    assert params["model"] == "claude-opus-5-5"
    assert params["output_config"] == {"effort": "high"}  # explicit: Opus 5.5 defaults to medium
    assert params["thinking"] == {"type": "adaptive"}
    assert params["max_tokens"] == 64_000
    assert params["system"] == SYSTEM
    digest, question = params["messages"][0]["content"]
    assert digest == {"type": "text", "text": DIGEST, "cache_control": {"type": "ephemeral"}}
    assert question == {"type": "text", "text": QUESTION}  # after the cached digest
    assert params["fallbacks"] == "default"
    assert params["betas"] == ["server-side-fallback-2026-07-01"]


def test_fallbacks_only_for_models_that_take_them() -> None:
    params = payload(model="claude-opus-4-8").params()
    assert "fallbacks" not in params and "betas" not in params


def test_without_a_digest_only_the_question_is_sent() -> None:
    params = payload(digest="", system="").params()
    assert params["messages"] == [{"role": "user", "content": [{"type": "text", "text": QUESTION}]}]
    assert "system" not in params


def test_an_empty_question_is_refused() -> None:
    with pytest.raises(ValueError, match="question"):
        payload(question="  \n")


def test_the_preview_shows_every_byte_that_is_sent() -> None:
    sent = payload()
    preview = ai.render_preview(sent, destination=DESTINATION)
    shown = sections(preview)
    texts = sent_texts(sent.params())
    assert set(shown) == {"instructions", "evidence", "question"} == set(texts)
    for name, text in texts.items():
        size, body = shown[name]
        assert size == len(text.encode("utf-8"))
        assert body.removesuffix("\n") == text.removesuffix("\n")


def test_the_preview_escapes_what_a_terminal_would_act_on() -> None:
    """The request carries the raw character; the preview shows it as a visible escape."""
    sent = payload(question="clear \x1b[2J screen?")
    preview = ai.render_preview(sent, destination=DESTINATION)
    assert "\x1b" not in preview
    assert "clear \\x1b[2J screen?" in preview
    assert sent.params()["messages"][0]["content"][-1]["text"] == "clear \x1b[2J screen?"
    assert printable_block(sent.question) in preview


def test_the_preview_says_where_it_goes_and_how() -> None:
    preview = ai.render_preview(payload(), destination=DESTINATION)
    assert "To: https://api.anthropic.com" in preview
    assert "claude-opus-5-5" in preview and "effort high" in preview
    assert "another Claude model" in preview  # server-side fallbacks are disclosed


def test_redaction_runs_over_the_digest_then_the_question_with_shared_labels() -> None:
    redactor = Redactor(keep_emails=[ME.email], keep_names=[ME.name], names=[COLLEAGUE.name])
    sent = payload(
        digest=f"- {COLLEAGUE.name} <{COLLEAGUE.email}> reviewed {ME.name}'s change",
        question=f"What did I build with {COLLEAGUE.name}?",
        redactor=redactor,
    )
    assert sent.digest == f"- [person 1] <[email 1]> reviewed {ME.name}'s change"
    assert sent.question == "What did I build with [person 1]?"
    assert (sent.redacted_names, sent.redacted_emails) == (1, 1)
    preview = ai.render_preview(sent, destination=DESTINATION)
    assert "Redaction: on — 1 name and 1 email address replaced" in preview


def test_the_preview_says_when_redaction_is_off() -> None:
    preview = ai.render_preview(payload(), destination=DESTINATION)
    assert "Redaction: off (ai.redact = false)" in preview


def test_the_token_estimate_is_local_and_leans_high() -> None:
    assert ai.estimate_tokens("") == 0
    assert ai.estimate_tokens("abc") == 1
    assert ai.estimate_tokens("abcd") == 2  # rounded up
    assert ai.estimate_tokens("é") == 1  # bytes, not characters: non-ASCII counts more


def test_the_cost_estimate_for_the_default_model() -> None:
    sent = payload(system="s" * 300, digest="d" * 2_700, question="q" * 30)
    estimate = ai.estimate(sent)
    assert (estimate.cached_tokens, estimate.input_tokens) == (1_000, 10)
    assert estimate.max_output_tokens == 64_000
    # $5.00/M to write 1,000 tokens to the cache, $4.00/M for 10 more, $20.00/M for output
    assert estimate.input_cost == pytest.approx(0.005 + 0.00004)
    assert estimate.max_output_cost == pytest.approx(1.28)
    preview = ai.render_preview(sent, destination=DESTINATION)
    assert "Estimate (local — nothing has been sent to count it)" in preview
    assert "if claude-opus-5-5 answers: at most $1.29" in preview
    assert ai.PRICES_AS_OF in preview


def test_the_total_is_the_worst_case_across_the_fallback_chain() -> None:
    """With `fallbacks: "default"`, a declined attempt is billed and
    the fallback models cost more, so the ceiling adds every model that may run — each with
    the cache write, the input and the full output limit at its own prices."""
    sent = payload(system="s" * 300, digest="d" * 2_700, question="q" * 30)
    # claude-opus-5-5: 1,000 x $5 + 10 x $4 + 64,000 x $20 = $1.28504
    # claude-opus-5 and claude-opus-4-8: 1,000 x $6.25 + 10 x $5 + 64,000 x $25 = $1.6063 each
    assert ai.estimate(sent).worst_case == pytest.approx(1.28504 + 2 * 1.6063)
    preview = ai.render_preview(sent, destination=DESTINATION)
    total = next(line for line in preview.splitlines() if line.startswith("  total"))
    assert total.startswith("  total   at most $4.50 —")
    assert "claude-opus-5" in total and "claude-opus-4-8" in total and "billed" in total
    assert "claude-opus-4-8: input $5.00" in preview  # every price used is shown


def test_without_fallbacks_the_total_is_one_attempt() -> None:
    sent = payload(system="s" * 300, digest="d" * 2_700, question="q" * 30, model="claude-opus-4-8")
    assert ai.estimate(sent).worst_case == pytest.approx(1.6063)
    assert "  total   at most $1.61\n" in ai.render_preview(sent, destination=DESTINATION)


def test_an_unpublished_fallback_chain_has_no_ceiling() -> None:
    sent = payload(model="claude-sonnet-5-5")
    preview = ai.render_preview(sent, destination=DESTINATION)
    total = next(line for line in preview.splitlines() if line.startswith("  total"))
    assert "cannot be computed" in total and "claude-sonnet-5-5" in total
    assert "if claude-sonnet-5-5 answers: at most $" in preview


def test_without_a_digest_nothing_is_cached() -> None:
    estimate = ai.estimate(payload(digest="", system="", question="q" * 30))
    assert (estimate.cached_tokens, estimate.input_tokens) == (0, 10)


def test_a_model_without_a_local_price_gets_tokens_only() -> None:
    sent = payload(model="claude-haiku-4-5")
    assert ai.estimate(sent).price is None
    preview = ai.render_preview(sent, destination=DESTINATION)
    assert "no local price for claude-haiku-4-5" in preview
    assert "$" not in preview.split("Estimate")[1]


def test_the_preview_can_say_what_the_evidence_covers_without_sending_it() -> None:
    """The preview names the repositories and commit count: the caller passes
    that summary as `about`; it is shown, never sent."""
    about = "12 repositories, 340 commits, 1 April 2026 to 30 June 2026"
    sent = payload(about=about)
    assert f"Evidence: {about}" in ai.render_preview(sent, destination=DESTINATION)
    assert about not in str(sent.params())
    assert sent.params() == payload().params()


def test_a_name_that_is_not_utf8_cannot_break_the_preview_or_the_request() -> None:
    """A folder name that is not UTF-8 reaches Python as a lone surrogate,
    which no UTF-8 stream can carry. The payload shows it as a visible escape, as text.py does,
    so the preview works and still equals what is sent."""
    sent = payload(digest="repo: proj-\udcff-old\n", question="what about \ud800?", about="x\udcfe")
    assert sent.digest == "repo: proj-\\xff-old\n" and sent.question == "what about \\ud800?"
    assert sent.about == "x\\xfe"
    preview = ai.render_preview(sent, destination=DESTINATION)
    preview.encode("utf-8")  # no UnicodeEncodeError
    shown = sections(preview)
    for name, value in sent_texts(sent.params()).items():
        value.encode("utf-8")
        assert shown[name][1].removesuffix("\n") == value.removesuffix("\n")
