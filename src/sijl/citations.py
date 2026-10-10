"""The citation check: every id an answer from Claude cites, looked up in the digest it was given.

`sijl ask` sends the digest and asks Claude to cite its ids — a commit's
short SHA, a request (`<repository>!N` from GitLab, `<repository>#N` from GitHub), a ticket's
key. Claude can mis-copy or invent one, and an invented id looks exactly like a real one, so
after every answer Sijl reads the answer itself, on this computer. The check is an integrity
control and **fails closed**: anything shaped like an id that is not written exactly as one of
`Digest.ids` is unknown — marked where it stands (`mark()`), counted and named under the answer
(`note()`), and `Check.ok` is False. There is no heuristic for "probably not an id" that an
invented id could satisfy; the one exemption is a key whose project is a standard (`UTF-8`,
`SHA-256`), which `jira.projects` refuses, so no digest can hold one.

**What it reads** is the answer exactly as the terminal shows it (`ai.shown_text()`: every
line through `text.printable()`), so what it verifies is what the user reads, and the marks go
into that same text.

**1. Normalise — to find tokens, never to verify them** (`_skeleton()`): every hidden character
— one a renderer shows as nothing (`_hidden()`: format characters such as the zero-width space,
soft hyphen and bidi controls; every default-ignorable character, `text.ignorable()`: variation
selectors, the combining grapheme joiner, Mongolian selectors, Khmer inherent vowels, Hangul
fillers; combining marks) — is dropped, itself or the visible escape `printable()` shows for it,
and where it was is remembered; the escape of a control character is dropped too. Every other
character is put in Unicode's compatibility form (NFKC: a full-width B is B, a circled 1 is 1);
dashes, the minus sign and the look-alikes of `!` and `#` are those characters; a decimal digit
of any script is its ASCII digit (Arabic-Indic one-two is 12); a letter of another script that
looks like a Latin letter is that letter (`CONFUSABLE`: the Cyrillic e, the Greek capital O, the
Cherokee A), and one that looks like 0, 1, 3 or 6 is that digit right after a digit or `-`,
`!`, `#`; any other letter outside ASCII becomes `a` or `A`, so it still fits an id shape. Each
character of the result remembers where it came from.

**2. Find candidates** in the normalised text with `ids.CANDIDATE` — the digest's own grammar
(`ids.ID_PATTERN`) widened to near misses: a request (a repository id, optionally a project
path, then `!N` or `#N` — or a bare `!N`/`#N`), a run of 7 to 40 hex digits in either case (also
after `0x`, `_` or a literal `\\U`), or a key-shaped word in any case (`proj-99`, even
`top-10`). An ASCII `l`, `I`, `O` or `o` inside a number is read as the digit it looks like
(`PROJ-l3`, `app!l2`). Letters and digits glued after an id belong to the token, and so does
a `_` between them (`acme!123`, `PROJ-12a`, `PROJ-12_fix` and `deadbeef1` are tokens of their
own, never `acme!12`, `PROJ-12` or `deadbeef`). Markdown around an id — code spans, emphasis,
brackets, a link's text — never hides it.

**3. Look up** each candidate: **verified only when the characters shown — the token's span of
the shown text, nothing removed — are exactly one of `Digest.ids`, and are a whole token of the
digest's grammar there** (`ids.ID_PATTERN`, glued to nothing: `x_PROJ-12`, `path/1a2b3c4d`,
`a@b!3`, `_PROJ-12_` are no ids). A candidate that is a known id only once normalised —
full-width digits, Cyrillic letters, an invisible character inside, another case, glued to a
word, a path or `_` emphasis — is unknown, and `looks_like` that id. **A candidate with a
hidden character inside it or right beside it is never verified**, whatever it reads as (`hidden`).
Any other unknown candidate gets a `hint` when exactly one known id fits it: a SHA it shortens
or lengthens, a request with the same number and no repository or another path (`#12` →
`app#12`). The hints stay on the `Check` for callers and tests; the note under the answer only
counts what is unknown: it is Sijl's line, and never repeats Claude's text.

**4. Read it as Markdown shows it**: `sijl ask … > answer.md` puts
the answer in a code block (`fenced()`), but the answer can be copied anywhere Markdown is
rendered, and `cafe**babe**`, `` `cafe`babe ``, `cafe<b></b>babe`, `cafe&#x200b;babe`,
`PROJ-<!-- -->99` or `<ab:cafe>babe` show "cafebabe" or "PROJ-99" where the text as written
holds no such token. `markdown_view()` removes, in one linear pass, at least what a renderer can
(markup that can pair on its line, link and image syntax, tags and comments), decodes entities
and backslash escapes, and shows an autolink's address as markdown-it shows it (`<ab:caf%65>`
and `<http://xn--cafe->` show "cafe"); steps 1 to 3 run on it too. A token it shows that the
text as written does not hold the same way — and that no mark already covers — is unknown and
marked in the answer right after the construct (`rendered`). markdown-it's parser is not used
— only its `normalizeLinkText()`, for an autolink's address:
its link parser is not linear (3.7 s for 200,000 `[`), and a test checks against it that the
view shows every token it renders.

**Failing closed.** An answer longer than `CHECK_MAX_CHARS` is not checked; a check that runs
past `CHECK_SECONDS` or fails in any way is stopped — each gives a Check that is `unverified`,
with the reason in the note ("could NOT be checked: …"), never one that is
`ok`; `sijl ask` then exits 5.

**One grammar module.** `ids.py` holds the digest's grammar and this detector side by side;
the digest never uses `CANDIDATE` to say what it holds, and this module never verifies with it.

Every pattern is linear on hostile text (the regex budget): see `sijl.ids`.
"""

