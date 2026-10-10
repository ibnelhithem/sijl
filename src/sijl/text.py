"""Text that reaches a terminal.

Sijl prints things it did not write: folder names, commit subjects, git's own error messages
(which quote paths and repository content). Any of them can carry characters a terminal acts on
— ESC starting a CSI or OSC sequence (clear the screen, change the title, write the clipboard),
other C0 and C1 controls, DEL — or that make text read differently from what it is: bidi
overrides and other invisible format characters, and every other character Unicode says a
renderer may show as nothing (Default_Ignorable_Code_Point: variation selectors, the combining
grapheme joiner, Hangul fillers…). A folder name that is not UTF-8 arrives as lone
surrogates, which a UTF-8 stream cannot even encode. `printable()` shows each of them as a
visible escape (`\\x1b`, `\\xff`, `\\u202e`) instead — never dropped, so a reader still sees
that something odd was there. It is applied where text is rendered, never to the data: JSON keeps
the exact value (`json_text()`), and the models hold what git said.
"""

from __future__ import annotations

import json
import re
import unicodedata

# Unicode categories a terminal may act on, or that hide what text is: control characters (Cc:
# C0, DEL, C1), format characters (Cf: bidi marks, embeddings, overrides and isolates, zero-width
# characters, tags), surrogates (Cs: os.fsdecode keeps each byte of a name that is not UTF-8 as
# U+DC80-U+DCFF, which a UTF-8 terminal cannot even print) and line/paragraph separators (Zl, Zp).
_ESCAPED = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
# Default_Ignorable_Code_Point, as Unicode's DerivedCoreProperties.txt lists it (16.0.0:
# https://www.unicode.org/Public/UCD/latest/ucd/DerivedCoreProperties.txt): characters a
# renderer may show as nothing. The format characters among them are escaped above anyway; the
# rest are not format characters — the combining grapheme joiner (Mn), variation selectors and
# Mongolian free variation selectors (Mn), Khmer inherent vowels (Mn), Hangul fillers (Lo,
# letters!), and reserved code points — and an id with one inside reads as another. Hard-coded:
# Python's unicodedata does not expose the property.
IGNORABLE_RANGES = (
    (0x00AD, 0x00AD),  # SOFT HYPHEN
    (0x034F, 0x034F),  # COMBINING GRAPHEME JOINER
    (0x061C, 0x061C),  # ARABIC LETTER MARK
    (0x115F, 0x1160),  # HANGUL CHOSEONG FILLER, HANGUL JUNGSEONG FILLER
    (0x17B4, 0x17B5),  # KHMER VOWEL INHERENT AQ, AA
    (0x180B, 0x180F),  # MONGOLIAN FREE VARIATION SELECTORS ONE-THREE, VOWEL SEPARATOR, FOUR
    (0x200B, 0x200F),  # ZERO WIDTH SPACE .. RIGHT-TO-LEFT MARK
    (0x202A, 0x202E),  # bidi embeddings and overrides
    (0x2060, 0x206F),  # WORD JOINER .. NOMINAL DIGIT SHAPES (2065 reserved)
    (0x3164, 0x3164),  # HANGUL FILLER
    (0xFE00, 0xFE0F),  # VARIATION SELECTOR-1 .. -16
    (0xFEFF, 0xFEFF),  # ZERO WIDTH NO-BREAK SPACE
    (0xFFA0, 0xFFA0),  # HALFWIDTH HANGUL FILLER
    (0xFFF0, 0xFFF8),  # reserved
    (0x1BCA0, 0x1BCA3),  # SHORTHAND FORMAT LETTER OVERLAP .. UP STEP
    (0x1D173, 0x1D17A),  # MUSICAL SYMBOL BEGIN BEAM .. END PHRASE
    (0xE0000, 0xE0FFF),  # tags, VARIATION SELECTOR-17 .. -256, reserved
)
_IGNORABLE = re.compile(
    "[" + "".join(f"\\U{low:08x}-\\U{high:08x}" for low, high in IGNORABLE_RANGES) + "]"
)


