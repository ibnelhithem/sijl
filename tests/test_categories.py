"""`categories.test_suites`: repositories the user names as test suites are read like the ones
whose names end in `-tests` or `-test` — whatever category review_category() gives a commit
there, the user's own judgement, it is the same for both."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl.classify import categoriser, parse_conventional, review_category
from sijl.cli import app
from sijl.models import Repo
from tests.gitfactory import ME, PERIOD_ARGS


def repo(name: str) -> Repo:
    return Repo(name, Path(f"/r/{name}"), Path(f"/r/{name}/.git"), False)


SUBJECTS = ["test: cover retry", "test(e2e): new billing flow", "feat: x", "fix: y", "chore: z"]


def test_without_test_suites_the_categoriser_is_review_category_itself() -> None:
    assert categoriser() is review_category
    assert categoriser([]) is review_category


@pytest.mark.parametrize("subject", SUBJECTS)
def test_a_named_repository_is_read_like_one_named_tests(subject: str) -> None:
    commit = parse_conventional(subject, f"{subject}\n")
    named = categoriser(["e2e-suite"])
    assert named(commit, repo("e2e-suite")) == review_category(commit, repo("e2e-tests"))
    assert named(commit, repo("api")) == review_category(commit, repo("api"))  # not named
    assert named(commit, repo("e2e-suite-2")) == review_category(commit, repo("api"))


def test_the_report_reads_test_suites_from_the_settings(make_repo) -> None:
    """End to end: the same history, once in a repository named only through the setting and
    once in one whose name ends in -tests, gets the same review categories."""

    def categories(name: str, env: dict[str, str]) -> list[str]:
        built = make_repo(name)
        built.commit("test: cover the export", at="2026-05-04T10:00:00+02:00")
        built.commit("feat: the export", at="2026-05-05T10:00:00+02:00")
        args = ["report", "--repo", str(built.path), "--author-email", ME.email, *PERIOD_ARGS]
        result = CliRunner().invoke(app, args, env=env)
        assert result.exit_code == 0, result.output
        text = result.stdout
        table = text.split("By review category:", 1)[1].split("By conventional-commit", 1)[0]
        return [
            line for line in table.splitlines() if line.startswith("| ") and "Share" not in line
        ]

    plain = categories("monorepo", {})
    named = categories("checks", {"SIJL_TEST_SUITES": "checks"})
    suffixed = categories("checks-tests", {})
    assert named == suffixed
    assert named != plain  # the setting changed where the test commit is counted
