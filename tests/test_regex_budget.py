"""Every regular expression Sijl compiles at module level runs in linear time on hostile text.

Commit subjects, messages, branch names and remote URLs are anyone's text. A pattern whose
quantifiers can split the same run of characters in many ways — `.*?` before `[ \\t]*`, both
matching spaces — or that `search` restarts at every position of a long run takes quadratic time
or worse; one such pattern would have stalled a report for half an hour on a
1 MB subject. This guard finds every compiled pattern in every Sijl module and runs it
with `search`, `match` and `fullmatch` on pathological text — long runs of one character,
alternations of two, and the pattern's own literal words repeated — of doubling sizes up to
200 KB, and checks how the time grows (`tests/scaling.py`): a linear pattern takes about twice
as long when the text doubles, a quadratic one four times. It judges that growth, not
a wall-clock limit per call (0.2 s), which a loaded machine broke for a linear pattern; any call
over 2 s at 200 KB still fails. A linear pattern needs a few milliseconds; a quadratic one,
minutes."""

import importlib
import pkgutil
import re
from collections.abc import Callable
from itertools import combinations

import pytest

import sijl
from tests.scaling import growth_problem

SIZE = 200_000
RUNS = " \t\r\n!'\"(#-_.:/@`[]<>|*+?)~%=;,09aAzZé\\"
PAIRS = " \t!'(#-_09aA\r/:"  # every two of these, alternating
FILLERS = " a1!'/"  # what follows a pattern's own words


def _patterns() -> dict[str, re.Pattern]:
    """Every compiled pattern bound at module level, or on a class, in Sijl — by name."""
    found: dict[str, re.Pattern] = {}
    for info in pkgutil.walk_packages(sijl.__path__, "sijl."):
        if info.name.endswith("__main__"):
            continue  # importing it runs the command line
        module = importlib.import_module(info.name)
        for name, value in vars(module).items():
            owners = [(name, value)]
            if isinstance(value, type):
                owners = [(f"{name}.{n}", v) for n, v in vars(value).items()]
            for label, item in owners:
                if isinstance(item, re.Pattern) and item not in found.values():
                    found[f"{info.name.removeprefix('sijl.')}.{label}"] = item
    return found


PATTERNS = _patterns()
# Doubling sizes up to SIZE: a quadratic pattern shows its 4x growth at the small ones, in seconds.
SIZES = (25_000, 50_000, 100_000, SIZE)
Text = Callable[[int], str]


def _texts(pattern: re.Pattern) -> list[Text]:
    """Pathological text of about the size asked for: long runs of one character (and the same
    ending in something nothing expects), two characters alternating, and the pattern's own
    literal pieces — `See merge request `, `https://`, `(#` — repeated, or followed by a long run:
    text that walks deep into the pattern before it fails. Each is a function of the size."""
    texts: list[Text] = [lambda n, c=c: c * n for c in RUNS]
    texts += [lambda n, c=c: c * n + "\x00!" for c in RUNS]
    texts += [lambda n, a=a, b=b: (a + b) * (n // 2) for a, b in combinations(PAIRS, 2)]
    for word in _literals(pattern):
        texts.append(lambda n, w=word: w * (n // len(w)))
        texts += [lambda n, w=word, f=filler: w + f * n for filler in FILLERS]
    return texts


def _literals(pattern: re.Pattern) -> list[str]:
    """The runs of literal characters in `pattern` — `See merge request `, `://`, `(#` — read from
    Python's own parse of it (3.12's `re._parser`), at least two characters long, the 12 longest."""
    source = pattern.pattern if isinstance(pattern.pattern, str) else pattern.pattern.decode()
    found: set[str] = set()

    def walk(items) -> None:
        run = ""
        for op, value in items:
            if op is re._constants.LITERAL:
                run += chr(value)
                continue
            if len(run) >= 2:
                found.add(run)
            run = ""
            for part in value if isinstance(value, tuple | list) else ():
                if isinstance(part, re._parser.SubPattern):
                    walk(part)
                elif isinstance(part, tuple | list):
                    for inner in part:
                        if isinstance(inner, re._parser.SubPattern):
                            walk(inner)
        if len(run) >= 2:
            found.add(run)

    walk(re._parser.parse(source, pattern.flags & ~re.UNICODE & ~re.ASCII))
    return sorted(found, key=lambda w: (-len(w), w))[:12]


def test_the_guard_finds_the_patterns_it_must_watch() -> None:
    assert _literals(re.compile(r"See merge request (?P<p>\S*?)!(\d+)")) == ["See merge request "]
    assert "://" in _literals(re.compile(r"(?<![a-z])[a-z]+://[^ ]*"))
    names = set(PATTERNS)
    assert {"deliveries._PULL_NUMBER_AT_END", "deliveries._GIT_MERGE", "report._LINK"} <= names
    assert len(PATTERNS) >= 25


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_every_pattern_runs_in_linear_time(name: str) -> None:
    pattern = PATTERNS[name]
    binary = isinstance(pattern.pattern, bytes)
    slow = []
    for text in _texts(pattern):
        make = (lambda n, t=text: t(n).encode()) if binary else text
        for method in (pattern.search, pattern.match, pattern.fullmatch):
            problem = growth_problem(method, make, SIZES)
            if problem:
                slow.append(f"{method.__name__}({make(12)!r}…) {problem}")
        assert not slow, f"{name} ({pattern.pattern!r}) is not linear: {slow[:5]}"
