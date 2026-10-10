"""`sijl digest` end to end: the digest — and with --with-prompt the instructions before
it — on stdout or in --out, nothing else there; its size, a local token estimate, the redaction
and anything a --max-chars budget omitted on stderr; no network, ever."""

import re
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl import ai, cli, digest
from sijl.cli import app
from sijl.git import GitRunner
from sijl.report import build_report
from tests.gitfactory import COLLEAGUE, ME, ME_GITHUB, PERIOD_ARGS, REVIEW_PERIOD
from tests.test_cli_report import plain
from tests.test_digest import MINE, three_squashes, webapp

runner = CliRunner()
TODAY = date(2026, 10, 8)


@pytest.fixture
def repos(make_repo, tmp_path, monkeypatch) -> list[Path]:
    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    return [webapp(make_repo, tmp_path)[0].path, three_squashes(make_repo, tmp_path)[0].path]


def args(repos: list[Path], *more: str) -> list[str]:
    where = [arg for path in repos for arg in ("--repo", str(path))]
    who = ["--author-email", ME.email, "--author-email", ME_GITHUB.email]
    return ["digest", *where, *who, *PERIOD_ARGS, "--jira-project", "PROJ", *more]


def library(repos: list[Path], **options) -> digest.Digest:
    """The same digest, built by the library."""
    report = build_report(roots=[], paths=repos, identities=MINE, window=REVIEW_PERIOD,
                          git=GitRunner(), generated_on=TODAY, jira_projects=["PROJ"])  # fmt: skip
    git = GitRunner()
    redactor = None if options.pop("no_redact", False) else digest.redactor_for(report, git=git)
    return digest.build(report, redactor=redactor, **options)


def test_the_digest_goes_to_stdout_and_its_size_to_stderr(repos, network) -> None:
    result = runner.invoke(app, args(repos))
    assert result.exit_code == 0, result.output
    expected = library(repos)
    assert result.stdout == expected.text
    tokens = ai.estimate_tokens(result.stdout)
    assert result.stderr == (
        f"Digest: {len(result.stdout):,} characters, about {tokens:,} tokens (estimated on this "
        "computer: nothing was sent), 20 ids to cite — 11 commits, 6 requests, 3 tickets.\n"
        "Redaction: on — 3 names and 2 email addresses replaced (Sijl knows 6 names and "
        "user names of other people from your repositories' history); only yours are kept "
        "(--no-redact keeps them all).\n"
        "Nothing was omitted.\n"
    )
    assert (network.attempts, network.used) == ([], [])  # no connection, no lookup


def test_with_prompt_the_instructions_come_first(repos) -> None:
    result = runner.invoke(app, args(repos, "--with-prompt"))
    assert result.exit_code == 0, result.output
    assert result.stdout == f"{digest.PROMPT}\n{library(repos).text}"
    assert 'say "not in the data"' in result.stdout and "Cite the id" in result.stdout


def test_out_writes_the_file_and_says_how_to_ask(repos, tmp_path) -> None:
    target = tmp_path / "digest.txt"
    result = runner.invoke(app, args(repos, "--with-prompt", "--out", str(target)))
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert target.read_text(encoding="utf-8") == f"{digest.PROMPT}\n{library(repos).text}"
    assert f"Wrote {target}" in result.stderr
    assert f"claude 'Read {target} and answer: …'" in result.stderr


def test_no_redact_keeps_names_and_addresses_as_written(repos) -> None:
    result = runner.invoke(app, args(repos, "--no-redact"))
    assert result.exit_code == 0, result.output
    assert result.stdout == library(repos, no_redact=True).text
    assert "sam@sam-laptop" in result.stdout and COLLEAGUE.name in result.stdout
    assert "Redaction: off (--no-redact) — names and email addresses are as written.\n" in (
        result.stderr
    )


def test_max_chars_bounds_everything_written(repos) -> None:
    whole = len(library(repos).text)
    for extra in ([], ["--with-prompt"]):
        budget = whole - 600 + (len(digest.PROMPT) + 1 if extra else 0)
        result = runner.invoke(app, args(repos, "--max-chars", str(budget), *extra))
        assert result.exit_code == 0, result.output
        assert len(result.stdout) <= budget
        assert "more omitted …]" in result.stdout
        assert f"Omitted to fit --max-chars {budget:,}: " in result.stderr


