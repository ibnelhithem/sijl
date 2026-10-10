"""Jira ticket keys in a commit message: found strictly, guarded against look-alikes.

A Jira key is a project key — a capital letter, then capitals, digits or `_`, two characters at
least — a hyphen and an issue number without a leading zero: `PROJ-1234`. Plenty of other
things are written the same way: standards and encodings (`UTF-8`, `ISO-8601`, `RFC-1234`),
hashes (`SHA-256`), versions (`PY-3.12`), pieces of longer ids (`TC-UI-07`, `CVE-2024-1234`).
`scan()` reads every id of that shape and sorts it into **keys** and **refused** ids, each refusal
with its reason (the false-positive guard). `find_keys()` adds *where* in the message each key
was written — the positions people use for tickets — which is the evidence the project
suggestions in aggregate.py are ranked by.

Which keys are tickets is decided by the allowlist (`jira.projects`) in aggregate.py, not here:
this module only finds them. The one place the allowlist is needed is the lenient match in branch
names (`Feature/proj 1234`), which is safe only for projects the user named.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

# Prefixes written like a project key that are standards, encodings, hashes and other well-known
# names with a number — never a Jira project (`jira.projects` refuses them too).
STANDARDS = frozenset({
    "UTF", "UCS", "ASCII",  # encodings: UTF-8, UCS-2
    "SHA", "CRC", "AES", "RSA", "HMAC", "ECDSA",  # hashes, checksums, ciphers: SHA-256, CRC-32
    "ISO", "IEC", "IEEE", "ANSI", "ECMA", "RFC", "FIPS", "SQL",  # standards: ISO-8601, RFC-1234
    "PEP", "JSR", "JEP", "EIP", "PSR", "WCAG",  # numbered proposals: PEP-8, JSR-310
    "CVE", "CWE",  # vulnerability catalogues: CVE-2024-1234
    "HTTP", "TLS", "SSL", "UTC", "GMT",  # protocols and time zones: HTTP-2, UTC-5
    "X86", "AVX", "MPEG", "GPT", "COVID",  # X86-64, AVX-512, MPEG-4, GPT-4, COVID-19
})  # fmt: skip

# Where tickets are written. A key seen only in running text has no position — a weaker hint.
POSITIONS = ("lead", "scope", "trailing", "branch", "closing", "trailer")

PROJECT = re.compile(r"[A-Z][A-Z0-9_]+")  # the shape of a Jira project key
_KEY = r"[A-Z][A-Z0-9_]+-[0-9]+"
# `\w` is Unicode in a str pattern: an id glued to a letter of any alphabet (GRÖSSE-2) is none
_CANDIDATE = re.compile(r"(?<!\w)(?P<project>[A-Z][A-Z0-9_]+)-(?P<number>[0-9]+)")
# A word — a letter, then letters or digits, any alphabet — and a hyphen right before an id
_WORD_BEFORE = re.compile(r"(?<![^\W_])[^\W\d_][^\W_]*-\Z")  # "TC-" of TC-UI-07
_LOOK_BACK = 64  # how far before an id the longer-id check reads: linear time on long messages
_WRITTEN_MAX = 40  # a refused id is quoted up to this long; a longer one shows the match alone
# What a refused id is quoted with, never a word hyphenated onto it (`non-UTF-8` is UTF-8): in
# front, upper-case words each with a hyphen (the `TC-` of TC-UI-07); after, letters or digits
# glued to it and `.` or `-` segments that start with a digit (PY-3.12, CVE-2024-1234).
_UPPER_BEFORE = re.compile(r"(?<!\w)[^\W\d_]\w*-\Z")
_ID_TAIL = re.compile(r"\w*(?:[.-][0-9]\w*)*")
_TRAILING = re.compile(r"[(\[](?P<inside>[^()\[\]]+)[)\]]\s*$")
_CLOSING = re.compile(
    r"^[ \t>*-]*"
    r"(?i:close[sd]?|fix(?:e[sd])|resolve[sd]?|refs?|references?|relate[sd]? to|part of|"
    r"implements?)\b"
    # one run of spaces, then an optional colon and its own: two `[ \t]*` around an optional `:`
    # could split a long run of spaces every way (quadratic; the regex budget)
    rf"[ \t]*+(?::[ \t]*+)?(?P<keys>{_KEY}(?:(?:[ \t]*[,&][ \t]*|[ \t]+and[ \t]+|[ \t]+){_KEY})*)",
    re.MULTILINE,
)
_MERGE = re.compile(
    r"^Merge (?:remote-tracking branch(?:es)?|branch(?:es)?|pull request #[0-9]+ from)"
    r" (?P<rest>.+)$"
)
_QUOTED = re.compile(r"'([^']+)'")
_INTO = re.compile(r"\binto (?!')(\S+)")


@dataclass(frozen=True)
class Mentions:
    """The Jira keys of one commit message, each once, and the ids the guard refused."""

    keys: Mapping[str, frozenset[str]]  # "PROJ-12" → the POSITIONS it was written in (may be none)
    rejected: frozenset[str] = frozenset()  # refused ids, as written: "UTF-8", "TC-UI-07"


def _after_longer_id(text: str, start: int, *, any_case: bool = False) -> bool:
    """An upper-case word and a hyphen right before `start`: the `TC-` of `TC-UI-07` — or, with
    `any_case` (branch names), a word in any case: the `tc-` of `tc-proj-5`.

    Only the `_LOOK_BACK` characters before are read, so a message full of ids is scanned in
    linear time; a word longer than that is not recognised (lookbehind still sees past `pos`).
    """
    word = _WORD_BEFORE.search(text, max(0, start - _LOOK_BACK), start)
    return bool(word) and (any_case or word.group()[:-1].isupper())


def _written(text: str, match: re.Match[str]) -> str:
    """The id a match is part of, as written: `TC-UI-07` around `UI-07`, `PY-3.12` around `PY-3`
    — but `UTF-8` in `non-UTF-8` or `UTF-8-encoded`: a word hyphenated onto an id is not the id,
    so every mention of one standard is counted under one name. Longer than `_WRITTEN_MAX`: the
    match alone."""
    start, end = match.start(), match.end()
    low = max(0, start - _WRITTEN_MAX)
    while start > low:  # upper-case words in front, each with its hyphen: part of the id
        word = _UPPER_BEFORE.search(text, low, start)
        if word is None or not word.group()[:-1].isupper():
            break
        start = word.start()
    tail = _ID_TAIL.match(text, end, min(len(text), end + _WRITTEN_MAX))  # always matches
    written = text[start : tail.end() if tail else end]
    return written if len(written) <= _WRITTEN_MAX else match.group()


def _refusal(text: str, match: re.Match[str]) -> str:
    """Why the guard refuses this key-shaped id, or "" when it is a key."""
    after = text[match.end() : match.end() + 2]
    if match["project"] in STANDARDS:
        return "standard"
    if _after_longer_id(text, match.start()):
        return "longer id"  # TC-UI-07: UI-07 is the tail of another id
    if after[:1].isalnum() or (after[:1] == "-" and after[1:].isdigit()):
        return "longer id"  # ABC-12abc, CVE-style ABC-2024-01; `_` separates (PROJ-12_fix)
    if after[:1] == "." and after[1:].isdigit():
        return "version"  # PY-3.12
    if match["number"].startswith("0"):
        return "leading zero"  # QC-007; Jira numbers start at 1 and have no padding
    return ""


def scan(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Every key in `text`, and every refused look-alike with its reason — each once, in order."""
    keys: dict[str, None] = {}  # dicts as ordered sets: a membership check costs nothing
    refused: dict[tuple[str, str], None] = {}
    for match in _CANDIDATE.finditer(text):
        reason = _refusal(text, match)
        if reason:
            refused[(_written(text, match), reason)] = None
        else:
            keys[f"{match['project']}-{match['number']}"] = None
    return list(keys), list(refused)


