"""Characters a renderer shows as nothing. Unicode's
Default_Ignorable_Code_Point characters outside the format category — the combining grapheme
joiner, variation selectors, Mongolian free variation selectors, Khmer inherent vowels, Hangul
fillers (which are letters) — and combining marks let an invented id pass unmarked
(`cafe<CGJ>babe` reads "cafebabe") or a longer id verify as a known one (`PROJ-12<VS16>3` read as
PROJ-12). `printable()` now shows every default-ignorable character as a visible escape, and the
citation check finds the token with them removed and marks it, never verifies it."""

import pytest

from sijl import citations, text
from sijl.citations import UNKNOWN
from sijl.text import printable, printable_block
from tests.test_citations import evidence

# Every character known to hide this way, and one of each kind beside them.
HIDDEN = {
    "\u034f": "COMBINING GRAPHEME JOINER",
    "\ufe00": "VARIATION SELECTOR-1",
    "\ufe0f": "VARIATION SELECTOR-16",
    "\U000e0100": "VARIATION SELECTOR-17",
    "\U000e01ef": "VARIATION SELECTOR-256",
    "\u180b": "MONGOLIAN FREE VARIATION SELECTOR ONE",
    "\u180d": "MONGOLIAN FREE VARIATION SELECTOR THREE",
    "\u180e": "MONGOLIAN VOWEL SEPARATOR",
    "\u180f": "MONGOLIAN FREE VARIATION SELECTOR FOUR",
    "\u17b4": "KHMER VOWEL INHERENT AQ",
    "\u17b5": "KHMER VOWEL INHERENT AA",
    "\u115f": "HANGUL CHOSEONG FILLER",
    "\u1160": "HANGUL JUNGSEONG FILLER",
    "\u3164": "HANGUL FILLER",
    "\uffa0": "HALFWIDTH HANGUL FILLER",
    "\u200b": "ZERO WIDTH SPACE",
    "\u00ad": "SOFT HYPHEN",
    "\u2065": "<reserved-2065>",
    "\U000e0001": "LANGUAGE TAG",
}
MARKS = {"\u0301": "COMBINING ACUTE ACCENT (Mn)", "\u20dd": "COMBINING ENCLOSING CIRCLE (Me)"}


@pytest.mark.parametrize("char", sorted(HIDDEN), ids=lambda c: f"U+{ord(c):04X}")
def test_printable_escapes_every_default_ignorable_character(char: str) -> None:
    shown = printable(f"a{char}b")
    assert char not in shown and shown.startswith("a\\") and shown.endswith("b")
    assert text.ignorable(char)


def test_printable_leaves_visible_text_alone() -> None:
    for plain in ("caf\u00e9", "cafe\u0301", "\u2713 done", "\u00e9t\u00e9 2026", "\u03bb x"):
        assert printable(plain) == plain  # combining accents stay: they are visible text


def test_the_ignorable_ranges_are_unicodes() -> None:
    """Spot checks of the hard-coded ranges against their neighbours (DerivedCoreProperties)."""
    assert [text.ignorable(chr(c)) for c in (0x034E, 0x034F, 0x0350)] == [False, True, False]
    assert [text.ignorable(chr(c)) for c in (0xFDFF, 0xFE00, 0xFE0F, 0xFE10)] == [
        False, True, True, False,
    ]  # fmt: skip
    assert [text.ignorable(chr(c)) for c in (0xE00FF, 0xE0100, 0xE01EF, 0xE0FFF, 0xE1000)] == [
        True, True, True, True, False,
    ]  # fmt: skip
    assert not any(text.ignorable(c) for c in "aZ09-_!#./ \t")


def where(char: str) -> list[tuple[str, str]]:
    """(written, what the reader sees) for a hidden character inside, before and after ids."""
    return [
        (f"PROJ-1{char}2", "inside a known key"),
        (f"{char}PROJ-12", "before a known key"),
        (f"PROJ-12{char}", "after a known key"),
        (f"PROJ-12{char}3", "making PROJ-123"),
        (f"cafe{char}babe", "making an invented SHA"),
        (f"dead{char}beef", "inside a known SHA"),
        (f"webapp!{char}999", "inside an invented request"),
        (f"acme!12{char}3", "making acme!123"),
        (f"X{char}PROJ-12", "making XPROJ-12"),
        (f"deadbeef{char}12", "making deadbeef12"),
        (f"{char}PROJ-99", "before an invented key"),
    ]