from __future__ import annotations

import bisect
import html
import re
import time
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, replace

from markdown_it.common.normalize_url import normalizeLinkText

from sijl import ids, text
from sijl.digest import Digest
from sijl.report import _code, _md
from sijl.text import printable, printable_block
from sijl.tickets import STANDARDS

COMMIT, REQUEST, TICKET = "commit", "request", "ticket"
KNOWN, UNKNOWN = "known", "unknown"
MARK = " (sijl: NOT in your evidence)"  # after each unknown id in the answer
HEADING = "Evidence check (by Sijl, not AI)"
# The check fails closed: an answer longer than this is not checked but "not verified" (its
# cost stays bounded: every step is linear, and this keeps the slowest under a second), and so
# is a check that runs longer than CHECK_SECONDS or fails.
CHECK_MAX_CHARS = 100_000
CHECK_SECONDS = 10.0


class _TooSlow(Exception):
    """The check ran past its deadline."""


class _Clock:
    """The check's deadline, read every 256 steps of any of its loops — counted by the clock
    itself, never from a loop's position, which may not be a multiple of anything (a loop stuck
    before position 1,024 would never look at the time)."""

    def __init__(self, deadline: float | None) -> None:
        self.deadline, self.steps = deadline, 0

    def tick(self) -> None:
        self.steps += 1
        if self.deadline is not None and not self.steps & 255 and time.monotonic() > self.deadline:
            raise _TooSlow


class _Stuck(RuntimeError):
    """A scanning loop of the check did not move forward, or outgrew its input: a bug, which
    makes the check "not verified" instead of hanging."""


def _forward(before: int, after: int) -> int:
    if after <= before:
        raise _Stuck(f"a scan stayed at {before}")
    return after


# ---------------------------------------------------------------------------- normalisation

# Letters of other scripts that look like a Latin letter (after NFKC), and the letter they look
# like: Cyrillic, Greek, Armenian, Cherokee and Latin extensions. Any other letter outside ASCII
# still becomes `a` or `A` — this table only makes the hint name the id it imitates.
_LOOKALIKES = (  # (letters, the Latin letters or digits they look like), script by script
    (  # Cyrillic small: a e i j o p c y x s d q w h palochka ge
        "\u0430\u0435\u0456\u0458\u043e\u0440\u0441\u0443\u0445\u0455\u0501\u051b\u051d"
        "\u04bb\u04cf\u050d",
        "aeijopcyxsdqwhlg",
    ),
    (  # Cyrillic capital: A B E S I J K M H O P C T X Y (two) Q W palochka, and Ze for 3
        "\u0410\u0412\u0415\u0405\u0406\u0408\u041a\u041c\u041d\u041e\u0420\u0421\u0422"
        "\u0425\u0423\u04ae\u051a\u051c\u04c0\u0417",
        "ABESIJKMHOPCTXYYQWI3",
    ),
    ("\u03b1\u03bf\u03b9\u03c1\u03c5\u03bd\u03ba\u03c7\u03f2", "aoipuvkxc"),  # Greek small
    (  # Greek capital
        "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5"
        "\u03a7\u03f9\u037f",
        "ABEZHIKMNOPTYXCJ",
    ),
    ("\u0131\u0237\u0251\u0261\u0269", "ijagi"),  # Latin: dotless i and j, alpha, script g
    ("\u0585\u057d\u0570\u0578\u0581", "ouhng"),  # Armenian small
    (  # Cherokee
        "\u13aa\u13f4\u13df\u13ac\u13bb\u13ab\u13e6\u13b7\u13e2\u13da\u13a2\u13d4\u13c3",
        "ABCEHJKMPSTWZ",
    ),
)
CONFUSABLE = {
    letter: plain
    for letters, plains in _LOOKALIKES
    for letter, plain in zip(letters, plains, strict=True)
}
# Letters that look like a digit, read as that digit where a digit is expected: right after a
# digit, or after the `-`, `!` or `#` that starts an id's number: a Greek or Cyrillic capital O
# there is a 0.
_DIGIT_LOOKALIKE = {  # Cyrillic and Greek O and o, Cyrillic Ze and be, the I-like letters
    "\u041e": "0", "\u043e": "0", "\u039f": "0", "\u03bf": "0", "\u0417": "3", "\u0431": "6",
    "\u0406": "1", "\u0399": "1", "\u04c0": "1", "\u04cf": "1", "\u0131": "1",
}  # fmt: skip


