"""`sijl ask`: questions about your period, answered by Claude from the digest,
with every id the answer cites checked on this computer.

One `Conversation` per command. Each question becomes one `ai.Payload` — `SYSTEM` (the digest's
own instructions and the rules of a conversation), the digest marked for Claude's cache,
the questions and answers so far, then the question — sent through the command's one
`ai.Session`, so the user sees every request and says yes once. A follow-up starts with exactly
the bytes of the first request (the digest is the same text, redacted by the same redactor, and
the history only grows at the end), so Claude reads the digest from its cache: about a
twentieth of the first question's input price.

An answer is part of the conversation only when it is complete (`Reply.complete`); a refusal or
a cut-off is reported by the caller and forgotten. A complete answer is shown labelled as
AI-written and read by `citations.read()`: every `[[ID]]` citation that is exactly one of the
digest's ids gets Sijl's check mark and the digest's own line in a section after the answer
(positive verification); anything else id-shaped is marked as not in the evidence (detection,
best effort). Only Claude's text goes back
in the history — never thinking blocks, which are bound to the conversation that produced them
and would make the payload something the preview cannot show.
"""

from __future__ import annotations

from dataclasses import dataclass

from sijl import ai, citations
from sijl.digest import PROMPT, Digest
from sijl.redact import Redactor
from sijl.report import _md
from sijl.text import printable, printable_block

# After the digest's instructions: how a conversation works, and that ids are checked.
RULES = """\
# This conversation

The person's questions come after the digest; earlier questions and your answers to them may
come first.

- Only the digest is evidence. Your earlier answers are not: check each fact against the digest
  again before you repeat it.
- Cite every id you use as [[ID]]: two square brackets, exactly one id from the digest inside,
  written exactly as the digest writes it, and nothing else — [[PROJ-12]], [[webapp!42]],
  [[1a2b3c4d]]. Cite only ids the digest writes; never shorten, extend or make one up.
  Sijl shows each [[ID]] as [ID] and lists the digest's own line for it after your answer
  when the digest holds it; anything else it marks as not in the evidence. Put a space or
  punctuation on both sides of a citation: anything glued to it is marked too.
- The digest covers only its period (Period, in its header): a question about any other time
  is "not in the data".
- Lead with the answer, then its evidence. Plain text or simple Markdown.
"""
SYSTEM = f"{PROMPT}\n{RULES}"


@dataclass(frozen=True)
class Answer:
    """What came back for one question: the reply, and — for a complete answer — Sijl's
    reading of it (`citations.read()`) and the digest it was checked against. An incomplete
    reply has no reading: it is not an answer."""

    reply: ai.Reply
    reading: citations.Reading | None
    evidence: Digest | None = None

    @property
    def ok(self) -> bool:
        """A complete answer with no unknown citation detected (exit 0)."""
        return self.reading is not None and self.reading.ok

    def render(self, *, terminal: bool = False) -> str:
        """The answer for stdout, every byte of it printable, in two parts (output
        trust boundary): Sijl's header naming the model, then Claude's text as Sijl
        shows it — `[[ID]]` rewritten, unknown ids marked — as a block it cannot leave: `\u2502 `
        lines on a `terminal`, otherwise a fenced code block (`citations.fenced()`), all text
        once rendered as Markdown; after it, only Sijl's own words: the digest's line for
        each id it verified, and its summary. Off a terminal, every piece of them that comes
        from elsewhere — the digest's lines, the model's name — is escaped Markdown, so the
        whole output can be opened as `answer.md`. Raises ValueError for an incomplete reply,
        as `ai.render_reply()` does."""
        if self.reading is None or self.evidence is None:
            raise ValueError(f"not a complete answer: {self.reply.problem}")
        by = self.reply.model
        if self.reply.fell_back:
            by += f", Anthropic's fallback after {self.reply.requested_model} declined"
        by = printable(by) if terminal else _md(printable(by))
        header = citations.QUOTE_HEADER.replace("AI-written", f"AI-written by {by}")
        if terminal:
            block = citations.quoted(self.reading.text, "\u2502 ")
        else:
            block = citations.fenced(self.reading.text)
        section = citations.evidence_section(self.reading, self.evidence, markdown=not terminal)
        note = printable_block(citations.note(self.reading))
        return f"{header}\n{block}\n\n{section}\n\n{note}\n"


class Conversation:
    """Questions about one digest, through one Session: one yes, one redactor, one cache."""

    def __init__(
        self,
        *,
        session: ai.Session,
        evidence: Digest,
        model: str,
        redactor: Redactor | None,
        max_tokens: int,
        about: str,
    ) -> None:
        self._session, self._evidence, self._model = session, evidence, model
        self._redactor, self._max_tokens, self._about = redactor, max_tokens, about
        self._turns: list[ai.Turn] = []

    @property
    def turns(self) -> tuple[ai.Turn, ...]:
        """The questions answered so far and their answers, as they were sent and received."""
        return tuple(self._turns)

    def ask(self, question: str) -> Answer | None:
        """Ask `question` after the conversation so far: the Answer, or None when the user said
        no to sending (then nothing was sent). AiError when Claude could not be used."""
        payload = ai.build_payload(
            model=self._model,
            system=SYSTEM,
            digest=self._evidence.text,
            question=question,
            redactor=self._redactor,
            max_tokens=self._max_tokens,
            about=self._about,
            history=self.turns,
        )
        reply = self._session.send(payload)
        if reply is None:
            return None
        if not reply.complete:
            return Answer(reply, None)
        self._turns.append(ai.Turn(payload.question, reply.text))
        return Answer(reply, citations.read(reply.text, self._evidence), self._evidence)
