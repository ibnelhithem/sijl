"""Sijl's settings: built-in defaults < config file < SIJL_* environment < flags.

Settings are loaded once per command by `load_settings()` and passed down explicitly — no module
reads global state. Every value remembers where it came from, so `sijl config
show` can say why a run used it. The file is TOML at `$XDG_CONFIG_HOME/sijl/config.toml`
(usually `~/.config/sijl/config.toml`), or wherever `--config` / `SIJL_CONFIG` point.
Unknown keys are refused with a did-you-mean, and nothing that looks like a secret is ever read
from the file: secrets belong in the environment.

The period is one choice with several spellings — `since`/`until`, `last`, `quarter`, `half`,
`year`. Each layer may name it one way only, and a layer that names it replaces
the period of every layer below, as soon as it is applied; `since` and `until` still combine
across layers that have not been replaced.
"""

from __future__ import annotations

import difflib
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path

from sijl.ai import ANSWER_LIMIT, ANSWER_LIMITS, DEFAULT_MODEL
from sijl.models import DEDUPE_MODES, HOST_NAME
from sijl.tickets import PROJECT, STANDARDS
from sijl.window import (
    COUNTING_RULE,
    NoPeriod,
    Window,
    check_year,
    parse_day,
    parse_half,
    parse_last,
    parse_quarter,
    resolve,
)


class ConfigError(ValueError):
    """A setting is wrong; the message names the key and where the value came from."""