def branch_names(subject: str) -> tuple[str, ...]:
    """The branch names in git's merge subjects: `Merge branch 'a' into b`; in GitHub's
    `Merge pull request #12 from owner/c`, the branch `c` — never the owner, a user name."""
    match = _MERGE.match(subject.strip())
    if not match:
        return ()
    rest = match["rest"]
    names = _QUOTED.findall(rest)
    into = _INTO.search(rest)
    if into:
        names.append(into[1])
    if not names:  # `Merge pull request #12 from owner/branch`
        ref = rest.split()[0]
        names.append(ref.split("/", 1)[1] if "/" in ref else ref)
    return tuple(names)


def _lenient(name: str, projects: Iterable[str]) -> list[str]:
    """Keys of the allowlisted projects in a branch name, in any case, with `-`, `_` or a space
    before the number: `Feature/proj 1234` → PROJ-1234.

    Project and number are whole words of the name: the project starts the name or follows `/`,
    `_`, `-` or a space, and the number ends it or is followed by something other than a letter
    or a digit — so `PROJ2-5` (project PROJ2) and `proj-12abc` are not PROJ tickets. A word and a
    hyphen before the project make it the tail of a longer id, in any case (`tc-proj-5`).
    """
    keys = []
    for project in projects:
        pattern = (
            rf"(?:^|(?<=[/_\-\s])){re.escape(project)}[-_ ](?P<number>[1-9][0-9]*)"
            r"(?![^\W_])(?!\.[0-9])(?!-[0-9])"
        )
        for match in re.finditer(pattern, name, re.IGNORECASE):
            if not _after_longer_id(name, match.start(), any_case=True):
                keys.append(f"{project}-{match['number']}")
    return keys