# Punctuation that looks like the `-`, `!` or `#` of an id: hyphens and dashes, the minus sign,
# the small em dash (after NFKC), the Latin letter retroflex click, the music sharp sign.
_PUNCT_LOOKALIKE = {
    "\u2010": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u01c3": "!", "\u266f": "#",
}  # fmt: skip


def _hidden(char: str) -> bool:
    """Does a renderer show `char` as nothing? Format characters (zero-width space, soft hyphen,
    bidi controls), every default-ignorable character (`text.ignorable()`: variation selectors,
    the combining grapheme joiner, Hangul fillers…) and combining marks (Mn, Me)."""
    return unicodedata.category(char) in ("Cf", "Mn", "Me") or text.ignorable(char)


def _plain(char: str, previous: str) -> str:
    """What one visible non-ASCII character of an answer stands for in an id."""
    folded = unicodedata.normalize("NFKC", char)
    if len(folded) > 4:  # a ligature of words (U+FDFA is 18 letters): no part of an id
        return " "
    if folded.isascii():
        return folded
    if len(folded) == 1:
        char = folded
    if char in _PUNCT_LOOKALIKE:
        return _PUNCT_LOOKALIKE[char]
    digit = unicodedata.decimal(char, None)
    if digit is not None:
        return str(digit)
    if char in _DIGIT_LOOKALIKE and (previous.isdigit() or previous in "-!#"):
        return _DIGIT_LOOKALIKE[char]
    if char in CONFUSABLE:
        return CONFUSABLE[char]
    if char.isalpha():
        return "A" if char.isupper() else "a"
    return folded


# How `text.printable()` shows a character a terminal would act on, or that hides what text is
# (a control, a format character, a lone surrogate): `\x1b`, `\u200b`, `\U000e0041`.
_ESCAPE = re.compile(r"\\(?:x[0-9a-f]{2}|u[0-9a-f]{4}|U00(?:0[0-9a-f]|10)[0-9a-f]{4})")
# ASCII letters that look like a digit, read as that digit inside a number: after a digit or the
# `-`, `!` or `#` that starts one, before a digit (`PROJ-l3`, `app!l2`) — or at the end of
# one (`PROJ-1O`). `top-level` and `co-op` keep their letters.
_ASCII_DIGIT = {"l": "1", "I": "1", "O": "0", "o": "0"}
_ASCII_LOOKALIKE = re.compile(r"[0-9!#-][lIOo]")


def _escaped_by_printable(sequence: str) -> bool:
    """Is `\\x..`/`\\u....`/`\\U........` an escape `printable()` writes — of a character it
    escapes, or one byte of a name that is not UTF-8 (`\\x80` to `\\xff`) — and not the answer's
    own text (`\\Ucafebabe`)?"""
    code = int(sequence[2:], 16)
    if sequence[1] == "x" and code >= 0x80:
        return True
    return code <= 0x10FFFF and text.hides(chr(code))


def _digit_lookalike(shown: str, index: int, previous: str) -> str:
    """The digit an ASCII `l`, `I`, `O` or `o` at `index` stands for inside a number, or ""."""
    char = shown[index]
    after = shown[index + 1] if index + 1 < len(shown) else " "
    if char not in _ASCII_DIGIT or not (previous.isdigit() or previous in "-!#"):
        return ""
    if after.isdigit() or (previous.isdigit() and not after.isalnum()):
        return _ASCII_DIGIT[char]
    return ""


def _skeleton(shown: str, clock: _Clock | None = None) -> tuple[str, list[int], list[int]]:
    """`shown` — the answer as the terminal shows it — normalised for finding tokens (the module
    docstring, step 1): for each character of the result, the index in `shown` it came from;
    and the positions of the result right after a hidden character (`_hidden()`), which was
    dropped — itself or the visible escape `printable()` shows for it."""
    escapes = {
        match.start(): match.end()
        for match in _ESCAPE.finditer(shown)
        if _escaped_by_printable(match.group())
    }
    if shown.isascii() and not escapes and not _ASCII_LOOKALIKE.search(shown):
        return shown, list(range(len(shown))), []
    chars: list[str] = []
    where: list[int] = []
    hidden: list[int] = []
    clock = clock or _Clock(None)
    index = 0
    while index < len(shown):
        clock.tick()
        if index in escapes:
            if _hidden(chr(int(shown[index + 2 : escapes[index]], 16))):
                hidden.append(len(chars))
            index = _forward(index, escapes[index])
            continue
        char = shown[index]
        if char.isascii():
            plain = _digit_lookalike(shown, index, chars[-1] if chars else " ") or char
        elif _hidden(char):
            hidden.append(len(chars))
            plain = ""
        else:
            plain = _plain(char, chars[-1] if chars else " ")
        chars += plain
        where += [index] * len(plain)
        index += 1
    return "".join(chars), where, hidden


