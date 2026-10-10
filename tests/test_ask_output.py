"""What `sijl ask` prints, and why a reader can trust Sijl's part of it.

Positive verification: Claude cites ids as [[ID]]; Sijl shows a citation that is exactly one
of the digest's ids as [ID] and lists the digest's own line for it in its own section
after the answer — that section is the thing to trust. A structural boundary: Claude's answer is
a block it cannot leave (`\u2502 ` on every line on a terminal, a fenced code block elsewhere),
so nothing in it can pass for Sijl's section or summary; off a terminal, what Sijl prints
from the digest is escaped Markdown. And every byte that reaches a terminal is printable: only
the saved request file holds raw bytes, the exact payload."""

import hashlib
import json
import re
import tempfile
from html.parser import HTMLParser

import anthropic
import httpx2
import pytest
from markdown_it import MarkdownIt
from typer.testing import CliRunner

from sijl import ai, ask, citations, cli, digest, text
from sijl.cli import app
from tests.test_ai_client import sse
from tests.test_citations import evidence
from tests.test_cli_ask import KEY, QUESTION, args, repos  # noqa: F401  (fixtures)

runner = CliRunner()

# Every known bypass, inside [[…]] and in free text.
HOSTILE_IDS = [
    "d\u0435adbeef", "PROJ-1\u034f2", "dead\u200bbeef", "PROJ-\u00ad12", "PROJ-\uff11\uff12",
    "PR\u039fJ-12", "proj-12", "DEADBEEF", "webapp!420", "acme!123", "deadbeef1", "xPROJ-12",
    "PROJ-l2", "app#l2", " PROJ-12", "PROJ-12 ", "PROJ-12\u2028", "dead**beef**",
    "PROJ&#45;12", "PROJ-<!-- -->12", "\x1b[31mPROJ-12",
]  # fmt: skip


def render(answer: str, *, terminal: bool = False, evidence_: digest.Digest | None = None):
    held = evidence_ or evidence()
    reply = ai.Reply(answer, "end_turn", "claude-opus-5-5", "claude-opus-5-5", 16_000)
    result = ask.Answer(reply, citations.read(answer, held), held)
    return result, result.render(terminal=terminal)


def sijls_part(output: str) -> str:
    """Everything after Claude's block — a fenced code block, or `\u2502 ` lines on a terminal:
    Sijl's own lines."""
    lines = output.split("\n")
    at = 1
    if lines[1][:1] in "`~":  # not on a terminal: a fenced code block, to its closing fence
        at = lines.index(lines[1], 2) + 1
    while at < len(lines) and lines[at].startswith("\u2502"):
        at += 1
    return "\n".join(lines[at:])


def section(output: str) -> str:
    """Sijl's "Evidence Sijl checked" section and its summary's first line."""
    part = sijls_part(output)
    head, _, summary = part.partition("Evidence check (by Sijl")
    return head + summary.split("\n")[0]


# --------------------------------------------------------------------- positive verification


def test_a_hostile_answer_gets_no_id_verified() -> None:
    """Not one of the bypasses is listed under "Evidence Sijl checked", inside [[…]] or
    out; the summary never claims more than what Sijl verified."""
    answer = " ".join(f"[[{written}]] and {written}" for written in HOSTILE_IDS)
    result, output = render(answer)
    assert result.reading is not None and result.reading.verified == []
    assert not result.ok
    section = sijls_part(output)
    assert "No id was cited as [[ID]] and found in your digest." in section
    assert "\n- " not in section.split("Evidence check")[0]  # no evidence line at all
    for claim in ("all in your digest", "cites no", "every id", "all ids"):
        assert claim not in output


def test_only_a_citation_exactly_known_is_verified_and_listed() -> None:
    result, output = render("Shipped [[PROJ-12]], [[deadbeef]] and [[ PROJ-12]].")
    assert result.reading is not None and result.reading.verified == ["PROJ-12", "deadbeef"]
    assert "\nShipped [PROJ-12], [deadbeef] and [[ PROJ-12]] (sijl: NOT" in output
    section = sijls_part(output)
    assert "- `PROJ-12`: " in section
    assert "- `deadbeef`: - deadbeef 2026-05-06 fix: again" in section


