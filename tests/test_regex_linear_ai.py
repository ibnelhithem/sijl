"""The AI client's address patterns on hostile text (the regex guard): its URL scrubber and the
redactor read text from outside — SDK errors, commit subjects and messages — with `sub` and
`finditer`, which restart at every position. A scheme that could start at every letter of a long
run, and a name word that could start after every `-` or `'` inside one, made both quadratic:
4.7 s for 16 KB of `-a`, minutes for 200 KB. They are linear now, and say exactly what they said
before — each is compared with its old pattern on thousands of random texts."""

import random
import re

import pytest

from sijl import ai, redact
from sijl.redact import Redactor
from tests.gitfactory import COLLEAGUE, ME
from tests.scaling import growth_problem

# Hostile text of any size n (200 KB at the largest): each test checks how the time grows as n
# doubles (tests/scaling.py) — never a wall-clock limit per call, which a loaded machine broke.
GROWING = {
    "letters": lambda n: "a" * n,
    "dash-a": lambda n: "-a" * (n // 2),
    "quote-a": lambda n: "'a" * (n // 2),
    "dot-a": lambda n: ".a" * (n // 2),
    "digits-then-letters": lambda n: "1" * (n // 2) + "a" * (n // 2),
}
SIZES = (25_000, 50_000, 100_000, 200_000)
OLD_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]*")
OLD_WORD = r"[^\W_][^\s<>:,;\"()\[\]@]*"
OLD_LINK = r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+"
ALPHABET = "aZ19+.-:/@%40<>'\" \t\n,;(*_x"


def texts(seed: int, count: int = 4000) -> list[str]:
    rng = random.Random(seed)
    return [
        "".join(rng.choice(ALPHABET) for _ in range(rng.randrange(1, 40))) for _ in range(count)
    ]


@pytest.mark.parametrize("name", sorted(GROWING))
def test_the_url_scrubber_is_linear(name: str) -> None:
    assert growth_problem(ai.scrub, GROWING[name], SIZES) == ""


def test_the_url_scrubber_scrubs_exactly_as_before(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    new = [ai.scrub(t) for t in texts(23)]
    monkeypatch.setattr(ai, "_URL", OLD_URL)
    assert new == [ai.scrub(t) for t in texts(23)]
    assert ai.scrub("1https://user:pw@host/x") == "1https://host/…"  # a run's digits stay text


@pytest.mark.parametrize("name", sorted(GROWING))
def test_the_redactor_is_linear(name: str) -> None:
    redactor = Redactor(keep_emails=[ME.email], names=[COLLEAGUE.name])
    assert growth_problem(redactor, GROWING[name], SIZES) == ""


def test_the_redactor_redacts_exactly_as_before(monkeypatch) -> None:
    def run() -> list[str]:
        redactor = Redactor(keep_emails=[ME.email], names=[COLLEAGUE.name])
        return [redactor(t) for t in texts(17)]

    new = run()
    old_pair = (
        r"(?:^|(?<=[:,;(\[\"'*>•-]))(?P<lead>[ \t]*)"
        rf"(?P<who>{OLD_WORD}(?:[ \t]+{OLD_WORD}){{0,3}})(?P<gap>[ \t]*)<(?P<addr>{redact._EMAIL})>"
    )
    old = re.compile(
        rf"(?P<pair>{old_pair})|(?P<link>{OLD_LINK})|(?P<email>{redact._EMAIL})",
        re.IGNORECASE | re.MULTILINE,
    )
    monkeypatch.setattr(redact, "_ADDRESSES", old)
    assert new == run()