# ---------------------------------------------------------------------------------- the adapter

# The fail-closed detector, from the one grammar module (`sijl.ids`): the digest's own
# `ids.ID_PATTERN` widened to near misses. Never used to say what the digest holds.
CANDIDATE = ids.CANDIDATE


def _kind(token: str) -> str:
    """A token's kind, from its shape alone, whatever tokenizer produced it: a request has `!` or
    `#`, a key has `-` after its project, anything else starts as a SHA."""
    if "!" in token or "#" in token:
        return REQUEST
    return TICKET if "-" in token else COMMIT


def _exempt(token: str) -> bool:
    """The one exemption: a key whose project is a standard, in any case (UTF-8, sha-256,
    ISO-8601) — no digest can hold one, because `jira.projects` refuses such a project."""
    return _kind(token) == TICKET and token.partition("-")[0].upper() in STANDARDS


# ---------------------------------------------------------------------------------- the check


@dataclass(frozen=True)
class Cited:
    """One id-shaped token of an answer: as written, where, its kind, whether the digest holds
    it, and — when it does not — the one known id it most likely stands for."""

    id: str  # the answer's characters, as shown
    kind: str  # COMMIT, REQUEST or TICKET
    status: str  # KNOWN or UNKNOWN
    start: int
    end: int
    hint: str = ""
    looks_like: bool = False  # the hint, written differently: look-alike characters or case
    hidden: bool = False  # a character a renderer shows as nothing is in it or right beside it
    rendered: bool = False  # seen only as Markdown shows the answer; marked after the construct


@dataclass(frozen=True)
class Check:
    """Every id an answer cites, in the order written."""

    cited: tuple[Cited, ...]
    unverified: str = ""  # why the answer could not be checked at all: then nothing is verified

    def ids(self, status: str = "") -> list[Cited]:
        """Each distinct id once — of `status`, or of any — in the order first written."""
        seen: dict[str, Cited] = {}
        for item in self.cited:
            if (not status or item.status == status) and item.id not in seen:
                seen[item.id] = item
        return list(seen.values())

    @property
    def ok(self) -> bool:
        """True only when the answer was checked and every id-shaped token in it is one of the
        digest's ids, exactly."""
        return not self.unverified and all(item.status == KNOWN for item in self.cited)


def _split(request: str) -> tuple[str, str]:
    """`acme/app!12` → (`acme/app`, `!12`): the repository part and the number with its sign."""
    cut = max(request.rfind("!"), request.rfind("#"))
    return request[:cut], request[cut:]


def _same_request(token: str, known: str) -> bool:
    """Could `token` be `known` written another way: the same sign and number, and no
    repository, or one path inside the other (`app!12` and `acme/app!12`)?"""
    (name, number), (head, its_number) = _split(token), _split(known)
    if number != its_number:
        return False
    return not name or head.endswith("/" + name) or name.endswith("/" + head)


class _Known:
    """The digest's ids, indexed once per check so every lookup is constant: by case-folded
    form (look-alikes in another case), SHAs by their first 7 digits (a SHA a token shortens or
    lengthens shares them), requests by sign and number (comparing every token with every id
    took 40 s for a long answer)."""

    def __init__(self, evidence: Digest) -> None:
        self.ids = evidence.ids
        self.folded = {known.casefold(): known for known in evidence.ids}
        self.shas: dict[str, list[str]] = {}
        for sha in evidence.shas:
            self.shas.setdefault(sha[:7], []).append(sha)
        self.requests: dict[str, list[str]] = {}
        for request in evidence.requests:
            self.requests.setdefault(_split(request)[1], []).append(request)

    def hint(self, token: str, kind: str) -> str:
        """The one known id `token` (normalised) most likely stands for — a SHA it shortens or
        lengthens, a request with the same number and no repository or another path — or ""."""
        if kind == COMMIT:
            low = token.lower()
            near = [s for s in self.shas.get(low[:7], ()) if s.startswith(low) or low.startswith(s)]
        elif kind == REQUEST:
            near = [r for r in self.requests.get(_split(token)[1], ()) if _same_request(token, r)]
        else:
            near = []
        return near[0] if len(near) == 1 else ""

    def look_alike(self, token: str) -> str | None:
        """The known id `token` (normalised) is in another case — or with the `_` of Markdown
        emphasis glued to its front (`_acme!12`) — or None."""
        for form in (token, token.lstrip("_")):
            if form.casefold() in self.folded:
                return self.folded[form.casefold()]
        return None