class InvalidSetting(ConfigError):
    """Raised by `Settings` itself, which knows the key but not where the value came from."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(message)
        self.key = key


@dataclass(frozen=True)
class Setting:
    key: str  # where it lives in the file: "scan.roots"
    attr: str  # the Settings field: "roots"
    kind: str  # "str", "list", "int" or "bool"
    env: str  # the environment variable that overrides the file
    flag: str  # the command-line flag that overrides everything ("" when there is none)
    help: str


SETTINGS = (
    Setting(
        "person.name",
        "name",
        "str",
        "SIJL_NAME",
        "",
        "Your name as the report's title shows it (default: the name in your commits)",
    ),
    Setting(
        "identity.emails",
        "emails",
        "list",
        "SIJL_EMAILS",
        "--author-email",
        "Every email address you commit with, including GitHub's noreply address",
    ),
    Setting(
        "scan.roots",
        "roots",
        "list",
        "SIJL_ROOTS",
        "--root",
        "Folders to search for repositories",
    ),
    Setting(
        "scan.repos",
        "repos",
        "list",
        "SIJL_REPOS",
        "--repo",
        "Repositories to include directly",
    ),
    Setting(
        "scan.max_depth",
        "max_depth",
        "int",
        "SIJL_MAX_DEPTH",
        "",
        "How many folder levels to search below each root",
    ),
    Setting(
        "scan.include_worktrees",
        "include_worktrees",
        "bool",
        "SIJL_INCLUDE_WORKTREES",
        "--include-worktrees",
        "Also read commits that exist only in a detached linked worktree",
    ),
    Setting(
        "scan.dedupe",
        "dedupe",
        "str",
        "SIJL_DEDUPE",
        "--dedupe",
        "How copies of one commit are matched: key (author, author time, subject) or patch-id",
    ),
    Setting(
        "jira.projects",
        "jira_projects",
        "list",
        "SIJL_JIRA_PROJECTS",
        "--jira-project",
        "Your Jira project keys, like PROJ: only their keys count as tickets (none: suggestions)",
    ),
    Setting(
        "categories.test_suites",
        "test_suites",
        "list",
        "SIJL_TEST_SUITES",
        "",
        "Repositories (folder names) whose tests are the deliverable: there `test:` is a feature",
    ),
    Setting(
        "window.since",
        "since",
        "str",
        "SIJL_SINCE",
        "--since",
        "First day of the period, YYYY-MM-DD",
    ),
    Setting(
        "window.until",
        "until",
        "str",
        "SIJL_UNTIL",
        "--until",
        "Last day of the period, YYYY-MM-DD (empty: today)",
    ),
    Setting(
        "window.last",
        "last",
        "str",
        "SIJL_LAST",
        "--last",
        "A period that ends today, today included: 30d, 6w, 6m or 1y",
    ),
    Setting(
        "window.quarter",
        "quarter",
        "str",
        "SIJL_QUARTER",
        "--quarter",
        "A calendar quarter such as 2026Q3; one still running stops at today",
    ),
    Setting(
        "window.half",
        "half",
        "str",
        "SIJL_HALF",
        "--half",
        "A half year: 2026H1 (January to June) or 2026H2 (July to December)",
    ),
    Setting(
        "window.year",
        "year",
        "int",
        "SIJL_YEAR",
        "--year",
        "A calendar year such as 2026; one still running stops at today",
    ),
    Setting(
        "forges.github_hosts",
        "github_hosts",
        "list",
        "SIJL_GITHUB_HOSTS",
        "",
        "GitHub Enterprise hosts, besides github.com, whose commits get links",
    ),
    Setting(
        "forges.gitlab_hosts",
        "gitlab_hosts",
        "list",
        "SIJL_GITLAB_HOSTS",
        "",
        "Self-hosted GitLab hosts, besides gitlab.com, whose commits get links",
    ),
    Setting(
        "forges.ssh_aliases",
        "ssh_aliases",
        "bool",
        "SIJL_SSH_ALIASES",
        "",
        "Ask `ssh -G` which host an ssh alias stands for (reads your ssh config, never connects)",
    ),
    Setting(
        "ai.enabled",
        "ai_enabled",
        "bool",
        "SIJL_AI",
        "--no-ai",
        "Use Claude when its credentials are found; false (or SIJL_AI=off) turns AI off",
    ),
    Setting(
        "ai.confirm",
        "ai_confirm",
        "bool",
        "SIJL_AI_CONFIRM",
        "--yes",
        "Show what will be sent to Claude and ask before sending; --yes answers for one command",
    ),
    Setting(
        "ai.model",
        "ai_model",
        "str",
        "SIJL_AI_MODEL",
        "",
        "The Claude model AI commands use",
    ),
    Setting(
        "ai.redact",
        "ai_redact",
        "bool",
        "SIJL_AI_REDACT",
        "",
        "Replace colleagues' names and email addresses before anything is sent to Claude",
    ),
    Setting(
        "ai.answer_limit",
        "ai_answer_limit",
        "int",
        "SIJL_AI_ANSWER_LIMIT",
        "--answer-limit",
        "The longest answer `sijl ask` may get, in tokens, Claude's thinking included",
    ),
)
_BY_KEY = {s.key: s for s in SETTINGS}
_BY_ATTR = {s.attr: s for s in SETTINGS}
_RANGE = ("since", "until")  # together one way to choose the period
PERIOD_FIELDS = (*_RANGE, "last", "quarter", "half", "year")  # registry order
_PARSE = {
    "since": parse_day,
    "until": parse_day,
    "last": parse_last,
    "quarter": parse_quarter,
    "half": parse_half,
    "year": check_year,
}
_YES, _NO = {"true", "yes", "on", "1"}, {"false", "no", "off", "0"}
# ASCII only: str.isdigit() also takes "²", which int() refuses, and "٣", which it reads as 3.
_DIGITS = re.compile(r"[0-9]+")
_SECRET = re.compile(r"token|secret|passw(or)?d|api[_-]?key|credential", re.IGNORECASE)


def _is_set(value: object) -> bool:
    return value not in ("", None)


def _choices(given: Iterable[Setting]) -> list[tuple[Setting, ...]]:
    """The ways one layer chose the period, in registry order: `since` and `until` together are
    one choice, each preset is one."""
    given = set(given)
    named = [s for s in SETTINGS if s in given and s.attr in PERIOD_FIELDS]
    ranges = tuple(s for s in named if s.attr in _RANGE)
    return ([ranges] if ranges else []) + [(s,) for s in named if s.attr not in _RANGE]


def _conflict(choices: list[tuple[Setting, ...]], name: Callable[[Setting], str]) -> str:
    """'--last and --quarter both set the period — choose one', in one layer's own words."""
    labels = ["/".join(name(s) for s in choice) for choice in choices]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]} both set the period — choose one"
    return f"{', '.join(labels[:-1])} and {labels[-1]} all set the period — choose one"