def test_a_max_chars_too_small_is_a_usage_error(repos) -> None:
    result = runner.invoke(app, args(repos, "--max-chars", "300", "--with-prompt"))
    assert result.exit_code == 2
    said = re.sub(r"[\s\u2502]+", " ", plain(result.output))  # the panel's borders, wrapped
    assert re.search(
        r"--max-chars 300 is too small: the smallest digest that fits, with the instructions, "
        r"takes [0-9,]+ characters",
        said,
    )
    smallest = int(re.search(r"takes ([0-9,]+) characters", said)[1].replace(",", ""))
    fits = runner.invoke(app, args(repos, "--max-chars", str(smallest), "--with-prompt"))
    assert fits.exit_code == 0 and len(fits.stdout) <= smallest
    below = runner.invoke(app, args(repos, "--max-chars", str(smallest - 1), "--with-prompt"))
    assert below.exit_code == 2
    zero = runner.invoke(app, args(repos, "--max-chars", "0"))
    assert zero.exit_code == 2


def test_the_note_counts_in_words_that_fit_the_number() -> None:
    one = digest.Digest(
        text="x\n", ids=frozenset({"PROJ-1"}), shas=frozenset(), requests=frozenset(),
        tickets=frozenset({"PROJ-1"}), listed=digest.Counts(0, 0, 1), omitted=digest.Counts(),
        about="", redaction=True, redacted_names=1, redacted_emails=1, projects=("PROJ",),
    )  # fmt: skip
    note = cli._digest_note(one, "x\n", known=1, out=None, max_chars=None)
    assert note.startswith("Digest: 2 characters, about 1 token (estimated on this computer: ")
    assert "1 id to cite — 0 commits, 0 requests, 1 ticket." in note
    assert "Redaction: on — 1 name and 1 email address replaced (Sijl knows 1 name and" in note


def test_the_note_never_counts_tickets_when_no_jira_project_is_set(repos) -> None:
    """The digest's header says no Jira projects are set; the note agrees. Key-shaped words a
    subject happens to write (UTF-8, PROJ-12 here) are still ids to cite, but no tickets."""
    without = [arg for arg in args(repos) if arg not in ("--jira-project", "PROJ")]
    result = runner.invoke(app, without)
    assert result.exit_code == 0, result.output
    assert "- tickets: no Jira projects set" in result.stdout
    first = result.stderr.splitlines()[0]
    assert re.search(
        r"[0-9]+ ids to cite — [0-9]+ commits, [0-9]+ requests, no tickets \(no Jira projects "
        r"are set\) and [0-9]+ key-like ids? written in subjects or titles\.$",
        first,
    ), first
    assert " 3 tickets" not in first


def test_the_note_names_other_key_like_ids_beside_the_tickets() -> None:
    built = digest.Digest(
        text="x\n", ids=frozenset({"PROJ-1", "UTF-8"}), shas=frozenset(), requests=frozenset(),
        tickets=frozenset({"PROJ-1", "UTF-8"}), listed=digest.Counts(0, 0, 1),
        omitted=digest.Counts(), about="", redaction=False, projects=("PROJ",),
    )  # fmt: skip
    note = cli._digest_note(built, "x\n", known=0, out=None, max_chars=None)
    assert (
        "2 ids to cite — 0 commits, 0 requests, 1 ticket and 1 other key-like id written in "
        "subjects or titles."
    ) in note


# `digest --out` follows report's --out rules (outfile.py): never into a scanned repository
# unless the path is exactly given, never into git's own files.


def test_out_is_refused_inside_a_scanned_repository_unless_exactly_given(
    repos, tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(repos[0])
    relative = runner.invoke(app, args(repos, "--out", "digest.txt"))
    assert relative.exit_code == 2 and relative.stdout == ""
    assert not (repos[0] / "digest.txt").exists()
    assert "would write into the scanned repository webapp" in relative.stderr
    git_dir = runner.invoke(app, args(repos, "--out", str(repos[0] / ".git" / "digest.txt")))
    assert git_dir.exit_code == 2 and not (repos[0] / ".git" / "digest.txt").exists()
    exact = runner.invoke(app, args(repos, "--out", str(repos[0] / "digest.txt")))
    assert exact.exit_code == 0, exact.output
    assert (repos[0] / "digest.txt").read_text(encoding="utf-8") == library(repos).text
