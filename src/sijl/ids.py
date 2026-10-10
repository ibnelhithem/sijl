"""The digest's ids, as one grammar: what the digest writes and what a citation check reads
come from this module and nowhere else.

- a **commit**: its short SHA, 8 to 40 lower-case hex digits (`COMMIT`) — 8, the report's length,
  or more where two listed commits would share a prefix;
- a **request**: its repository's id in the digest — letters, digits, `.`, `_`, `-` (`NAME`) —
  then `!N` for a GitLab merge request or `#N` for a GitHub pull request (`REQUEST`). Never a
  project path or an owner: a remote's namespace can be a colleague's own account;
- a **ticket**: a Jira key, a capital letter, then capitals, digits or `_`, a `-` and a number
  (`TICKET`).

`ID` says whether a whole string is one of them (`ID.match()`; anchored at both ends).
`ID_PATTERN` is the whole-token tokenizer: it finds each id as exactly that token, never glued
to a word, a path, an address or another id on either side (`x1a2b3c4d`, `path/1a2b3c4d`,
`a@b!3`, the `40400001` of a noreply address are no ids), and names the kind that matched
(`commit`, `request`, `ticket`). The digest's `Digest.ids` is every token of it the finished
text holds.

`CANDIDATE` is the citation check's: the same grammar widened so that the check fails closed —
used by `citations.py` only, never to say what the digest holds. It finds every id the grammar can
write as exactly that token (a round-trip test generates them), and near misses as
tokens of their own: hex of 7 to 40 digits in either case, also after `0x`, `_` or a literal
`\\x`/`\\u`/`\\U`; a request with a project path before it, or none (a bare `!N`/`#N`); a
key-shaped word in any case (`proj-99`, `top-10`); and the letters, digits and inner `_` glued
after any of them (`deadbeef1` is never `deadbeef`, `acme!123` never `acme!12`). A SHA or a key
glued after a word is a candidate too (`x1a2b3c4d`, `3PROJ-12`), never the id it ends with.

Every pattern is linear on hostile text (the regex guard): a match starts only where its run
starts, a project path is at most 8 segments of 64 characters, and the quantifiers that cannot
give a character back are possessive.
"""

from __future__ import annotations

import re

NAME = r"[A-Za-z0-9._-]"  # one character of a repository id
COMMIT = r"[0-9a-f]{8,40}"
REQUEST = rf"{NAME}++[!#][0-9]++"
TICKET = r"[A-Z][A-Z0-9_]++-[0-9]++"

# Is this whole string an id? (`\A`…`\Z`: one start only, so even `search` stays linear)
ID = re.compile(rf"\A(?:{COMMIT}|{REQUEST}|{TICKET})\Z")

# Every id in a text, as whole tokens, by kind (`match.lastgroup`).
ID_PATTERN = re.compile(
    rf"(?<![\w.!#/@+-])(?:(?P<commit>{COMMIT})|(?P<request>{REQUEST})|(?P<ticket>{TICKET}))"
    r"(?![\w!#@+-])"
)

# ------------------------------------------------------- the citation check's fail-closed detector

# What glues onto an id and makes it another token: letters and digits, and `_` between them
# (`PROJ-12_fix`) — but not a `_` that closes Markdown emphasis (`_PROJ-12_`).
_TAIL = r"(?:[A-Za-z0-9]|_(?=[A-Za-z0-9]))*+"
# Where a SHA or a key may start: where its run starts — a SHA after anything but a hex digit
# (`/`, `_`, `(`, `*`, a word: `x1a2b3c4d`), a key after anything but a letter (`3PROJ-12`) —
# or right after `0x` or a literal `\x`, `\u`, `\U` (`0xcafebabe` is a hex run too). A word
# glued in front makes no other token here, so the id is read glued: unknown.
_ESCAPED = r"(?<=0x)|(?<=\\[xuU])"
_HEX_START = rf"(?:(?<![0-9A-Fa-f])|{_ESCAPED})"
_KEY_START = rf"(?:(?<![A-Za-z])|{_ESCAPED})"
CANDIDATE = re.compile(
    rf"(?<![A-Za-z0-9._-])(?:{NAME}{{1,64}}+/){{0,8}}+{NAME}*+[!#][0-9]++{_TAIL}"
    rf"|{_HEX_START}[0-9A-Fa-f]{{7,40}}+{_TAIL}"
    rf"|{_KEY_START}[A-Za-z][A-Za-z0-9_]{{0,63}}+-[0-9]++{_TAIL}"
)