def _tokens(shown: str, clock: _Clock) -> Iterator[tuple[int, int, str, bool]]:
    """Every id-shaped token of `shown`: its span in `shown`, the token as normalised, and
    whether a hidden character is inside it or touches it."""
    plain, where, hidden = _skeleton(shown, clock)

    def touched(start: int, end: int) -> bool:
        at = bisect.bisect_left(hidden, start)
        return at < len(hidden) and hidden[at] <= end

    for match in CANDIDATE.finditer(plain):
        clock.tick()
        span = where[match.start()], where[match.end() - 1] + 1
        yield *span, match.group(), touched(*match.span())


def _whole(shown: str, start: int, end: int) -> bool:
    """Is `shown[start:end]` an id by the digest's own grammar — a whole token of
    `ids.ID_PATTERN`, glued to nothing on either side (`x_PROJ-12`, `path/1a2b3c4d`, `a@b!3`
    are not)? The lookbehind sees the text before `start`."""
    match = ids.ID_PATTERN.match(shown, start)
    return match is not None and match.end() == end


def _cite(source: str, known: _Known, clock: _Clock) -> list[Cited]:
    """Every id-shaped token of `source`, known or unknown (step 3 of the module docstring)."""
    found = []
    for start, end, token, hidden in _tokens(source, clock):
        written, kind = source[start:end], _kind(token)
        if hidden:  # never verified, whatever it reads as
            key = known.look_alike(token) or ""
            hint = key or known.hint(token, kind)
            found.append(Cited(written, kind, UNKNOWN, start, end, hint, bool(key), hidden=True))
        elif written in known.ids and _whole(source, start, end):
            found.append(Cited(written, kind, KNOWN, start, end))
        elif written in known.ids:  # glued to a path, an address or a word: not that id
            found.append(Cited(written, kind, UNKNOWN, start, end, written, looks_like=True))
        elif (key := known.look_alike(token)) is not None:  # a known id, written differently
            found.append(Cited(written, kind, UNKNOWN, start, end, key, looks_like=True))
        elif written != token or not _exempt(token):  # a look-alike is never exempt
            found.append(Cited(written, kind, UNKNOWN, start, end, known.hint(token, kind)))
    return found


# What Markdown shows of an answer: markup it drops (emphasis, code ticks, strikethrough), `_`
# where it can be emphasis, link and image brackets and destinations, reference labels, HTML
# tags and comments; entities it decodes; backslash escapes it resolves.
_ENTITY = re.compile(r"&(?:#[0-9]{1,8};?|#[xX][0-9A-Fa-f]{1,8};?|[A-Za-z][A-Za-z0-9]{1,31};?)")
_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_CLOSING_MARKUP = frozenset("*_~`]")
# CommonMark's autolinks, `<scheme:address>` and `<user@host>`: shown as their address. (A
# scheme markdown-it refuses, `javascript:`, shows the brackets too: the view joins more.)
_ANGLE = re.compile(r"[<>]")
_URI_AUTOLINK = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,31}+:[^<>\x00-\x20]*+")
_EMAIL_AUTOLINK = re.compile(  # a match starts only where its run starts: linear
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]++@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*+"
)


def _autolink(shown: str, index: int, close: int) -> str | None:
    """The text markdown-it shows for the autolink `shown[index:close + 1]` — its address with
    percent-escapes decoded and a host's punycode (`xn--`) as Unicode, by markdown-it's own
    `normalizeLinkText()` (linear) — or None when that is no autolink."""
    address = shown[index + 1 : close]
    if _URI_AUTOLINK.fullmatch(address) is None and _EMAIL_AUTOLINK.fullmatch(address) is None:
        return None
    text_ = normalizeLinkText(address)
    return text_ if len(text_) <= len(address) + 2 else address


def _emphasis_underscores(shown: str, start: int, end: int) -> set[int]:
    """The `_` of `shown[start:end]` (one line) that Markdown can take as emphasis: the runs
    CommonMark's flanking rules let open or close — when the line has two such runs or more.
    `_` inside a word (`my_repo`) or alone (`_private!3`) stays text."""
    runs: list[tuple[int, int]] = []
    at = shown.find("_", start, end)
    while at >= 0:
        stop = at
        while stop < end and shown[stop] == "_":
            stop += 1
        before = shown[at - 1] if at > start else " "
        after = shown[stop] if stop < end else " "
        p_before, p_after = before in _PUNCTUATION, after in _PUNCTUATION
        left = not after.isspace() and (not p_after or before.isspace() or p_before)
        right = not before.isspace() and (not p_before or after.isspace() or p_after)
        if (left and (not right or p_before)) or (right and (not left or p_after)):
            runs.append((at, stop))
        at = shown.find("_", stop, end)
    if len(runs) < 2:
        return set()
    return {index for first, stop in runs for index in range(first, stop)}


