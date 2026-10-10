"""sijl.config: defaults < config file < SIJL_* environment < flags, every value with
its source; unknown keys get a did-you-mean; secrets are never read from the file."""

import os
from datetime import date
from pathlib import Path

import pytest

from sijl.config import ConfigError, Settings, load_settings, render_sources
from sijl.window import NoPeriod

TODAY = date(2026, 10, 8)  # config show resolves presets against a fixed day, never the clock


def config_file(text: str) -> Path:
    """Write the user's config where Sijl looks by default (XDG_CONFIG_HOME, set by the
    hermetic fixture to a throwaway home)."""
    path = Path(os.environ["XDG_CONFIG_HOME"]) / "sijl" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def load(env: dict | None = None, **flags):
    return load_settings(config=None, env={**os.environ, **(env or {})}, flags=flags)


def test_without_a_file_every_value_is_a_default() -> None:
    loaded = load()
    assert loaded.settings == Settings()
    assert not loaded.found
    assert set(loaded.sources.values()) == {"default"}


def test_the_file_is_read() -> None:
    path = config_file(
        '[identity]\nemails = ["me@acme.test", "1+me@users.noreply.github.com"]\n'
        '[scan]\nroots = ["~/code/work", "~/code/personal"]\nmax_depth = 3\n'
        '[window]\nsince = "2026-04-06"\n'
    )
    loaded = load()
    assert loaded.found and loaded.path == path
    assert loaded.settings.emails == ("me@acme.test", "1+me@users.noreply.github.com")
    assert loaded.settings.roots == ("~/code/work", "~/code/personal")
    assert (loaded.settings.max_depth, loaded.settings.since) == (3, "2026-04-06")
    assert loaded.sources["identity.emails"] == "file"
    assert loaded.sources["window.until"] == "default"


def test_the_environment_beats_the_file_and_flags_beat_both() -> None:
    config_file('[identity]\nemails = ["file@acme.test"]\n')
    from_env = load(env={"SIJL_EMAILS": "env1@acme.test, env2@acme.test"})
    assert from_env.settings.emails == ("env1@acme.test", "env2@acme.test")
    assert from_env.sources["identity.emails"] == "env SIJL_EMAILS"
    from_flag = load(env={"SIJL_EMAILS": "env1@acme.test"}, emails=["flag@acme.test"])
    assert from_flag.settings.emails == ("flag@acme.test",)
    assert from_flag.sources["identity.emails"] == "flag --author-email"


@pytest.mark.parametrize(
    ("text", "suggestion"),
    [
        ('[scan]\nroot = ["~/code"]\n', "did you mean 'scan.roots'"),
        ('[identiy]\nemails = ["a@b.test"]\n', "did you mean 'identity.emails'"),
    ],
)
def test_unknown_keys_get_a_did_you_mean(text: str, suggestion: str) -> None:
    config_file(text)
    with pytest.raises(ConfigError, match=suggestion):
        load()