def ignorable(char: str) -> bool:
    """Is `char` a Default_Ignorable_Code_Point: one a renderer may show as nothing?"""
    return _IGNORABLE.fullmatch(char) is not None


def hides(char: str) -> bool:
    """Should `printable()` show `char` as an escape? (Tab never: it is ordinary text.)"""
    return char != "\t" and (unicodedata.category(char) in _ESCAPED or ignorable(char))


def escape(char: str) -> str:
    """`char` as the visible escape `printable()` shows: `\\x1b`, `\\u202e`, `\\U000e0001`."""
    code = ord(char)
    if 0xDC80 <= code <= 0xDCFF:  # one byte of a name that is not UTF-8: show that byte
        return f"\\x{code - 0xDC00:02x}"
    if code < 0x100:
        return f"\\x{code:02x}"
    if code < 0x10000:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


# The text and emoji presentation selectors: right after a symbol (`\\u2714\\ufe0f`), they only
# say how it is drawn; anywhere else they hide (`PROJ-1\\ufe0f2`).
_PRESENTATION = frozenset((chr(0xFE0E), chr(0xFE0F)))


def _presents(char: str, previous: str) -> bool:
    """Is `char` a presentation selector right after a non-ASCII symbol (So, Sk) — an emoji?"""
    return (
        char in _PRESENTATION
        and not previous.isascii()
        and unicodedata.category(previous) in ("So", "Sk")
    )


def printable(text: str) -> str:
    """`text` with every character a terminal acts on, or that hides what text is, shown as a
    visible escape (`\\x1b`, `\\xff`, `\\u202e`); everything else — tab included — unchanged,
    and so is U+FE0E/U+FE0F right after a non-ASCII symbol: the emoji it draws (`_presents()`)."""
    if text.isprintable() and not _IGNORABLE.search(text):  # the common case: nothing to show
        return text
    return "".join(
        escape(char) if hides(char) and not _presents(char, previous) else char
        for previous, char in zip(" " + text[:-1], text, strict=True)
    )


def utf8_safe(text: str) -> str:
    """`text` with every lone surrogate — a byte of a name that is not UTF-8 (U+DC80-U+DCFF,
    from os.fsdecode), or any other — shown as the same visible escape `printable()` uses
    (`\\xff`, `\\ud800`), and nothing else changed: the result can always be encoded as UTF-8.
    For text that must leave the machine (the AI payload), where an escape is better than a
    crash and must be what the preview shows."""
    if not any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        return text
    return "".join(escape(char) if 0xD800 <= ord(char) <= 0xDFFF else char for char in text)


def printable_block(text: str) -> str:
    """Several lines (a message that is meant to span lines): each line made `printable()`."""
    return "\n".join(printable(line) for line in text.split("\n"))


def _json_escape(char: str) -> str:
    code = ord(char)
    if code > 0xFFFF:  # JSON spells a character beyond the BMP as a UTF-16 surrogate pair
        code -= 0x10000
        return f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}"
    return f"\\u{code:04x}"


def json_text(value: object) -> str:
    """`value` as stable JSON — keys sorted, two-space indent, readable non-ASCII — with nothing a
    terminal acts on left raw and nothing a UTF-8 stream cannot encode: `json.dumps` escapes C0
    but leaves DEL, C1, format characters, separators and lone surrogates raw, so each becomes its
    `\\uNNNN` escape, and so does every default-ignorable character (`ignorable()`). Raw newlines
    in the output are the indentation's own (json escapes those in strings). `json.loads()` gives
    back exactly `value`, a non-UTF-8 name included."""
    out = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return "".join(
        _json_escape(char)
        if char != "\n" and (unicodedata.category(char) in _ESCAPED or ignorable(char))
        else char
        for char in out
    )
