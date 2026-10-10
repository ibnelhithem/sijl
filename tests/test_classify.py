"""sijl.classify: conventional-commit subjects parsed into kind, scope, area and breaking;
review_category() — the user's own judgement — checked only against its contract."""

from pathlib import Path

import pytest

from sijl.classify import (
    REVIEW_CATEGORIES,
    STANDARD_KINDS,
    Conventional,
    parse_conventional,
    review_category,
)
from sijl.models import Repo


@pytest.mark.parametrize(
    ("subject", "body", "expected"),
    [
        ("feat: add login", "", ("feat", "", "", False, "add login")),
        ("fix(api): handle 404", "", ("fix", "api", "", False, "handle 404")),
        ("feat(auth)!: drop v1 tokens", "", ("feat", "auth", "", True, "drop v1 tokens")),
        ("refactor!: new layout", "", ("refactor", "", "", True, "new layout")),
        ("Feat:  Add  login ", "", ("feat", "", "", False, "Add login")),  # case, spacing
        ("feature: add login", "", ("feat", "", "", False, "add login")),  # an alias
        ("bugfix(ui): align button", "", ("fix", "ui", "", False, "align button")),
        ("tests: cover retry", "", ("test", "", "", False, "cover retry")),
        ("ui: new header", "", ("other", "", "ui", False, "new header")),  # a non-standard type
        ("shell(zsh): faster prompt", "", ("other", "zsh", "shell", False, "faster prompt")),
        (
            "chore: bump deps",
            "chore: bump deps\n\nBREAKING CHANGE: needs Python 3.12\n",
            ("chore", "", "", True, "bump deps"),
        ),
        ("fix: x", "fix: x\n\nBREAKING-CHANGE: y\n", ("fix", "", "", True, "x")),
        # the footer's text on the next, indented line, as a trailer's continuation
        ("fix: x", "fix: x\n\nBREAKING CHANGE:\n  needs Python 3.12\n", ("fix", "", "", True, "x")),
        ("fix: x", "fix: x\n\nBREAKING-CHANGE:\n\ty\n", ("fix", "", "", True, "x")),
        ("fix: x", "fix: x\n\nBREAKING CHANGE:\nnot indented\n", ("fix", "", "", False, "x")),
        ('Revert "feat: add login"', "", ("revert", "", "", False, "feat: add login")),
        # GitHub's squash merge of a revert ends with its pull request (#N): still a revert
        ('Revert "feat: x" (#123)', "", ("revert", "", "", False, "feat: x")),
        ('Revert "Revert "feat: x""', "", ("revert", "", "", False, 'Revert "feat: x"')),
        # git >= 2.43 names a revert of a revert `Reapply`: the work it re-applies, read inside
        ('Reapply "feat: x"', "", ("feat", "", "", False, "x")),
        ('Reapply "fix(api)!: y" (#12)', "", ("fix", "api", "", True, "y")),
        ('Reapply "Add login"', "", ("other", "", "", False, "Add login")),
        ("Add login page", "", ("other", "", "", False, "Add login page")),  # not conventional
        ("WIP", "", ("other", "", "", False, "WIP")),
        ("feat:missing space", "", ("other", "", "", False, "feat:missing space")),
        ("feat(): empty scope", "", ("feat", "", "", False, "empty scope")),
    ],
)
def test_conventional_subjects_are_parsed(subject, body, expected) -> None:
    c = parse_conventional(subject, body or f"{subject}\n")
    assert (c.kind, c.scope, c.area, c.breaking, c.description) == expected
    assert c.kind in (*STANDARD_KINDS, "other")


def test_only_a_real_prefix_marks_a_commit_conventional() -> None:
    assert parse_conventional("feat: x", "feat: x\n").conventional
    assert parse_conventional("ui: x", "ui: x\n").conventional  # a prefix, just not a standard one
    assert not parse_conventional("Add x", "Add x\n").conventional


REPOS = [
    Repo("web-tests", Path("/r/web-tests"), Path("/r/web-tests/.git"), False),
    Repo("api", Path("/r/api"), Path("/r/api/.git"), False),
]
SUBJECTS = [
    "feat: add login",
    "fix(api)!: handle 404",
    "test: cover retry",
    "test(e2e): new billing flow",
    "docs: readme",
    "refactor: split module",
    "perf: cache",
    "style: format",
    "build: bump uv",
    "ci: pin actions",
    "chore: tidy",
    'Revert "feat: x"',
    "ui: header",
    "Add something",
    "",
]


@pytest.mark.parametrize("repo", REPOS, ids=lambda r: r.name)
@pytest.mark.parametrize("subject", SUBJECTS)
def test_review_category_keeps_its_contract(subject: str, repo: Repo) -> None:
    """Only the contract is tested — which category is the user's judgement, not the tests'."""
    category = review_category(parse_conventional(subject, f"{subject}\n"), repo)
    assert category in REVIEW_CATEGORIES


def test_the_categories_a_reviewer_reads() -> None:
    assert REVIEW_CATEGORIES == ("feature", "fix", "quality", "docs", "tooling", "other")
    assert isinstance(parse_conventional("feat: x", "feat: x\n"), Conventional)