def markdown_view(shown: str, clock: _Clock | None = None) -> tuple[str, list[int]]:
    """The answer as Markdown would show it, for finding ids — and for each of its characters,
    the index in `shown` it came from. It removes at least what a renderer can: `*`, `` ` ``, `~`
    on a line that holds two of them, `_` that can be emphasis on a line with two such runs,
    `[`…`]` with a destination or a label after, `![`, tag-like `<…>`, comments; an autolink
    (`<ab:cafe>`, `<x@cafe>`) shows its address as markdown-it does (`_autolink()`); so it shows
    every token rendering can join, and a test checks that against markdown-it. One pass,
    linear: a closer searched for and not found is never searched for again from later on, and
    every step moves forward (`_forward()`); an unclosed comment runs to the end of the text."""
    clock = clock or _Clock(None)
    chars: list[str] = []
    where: list[int] = []
    missing: dict[str, int] = {}  # closer → a position after which it does not occur

    def find(closer: str, start: int) -> int:
        if start >= missing.get(closer, len(shown) + 1):
            return -1
        at = shown.find(closer, start)
        if at < 0:
            missing[closer] = start
        return at

    index, size, brackets = 0, len(shown), 0  # brackets: link texts open, their `[` dropped
    pairs: str = ""  # the markup characters the current line holds two of: they can pair
    emphasis: set[int] = set()  # the current line's `_` that can be emphasis
    line_end = -1
    while index < size:
        clock.tick()
        if len(chars) > size:  # a decoded entity is never longer than the entity itself
            raise _Stuck("the Markdown view outgrew the answer")
        if index > line_end:  # a new line: what markup on it can pair
            line_end = shown.find("\n", index)
            line_end = size if line_end < 0 else line_end
            pairs = "".join(mark for mark in "*`~" if shown.count(mark, index, line_end) >= 2)
            emphasis = _emphasis_underscores(shown, index, line_end)
        char = shown[index]
        after = shown[index + 1] if index + 1 < size else ""
        if char == "\\" and after in _PUNCTUATION:  # a backslash escape: the character shown
            chars.append(after)
            where.append(index + 1)
            index += 2
            continue
        if (char in pairs or index in emphasis) or (
            char == "!" and after == "[" and find("]", index + 2) >= 0
        ):
            index += 1
            continue
        if char == "[" and find("]", index + 1) >= 0:  # a link's or an image's text
            brackets += 1
            index += 1
            continue
        if char == "]" and brackets:
            brackets -= 1
            closer = {"(": ")", "[": "]"}.get(after)
            end = find(closer, index + 2) if closer else -1
            index = _forward(index, end + 1 if end >= 0 else index + 1)  # not shown: the label
            continue
        angle = _ANGLE.search(shown, index + 1) if char == "<" else None
        if angle is not None and angle.group() == ">":  # no `<` inside an autolink: linear
            close = angle.start()
            address = _autolink(shown, index, close)
            if address is not None:  # an autolink: its address shown, the brackets not
                same = address == shown[index + 1 : close]
                chars += address
                where += range(index + 1, close) if same else [close] * len(address)
                index = _forward(index, close + 1)
                continue
        if char == "<":
            end = -1
            if shown.startswith("<!--", index):  # a comment, to its `-->` or to the end
                close = find("-->", index + 4)
                end = close + 2 if close >= 0 else size - 1
            elif after.isalpha() or after in "/!?":
                end = find(">", index + 1)
            if end >= 0:
                index = _forward(index, end + 1)
                continue
        if char == "&" and (entity := _ENTITY.match(shown, index)) is not None:
            decoded = html.unescape(entity.group())
            if decoded != entity.group():
                chars += decoded
                where += [entity.end() - 1] * len(decoded)
                index = _forward(index, entity.end())
                continue
        chars.append(char)
        where.append(index)
        index += 1
    return "".join(chars), where


def _check(shown: str, evidence: Digest, deadline: float | None) -> Check:
    """The check itself: the answer as written, then as Markdown shows it."""
    clock = _Clock(deadline)
    known = _Known(evidence)
    found = _cite(shown, known, clock)
    seen = {(item.start, item.end): (item.id, item.status) for item in found}
    marked = [(item.start, item.end) for item in found if item.status == UNKNOWN]  # in order
    view, where = markdown_view(shown, clock)
    for item in _cite(view, known, clock):
        start, end = where[item.start], where[item.end - 1] + 1
        if seen.get((start, end)) == (item.id, item.status):
            continue  # the same token, read the same way: decided above. A known `[PROJ-12]`
            # that Markdown shows glued — `_PROJ-12`, `PROJ-12\u200b` — is not the same.
        near = bisect.bisect_right(marked, (end, end)) - 1
        if near >= 0 and marked[near][1] > start:
            continue  # it overlaps a token already marked unknown: that mark covers it
        while end < len(shown) and shown[end] in _CLOSING_MARKUP:
            end += 1  # the mark goes after the construct
        hint, like = item.hint, item.looks_like
        if item.id in known.ids:  # rendering shows a known id the text does not hold as such
            hint, like = item.id, True
        found.append(replace(item, status=UNKNOWN, start=end, end=end, hint=hint,
                             looks_like=like, rendered=True))  # fmt: skip
    return Check(tuple(sorted(found, key=lambda item: (item.start, item.end))))