@dataclass(frozen=True)
class Settings:
    name: str = ""
    emails: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()  # as written; "~" is expanded where they are used
    repos: tuple[str, ...] = ()
    max_depth: int = 4
    include_worktrees: bool = False
    dedupe: str = "key"
    jira_projects: tuple[str, ...] = ()  # project keys as Jira writes them: "PROJ"
    test_suites: tuple[str, ...] = ()  # repository folder names, besides *-tests and *-test
    since: str = ""
    until: str = ""
    last: str = ""
    quarter: str = ""
    half: str = ""
    year: int | None = None
    github_hosts: tuple[str, ...] = ()  # besides github.com
    gitlab_hosts: tuple[str, ...] = ()  # besides gitlab.com
    ssh_aliases: bool = True  # resolve ssh host aliases with `ssh -G`
    ai_enabled: bool = True
    ai_confirm: bool = True
    ai_model: str = DEFAULT_MODEL
    ai_redact: bool = True
    ai_answer_limit: int = ANSWER_LIMIT

    def __post_init__(self) -> None:
        for attr, parse in _PARSE.items():
            value = getattr(self, attr)
            if _is_set(value):
                key = _BY_ATTR[attr].key
                try:
                    parse(value)
                except ValueError as exc:
                    raise InvalidSetting(key, f"{key} — {exc}") from None
        choices = _choices([_BY_ATTR[a] for a in PERIOD_FIELDS if _is_set(getattr(self, a))])
        if len(choices) > 1:
            raise InvalidSetting(choices[-1][0].key, _conflict(choices, lambda s: s.key))
        if self.max_depth < 1:
            raise InvalidSetting("scan.max_depth", "scan.max_depth must be at least 1")
        if self.dedupe not in DEDUPE_MODES:
            modes = ", ".join(DEDUPE_MODES)
            raise InvalidSetting("scan.dedupe", f"scan.dedupe must be one of {modes}")
        for attr in ("github_hosts", "gitlab_hosts"):
            key = _BY_ATTR[attr].key
            if not all(HOST_NAME.fullmatch(host) for host in getattr(self, attr)):
                # The value is not repeated: someone may have pasted a URL with a token in it.
                raise InvalidSetting(
                    key,
                    f"{key} — each entry must be a host name alone, like git.example.com (no "
                    "https://, user, port or path)",
                )
        both = {h.lower() for h in self.github_hosts} & {h.lower() for h in self.gitlab_hosts}
        if both:
            raise InvalidSetting(
                "forges.gitlab_hosts",
                f"forges.github_hosts and forges.gitlab_hosts both list {', '.join(sorted(both))}",
            )
        low, high = ANSWER_LIMITS
        if not low <= self.ai_answer_limit <= high:
            raise InvalidSetting(
                "ai.answer_limit",
                f"ai.answer_limit must be between {low:,} and {high:,} tokens (Claude's thinking "
                "counts toward it)",
            )
        if not self.ai_model:
            raise InvalidSetting(
                "ai.model", f"ai.model must name a Claude model, e.g. {DEFAULT_MODEL}"
            )
        for project in self.jira_projects:
            if not PROJECT.fullmatch(project):
                near = project.split("-")[0].upper()  # "proj" or "PROJ-1" → PROJ
                like = near if PROJECT.fullmatch(near) and near not in STANDARDS else "PROJ"
                raise InvalidSetting(
                    "jira.projects",
                    f"jira.projects — {project!r} is not a Jira project key: a capital letter, "
                    f"then capitals, digits or _, like {like}",
                )
            if project in STANDARDS:
                raise InvalidSetting(
                    "jira.projects",
                    f"jira.projects — {project!r} is a standard or a well-known name ({project}-…, "
                    "as in UTF-8 or SHA-256) and never counts as a ticket project",
                )

        for name in self.test_suites:
            if not name.strip() or "/" in name or "\\" in name:
                raise InvalidSetting(
                    "categories.test_suites",
                    f"categories.test_suites — {name!r} is not a repository's folder name: write "
                    "the name alone, like e2e-suite (no path)",
                )

    def window(self, today: date) -> Window:
        """The period these settings choose, on `today` (raises NoPeriod, or ValueError for a
        period that cannot be used, such as a quarter that has not started)."""
        return resolve(today=today, **{f: getattr(self, f) for f in PERIOD_FIELDS})


