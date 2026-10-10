"""A preview too long to read on screen: six months of evidence can be a few hundred thousand
characters.
The full preview — every byte that will be sent — is saved whole where the user can open it
(`save`), and the screen shows a summary instead: where and how, what was redacted, each text
with its size, the question in full, where the full text is with its size and sha256, and the
estimate — all before the question is asked. Without a place to save, the whole preview is shown
on screen; a short preview is always shown whole."""

import hashlib
import json

from sijl import ai
from sijl.text import printable
from tests.test_ai_client import FakeApi, answer, text
from tests.test_ai_conversation import ANSWER, FIRST
from tests.test_ai_preview import DESTINATION, payload

LONG = "".join(f"- {n:08x} 2026-05-04 feat: change number {n}\n" for n in range(1, 600))
WHERE = "/tmp/sijl-ask-test/request-1.txt  (deleted when this command ends)"


class Fake:
    destination = DESTINATION

    def __init__(self, network) -> None:
        self.network, self.sent = network, []

    def send(self, sent: ai.Payload) -> ai.Reply:
        self.network.use("send to Claude")
        self.sent.append(sent)
        return ai.Reply("You shipped it.", "end_turn", sent.model, sent.model, sent.max_tokens)


def session(network, saved: list[str], shown: list[str], confirm=None) -> ai.Session:
    def save(text: str) -> str:
        saved.append(text)
        return WHERE

    return ai.Session(sender=Fake(network), show=shown.append, confirm=confirm, save=save)


def test_a_short_preview_is_shown_whole(network) -> None:
    network.consent()
    saved, shown = [], []
    session(network, saved, shown).send(payload())
    assert saved == [] and shown[0] == ai.render_preview(payload(), destination=DESTINATION)


def test_a_long_preview_is_saved_whole_and_the_screen_shows_a_summary(network) -> None:
    saved, shown = [], []
    sent = payload(digest=LONG, question=FIRST)
    full = ai.render_exact(sent, destination=DESTINATION)
    assert len(ai.render_preview(sent, destination=DESTINATION)) > ai.PREVIEW_MAX_CHARS

    def confirm(question: str) -> bool:
        assert saved == [full]  # the whole text is there before the user is asked
        network.consent()
        return True

    session(network, saved, shown, confirm).send(sent)
    summary = shown[0]
    assert LONG.splitlines()[0] not in summary  # no evidence on screen
    lines = summary.splitlines()
    assert lines[0] == (
        "What Sijl will send to Claude — too long to read here: every byte of it is in the "
        "file named below"
    )
    assert "To: https://api.anthropic.com" in lines and "Redaction: off (ai.redact = false) — " \
        "names and email addresses are sent as they are" in lines  # fmt: skip
    size = len(LONG.encode())
    assert "In the order it is sent:" in lines
    assert f"  evidence      {size:,} bytes, cached" in lines
    assert f"  question          {len(FIRST)} bytes: {FIRST}" in lines
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()
    at = lines.index("Every byte of it, exactly as it will be sent:")
    assert lines[at + 1] == f"  {WHERE}"
    assert lines[at + 2] == (
        f"  {full.count(chr(10)):,} lines, {len(full.encode()):,} bytes, sha256 {digest}"
    )
    assert "Estimate (local — nothing has been sent to count it):" in lines
    assert summary.endswith("The real usage is shown after the answer.\n")


def test_a_follow_up_summary_lists_the_earlier_turns(network) -> None:
    network.consent()
    saved, shown = [], []
    sent = payload(digest=LONG, question="And June?", history=(ai.Turn(FIRST, ANSWER),))
    session(network, saved, shown).send(sent)
    lines = shown[0].splitlines()
    assert f"  question 1        {len(FIRST)} bytes, earlier" in lines
    assert f"  answer 1          {len(ANSWER)} bytes, earlier, Claude's" in lines
    assert "  question           9 bytes: And June?" in lines
    assert saved == [ai.render_exact(sent, destination=DESTINATION)]


def test_the_summary_escapes_what_a_terminal_would_act_on(network) -> None:
    network.consent()
    saved, shown = [], []
    session(network, saved, shown).send(payload(digest=LONG, question="clear \x1b[2J?"))
    assert "\x1b" not in shown[0] and "bytes: clear \\x1b[2J?" in shown[0]  # the screen
    assert "clear \x1b[2J?" in saved[0]  # the file: the exact characters sent


def test_without_a_place_to_save_the_whole_preview_is_shown(network) -> None:
    network.consent()
    shown: list[str] = []
    sent = payload(digest=LONG)
    ai.Session(sender=Fake(network), show=shown.append, confirm=None).send(sent)
    assert shown[0] == ai.render_preview(sent, destination=DESTINATION)


def sections(saved: str, payload: ai.Payload) -> dict[str, bytes]:
    """Each text of the saved file, cut by the byte size its header line gives."""
    data, found = saved.encode("utf-8"), {}
    for name, note, value in payload.texts():
        header = (printable(ai._header(name, note, value)) + "\n").encode("utf-8")
        start = data.index(header) + len(header)
        found[name] = data[start : start + len(value.encode("utf-8"))]
    return found


def test_the_saved_file_holds_the_exact_bytes_sent_and_their_sha256(network) -> None:
    """Every text in the file is the request's text byte for byte — a terminal control, a
    zero-width space and a combining joiner included, which the screen shows escaped — and the
    summary's sha256 is of exactly the file's bytes. Checked against the body the real SDK puts
    on the wire, over a mock transport."""
    odd = LONG + "- a1b2c3d4 clear \x1b[2J, dead\u200bbeef, PROJ-12\u034f3\n"
    sent = payload(digest=odd, question="what about \x1b]0;title\x07 and caf\u00e9?")
    api = FakeApi(network, answer(text("Nothing.")))
    saved: list[str] = []
    shown: list[str] = []

    def save(file: str) -> str:
        saved.append(file)
        return WHERE

    def confirm(question: str) -> bool:
        network.consent()
        return True

    ai.Session(sender=api.claude(), show=shown.append, confirm=confirm, save=save).send(sent)
    (request,) = api.requests
    body = json.loads(request.content)
    content = body["messages"][0]["content"]
    on_the_wire = {
        "instructions": body["system"],
        "evidence": content[0]["text"],
        "question": content[1]["text"],
    }
    (file,) = saved
    for name, data in sections(file, sent).items():
        assert data == on_the_wire[name].encode("utf-8"), name
    assert hashlib.sha256(file.encode("utf-8")).hexdigest() in shown[0]
    assert "\x1b" not in shown[0] and "\u200b" not in shown[0]  # the screen stays escaped
