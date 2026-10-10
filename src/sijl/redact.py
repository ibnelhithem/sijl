"""Redaction: before text leaves the machine, only the user's own identities are kept.

Colleagues' names and emails are redacted by default before anything is sent to Claude (Tier 2),
and the Tier 1 digest shares the rule. The mechanism is an allowlist:

- **emails** are found by their shape, so no address can slip through for being unknown: every
  one that is not one of the user's own (`identity.emails`, compared without case) becomes
  `[email N]`. The shape is broad on purpose: an RFC 5322 local
  part (letters, digits and ``!#$%&'*+/=?^_`{|}~.-``), an `@` or its percent-encoding `%40`, and
  a domain with or without dots — git's default identity is `user@hostname`, or
  `user@host.(none)` — that has at least one letter, so `lodash@4.17.21` stays a version;
- **names** cannot be found by shape, so the caller passes the names Sijl knows from the
  history — authors, co-authors — and every one that is not the user's becomes `[person N]`.
  Names match as whole words, longest first, however they are folded (below); the user's own
  names are matched with them: at any position the longer name wins, so a colleague
  called "Alex" can never cut into "Alex Example", and a look-alike such as "Alex Examplov"
  never touches it;
- **a name beside a colleague's address** — `Reviewed-by: Jane Reviewer <jane@…>`, any trailer,
  any `Name <address>` — is replaced with the address, known or not.

**Addresses first, names between them.** One pass finds every link and email; names are matched
only in the text between them, so a name can never cut into an address ("Kim" + "lee.kim@…").
Inside a link (`scheme://…`) the address pattern is narrower — no `/ = ? &` in the local part —
so `…?author_email=sam%40acme.test` loses the address and keeps the link.

**Matching reads a folded copy; replacing cuts the text as written**. Every pattern and
every name is matched in `_read(text)`, the text folded the way people's names and addresses
vary: Unicode's compatibility decomposition (NFKD) with accents and other combining marks dropped
(José reads `jose`; the FULLWIDTH and SMALL COMMERCIAL AT, U+FF20 and U+FE6B — the only
characters it turns into `@` — read `@`; full-width letters read as ASCII), invisible format
characters dropped, every apostrophe and quote look-alike read as `'` (U+2019, U+02BC, …),
every hyphen and dash look-alike as `-` (U+2010, U+2011, …), every run of blanks as one blank
(a newline when it holds one), and no case. Folding changes the text's length — the ligature
U+FB01 reads `fi`, a mark disappears, a run of blanks shrinks — so `_read()` keeps, for every
folded character, the position of the character it came from: a match is mapped back and cut
from the text as written, and everything else is returned unchanged, character for character.
Labels are keyed on the folded form: every spelling of one address or name is one label.

Labels are numbered in order of first appearance and kept for the redactor's life — one
command — so the digest and a question about the same colleague use the same label, and the
same input always gives the same output.

Limits (stated in the docs): a name typed freely in a subject is only caught when it matches a
known name; GitHub `@mentions` are not addresses; an `@` spelled out (`sam at acme dot test`), or
a full-width one percent-encoded as UTF-8 (`%EF%BC%A0`), is not one either; over-matching
(`a@b` in code) only redacts more.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from functools import lru_cache
from urllib.parse import unquote

_ATEXT = r"\w!#$%&'*+/=?^`{|}~.-"  # RFC 5322 atext, plus the dots between atoms
_LINK_ATEXT = r"\w!#$%'*+^`{|}~.-"  # inside a link, / = ? & separate parameters
# A domain: in brackets (`[10.0.0.1]`, `[IPv6:…]`), an IPv4 address (four numbers, not a fifth),
# or a name with or without dots — at least one letter in it — and git's "(none)" for none.
_DOMAIN = (
    r"(?:\[[^\]\s]{1,64}\]|[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?!\.?[\w-])"
    r"|(?=[\w.-]*[^\W\d_])[\w-]+(?:\.[\w-]+)*(?:\.\(none\))?)"
)
_AT = r"(?:@|%40)"
_EMAIL = rf"(?<![{_ATEXT}])[{_ATEXT}]+{_AT}{_DOMAIN}"
_LINK_EMAIL = rf"(?<![{_LINK_ATEXT}])[{_LINK_ATEXT}]+{_AT}{_DOMAIN}"
# A link is matched from the start of its run of scheme characters (any leading digits, `+`, `.`
# and `-` ride along): free to start at every letter of a long run, `finditer()` walked the rest
# of the run from each one — quadratic on a subject (the regex guard).
_LINK = r"(?<![A-Za-z0-9+.-])[0-9+.-]*[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+"
# A git identity, `Name <address>`: one to four words, each starting with a letter or digit, that
# start a line or follow a trailer's colon, a comma, a semicolon, a bracket, a quote or a list
# bullet, then the address in angle brackets. A word is at most 64 characters: `-` and `'` both
# start a name and sit inside words, so in a long run like `-a-a-a…` every one was a start that
# walked to the end of the run — quadratic (the regex guard). No name has a longer word; its
# address is still redacted on its own.
# Possessive (`*+`, `++`, `{0,63}+`): what follows a word, its blanks or the run of blanks before
# one can never start inside them, so giving characters back can never make a match — and not
# trying keeps each start at 64 steps.
_WORD = r"[^\W_][^\s<>:,;\"()\[\]@]{0,63}+"  # starts with a letter or digit
_PAIR = (
    r"(?:^|(?<=[:,;(\[\"'*>•-]))(?P<lead>[ \t]*+)"
    rf"(?P<who>{_WORD}(?:[ \t]++{_WORD}){{0,3}})[\"']?(?P<gap>[ \t]*+)<(?P<addr>{_EMAIL})>"
)  # a quoted name too, `"Rita Reviewer" <…>`: its closing quote (a typographic one reads ')
_ADDRESSES = re.compile(
    rf"(?P<pair>{_PAIR})|(?P<link>{_LINK})|(?P<email>{_EMAIL})", re.IGNORECASE | re.MULTILINE
)
_IN_LINK = re.compile(rf"(?P<email>{_LINK_EMAIL})", re.IGNORECASE)


# Characters people write for an apostrophe or a quote, and for a hyphen or a dash: each one
# reads as the ASCII one (U+2019, U+02BC, U+2010 among them).
_APOSTROPHES = frozenset("\u2018\u2019\u201a\u201b\u2032\u2035\u02b9\u02bb\u02bc\u02bd\uff07"
                         "\u201c\u201d\u201e\u201f\u2033\u2036\uff02")  # fmt: skip
_DASHES = frozenset("\u2010\u2011\u2012\u2013\u2014\u2015\u2043\u2212\ufe58\ufe63\uff0d")
_NEWLINES = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
_DROPPED = frozenset({"Mn", "Me", "Cf"})  # combining marks, invisible format characters


@lru_cache(maxsize=8192)
def _fold_char(char: str) -> str:
    """How one character that is not a blank reads: its compatibility decomposition (NFKD)
    without combining marks or format characters, apostrophes as `'`, dashes as `-`, no case.
    Zero, one or more characters (e with an acute is `e`, U+FB01 is `fi`, U+0301 is nothing)."""
    out = []
    for part in unicodedata.normalize("NFKD", char):
        if part in _APOSTROPHES:
            out.append("'")
        elif part in _DASHES:
            out.append("-")
        elif unicodedata.category(part) not in _DROPPED:
            out.append(part.casefold())
    return "".join(out)


_BLANKS = re.compile(r"\s+|\S+")  # alternately a run of blanks and a run of anything else

# Blanks that folding would change in ASCII text: a run of two, or one that is not a space or a
# newline (a tab, a carriage return). Without any, ASCII text folds to itself in lower case.
_ODD_BLANKS = re.compile(r"[ \n]\s|[^\S \n]")


def _read(text: str) -> tuple[str, list[int] | None]:
    """`text` folded (the module's docstring), and where each folded character came from:
    `origin[i]` is the position in `text` of the character folded character i came from, plus
    `len(text)` at the end — or None when folding kept every position (ASCII text with single
    blanks: only its case changes)."""
    if text.isascii() and not _ODD_BLANKS.search(text):
        return text.lower(), None
    folded: list[str] = []
    origin: list[int] = []
    for run in _BLANKS.finditer(text):
        start, piece = run.start(), run.group()
        if piece[0].isspace():  # a run of blanks reads as one: a newline when it holds one
            folded.append("\n" if any(c in _NEWLINES for c in piece) else " ")
            origin.append(start)
        elif piece.isascii():
            folded.append(piece.lower())
            origin.extend(range(start, start + len(piece)))
        else:
            for offset, char in enumerate(piece):
                part = char.lower() if char.isascii() else _fold_char(char)
                folded.append(part)
                origin.extend([start + offset] * len(part))
    origin.append(len(text))
    return "".join(folded), origin


# An address wrapped as a name's is: `<…>` or `(…)`, the folded characters around it.
_WRAPS = frozenset({"<>", "()"})


def _capitalised_before(
    folded: str, text: str, origin: list[int] | None, wrap: int, floor: int
) -> tuple[int, int] | None:
    """The words just before `wrap` (the `<` or `(` around a colleague's address), up to four,
    each starting with a capital letter as written — `reported by Rita Reviewer <rita@…>`:
    the name the address belongs to, though no colon or comma sets it apart. Folded positions;
    never before `floor` (the previous address); None without one. Each word is at most 64
    characters, so a long run is read once."""
    at = wrap
    while at > floor and folded[at - 1] in " \t":
        at -= 1
    end, start = at, None
    for _ in range(4):
        word = at
        while (
            word > floor
            and at - word < 64
            and (folded[word - 1].isalnum() or folded[word - 1] in "'.-")
        ):
            word -= 1
        letter = word if origin is None else origin[word]
        if (
            word == at
            or (word > floor and folded[word - 1].isalnum())
            or not text[letter].isupper()
        ):
            break  # no word, one longer than 64 characters, or one not capitalised
        start, at = word, word
        while at > floor and folded[at - 1] in " \t":
            at -= 1
        if at == word:
            break  # the word is glued to what is before it
    return (start, end) if start is not None else None


def _fold_text(text: str) -> str:
    """`text` as matching reads it (`_read()`), without the positions."""
    return _read(text)[0]


def _key(text: str) -> str:
    """One spelling per name or address: folded, single blanks, no blanks at the ends."""
    return " ".join(_fold_text(text).split())


# A word (letters, digits, `_`) or one other character that is not a blank: names are matched
# as sequences of these, so a name is always whole words ("Sam" is never in "Samsung").
_TOKEN = re.compile(r"\w+|[^\w\s]")


def _words(text: str) -> tuple[str, ...]:
    """The tokens of folded text, blanks left out: `sean o'brien` is (sean, o, ', brien)."""
    return tuple(_TOKEN.findall(text))


class Redactor:
    """`redactor(text)` → the text with only the user's own identities left; `emails` and
    `names` count the distinct addresses and names replaced so far."""

    def __init__(
        self,
        *,
        keep_emails: Iterable[str],
        keep_names: Iterable[str] = (),
        names: Iterable[str] = (),
    ) -> None:
        keep_names = list(keep_names)
        self._keep_emails = {_key(email) for email in keep_emails}
        # The user's own user names, kept in a link's `user@host` (never with a password): the
        # local parts of their addresses, and the login of a GitHub noreply address.
        self._keep_users = {email.partition("@")[0] for email in self._keep_emails}
        self._keep_users |= {
            local.partition("+")[2]
            for local, _, domain in (email.partition("@") for email in self._keep_emails)
            if domain == "users.noreply.github.com" and "+" in local
        }
        self._keep_names = {_key(name) for name in keep_names if len(_key(name)) > 1}
        # Every known name by its first word: the scan looks a name up only where a text's word
        # starts one, so its time grows with the text, hardly with the number of names (a single
        # alternation of 10,000 names, tried at every position, took 15 s on 150 KB). At one
        # word, the longest name that matches wins — the user's own among them — so a colleague
        # called "Alex" never cuts into "Alex Example"; ties sorted for one order every run.
        index: dict[str, dict[tuple[str, ...], str]] = defaultdict(dict)
        for name in [*keep_names, *names]:
            key = _key(name)
            words = _words(key)
            if len(key) > 1 and words:
                index[words[0]][words] = key
        self._index = {
            first: sorted(found.items(), key=lambda item: (-len(item[0]), item[1]))
            for first, found in index.items()
        }
        # how many other people's names it knows: what the digest's note reports
        self.known = len({key for found in index.values() for key in found.values()}
                         - self._keep_names)  # fmt: skip
        self._labels: dict[tuple[str, str], str] = {}  # (kind, key) → label

    @property
    def emails(self) -> int:
        return sum(1 for kind, _ in self._labels if kind == "email")

    @property
    def names(self) -> int:
        return sum(1 for kind, _ in self._labels if kind == "person")

    def __call__(self, text: str) -> str:
        """`text` with every address and name that is not the user's replaced by its label."""
        folded, origin = _read(text)
        spans: list[tuple[int, int, str, str]] = []  # (start, end, kind, key), folded positions
        shields: list[tuple[int, int]] = []  # folded stretches no name may touch: addresses
        if "@" in folded or "%40" in folded or "://" in folded:  # nothing to find otherwise
            for match in _ADDRESSES.finditer(folded):
                self._address(match, spans, shields, text, origin)
        self._names_between(folded, shields, spans)
        out, done = [], 0
        for start, end, kind, key in sorted(spans):  # labels numbered in reading order
            first = start if origin is None else origin[start]
            last = end if origin is None else origin[end]
            out += [text[done:first], self._label(kind, key)]
            done = last
        out.append(text[done:])
        return "".join(out)

    def _address(
        self,
        match: re.Match[str],
        spans: list[tuple[int, int, str, str]],
        shields: list[tuple[int, int]],
        text: str,
        origin: list[int] | None,
    ) -> None:
        """A link, an address or a `Name <address>` pair, found in the folded text: what of it is
        replaced, and where no name may be looked for."""
        floor = shields[-1][1] if shields else 0  # the end of the address before this one
        if match.lastgroup == "pair":  # the outer group closes last
            self._pair(match, spans, shields, text, origin)
        elif match.lastgroup == "link":
            shields.append(match.span())
            self._link(match, spans)
        else:
            start, end = match.span()
            shields.append((start, end))
            folded = match.string
            wrapped = start and end < len(folded) and folded[start - 1] + folded[end] in _WRAPS
            if self._email(match.group(), (start, end), spans) and wrapped:
                name = _capitalised_before(folded, text, origin, start - 1, floor)
                if name is not None:
                    spans.append((*name, "person", _key(folded[name[0] : name[1]])))
                    shields.append(name)

    def _link(self, match: re.Match[str], spans: list[tuple[int, int, str, str]]) -> None:
        """A link keeps its host and path; the user before its `@` goes — a colleague's always,
        the user's own only with a password, which is never shown — and
        an address in its path or query, `?author_email=sam%40acme.test`, is an address."""
        start, end = match.span()
        link = match.group()
        after = link.index("://") + 3
        authority = link[after:]
        for stop in "/?#\\":
            authority = authority.partition(stop)[0]
        cut = max(authority.rfind("@"), authority.rfind("%40"))
        rest = start + after
        if cut >= 0:
            user, password, _ = authority[:cut].partition(":")
            user = unquote(user)
            if password or user not in self._keep_users:
                spans.append((start + after, start + after + cut, "person", _key(user)))
            rest += cut + (1 if authority[cut] == "@" else 3)
        for inner in _IN_LINK.finditer(match.string, rest, end):
            self._email(inner.group(), inner.span(), spans)

    def _email(
        self, address: str, span: tuple[int, int], spans: list[tuple[int, int, str, str]]
    ) -> bool:
        """Replace the (folded) address unless it is the user's; True when it was replaced.
        sam%40acme.test and sam(U+FF20)acme.test are sam@acme.test."""
        key = unquote(address)
        if key in self._keep_emails:
            return False
        spans.append((*span, "email", key))
        return True

    def _pair(
        self,
        match: re.Match[str],
        spans: list[tuple[int, int, str, str]],
        shields: list[tuple[int, int]],
        text: str,
        origin: list[int] | None,
    ) -> None:
        """`Name <address>`: when the address is a colleague's, the name beside it is theirs
        too, known or not — unless it is one of the user's names,
        which is kept. With the user's own address, the name is checked like any other text.
        At the start of a line the words may begin a sentence (`reported by Rita Reviewer
        <…>`): when one of them is capitalised as written, the name starts at the first that
        is, and the words before it stay."""
        folded = match.string
        start, end = match.span("who")
        who = match.group("who")
        end -= len(who) - len(who.rstrip("'"))  # a closing quote frames the name: kept
        shields.append(match.span("addr"))
        if not self._email(match.group("addr"), match.span("addr"), spans):
            return  # the user's own address: names are looked for in `who` as anywhere
        words = [start + found.start() for found in re.finditer(r"\S+", folded[start:end])]
        capitals = [at for at in words if text[at if origin is None else origin[at]].isupper()]
        if capitals:
            start = capitals[0]
        shields.append((start, end))
        name = _key(folded[start:end])
        if name not in self._keep_names:
            spans.append((start, end, "person", name))

    def _names_between(
        self,
        folded: str,
        shields: list[tuple[int, int]],
        spans: list[tuple[int, int, str, str]],
    ) -> None:
        """Every known name in `folded` outside the shields — a name never cuts into an
        address — longest first at each word; the user's own are left as they are. One pass
        over the text's words, a dictionary look-up at each."""
        if not self._index:
            return
        walls = sorted(shields)
        words: list[str] = []
        starts: list[int] = []
        ends: list[int] = []
        free: list[bool] = []  # the word is outside every shield
        wall = 0
        for match in _TOKEN.finditer(folded):
            start, end = match.span()
            while wall < len(walls) and walls[wall][1] <= start:
                wall += 1
            words.append(match.group())
            starts.append(start)
            ends.append(end)
            free.append(not (wall < len(walls) and walls[wall][0] < end))
        index, at = self._index, 0
        while at < len(words):
            size = 1
            for name, key in index.get(words[at], ()) if free[at] else ():
                if tuple(words[at : at + len(name)]) == name and all(free[at : at + len(name)]):
                    size = len(name)
                    if key not in self._keep_names:
                        spans.append((starts[at], ends[at + size - 1], "person", key))
                    break
            at += size

    def _label(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self._labels:
            number = sum(1 for k, _ in self._labels if k == kind) + 1
            self._labels[key] = f"[{kind} {number}]"
        return self._labels[key]