@pytest.mark.parametrize("text", ['[identity]\ntoken = "abc"\n', '[ai]\napi_key = "sk-1"\n'])
def test_secrets_are_never_read_from_the_file(text: str) -> None:
    config_file(text)
    with pytest.raises(ConfigError, match="environment variable"):
        load()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[identity]\nemails = "me@acme.test"\n', "identity.emails must be a list of strings"),
        ('[scan]\nmax_depth = "4"\n', "scan.max_depth must be a whole number"),
        ("[scan]\nmax_depth = 0\n", "scan.max_depth must be at least 1"),
        ('[window]\nsince = "06/04/2026"\n', "window.since.*YYYY-MM-DD"),
        ("[scan\n", "config.toml"),
        ('emails = ["x@y.test"]\n', "must be inside a \\[section\\]"),
    ],
)
def test_bad_values_name_the_key_and_the_file(text: str, message: str) -> None:
    path = config_file(text)
    with pytest.raises(ConfigError, match=message) as error:
        load()
    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"max_depth": 0}, "scan.max_depth must be at least 1"),
        ({"since": "2026-13-01"}, "window.since"),
        ({"until": "soon"}, "window.until"),
    ],
)
def test_impossible_values_fail_at_construction(values: dict, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings(**values)


@pytest.mark.parametrize("variable", ["SIJL_YEAR", "SIJL_MAX_DEPTH"])
@pytest.mark.parametrize("raw", ["²", "٣", "2026²"])
def test_a_number_from_the_environment_must_be_ascii_digits(variable: str, raw: str) -> None:
    # str.isdigit() accepts "²", which int() then rejects with a crash; "٣" would pass as 3.
    with pytest.raises(ConfigError, match=rf"\${variable}: .* must be a whole number"):
        load(env={variable: raw})


def test_a_bad_environment_value_names_the_variable() -> None:
    with pytest.raises(ConfigError, match=r"SIJL_SINCE.*YYYY-MM-DD"):
        load(env={"SIJL_SINCE": "yesterday"})


@pytest.mark.parametrize(("raw", "expected"), [("yes", True), ("1", True), ("False", False)])
def test_a_yes_no_setting_reads_the_usual_words_from_the_environment(raw, expected) -> None:
    loaded = load(env={"SIJL_INCLUDE_WORKTREES": raw})
    assert loaded.settings.include_worktrees is expected
    assert loaded.sources["scan.include_worktrees"] == "env SIJL_INCLUDE_WORKTREES"


def test_a_yes_no_setting_refuses_anything_else() -> None:
    with pytest.raises(ConfigError, match=r"SIJL_INCLUDE_WORKTREES.*true or false"):
        load(env={"SIJL_INCLUDE_WORKTREES": "maybe"})
    config_file('[scan]\ninclude_worktrees = "true"\n')
    with pytest.raises(ConfigError, match=r"scan\.include_worktrees must be true or false"):
        load()


def test_dedupe_is_key_by_default_and_patch_id_on_request() -> None:
    assert load().settings.dedupe == "key"
    loaded = load(env={"SIJL_DEDUPE": "patch-id"})
    assert loaded.settings.dedupe == "patch-id"
    assert loaded.sources["scan.dedupe"] == "env SIJL_DEDUPE"


def test_dedupe_refuses_an_unknown_mode() -> None:
    with pytest.raises(ConfigError, match=r"scan\.dedupe must be one of key, patch-id"):
        load(dedupe="sha")


def test_an_explicit_config_path_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_settings(config=tmp_path / "missing.toml", env=dict(os.environ), flags={})
    with pytest.raises(ConfigError, match="does not exist"):
        load(env={"SIJL_CONFIG": str(tmp_path / "also-missing.toml")})


def test_render_sources_lists_every_setting_with_its_source() -> None:
    config_file('[identity]\nemails = ["me@acme.test"]\n')
    text = render_sources(load(env={"SIJL_SINCE": "2026-04-06"}), today=TODAY)
    assert "(found)" in text.splitlines()[0]
    rows = {line.split()[0]: line.split()[-1] for line in text.splitlines()[2:] if line.strip()}
    assert rows["identity.emails"] == "file"
    assert rows["window.since"] == "SIJL_SINCE"
    assert rows["scan.max_depth"] == "default"


# ---------------------------------------------------------------------- period presets


def test_presets_are_settings_like_any_other() -> None:
    path = config_file('[window]\nlast = "6m"\n')
    loaded = load()
    assert loaded.settings.last == "6m"
    assert loaded.sources["window.last"] == "file"
    config_file("[window]\nyear = 2026\n")  # a year is a number in the file, as people write it
    assert load().settings.year == 2026
    from_env = load(env={"SIJL_QUARTER": "2026Q3"})
    assert from_env.settings.quarter == "2026Q3"
    assert from_env.sources["window.quarter"] == "env SIJL_QUARTER"
    path.unlink()
    from_flag = load(half="2026H1")
    assert (from_flag.settings.half, from_flag.sources["window.half"]) == ("2026H1", "flag --half")
    assert load(env={"SIJL_YEAR": "2025"}).settings.year == 2025


@pytest.mark.parametrize(
    ("origin", "message"),
    [
        ({"file": '[window]\nlast = "6 months"\n'}, r"config\.toml: window\.last — '6 months'"),
        ({"file": '[window]\nyear = "2026"\n'}, "window.year must be a whole number"),
        ({"env": {"SIJL_HALF": "2026H3"}}, r"\$SIJL_HALF: window\.half — .*2026H1"),
        ({"flags": {"quarter": "2026Q5"}}, r"--quarter: window\.quarter — .*like 2026Q3"),
        ({"flags": {"year": 0}}, r"--year: window\.year — 0 is outside the years 1970 to 9999"),
    ],
)
def test_a_malformed_preset_names_the_key_and_where_it_came_from(origin, message) -> None:
    if "file" in origin:
        config_file(origin["file"])
    with pytest.raises(ConfigError, match=message):
        load(env=origin.get("env"), **origin.get("flags", {}))


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        ({"last": "6m", "quarter": "2026Q3"}, "--last and --quarter both set the period"),
        ({"since": "2026-04-06", "last": "6m"}, "--since and --last both set the period"),
        (
            {"since": "2026-04-06", "until": "2026-06-30", "year": 2026},
            "--since/--until and --year both set the period",
        ),
        ({"until": "2026-06-30", "half": "2026H1"}, "--until and --half both set the period"),
        (
            {"last": "6m", "half": "2026H1", "year": 2026},
            "--last, --half and --year all set the period",
        ),
    ],
)
def test_two_period_choices_on_one_command_line_are_refused(flags, message) -> None:
    with pytest.raises(ConfigError, match=message + " — choose one"):
        load(**flags)