def find_keys(
    subject: str,
    body: str,
    trailers: Iterable[tuple[str, str]] = (),
    *,
    scope: str = "",
    lead: str = "",
    projects: Iterable[str] = (),
) -> Mentions:
    """The keys of one commit and where each was written; `body` is the full message.

    `scope` and `lead` come from `classify.parse_conventional()` — the brackets of `feat(…):` and
    a ticket key written before the subject — and `trailers` from git (`RawCommit.trailers`).
    """
    found, refused = scan(f"{subject}\n{body}")
    positions: dict[str, set[str]] = {key: set() for key in found}

    def mark(position: str, part: str) -> None:
        for key in scan(part)[0]:
            if key in positions:  # what the whole message refused stays refused
                positions[key].add(position)

    mark("lead", lead)
    mark("scope", scope)
    trailing = _TRAILING.search(subject)
    if trailing:
        mark("trailing", trailing["inside"])
    for name in branch_names(subject):
        mark("branch", name)
        for key in _lenient(name, projects):
            positions.setdefault(key, set()).add("branch")
    for closing in _CLOSING.finditer(body):
        mark("closing", closing["keys"])
    for name, value in trailers:
        if not name.endswith("-by"):  # co-authored-by, signed-off-by: people, not tickets
            mark("trailer", value)
    return Mentions(
        keys={key: frozenset(where) for key, where in positions.items()},
        rejected=frozenset(written for written, _ in refused),
    )


def key_order(key: str) -> tuple[str, int, str]:
    """PROJ-9 before PROJ-10: by project, then by number; anything else after, as written."""
    project, _, number = key.rpartition("-")
    return project, int(number) if number.isdigit() else 0, key


def ticket_finder(projects: Iterable[str]) -> Callable[[str], frozenset[str]]:
    """The `tickets_of` that `deliveries.find_deliveries()` calls on each delivery's source
    branch, title and merge message: the keys of `projects` in one of those texts.

    A text's first line is read as a subject, so the merge message's `Merge branch '…'` or
    `Merge pull request #N from owner/…` gives its branch names — matched leniently for your
    projects, the owner never read — and the rest as the message. With no projects, no tickets.
    """
    allowed = tuple(sorted(set(projects)))

    def tickets_of(text: str) -> frozenset[str]:
        if not allowed:
            return frozenset()
        found = find_keys(text.split("\n", 1)[0], text, projects=allowed)
        return frozenset(key for key in found.keys if key.rpartition("-")[0] in allowed)

    return tickets_of
