"""The config file through the CLI: `sijl report` with no flags once a config exists, flags
still winning, `sijl config show`, and clear errors that point at the key to set."""

from datetime import date

from typer.testing import CliRunner

from sijl import cli
from sijl.cli import app
from tests.gitfactory import COLLEAGUE, ME, REVIEW_PERIOD
from tests.test_cli_report import plain
from tests.test_config import config_file

runner = CliRunner()


def configure(roots: list[str], emails: list[str]) -> None:
    quoted = ", ".join(f'"{r}"' for r in roots)
    mails = ", ".join(f'"{e}"' for e in emails)
    config_file(
        f"[identity]\nemails = [{mails}]\n[scan]\nroots = [{quoted}]\n"
        f'[window]\nsince = "{REVIEW_PERIOD.start}"\nuntil = "{REVIEW_PERIOD.end}"\n'
    )


def test_report_needs_no_flags_once_a_config_exists(make_repo, tmp_path) -> None:
    make_repo("repos/app").commit("feat: from the config", at="2026-05-04T10:00:00+02:00")
    configure([str(tmp_path / "repos")], [ME.email])
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0, result.output
    assert "feat: from the config" in result.stdout


def test_flags_still_win_over_the_config(make_repo, tmp_path) -> None:
    repo = make_repo("repos/app")
    repo.commit("feat: mine", at="2026-05-04T10:00:00+02:00")
    repo.commit("feat: a colleague's", at="2026-05-05T10:00:00+02:00", who=COLLEAGUE)
    configure([str(tmp_path / "repos")], [ME.email])
    result = runner.invoke(app, ["report", "--author-email", COLLEAGUE.email])
    assert "feat: a colleague's" in result.stdout and "feat: mine" not in result.stdout


def test_a_root_flag_replaces_the_configured_roots_and_repos(make_repo, tmp_path) -> None:
    make_repo("configured/app").commit("feat: configured", at="2026-05-04T10:00:00+02:00")
    make_repo("other/app2").commit("feat: from the flag", at="2026-05-04T10:00:00+02:00")
    configure([str(tmp_path / "configured")], [ME.email])
    result = runner.invoke(app, ["report", "--root", str(tmp_path / "other")])
    assert "feat: from the flag" in result.stdout and "feat: configured" not in result.stdout


def test_config_show_names_every_source(tmp_path) -> None:
    configure([str(tmp_path)], [ME.email])
    result = runner.invoke(app, ["config", "show"], env={"SIJL_UNTIL": "2026-06-15"})
    assert result.exit_code == 0, result.output
    rows = {line.split()[0]: line.split()[-1] for line in result.stdout.splitlines()[2:] if line}
    assert rows["identity.emails"] == "file"
    assert rows["window.until"] == "SIJL_UNTIL"
    assert rows["scan.max_depth"] == "default"


def test_a_broken_config_is_a_usage_error_with_a_suggestion(tmp_path) -> None:
    config_file('[scan]\nroot = ["~/code"]\n')
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 2
    assert "did you mean 'scan.roots'" in plain(result.output)


def test_a_missing_value_points_at_the_flag_and_the_config_key() -> None:
    result = runner.invoke(app, ["report", "--since", "2026-04-01"])
    assert result.exit_code == 2
    text = plain(result.output)
    assert "--author-email" in text and "identity.emails" in text


def test_config_show_states_the_period_and_what_replaced_a_lower_one(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 8))
    config_file('[window]\nsince = "2026-04-06"\n')
    result = runner.invoke(app, ["config", "show"], env={"SIJL_HALF": "2026H2"})
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    since = next(line for line in lines if line.startswith("window.since"))
    assert since.split(None, 1)[1].split() == [
        "(not", "set)", "file,", "overridden", "by", "env", "SIJL_HALF"
    ]  # fmt: skip
    assert lines[-1] == (
        "Period: 1 July 2026 to 8 October 2026 — H2 2026 to date (inclusive; by author date, "
        "in each commit's own timezone)"
    )