@pytest.mark.parametrize("char", sorted(HIDDEN | MARKS), ids=lambda c: f"U+{ord(c):04X}")
def test_an_id_touched_by_a_hidden_character_is_never_verified(char: str) -> None:
    """In the raw answer and as the terminal shows it (escaped): one token, unknown, marked."""
    for written, case in where(char):
        for answer in (f"See {written} today.", printable(f"See {written} today.")):
            check = citations.check(answer, evidence())
            assert not check.ok, (case, answer)
            assert [c.status for c in check.cited] == [UNKNOWN], (case, answer, check.cited)
            assert citations.MARK in citations.mark(answer, check)


def test_the_note_counts_ids_written_with_hidden_characters() -> None:
    reading = citations.read("Ticket PROJ-12\u034f3 and PROJ-1\ufe0f2.", evidence())
    assert [c.hidden for c in reading.check.cited] == [True, True]
    assert citations.note(reading).endswith("marked in the answer above: 2 other id-like texts.")


def test_a_visible_symbol_with_its_variation_selector_beside_an_id_is_harmless() -> None:
    """An emoji's selector is hidden, but a space keeps it away from the id."""
    check = citations.check(printable("\u267b\ufe0f PROJ-12 done"), evidence())
    assert [(c.id, c.status) for c in check.cited] == [("PROJ-12", "known")]


# ----------------------------------------------- an emoji's selector, right after its symbol

SYMBOLS = {
    "recycling (So) + emoji": "\u267b\ufe0f",
    "check mark (So) + emoji": "\u2714\ufe0f",
    "heart (So) + text": "\u2764\ufe0e",
    "small tilde (Sk) + emoji": "\u02dc\ufe0f",
}


@pytest.mark.parametrize("symbol", SYMBOLS.values(), ids=SYMBOLS.keys())
def test_a_selector_right_after_a_symbol_is_printed_as_written(symbol: str) -> None:
    """U+FE0E/U+FE0F after a non-ASCII symbol pick how it is drawn — text or emoji — and hide
    nothing: printed as written, not as `\\ufe0f`."""
    assert printable(f"Done {symbol}.") == f"Done {symbol}."
    assert printable_block(f"Done {symbol}\nnext") == f"Done {symbol}\nnext"


@pytest.mark.parametrize(
    ("written", "shown"),
    [
        ("x\ufe0f", "x\\ufe0f"),  # after a letter
        ("\ufe0f", "\\ufe0f"),  # at the start
        ("1\ufe0e", "1\\ufe0e"),  # after a digit
        ("^\ufe0f", "^\\ufe0f"),  # after an ASCII symbol (Sk)
        ("\u00e9\ufe0f", "\u00e9\\ufe0f"),  # after a letter that is not ASCII
        ("\u267b\ufe0f\ufe0f", "\u267b\ufe0f\\ufe0f"),  # a second one
        ("\u267b\u200b\ufe0f", "\u267b\\u200b\\ufe0f"),  # not right after the symbol
        ("\u267b\ufe01", "\u267b\\ufe01"),  # another variation selector
    ],
)
def test_a_selector_anywhere_else_is_escaped(written: str, shown: str) -> None:
    assert printable(written) == shown


@pytest.mark.parametrize(
    "answer", ["Ticket \u267b\ufe0fPROJ-12.", "Ticket PROJ-12\ufe0f.", "Ticket PROJ-1\ufe0f2."]
)
def test_a_selector_inside_or_beside_an_id_still_hides_it(answer: str) -> None:
    reading = citations.read(answer, evidence())
    assert [(c.status, c.hidden) for c in reading.check.cited] == [("unknown", True)]
    assert citations.MARK in reading.text and not reading.ok