@dataclass(frozen=True)
class Loaded:
    settings: Settings
    # key → "default" | "file" | "env SIJL_X" | "flag --x"; a period value another layer
    # replaced keeps its origin and adds ", overridden by flag --last"
    sources: dict[str, str]
    path: Path  # the config file that was looked for
    found: bool


def config_path(explicit: Path | None, env: Mapping[str, str]) -> tuple[Path, bool]:
    """The file to read, and whether the user named it (a named file must exist)."""
    if explicit is not None:
        return explicit.expanduser(), True
    if env.get("SIJL_CONFIG"):
        return Path(env["SIJL_CONFIG"]).expanduser(), True
    base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME") or Path.home()) / ".config")
    return Path(base).expanduser() / "sijl" / "config.toml", False


def _did_you_mean(key: str) -> str:
    match = difflib.get_close_matches(key, sorted(_BY_KEY), n=1, cutoff=0.6)
    return f" — did you mean {match[0]!r}?" if match else ""


def read_file(path: Path) -> dict[str, object]:
    """`[section] key = value` flattened to {"section.key": value}; unknown keys and anything
    secret-looking are refused before a single value is used."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML ({exc})") from None
    flat: dict[str, object] = {}
    for section, table in data.items():
        if not isinstance(table, dict):
            raise ConfigError(
                f"{path}: {section!r} must be inside a [section]{_did_you_mean(section)}"
            )
        for name, value in table.items():
            key = f"{section}.{name}"
            if _SECRET.search(name) and not name.endswith("_env"):
                raise ConfigError(
                    f"{path}: {key!r} looks like a secret — Sijl never reads secrets from the "
                    "config file; keep it in an environment variable"
                )
            if key not in _BY_KEY:
                raise ConfigError(f"{path}: unknown setting {key!r}{_did_you_mean(key)}")
            flat[key] = value
    return flat


def _from_env(setting: Setting, raw: str) -> object:
    """Environment values are always text: lists are comma-separated, numbers are digits,
    yes/no values the usual words."""
    if setting.kind == "list":
        return [part.strip() for part in raw.split(",") if part.strip()]
    if setting.kind == "int" and _DIGITS.fullmatch(raw.strip()):
        return int(raw)
    if setting.kind == "bool" and raw.strip().lower() in _YES | _NO:
        return raw.strip().lower() in _YES
    return raw  # anything else is checked, and refused if wrong, by _coerce


def _coerce(setting: Setting, value: object, where: str) -> object:
    """Strict: a value must already have the type its setting needs (TOML and flags are typed)."""
    if setting.kind == "list":
        if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
            raise ConfigError(
                f'{where}: {setting.key} must be a list of strings, like ["you@company.com"]'
            )
        return tuple(value)
    if setting.kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(f"{where}: {setting.key} must be a whole number")
        return value
    if setting.kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"{where}: {setting.key} must be true or false")
        return value
    if not isinstance(value, str):
        raise ConfigError(f"{where}: {setting.key} must be text")
    return value.strip()


def load_settings(
    *, config: Path | None, env: Mapping[str, str], flags: Mapping[str, object]
) -> Loaded:
    """Settings for one command. `flags` maps Settings fields to values; None means "not given"."""
    path, named = config_path(config, env)
    found = path.is_file()
    if named and not found:
        raise ConfigError(f"config file {path} does not exist")
    defaults = Settings()
    values = {f.name: getattr(defaults, f.name) for f in fields(Settings)}
    sources = {s.key: "default" for s in SETTINGS}
    where = {s.key: "default" for s in SETTINGS}  # how an error names the origin of a value

    def put(setting: Setting, value: object, source: str, origin: str) -> None:
        if setting.attr in _PARSE and _is_set(value):  # checked here: a lower layer's period
            try:  # may be replaced below, but a malformed one is still an error
                _PARSE[setting.attr](value)
            except ValueError as exc:
                raise ConfigError(f"{origin}: {setting.key} — {exc}") from None
        values[setting.attr] = value
        sources[setting.key], where[setting.key] = source, origin

    def one_period(given: list[Setting], name: Callable[[Setting], str], prefix: str) -> None:
        """After each layer: refuse two choices in it; a choice replaces the lower layers' at
        once, so a period a middle layer replaced can never come back."""
        choices = _choices(s for s in given if _is_set(values[s.attr]))
        if len(choices) > 1:
            raise ConfigError(prefix + _conflict(choices, name))
        if choices:
            _keep_one_period(choices[0], values, sources, defaults)

    if found:
        given = []
        for key, raw in read_file(path).items():
            setting = _BY_KEY[key]
            put(setting, _coerce(setting, raw, str(path)), "file", str(path))
            given.append(setting)
        one_period(given, lambda s: s.key, f"{path}: ")
    given = []
    for setting in SETTINGS:
        raw = env.get(setting.env, "")
        if raw.strip():
            value = _coerce(setting, _from_env(setting, raw), f"${setting.env}")
            put(setting, value, f"env {setting.env}", f"${setting.env}")
            given.append(setting)
    one_period(given, lambda s: f"${s.env}", "")
    given = []
    for attr, raw in flags.items():
        if raw is None:
            continue
        setting = _BY_ATTR[attr]
        put(setting, _coerce(setting, raw, setting.flag), f"flag {setting.flag}", setting.flag)
        given.append(setting)
    one_period(given, lambda s: s.flag, "")
    try:
        settings = Settings(**values)
    except InvalidSetting as exc:
        raise ConfigError(f"{where[exc.key]}: {exc}") from None
    return Loaded(settings=settings, sources=sources, path=path, found=found)


def _keep_one_period(
    winner: tuple[Setting, ...], values: dict, sources: dict[str, str], defaults: Settings
) -> None:
    """A layer's period choice wins over the layers below it: every other way they chose the
    period is reset to its default, and its source says what replaced it. Run after each layer,
    so the highest layer's choice is the one left. Only values that are set are
    reset — an empty one (`since = ""`) chose nothing, so nothing replaced it. `since` and
    `until` are one choice, so a lower layer's `until` still completes a higher layer's
    `since`."""
    keep = _RANGE if winner[0].attr in _RANGE else (winner[0].attr,)
    by = sources[winner[0].key]
    for attr in PERIOD_FIELDS:
        key = _BY_ATTR[attr].key
        if attr not in keep and _is_set(values[attr]):
            values[attr] = getattr(defaults, attr)
            sources[key] = f"{sources[key]}, overridden by {by}"


def render_sources(loaded: Loaded, *, today: date) -> str:
    """The text of `sijl config show`: every setting, its value and where it came from, then
    the period those settings resolve to on `today`, with how it counts."""
    state = "found" if loaded.found else "not found — defaults and environment only"
    lines = [f"Config file: {loaded.path} ({state})", ""]
    rows = []
    for setting in SETTINGS:
        value = getattr(loaded.settings, setting.attr)
        if isinstance(value, tuple):
            shown = ", ".join(value) if value else "(none)"
        elif isinstance(value, bool):
            shown = "true" if value else "false"
        else:
            shown = str(value) if _is_set(value) else "(not set)"
        source = loaded.sources[setting.key]
        rows.append((setting.key, shown, source))
    width_key = max(len(r[0]) for r in rows)
    width_value = max(len(r[1]) for r in rows)
    lines += [f"{k:<{width_key}}  {v:<{width_value}}  {s}" for k, v, s in rows]
    lines += ["", f"Period: {_period_line(loaded.settings, today)}"]
    return "\n".join(lines) + "\n"


def _period_line(settings: Settings, today: date) -> str:
    try:
        window = settings.window(today)
    except NoPeriod:
        return (
            "not set — give --since, --last, --quarter, --half or --year, or set one under [window]"
        )
    except ValueError as exc:
        return f"cannot be used — {exc}"
    return f"{window.describe()} ({COUNTING_RULE})"