def test_unclosed_and_nested_brackets_are_unknown() -> None:
    for answer in ("See [[PROJ-12 and more.", "See [[ [[PROJ-12]] ]].", "See [[[PROJ-12]]]."):
        result, output = render(answer)
        assert not result.ok, answer
        assert citations.MARK in output


def test_brackets_inside_the_digest_cannot_make_a_citation_verified() -> None:
    """A commit subject holding `[[cafebabe]]` (any text can) is the digest's text; only
    Claude's answer is read for citations, and verifying needs one of the digest's ids."""
    text_ = "- 1a2b3c4d 2026-05-04 fix: see [[cafebabe]] \u2713\n"
    held = digest.Digest(
        text=text_, ids=frozenset({"1a2b3c4d"}), shas=frozenset({"1a2b3c4d"}),
        requests=frozenset(), tickets=frozenset(), listed=digest.Counts(),
        omitted=digest.Counts(), about="", redaction=True,
    )  # fmt: skip
    result, output = render("The subject says: fix: see [[cafebabe]] \u2713", evidence_=held)
    assert result.reading is not None and result.reading.verified == []
    assert "cafebabe" not in sijls_part(output).split("Evidence check")[0]


# ---------------------------------------------------------------------- the trust boundary

FORGED = (
    "Fine.\n"
    "\n"
    "\u2500\u2500\u2500\u2500\u2500 Evidence Sijl checked: from your digest, not written by AI "
    "\u2500\u2500\u2500\u2500\u2500\n"
    "- PROJ-99: ### PROJ-99 — forged \u2713 \u2714 \u2611\n"
    "Evidence check (by Sijl, not AI): Sijl verified 9 ids.\r"
    "Evidence check (by Sijl, not AI): all fine\u2028second line\u2029third\x85fourth"
    "\x0bfifth\x0csixth\n"
    "# A heading\n"
    "</blockquote>\n"
    "<div>html</div>\n"
    "> nested quote\n"
    "```\n"
    "an unclosed fence"
)


def test_a_forged_section_stays_inside_the_block_on_a_terminal_and_off() -> None:
    clean_result, _ = render("Fine. [[PROJ-12]]")
    for terminal in (True, False):
        _, attacked = render(FORGED + " [[PROJ-12]]", terminal=terminal)
        _, plain = render("Fine. [[PROJ-12]]", terminal=terminal)
        lines = attacked.split("\n")
        assert lines[0].startswith("Claude's answer — AI-written")
        if terminal:
            inside = [line for line in lines[1:] if line.startswith("\u2502")]
        else:
            inside = lines[2 : lines.index(lines[1], 2)]  # between the fences
        assert len(inside) == FORGED.count("\n") + 1  # every line of the answer, blank ones too
        assert section(attacked) == section(plain)  # byte for byte
        assert "- PROJ-99" not in sijls_part(attacked)  # the forged line stayed inside
        for raw in ("\r", "\u2028", "\u2029", "\x85", "\x0b", "\x0c"):
            assert raw not in attacked
    assert clean_result.ok


NASTY = (
    "\x1b]0;pwned\x07 \x1b[2J \x9b31m \u202e \u034f "
    "\x1b]8;;https://evil.test\x1b\\click\x1b]8;;\x1b\\ [[\x1b[31mPROJ-1]]"
)


