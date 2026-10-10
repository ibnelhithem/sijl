"""`sijl report` end to end: the Markdown on stdout (or in --out) and nothing else there;
usage errors exit 2, finding no repository exits 3."""

import re

from typer.testing import CliRunner

from sijl.cli import app
from tests.gitfactory import ME, PERIOD_ARGS

runner = CliRunner()
PERIOD = PERIOD_ARGS


def plain(text: str) -> str:
    """What a person reads: on CI (GITHUB_ACTIONS) Typer forces colour, and Rich's ANSI codes
    can split an option name such as --author-email in two."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_the_report_goes_to_stdout_and_nothing_else_does(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    result = runner.invoke(
        app, ["report", "--root", str(tmp_path), "--author-email", ME.email, *PERIOD]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"# Work summary — {ME.name}")
    assert "feat: shipped it" in result.stdout
    assert result.stderr == ""


def test_out_writes_the_file_and_keeps_stdout_empty(make_repo, tmp_path) -> None:
    make_repo("repos/app").commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    target = tmp_path / "review.md"
    result = runner.invoke(app, ["report", "--root", str(tmp_path / "repos"), "--author-email",
                                 ME.email, *PERIOD, "--out", str(target)])  # fmt: skip
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert target.read_text().startswith("# Work summary")
    assert f"Wrote {target}" in result.stderr


def test_a_repo_can_be_named_directly(make_repo) -> None:
    repo = make_repo("app")
    repo.commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    result = runner.invoke(
        app, ["report", "--repo", str(repo.path), "--author-email", ME.email, *PERIOD]
    )
    assert result.exit_code == 0 and "feat: shipped it" in result.stdout


def test_usage_errors_exit_2(tmp_path) -> None:
    no_email = runner.invoke(app, ["report", "--root", str(tmp_path), *PERIOD])
    assert no_email.exit_code == 2 and "--author-email" in plain(no_email.output)
    no_source = runner.invoke(app, ["report", "--author-email", ME.email, *PERIOD])
    assert no_source.exit_code == 2 and "--root" in plain(no_source.output)
    bad_day = runner.invoke(app, ["report", "--root", str(tmp_path), "--author-email", ME.email,
                                  "--since", "06/04/2026"])  # fmt: skip
    assert bad_day.exit_code == 2 and "YYYY-MM-DD" in plain(bad_day.output)
    backwards = runner.invoke(app, ["report", "--root", str(tmp_path), "--author-email", ME.email,
                                    "--since", "2026-07-01", "--until", "2026-06-30"])  # fmt: skip
    assert backwards.exit_code == 2


def test_no_repository_found_exits_3_with_the_reasons(tmp_path) -> None:
    (tmp_path / "empty").mkdir()
    result = runner.invoke(
        app, ["report", "--root", str(tmp_path), "--author-email", ME.email, *PERIOD]
    )
    assert result.exit_code == 3
    assert result.stdout == ""
    assert "no git repository" in result.stderr


def test_a_blank_email_is_a_usage_error_not_a_crash(make_repo, tmp_path) -> None:
    make_repo("app")
    result = runner.invoke(
        app, ["report", "--root", str(tmp_path), "--author-email", "  ", *PERIOD]
    )
    assert result.exit_code == 2 and "email" in plain(result.output)
