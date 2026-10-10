"""`sijl report` with period presets: --last, --quarter, --half and --year choose the
commits; two period choices on one command line exit 2 naming both; a flag replaces a period from
the config file. "Today" is pinned by replacing the CLI's one read of the clock."""

from datetime import date

import pytest
from typer.testing import CliRunner

from sijl import cli
from sijl.cli import app
from tests.gitfactory import ME
from tests.test_cli_report import plain
from tests.test_config import config_file

runner = CliRunner()
TODAY = date(2026, 10, 8)


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch: pytest.MonkeyPatch) -> date:
    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    return TODAY


@pytest.fixture
def history(make_repo, tmp_path):
    """One commit just inside and one just outside each period the tests ask for."""
    repo = make_repo("repos/app")
    for day, subject in [
        ("2026-04-08", "feat: the day before the last 6 months"),
        ("2026-04-09", "feat: first day of the last 6 months"),
        ("2026-06-30", "feat: last day of H1"),
        ("2026-07-01", "feat: first day of Q3"),
        ("2026-09-30", "feat: last day of Q3"),
        ("2026-10-08", "feat: today"),
    ]:
        repo.commit(subject, at=f"{day}T12:00:00+02:00")
    return ["--root", str(tmp_path / "repos"), "--author-email", ME.email]


def words(output: str) -> str:
    """The error as one line: Rich wraps it inside a box drawn with │."""
    return " ".join(plain(output).replace("│", " ").split())


def subjects(stdout: str) -> list[str]:
    return [line.split(" | ")[-1].rstrip(" |") for line in stdout.splitlines() if "| feat:" in line]


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (
            ["--last", "6m"],
            [
                "feat: today",
                "feat: last day of Q3",
                "feat: first day of Q3",
                "feat: last day of H1",
                "feat: first day of the last 6 months",
            ],
        ),
        (["--quarter", "2026Q3"], ["feat: last day of Q3", "feat: first day of Q3"]),
        (["--half", "2026H2"], ["feat: today", "feat: last day of Q3", "feat: first day of Q3"]),
        (["--year", "2026"], 6),
        (["--last", "1d"], ["feat: today"]),
    ],
)
def test_a_preset_chooses_exactly_its_days(history, flags, expected) -> None:
    result = runner.invoke(app, ["report", *history, *flags])
    assert result.exit_code == 0, result.output
    found = subjects(result.stdout)
    if isinstance(expected, int):
        assert len(found) == expected
    else:
        assert sorted(found) == sorted(expected)


@pytest.mark.parametrize(
    ("flags", "named"),
    [
        (["--last", "6m", "--quarter", "2026Q3"], "--last and --quarter both set the period"),
        (["--since", "2026-01-01", "--year", "2026"], "--since and --year both set the period"),
        (["--half", "2026H1", "--until", "2026-03-31"], "--until and --half both set the period"),
    ],
)
def test_conflicting_period_flags_exit_2_naming_both(history, flags, named) -> None:
    result = runner.invoke(app, ["report", *history, *flags])
    assert result.exit_code == 2
    assert named in words(result.output)
    assert result.stdout == ""


def test_a_period_flag_replaces_the_period_in_the_config_file(history) -> None:
    config_file('[window]\nlast = "1d"\n')
    from_file = runner.invoke(app, ["report", *history])
    assert subjects(from_file.stdout) == ["feat: today"]
    from_flag = runner.invoke(app, ["report", *history, "--since", "2026-06-30", "--until",
                                    "2026-07-01"])  # fmt: skip
    assert from_flag.exit_code == 0, from_flag.output
    assert sorted(subjects(from_flag.stdout)) == ["feat: first day of Q3", "feat: last day of H1"]


def test_a_period_that_has_not_started_is_a_usage_error(history) -> None:
    result = runner.invoke(app, ["report", *history, "--quarter", "2027Q1"])
    assert result.exit_code == 2
    assert "'--quarter': Q1 2027 has not started yet (today is 8 October 2026)" in words(
        result.output
    )


def test_a_preset_from_the_file_that_cannot_be_used_names_the_file(history) -> None:
    path = config_file("[window]\nyear = 2027\n")
    result = runner.invoke(app, ["report", *history])
    assert result.exit_code == 2
    text = words(result.output)
    assert "Invalid value for window.year in" in text and "2027 has not started yet" in text
    assert path.name in text  # a long path may be wrapped, but the file's name is whole


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--last", "6 months"], "like 30d, 6w, 6m or 1y"),
        (["--quarter", "Q3"], "like 2026Q3"),
        (["--year", "twenty"], "twenty"),
    ],
)
def test_a_malformed_preset_is_a_usage_error(history, flags, message) -> None:
    result = runner.invoke(app, ["report", *history, *flags])
    assert result.exit_code == 2
    assert message in words(result.output)


def test_no_period_at_all_points_at_every_way_to_give_one(history) -> None:
    result = runner.invoke(app, ["report", *history])
    assert result.exit_code == 2
    text = words(result.output)
    for way in ("--since", "--last", "--quarter", "--half", "--year", "[window]"):
        assert way in text


def test_the_last_100_years_finds_a_1971_commit(make_repo, tmp_path) -> None:
    repo = make_repo("old/app")
    repo.commit("feat: in 1971", at="1971-05-04T10:00:00+01:00")
    repo.commit("feat: in 2026", at="2026-05-04T10:00:00+02:00")
    args = ["report", "--root", str(tmp_path / "old"), "--author-email", ME.email]
    result = runner.invoke(app, [*args, "--last", "100y"])
    assert result.exit_code == 0, result.output
    assert sorted(subjects(result.stdout)) == ["feat: in 1971", "feat: in 2026"]


@pytest.mark.parametrize(
    "flags",
    [
        ["--year", "1"],
        ["--year", "1969"],
        ["--quarter", "0001Q1"],
        ["--half", "0001H1"],
        ["--since", "1969-12-31"],
    ],
)
def test_a_year_before_1970_is_a_usage_error_naming_the_limit(history, flags) -> None:
    result = runner.invoke(app, ["report", *history, *flags])
    assert result.exit_code == 2, result.output
    assert "years 1970 to 9999" in words(result.output)


@pytest.mark.parametrize(
    ("file", "env"),
    [
        ('[window]\nlast = "6m"\n', {}),
        ('[window]\nsince = "2026-01-01"\nuntil = "2026-03-31"\n', {"SIJL_LAST": "1m"}),
    ],
)
def test_until_alone_over_a_configured_preset_asks_for_the_period(history, file, env) -> None:
    config_file(file)
    result = runner.invoke(app, ["report", *history, "--until", "2026-06-30"], env=env)
    assert result.exit_code == 2, result.output
    assert "give the period" in words(result.output)


def test_a_superscript_year_from_the_environment_is_a_usage_error_not_a_crash(history) -> None:
    result = runner.invoke(app, ["report", *history], env={"SIJL_YEAR": "²"})
    assert result.exit_code == 2, result.output
    assert "SIJL_YEAR" in words(result.output)