def test_two_period_choices_in_the_file_or_the_environment_are_refused() -> None:
    path = config_file('[window]\nsince = "2026-04-06"\nlast = "6m"\n')
    with pytest.raises(ConfigError, match=r"window\.since and window\.last both set") as error:
        load()
    assert str(path) in str(error.value)
    path.unlink()
    both = {"SIJL_LAST": "6m", "SIJL_YEAR": "2026"}
    with pytest.raises(ConfigError, match=r"\$SIJL_LAST and \$SIJL_YEAR both set"):
        load(env=both)


def test_an_empty_until_in_the_file_is_not_a_second_choice() -> None:
    config_file('[window]\nlast = "6m"\nuntil = ""\n')
    assert load().settings.last == "6m"


def test_an_empty_period_value_is_never_labelled_overridden() -> None:
    # An empty value chooses nothing, so nothing replaced it: "file, overridden by file" was false.
    config_file('[window]\nsince = ""\nlast = "6m"\n')
    loaded = load()
    assert (loaded.sources["window.since"], loaded.sources["window.last"]) == ("file", "file")
    config_file('[window]\nuntil = ""\n')
    assert load(last="6m").sources["window.until"] == "file"


def test_a_period_flag_replaces_the_period_from_the_file() -> None:
    config_file('[window]\nsince = "2026-04-06"\nuntil = "2026-06-30"\n')
    loaded = load(last="6m")
    assert (loaded.settings.since, loaded.settings.until, loaded.settings.last) == ("", "", "6m")
    assert loaded.sources["window.since"] == "file, overridden by flag --last"
    assert loaded.sources["window.until"] == "file, overridden by flag --last"
    config_file('[window]\nlast = "6m"\n')
    loaded = load(since="2026-01-01", until="2026-03-31")
    assert (loaded.settings.last, loaded.settings.since) == ("", "2026-01-01")
    assert loaded.sources["window.last"] == "file, overridden by flag --since"


def test_the_environment_replaces_the_file_s_period_and_a_flag_replaces_both() -> None:
    config_file('[window]\nlast = "6m"\n')
    loaded = load(env={"SIJL_QUARTER": "2026Q3"})
    assert (loaded.settings.last, loaded.settings.quarter) == ("", "2026Q3")
    assert loaded.sources["window.last"] == "file, overridden by env SIJL_QUARTER"
    loaded = load(env={"SIJL_QUARTER": "2026Q3"}, year=2025)
    assert (loaded.settings.last, loaded.settings.quarter, loaded.settings.year) == ("", "", 2025)


def test_a_period_replaced_by_a_middle_layer_stays_replaced() -> None:
    # The file's range is replaced by the environment's preset, which the flag replaces in turn:
    # the file's since must not come back to complete the flag's --until.
    config_file('[window]\nsince = "2026-01-01"\nuntil = "2026-03-31"\n')
    loaded = load(env={"SIJL_LAST": "1m"}, until="2026-06-30")
    s = loaded.settings
    assert (s.since, s.until, s.last) == ("", "2026-06-30", "")
    assert loaded.sources["window.since"] == "file, overridden by env SIJL_LAST"
    assert loaded.sources["window.last"] == "env SIJL_LAST, overridden by flag --until"
    with pytest.raises(NoPeriod):  # what config show says is what the report uses
        s.window(TODAY)


def test_the_mirror_case_keeps_the_flag_s_since_and_drops_the_file_s_until() -> None:
    config_file('[window]\nuntil = "2026-03-31"\n')
    loaded = load(env={"SIJL_QUARTER": "2026Q3"}, since="2026-01-01")
    s = loaded.settings
    assert (s.since, s.until, s.quarter) == ("2026-01-01", "", "")
    assert loaded.sources["window.until"] == "file, overridden by env SIJL_QUARTER"
    window = s.window(TODAY)
    assert (window.start, window.end) == (date(2026, 1, 1), TODAY)


def test_since_and_until_still_combine_across_layers() -> None:
    config_file('[window]\nsince = "2026-04-06"\n')
    loaded = load(until="2026-06-30")
    assert (loaded.settings.since, loaded.settings.until) == ("2026-04-06", "2026-06-30")
    assert loaded.sources["window.since"] == "file"


@pytest.mark.parametrize(
    "values",
    [{"last": "6m", "quarter": "2026Q3"}, {"since": "2026-01-01", "year": 2026}],
)
def test_a_settings_object_never_holds_two_period_choices(values) -> None:
    with pytest.raises(ConfigError, match="both set the period"):
        Settings(**values)