def check(shown: str, evidence: Digest) -> Check:
    """Every id-shaped token of `shown` — the answer exactly as the terminal shows it, and as
    Markdown would show it — known when the characters shown are exactly one of `evidence.ids`,
    unknown otherwise.

    It fails closed: an answer over `CHECK_MAX_CHARS`, a check running past `CHECK_SECONDS`,
    and any failure inside it give a Check that is `unverified`, never one that is `ok`."""
    if len(shown) > CHECK_MAX_CHARS:
        return Check((), f"the answer is {len(shown):,} characters long; Sijl checks "
                         f"answers of up to {CHECK_MAX_CHARS:,}")  # fmt: skip
    try:
        return _check(shown, evidence, time.monotonic() + CHECK_SECONDS)
    except _TooSlow:
        return Check((), "checking it took too long")
    except Exception as exc:  # a broken check must never read as "all found"
        return Check((), f"the check failed ({type(exc).__name__})")


def mark(answer: str, result: Check) -> str:
    """`answer` with `MARK` right after every unknown id; nothing else changed."""
    out, done = [], 0
    for item in result.cited:
        if item.status == UNKNOWN:
            out += [answer[done : item.end], MARK]
            done = item.end
    return "".join([*out, answer[done:]])


def _n(count: int, noun: str) -> str:
    return f"{count:,} {noun if count == 1 else noun + 's'}"


# ------------------------------------------------------------ positive verification, [[ID]]

SECTION = "Evidence Sijl checked"


@dataclass(frozen=True)
class Citation:
    """One `[[…]]` of an answer: what is inside, exactly as Claude wrote it, and whether it is
    one of the digest's ids, byte for byte. `inside` is "" for a `[[` never closed or with
    another `[[` before its `]]`: an unknown citation."""

    inside: str
    known: bool


@dataclass(frozen=True)
class Reading:
    """An answer as Sijl shows it: `text` — printable, every `[[ID]]`
    rewritten, every unknown id the detection finds marked; only `\\n` separates its lines —
    its citations, and the detection's Check of the free text around them."""

    text: str
    citations: tuple[Citation, ...]
    check: Check

    @property
    def verified(self) -> list[str]:
        """The digest's ids the answer cited as `[[ID]]`, each once, in the order cited."""
        return list(dict.fromkeys(c.inside for c in self.citations if c.known))

    @property
    def unknown_citations(self) -> list[str]:
        """Every `[[…]]` that is not one of the digest's ids, as shown: escaped."""
        return list(dict.fromkeys(_shown_citation(c) for c in self.citations if not c.known))

    @property
    def ok(self) -> bool:
        """No unknown citation, and nothing the detection found or could not check: exit 0
        means "no unknown citation detected" — never "every claim is true"."""
        return all(c.known for c in self.citations) and self.check.ok


def _shown_citation(citation: Citation) -> str:
    return f"[[{printable(citation.inside)}]]" if citation.inside or citation.known else "[["


def _pieces(answer: str) -> Iterator[tuple[str, Citation | None]]:
    """`answer` cut into free text and `[[…]]` citations, in order: (text, None) or
    (raw `[[…]]`, its Citation). Linear: a `]]` found is reused by every `[[` before it, and one
    not found is never looked for again."""
    done, close = 0, -1
    while True:
        start = answer.find("[[", done)
        if start < 0:
            yield answer[done:], None
            return
        if close != -2 and close < start + 2:
            close = answer.find("]]", start + 2)
            close = -2 if close < 0 else close  # -2: none after here, ever
        nested = answer.find("[[", start + 2, close) if close >= 0 else -1
        yield answer[done:start], None
        if close < 0 or nested >= 0:  # never closed, or another [[ first: an unknown citation
            yield "[[", Citation("", False)
            done = start + 2
            continue
        yield answer[start : close + 2], Citation(answer[start + 2 : close], False)
        done = close + 2


def read(answer: str, evidence: Digest) -> Reading:
    """`answer` — Claude's text, untrusted — as Sijl shows it: every `[[…]]` whose inside is
    exactly one of the digest's ids (and an id by its grammar) becomes `[ID]` — no trust mark:
    inside Claude's quoted answer one could be forged; the id is listed in Sijl's own
    section instead (`evidence_section()`); any other `[[…]]`, and a `[[` never closed, is shown
    escaped with `MARK`; the rest through `printable()`. Then `check()` reads that text, exactly
    as it will be shown (best effort): an id it finds unknown — one glued to a `[ID]` too — is
    marked. Only the text this returns is printed, and only inside the quoted block
    (`quoted()`)."""
    rendered: list[str] = []
    citations: list[Citation] = []
    unknown: list[tuple[int, int]] = []  # where each unknown citation is shown, with its MARK
    size = 0
    for piece, citation in _pieces(answer.strip()):
        if citation is None:
            out = printable_block(piece)
        else:
            known = citation.inside in evidence.ids and ids.ID.match(citation.inside) is not None
            citation = Citation(citation.inside, known)
            citations.append(citation)
            # A verified id keeps visible delimiters: `[[PROJ-12]]3` must never
            # show as `PROJ-123`, and whatever is glued to `[PROJ-12]` the check below sees.
            out = f"[{citation.inside}]" if known else f"{_shown_citation(citation)}{MARK}"
            if not known:
                unknown.append((size, size + len(out)))
        rendered.append(out)
        size += len(out)
    shown = "".join(rendered)
    found = _outside(check(shown, evidence), unknown)  # the text exactly as it will be shown
    return Reading(mark(shown, found), tuple(citations), found)