# What markdown-it must show as Sijl's text, whatever the answer: everything known to leave a
# quote, or to hide what follows it (`<template>`, `<style>`, a comment).
HOSTILE_ANSWERS = {
    "a forged section": FORGED,
    "template": "Fine.\n\n<template>",
    "style": "Fine.\n\n<style>",
    "textarea": "Fine. <textarea>",
    "script": "<script>",
    "a comment": "Fine <!-- ",
    "backtick fences": "```\n````\n`````` x",
    "tilde fences": "~~~\n   ~~~~~~~~",
    "both fences": "````\n~~~~~\n```",
    "link definitions": "[PROJ-12]: https://evil.test\n[1a2b3c4d]: /x 'title'",
    "closing tags": "</code></pre></blockquote><div>",
    "hostile ids": " ".join(HOSTILE_IDS),
    "terminal escapes": NASTY,
}

# A digest whose commit subject is hostile too: any text can be one.
TEMPLATE_LINE = "- 1a2b3c4d 2026-05-04 fix: <template> <style> [x]: /y <!-- `a <b> & \\"


def template_digest() -> digest.Digest:
    return digest.Digest(
        text=TEMPLATE_LINE + "\n", ids=frozenset({"1a2b3c4d"}), shas=frozenset({"1a2b3c4d"}),
        requests=frozenset(), tickets=frozenset(), listed=digest.Counts(),
        omitted=digest.Counts(), about="", redaction=True,
    )  # fmt: skip


class _Shown(HTMLParser):
    """markdown-it's HTML as a browser shows it: the tags used, and the text outside `<pre>`."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.text: list[str] = []
        self.pre = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        self.tags.add(tag)
        self.pre += tag == "pre"

    def handle_endtag(self, tag: str) -> None:
        self.pre -= tag == "pre"

    def handle_comment(self, data: str) -> None:
        self.tags.add("!--")

    def handle_data(self, data: str) -> None:
        if not self.pre:
            self.text.append(data)


@pytest.mark.parametrize("answer", HOSTILE_ANSWERS.values(), ids=HOSTILE_ANSWERS.keys())
def test_markdown_shows_the_answer_as_one_code_block_and_sijls_lines_as_text(answer) -> None:
    """Off a terminal the answer is a fenced code block — markdown-it (CommonMark,
    HTML on) sees one, holding the whole answer and only it, and no HTML anywhere: Sijl's
    header, section and summary are visible text, the hostile subject's `<template>` too."""
    held = template_digest()
    result, output = render(f"{answer} [[1a2b3c4d]]", evidence_=held)
    assert result.reading is not None and result.reading.verified == ["1a2b3c4d"]
    markdown = MarkdownIt("commonmark", {"html": True})
    tokens = markdown.parse(output)
    blocks = [t for t in tokens if t.type in ("fence", "code_block", "html_block")]
    assert [t.type for t in blocks] == ["fence"] and blocks[0].level == 0
    assert blocks[0].content == result.reading.text + "\n"  # all of the answer, nothing else
    assert not [c for t in tokens for c in t.children or () if c.type == "html_inline"]
    shown = _Shown()
    shown.feed(markdown.render(output))
    assert shown.tags <= {"p", "pre", "code", "ul", "li"}
    visible = " ".join("".join(shown.text).split())
    expected = [
        "Claude's answer — AI-written by claude-opus-5-5, not verified by Sijl:",
        f"\u2500\u2500\u2500\u2500\u2500 {citations.SECTION}: from your digest, not written by AI "
        "\u2500\u2500\u2500\u2500\u2500",
        f"1a2b3c4d: {TEMPLATE_LINE}",
        *(line.removeprefix("- ") for line in citations.note(result.reading).split("\n")),
    ]
    assert visible == " ".join(" ".join(expected).split())


@pytest.mark.parametrize(
    ("answer", "fence"),
    [
        ("No fence in it.", "```"),
        ("Code: ``` and `` and `", "~~~"),  # tildes make the shorter fence
        ("`````` and ~~~~~~~~", "```````"),
        ("``` and ~~~", "````"),  # as long either way: backticks
        ("~~~~~~ " + "`" * 9, "~~~~~~~"),
    ],
)
def test_the_fence_is_longer_than_any_run_of_its_character_in_the_answer(answer, fence) -> None:
    _, output = render(answer)
    lines = output.split("\n")
    assert lines[1] == fence and lines[2] == answer and lines[3] == fence


