"""What kind of work each commit is: its conventional-commit prefix, then a review category.

`parse_conventional()` reads the subject the way the Conventional Commits spec writes it —
`type(scope)!: description`, plus a `BREAKING CHANGE:` footer — and tolerates what real
repositories contain: upper case, extra spaces, common aliases (`feature`, `bugfix`, `tests`)
and team-specific prefixes (`ui:`, `shell:`), which are kept as an *area* rather than guessed
into a standard type. Anything else is "other", never dropped. A ticket key or a backlog id
written *before* all that (`ABC-123: …`, `[ABC-123] fix: …`, `B12: …`) is a *lead*: it is
set aside first, so it never becomes an area and the prefix after it is still read.

`review_category()` turns that into the handful of headings a reviewer reads. Which heading a
kind of work belongs under is a judgement about your own job — so that function is the user's
own code; the tests check only its contract.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass

from sijl.models import Repo
from sijl.tickets import STANDARDS

STANDARD_KINDS = (
    "feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert",
)  # fmt: skip
ALIASES = {
    "feature": "feat",
    "features": "feat",
    "bugfix": "fix",
    "hotfix": "fix",
    "bug": "fix",
    "doc": "docs",
    "documentation": "docs",
    "tests": "test",
    "testing": "test",
    "refactoring": "refactor",
    "performance": "perf",
    "chores": "chore",
}
REVIEW_CATEGORIES = ("feature", "fix", "quality", "docs", "tooling", "other")

_PREFIX = re.compile(
    r"^(?P<kind>[A-Za-z][A-Za-z0-9_-]*)(?:\((?P<scope>[^()]*)\))?(?P<bang>!)?: (?P<desc>\S.*)$"
)
# git's revert and re-apply subjects; GitHub's squash merge adds its pull request, ` (#123)`
_REVERT = re.compile(r'^Revert "(?P<inner>.+)"(?: \(#[0-9]+\))?$')
_REAPPLY = re.compile(r'^Reapply "(?P<inner>.+)"(?: \(#[0-9]+\))?$')
# `BREAKING CHANGE: text`, or the text on the next, indented line (a trailer's continuation)
_BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:(?: |[ \t]*\r?\n[ \t]+\S)", re.MULTILINE)
_LEAD_KEY = r"[A-Z][A-Z0-9_]+-[0-9]+"  # a Jira-shaped key; tickets.py decides if it is a ticket
_LEAD_KEYS = rf"{_LEAD_KEY}(?:(?:\s*,\s*|\s+){_LEAD_KEY})*"  # separated by commas or spaces
_LEAD = re.compile(
    rf"^(?P<lead>(?:\[{_LEAD_KEYS}\]\s*)+|{_LEAD_KEYS}(?=[\s:])|[A-Z][0-9]+(?=:))"
    r"\s*(?:[:|]|[-\u2013\u2014](?=\s))?\s*(?P<rest>\S.*)$"  # `:`, `|`, a hyphen, en or em dash
)
# each id from its first character only: a search restarted inside a long run of capitals is
# quadratic (the regex budget)
_LEAD_ID = re.compile(rf"(?<![A-Za-z0-9_])(?:{_LEAD_KEY}|[A-Z][0-9]+)")


@dataclass(frozen=True)
class Conventional:
    kind: str  # one of STANDARD_KINDS, or "other"
    scope: str  # "api" in `fix(api): …`; "" when there is none
    area: str  # a non-standard prefix kept as-is: "ui" in `ui: …`
    breaking: bool  # `!` before the colon, or a BREAKING CHANGE footer
    description: str  # the subject after the prefix, spacing normalised
    conventional: bool  # the subject had a `prefix: ` at all
    lead: str = ""  # ids written before the prefix: "ABC-123" in `[ABC-123] fix: …`; "B12"


def split_lead(text: str) -> tuple[str, str]:
    """A ticket key or backlog id written before the subject, and the rest: ("", text) if none.

    `ABC-123: x`, `ABC-123 feat: x`, `ABC-1 ABC-2 fix: y`, `[ABC-1][ABC-2] fix: y`,
    `ABC-1, ABC-2 - x` and `B12: x` lead. A subject that *starts* with a standard (`UTF-8: …`,
    `UTF-8 support`) has none — the standard is its topic. In brackets every item is an id, so
    `[ABC-1, UTF-8] fix: y` still leads; tickets.py refuses UTF-8 and counts ABC-1.
    """
    match = _LEAD.match(text)
    if not match:
        return "", text
    lead = match["lead"]
    ids = _LEAD_ID.findall(lead)
    if not lead.startswith("[") and ids[0].split("-")[0] in STANDARDS:
        return "", text
    return ", ".join(ids), match["rest"]


def parse_conventional(subject: str, body: str) -> Conventional:
    """Parse one commit message; `body` is the full message (subject included)."""
    text = " ".join(subject.split())
    breaking = bool(_BREAKING_FOOTER.search(body))
    revert = _REVERT.match(text)
    if revert:
        return Conventional("revert", "", "", breaking, revert["inner"], True)
    reapply = _REAPPLY.match(text)
    if reapply:  # git >= 2.43 for a revert of a revert: the work it re-applies, read inside
        return parse_conventional(reapply["inner"], body)
    lead, text = split_lead(text)
    match = _PREFIX.match(text)
    if not match:
        return Conventional("other", "", "", breaking, text, False, lead)
    prefix = match["kind"].lower()
    kind = ALIASES.get(prefix, prefix)
    area = "" if kind in STANDARD_KINDS else prefix
    return Conventional(
        kind=kind if kind in STANDARD_KINDS else "other",
        scope=(match["scope"] or "").strip(),
        area=area,
        breaking=breaking or bool(match["bang"]),
        description=match["desc"],
        conventional=True,
        lead=lead,
    )


def review_category(
    commit: Conventional, repo: Repo, test_suites: Collection[str] = frozenset()
) -> str:
    """Which heading of a review this commit belongs under — the user's own judgement.

    Return one of REVIEW_CATEGORIES:
      feature  a new capability someone can use
      fix      something broken now works
      quality  tests, refactoring, performance, style: better code, same behaviour
      docs     documentation
      tooling  build, CI, dependencies, chores: the machinery around the code
      other    anything that cannot be placed (non-conventional subjects land here)

    Available: commit.kind (STANDARD_KINDS or "other"), commit.scope, commit.area (a
    non-standard prefix such as "ui"), commit.breaking, commit.description,
    commit.conventional, repo.name, and `test_suites` — the repositories the user named in
    `categories.test_suites` (`categoriser()` fills it in). Never raise — the tests check only
    that a category from REVIEW_CATEGORIES comes back, for every kind of subject.
    """
    # The default mapping, the user's to change at any time. In a test-suite repository a new
    # test is the deliverable, so `test:` counts as feature work there: a name ending in -tests
    # or -test, or one the user lists in categories.test_suites.
    test_suite = repo.name.endswith(("-tests", "-test")) or repo.name in test_suites
    if commit.kind == "feat":
        return "feature"
    if commit.kind == "test":
        return "feature" if test_suite else "quality"
    if commit.kind == "fix":
        return "fix"
    if commit.kind in ("refactor", "perf", "style"):
        return "quality"
    if commit.kind == "docs":
        return "docs"
    if commit.kind in ("build", "ci", "chore"):
        return "tooling"
    return "other"  # revert, team prefixes, no prefix


def categoriser(test_suites: Iterable[str] = ()) -> Callable[[Conventional, Repo], str]:
    """`review_category()` with the user's `categories.test_suites` filled in: what a report
    categorises with."""
    suites = frozenset(name.strip() for name in test_suites)
    return functools.partial(review_category, test_suites=suites) if suites else review_category
