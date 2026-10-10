"""The CLI shell: version reporting, help, and `python -m sijl`."""

import subprocess
import sys

from typer.testing import CliRunner

from sijl import __version__, cli
from sijl.cli import app

runner = CliRunner()


def test_version_flag_prints_the_package_version_on_stdout_only() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == f"sijl {__version__}\n"
    assert result.stderr == ""


def test_version_comes_from_installed_metadata() -> None:
    # `uv sync` installs the package, so the fallback for a never-installed tree is not in use.
    assert __version__ != "0+unknown"


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Summarise your own git work" in result.output


def test_python_dash_m_runs_the_same_cli() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "sijl", "--version"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    assert completed.stdout == f"sijl {__version__}\n"


COMMANDS = (["report"], ["digest"], ["repos"], ["ask"], ["config", "show"], ["ai", "status"])


def help_text(*command: str) -> str:
    result = runner.invoke(app, [*command, "--help"], env={"COLUMNS": "80"})
    assert result.exit_code == 0, result.output
    return result.stdout


def test_help_fits_80_columns_and_never_cuts_a_flag_name() -> None:
    for command in [[], *COMMANDS]:
        text = help_text(*command)
        assert all(len(line) <= 80 for line in text.splitlines()), command
        assert "…" not in text and "..." not in text.replace("[ARGS]...", ""), command
    report = help_text("report")
    for flag in ("--include-worktrees / --no-include-worktrees", "--allow-template-links"):
        assert flag in report


def test_command_help_is_wrapped_by_the_terminal_not_the_source() -> None:
    """A docstring's own line breaks would cut sentences in two: each paragraph is one line,
    wrapped at the terminal's width."""
    listing = help_text()
    assert "report  Summarise the commits you authored in a period, as Markdown." in listing
    assert "digest  Write your period's evidence as compact text with ids to cite." in listing
    for function in (cli.report, cli.digest_command, cli.repos, cli.ask_command, cli.ai_status):
        for paragraph in (function.__doc__ or "").split("\n\n"):
            assert "\n" not in paragraph, function.__name__


def test_report_help_says_what_is_required_and_shows_an_example() -> None:
    text = " ".join(help_text("report").split())
    assert "the email addresses you commit with, a folder to search (or a repository) and a" in text
    assert "set them once in the config file" in text
    assert "Example: sijl report --author-email you@example.com --root ~/code --last 6m" in text