def test_an_unset_year_is_shown_as_not_set() -> None:
    rows = render_sources(load(), today=TODAY).splitlines()
    assert any(row.split()[:3] == ["window.year", "(not", "set)"] for row in rows)


@pytest.mark.parametrize(
    ("text", "period"),
    [
        (
            '[window]\nlast = "6m"\n',
            "Period: 9 April 2026 to 8 October 2026 — the last 6 months (inclusive; by author "
            "date, in each commit's own timezone)",
        ),
        (
            '[window]\nsince = "2026-04-06"\n',
            "Period: 6 April 2026 to 8 October 2026 (inclusive; by author date, in each "
            "commit's own timezone)",
        ),
        (
            "[window]\nyear = 2027\n",
            "Period: cannot be used — 2027 has not started yet (today is 8 October 2026)",
        ),
        (
            "[scan]\nmax_depth = 3\n",
            "Period: not set — give --since, --last, --quarter, --half or --year, or set one "
            "under [window]",
        ),
    ],
)
def test_config_show_ends_with_the_period_it_resolves_to(text: str, period: str) -> None:
    config_file(text)
    assert render_sources(load(), today=TODAY).splitlines()[-1] == period


def test_jira_projects_are_none_by_default_and_read_from_every_layer() -> None:
    assert load().settings.jira_projects == ()
    config_file('[jira]\nprojects = ["PROJ", "OPS"]\n')
    from_file = load()
    assert from_file.settings.jira_projects == ("PROJ", "OPS")
    assert from_file.sources["jira.projects"] == "file"
    from_env = load(env={"SIJL_JIRA_PROJECTS": "TASK, PROJ_SUB"})
    assert from_env.settings.jira_projects == ("TASK", "PROJ_SUB")
    assert from_env.sources["jira.projects"] == "env SIJL_JIRA_PROJECTS"
    from_flag = load(jira_projects=["X2"])
    assert from_flag.settings.jira_projects == ("X2",)
    assert from_flag.sources["jira.projects"] == "flag --jira-project"


@pytest.mark.parametrize(
    ("project", "message"),
    [
        ("proj", r"jira\.projects — 'proj' is not a Jira project key.*like PROJ"),
        ("PROJ-1", r"jira\.projects — 'PROJ-1' is not a Jira project key.*like PROJ"),
        ("B", r"jira\.projects — 'B' is not a Jira project key.*like PROJ"),
        ("UTF", r"jira\.projects — 'UTF' is a standard or a well-known name"),
    ],
)
def test_jira_projects_refuses_what_cannot_be_a_project(project: str, message: str) -> None:
    with pytest.raises(ConfigError, match=f"--jira-project: {message}"):
        load(jira_projects=[project])


def test_config_show_lists_the_jira_projects_and_their_source() -> None:
    config_file('[jira]\nprojects = ["PROJ", "OPS"]\n')
    rows = {
        line.split()[0]: line.split()[1:]
        for line in render_sources(load(), today=TODAY).splitlines()[2:]
        if line.strip()
    }
    assert rows["jira.projects"] == ["PROJ,", "OPS", "file"]


def test_test_suites_are_none_by_default_and_read_from_the_file_and_the_environment() -> None:
    assert load().settings.test_suites == ()
    assert load().sources["categories.test_suites"] == "default"
    config_file('[categories]\ntest_suites = ["e2e-suite", "checks"]\n')
    from_file = load()
    assert from_file.settings.test_suites == ("e2e-suite", "checks")
    assert from_file.sources["categories.test_suites"] == "file"
    from_env = load(env={"SIJL_TEST_SUITES": "checks, smoke"})
    assert from_env.settings.test_suites == ("checks", "smoke")
    assert from_env.sources["categories.test_suites"] == "env SIJL_TEST_SUITES"


@pytest.mark.parametrize("name", ["", "  ", "~/code/e2e-suite", "group/qa", "C:\\repos\\qa"])
def test_test_suites_take_folder_names_only(name: str) -> None:
    config_file(f"[categories]\ntest_suites = [{name!r}]\n".replace("'", '"').replace("\\", "\\\\"))
    with pytest.raises(ConfigError, match=r"categories\.test_suites — .* is not a repository's"):
        load()


def test_config_show_lists_the_test_suites_and_their_source() -> None:
    config_file('[categories]\ntest_suites = ["e2e-suite"]\n')
    rows = {
        line.split()[0]: line.split()[1:]
        for line in render_sources(load(), today=TODAY).splitlines()[2:]
        if line.strip()
    }
    assert rows["categories.test_suites"] == ["e2e-suite", "file"]