def _outside(found: Check, spans: list[tuple[int, int]]) -> Check:
    """`found` without the ids inside the `spans` — the unknown citations, marked already."""
    starts = [start for start, _ in spans]

    def inside(item: Cited) -> bool:
        at = bisect.bisect_right(starts, item.start) - 1
        return at >= 0 and item.end <= spans[at][1]

    return replace(found, cited=tuple(item for item in found.cited if not inside(item)))


QUOTE_HEADER = "Claude's answer — AI-written, not verified by Sijl:"


def quoted(shown: str, prefix: str) -> str:
    """Claude's answer as a block it cannot leave, on a terminal (output trust
    boundary): every line of `shown` — already printable, so only `\\n` separates lines (`\\r`,
    U+2028, U+0085, VT, FF are escapes by now) — prefixed, blank lines too (`\u2502 `). Only
    Sijl writes unprefixed lines."""
    bar = prefix.rstrip()
    return "\n".join(f"{prefix}{line}" if line else bar for line in shown.split("\n"))


_BACKTICKS = re.compile(r"`+")
_TILDES = re.compile(r"~+")


def fenced(shown: str) -> str:
    """Claude's answer as a block it cannot leave, off a terminal: a fenced code
    block, so that `> answer.md`, rendered, shows all of it as text — no tag, no link, no
    heading, nothing that hides what follows (`<template>`, `<style>`, a comment). The fence is
    longer than any run of its character in `shown`, so no line of it can close the block:
    backticks, or tildes when the tilde fence is the shorter."""
    longest = [max((len(run) for run in runs.findall(shown)), default=0)
               for runs in (_BACKTICKS, _TILDES)]  # fmt: skip
    mark, run = ("`", longest[0]) if longest[0] <= longest[1] else ("~", longest[1])
    fence = mark * max(3, run + 1)
    return f"{fence}\n{shown}\n{fence}"


def evidence_section(reading: Reading, evidence: Digest, *, markdown: bool = False) -> str:
    """Sijl's own section after the answer: for each id the answer cited as `[[ID]]` and the
    digest holds, the digest's own line for it (`Digest.line_for()`) — from the user's data,
    never from Claude's text, but any text can be a commit subject: always printable, and
    with `markdown` (off a terminal) escaped as the report escapes it (`report._md()`, the id as
    `report._code()`), so a `<template>` in a subject is text, never a tag."""
    lines = [f"\u2500\u2500\u2500\u2500\u2500 {SECTION}: from your digest, not written by AI "
             "\u2500\u2500\u2500\u2500\u2500"]  # fmt: skip
    for known in reading.verified:
        line = printable(evidence.line_for(known) or "(in your digest)")
        known_ = printable(known)
        lines.append(f"- {_code(known_)}: {_md(line)}" if markdown else f"- {known_}: {line}")
    if not reading.verified:
        lines.append("No id was cited as [[ID]] and found in your digest.")
    return "\n".join(lines)


def note(reading: Reading) -> str:
    """Sijl's summary under the answer. It claims only what Sijl verified — never that
    every id is, nor that the answer cites none: finding no fake is not proof there is none — and
    holds no text of Claude's: what is not in the evidence is counted, not quoted."""
    verified = reading.verified
    lines = [
        f"{HEADING}: Sijl verified {_n(len(verified), 'id')} (listed under {SECTION}). "
        "Treat any other id-like text in the answer as unverified."
    ]
    # Counts only: this line is Sijl's, outside the quoted answer, so it never repeats a
    # word of Claude's — not even an unknown id, which full-width brackets could turn into a
    # forged sentence. The ids are marked where they stand, inside the quote.
    counts = [
        _n(count, noun)
        for count, noun in (
            (len(reading.unknown_citations), "[[…]] citation"),
            (len(reading.check.ids(UNKNOWN)), "other id-like text"),
        )
        if count
    ]
    if counts:
        lines.append(f"- NOT in your evidence, marked in the answer above: {' and '.join(counts)}.")
    if reading.check.unverified:
        lines.append(
            f"- The rest of the answer could NOT be checked: {reading.check.unverified}. Treat "
            "every other id in it as unverified."
        )
    return "\n".join(lines)