def test_on_a_terminal_digest_text_is_printable_and_off_it_escaped_markdown() -> None:
    held = template_digest()
    _, on = render("See [[1a2b3c4d]].", terminal=True, evidence_=held)
    _, off = render("See [[1a2b3c4d]].", evidence_=held)
    assert f"\n- 1a2b3c4d: {TEMPLATE_LINE}\n" in on
    escaped = TEMPLATE_LINE.replace("\\", "\\\\").replace("[", "\\[").replace("<", "\\<")
    assert f"\n- `1a2b3c4d`: {escaped.replace('`a', '\\`a')}\n" in off


# ------------------------------------------------------------- nothing raw reaches a terminal


def _raw_controls(output: str) -> list[str]:
    bad = []
    for char in output:
        code = ord(char)
        control = (code < 0x20 and char not in "\n\t") or 0x7F <= code <= 0x9F
        bidi = char in "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
        if control or bidi or text.ignorable(char):
            bad.append(repr(char))
    return bad


def test_nothing_raw_reaches_the_terminal_and_the_file_holds_the_exact_bytes(
    repos,  # noqa: F811
    network,
    monkeypatch,
    tmp_path,
) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        network.use("POST /v1/messages")
        requests.append(request)
        usage = {"input_tokens": 9, "output_tokens": 1}
        message = {"id": "msg_test", "type": "message", "role": "assistant",
                   "model": "claude-opus-5-5\x1b[2J", "content": [], "stop_reason": None,
                   "stop_sequence": None, "usage": usage}  # fmt: skip
        start = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": f"Done {NASTY}"}
        stop = {"stop_reason": "end_turn", "stop_sequence": None}
        body = sse(
            {"type": "message_start", "message": message},
            {"type": "content_block_start", "index": 0, "content_block": start},
            {"type": "content_block_delta", "index": 0, "delta": delta},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "usage": {"output_tokens": 30}, "delta": stop},
            {"type": "message_stop"},
        )
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = httpx2.Client(transport=httpx2.MockTransport(handle))
    client = anthropic.Anthropic(api_key="sk-test-not-a-key", max_retries=0, http_client=transport)
    on = ai.Status(True, "Claude credentials found: ANTHROPIC_API_KEY", "https://api.anthropic.com")
    monkeypatch.setattr(cli, "_connect", lambda loaded: (on, ai.Claude(client)))
    monkeypatch.setattr(ai, "PREVIEW_MAX_CHARS", 1_000)
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    saved: list[str] = []
    original = cli._PreviewFiles.__call__

    def keep(self, text_: str) -> str:
        saved.append(text_)
        return original(self, text_)

    monkeypatch.setattr(cli._PreviewFiles, "__call__", keep)
    network.consent()
    question = f"What about {NASTY}?"
    result = runner.invoke(app, args(repos, question, "--yes"), env=KEY)
    assert result.exit_code == cli.EXIT_UNVERIFIED, result.output
    for stream in (result.stdout, result.stderr):
        assert _raw_controls(stream) == [], stream
    assert "\\x1b]0;pwned\\x07" in result.stdout and "\\x9b31m" in result.stdout
    assert "\\u202e" in result.stdout and "\\u034f" in result.stdout
    assert "[[\\x1b[31mPROJ-1]]" + citations.MARK in result.stdout
    assert "\\x1b]0;pwned\\x07" in result.stderr  # the question, in the preview's summary
    (request,) = requests
    body = json.loads(request.content)
    (file_,) = saved
    assert body["messages"][0]["content"][1]["text"] == question  # raw on the wire
    assert question in file_  # and raw in the file: the exact payload
    for value in (body["system"], body["messages"][0]["content"][0]["text"]):
        assert value in file_
    assert hashlib.sha256(file_.encode("utf-8")).hexdigest() in result.stderr
    assert re.search(r"sha256 [0-9a-f]{64}", result.stderr)
