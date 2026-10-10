"""The Sijl command line.

stdout carries the command's output and nothing else, so `sijl report > review.md` is always
clean; messages and errors go to stderr. Settings come from `config.load_settings()` — defaults <
config file < SIJL_* environment < flags — once per command. The clock is read
once, by `_today()`, and that day is passed down. Exit codes: 0 done, 1 git missing or
failing, 2 usage or configuration error (including two period choices at once), 3 no repository
found, 4 AI is off, failed, or gave no complete answer (declining to send is not an error),
5 an answer cites an id that is not in the evidence (it is shown, every such id marked).

`report` and `digest` read the same flags (`_scan_flags()`) into the same report
(`_summary()`); `digest` writes the evidence text for Claude Code and tells, on stderr, its size,
a local token estimate, the redaction and what a --max-chars budget left out.

AI commands (`sijl ai …`, `sijl ask`) ask `ai.connect()` first, through
`_connect()`; when AI is off they print the reason and the Tier 1 route. The one client is built
there, before the preview, locally — nothing is sent before the user's yes (`ai.Session`). Their
previews and questions go to stderr, the answer to stdout. `ask` reads the period like `digest`
and asks Claude about the digest (`ask.Conversation`); a preview too long for the terminal goes
whole to a private file (`_PreviewFiles`) and a summary is shown.
"""

import atexit
import contextlib
import inspect
import os
import shlex
import shutil
import signal
import sys
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from sijl import __version__, ai, citations
from sijl import ask as asking
from sijl import digest as digests
from sijl.classify import categoriser
from sijl.config import (
    PERIOD_FIELDS,
    SETTINGS,
    ConfigError,
    Loaded,
    load_settings,
    render_sources,
)
from sijl.discover import discover, listing_json, render_listing
from sijl.git import GitError, GitRunner, SshRunner
from sijl.identity import IdentitySet
from sijl.outfile import OutRefused, places
from sijl.outfile import check as check_out
from sijl.outfile import write as write_out
from sijl.redact import Redactor
from sijl.remotes import Forges
from sijl.render.markdown import CustomTemplate, check, read_template, render_report
from sijl.render.safety import TemplateProblem
from sijl.report import Report, build_report
from sijl.text import printable, printable_block
from sijl.window import NoPeriod

EXIT_GIT, EXIT_USAGE, EXIT_NO_REPOS, EXIT_AI, EXIT_UNVERIFIED = 1, 2, 3, 4, 5

app = typer.Typer(
    name="sijl",
    help="Summarise your own git work for a review period, from your local repos.",
    no_args_is_help=True,
    add_completion=False,
    # Plain help: Rich's option table cuts long flag names (--no-include-worktrees) to fit 80
    # columns, and Typer has no setting to widen it; click's layout wraps instead.
    rich_markup_mode=None,
)
config_app = typer.Typer(
    help="Show Sijl's settings and where each one comes from.", no_args_is_help=True
)
app.add_typer(config_app, name="config")
ai_app = typer.Typer(
    help="Claude (Tier 2): whether it is on, and a check that it can be reached.",
    short_help="Claude (Tier 2): is it on, and can it be reached?",
    no_args_is_help=True,
)
app.add_typer(ai_app, name="ai")


def _help(command: Callable[..., None]) -> Callable[..., None]:
    """A command's docstring as `--help` shows it: each paragraph on one line, so the terminal
    wraps it at its own width — Typer keeps the source's line breaks, which cut sentences in
    two."""
    paragraphs = inspect.cleandoc(command.__doc__ or "").split("\n\n")
    command.__doc__ = "\n\n".join(" ".join(paragraph.split()) for paragraph in paragraphs)
    return command


ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config", help="Read settings from this file instead of the usual one (sijl config show)."
    ),
]


NoAiOption = Annotated[
    bool, typer.Option("--no-ai", help="Turn AI off for this command (same as SIJL_AI=off).")
]


def _today() -> date:
    """The one read of the clock per command: it ends `--last`, cuts a quarter, half or year
    still running, is the default `--until` and the report's date. Tests replace it."""
    return date.today()


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"sijl {__version__}")
        raise typer.Exit()


def _fail(message: str, code: int) -> NoReturn:
    typer.echo(f"sijl: {printable_block(message)}", err=True)  # git's words, paths: escaped
    raise typer.Exit(code)


def _load(config: Path | None, flags: dict[str, object]) -> Loaded:
    try:
        return load_settings(config=config, env=os.environ, flags=flags)
    except ConfigError as exc:
        _fail(str(exc), EXIT_USAGE)


def _missing(what: str, flag: str, key: str, loaded: Loaded) -> NoReturn:
    raise typer.BadParameter(
        printable(f"give {what} — with {flag}, or set {key} in {loaded.path}"),
        param_hint=f"'{flag}'",
    )


def _period_origin(loaded: Loaded) -> str:
    """Where the chosen period came from, as an error names it: '--quarter', $SIJL_LAST, or
    window.year in the config file."""
    names = []
    for setting in SETTINGS:
        value = getattr(loaded.settings, setting.attr)
        if setting.attr not in PERIOD_FIELDS or value in ("", None):
            continue
        source = loaded.sources[setting.key]
        if source.startswith("flag "):
            names.append(f"'{setting.flag}'")
        elif source.startswith("env "):
            names.append(f"${setting.env}")
        else:
            names.append(f"{setting.key} in {loaded.path}")
    return " / ".join(names)


@app.callback()
@_help
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Summarise your own git work for a review period, from your local repos."""


def _scan_flags(
    *,
    root: list[Path] | None,
    repo: list[Path] | None,
    author_email: list[str] | None,
    since: str | None,
    until: str | None,
    last: str | None,
    quarter: str | None,
    half: str | None,
    year: int | None,
    include_worktrees: bool | None,
    dedupe: str | None,
    jira_project: list[str] | None,
) -> dict[str, object]:
    """The flags of a command that reads your work (`report`, `digest`, `ask`), as setting
    values; None where a flag was not given. --root or --repo on the command line replaces both
    configured lists: the flags say exactly where to look."""
    named_sources = bool(root) or bool(repo)
    return {
        "emails": author_email or None,
        "roots": [str(p) for p in root or []] if named_sources else None,
        "repos": [str(p) for p in repo or []] if named_sources else None,
        "since": since,
        "until": until,
        "last": last,
        "quarter": quarter,
        "half": half,
        "year": year,
        "include_worktrees": include_worktrees,
        "dedupe": dedupe,
        "jira_projects": jira_project or None,
    }


def _given_flags(
    *,
    since: str | None,
    until: str | None,
    last: str | None,
    quarter: str | None,
    half: str | None,
    year: int | None,
    root: list[Path] | None,
    repo: list[Path] | None,
    author_email: list[str] | None,
    include_worktrees: bool | None,
    dedupe: str | None,
    jira_project: list[str] | None,
    config: Path | None,
) -> list[str]:
    """The period and scope flags a command was given, as `sijl digest` takes them: what the
    Tier 1 route repeats, so its digest covers the same work as the command that failed."""
    flags: list[str] = []
    for flag, value in (("--since", since), ("--until", until), ("--last", last),
                        ("--quarter", quarter), ("--half", half), ("--year", year)):  # fmt: skip
        if value is not None:
            flags += [flag, str(value)]
    for flag, values in (("--root", root), ("--repo", repo), ("--author-email", author_email)):
        for value in values or ():
            flags += [flag, str(value)]
    if include_worktrees is not None:
        flags.append("--include-worktrees" if include_worktrees else "--no-include-worktrees")
    if dedupe is not None:
        flags += ["--dedupe", dedupe]
    for project in jira_project or ():
        flags += ["--jira-project", project]
    if config is not None:
        flags += ["--config", str(config)]
    return flags


def _summary(loaded: Loaded) -> Report:
    """The report the settings ask for — identities, folders, period — built once; usage errors
    exit 2, git missing or failing 1, no repository found 3."""
    settings = loaded.settings
    try:  # the identity rules live in IdentitySet; the CLI only turns them into a usage error
        identities = IdentitySet.from_emails(settings.emails)
    except ValueError:
        _missing("at least one email you commit with", "--author-email", "identity.emails", loaded)
    if not settings.roots and not settings.repos:
        _missing("a folder to search or a repository", "--root", "scan.roots", loaded)
    today = _today()
    try:  # one choice at a time is already guaranteed by load_settings()
        window = settings.window(today)
    except NoPeriod:
        raise typer.BadParameter(
            printable(
                "give the period — with --since (and --until), --last, --quarter, --half or "
                f"--year, or set one of them under [window] in {loaded.path}"
            ),
            param_hint="'--since' / '--last'",
        ) from None
    except ValueError as exc:  # the user's own value, quoted back: escaped like all output
        hint = printable(_period_origin(loaded))
        raise typer.BadParameter(printable(str(exc)), param_hint=hint) from None

    git = GitRunner()
    try:
        git.require_version()
        summary = build_report(
            roots=[Path(p).expanduser() for p in settings.roots],
            paths=[Path(p).expanduser() for p in settings.repos],
            identities=identities,
            window=window,
            git=git,
            generated_on=today,
            max_depth=settings.max_depth,
            person=settings.name,
            include_worktrees=settings.include_worktrees,
            dedupe=settings.dedupe,
            forges=Forges.from_hosts(settings.github_hosts, settings.gitlab_hosts),
            ssh=SshRunner() if settings.ssh_aliases else None,
            jira_projects=settings.jira_projects,
            categorise=categoriser(settings.test_suites),
        )
    except FileNotFoundError:
        _fail("git is not installed, or not on PATH", EXIT_GIT)
    except GitError as exc:
        _fail(str(exc), EXIT_GIT)

    if summary.repos_scanned == 0:
        reasons = "; ".join(printable(f"{s.path}: {s.reason}") for s in summary.not_scanned)
        _fail(f"no git repository found ({reasons or 'nothing to search'})", EXIT_NO_REPOS)
    return summary


@app.command()
@_help
def report(
    since: Annotated[
        str | None, typer.Option("--since", help="First day of the period, YYYY-MM-DD.")
    ] = None,
    until: Annotated[
        str | None,
        typer.Option("--until", help="Last day of the period, YYYY-MM-DD (default: today)."),
    ] = None,
    last: Annotated[
        str | None,
        typer.Option("--last", help="A period that ends today, today included: 30d, 6w, 6m or 1y."),
    ] = None,
    quarter: Annotated[
        str | None,
        typer.Option(
            "--quarter", help="A calendar quarter, e.g. 2026Q3; one still running stops today."
        ),
    ] = None,
    half: Annotated[
        str | None,
        typer.Option(
            "--half",
            help="A half year: 2026H1 (January to June) or 2026H2 (July to December).",
        ),
    ] = None,
    year: Annotated[
        int | None,
        typer.Option("--year", help="A calendar year, e.g. 2026; one still running stops today."),
    ] = None,
    root: Annotated[
        list[Path] | None,
        typer.Option("--root", help="A folder to search for repositories (repeatable)."),
    ] = None,
    repo: Annotated[
        list[Path] | None,
        typer.Option("--repo", help="A repository to include (repeatable)."),
    ] = None,
    author_email: Annotated[
        list[str] | None,
        typer.Option("--author-email", help="An email address you commit with (repeatable)."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write the Markdown to this file instead of stdout."),
    ] = None,
    include_worktrees: Annotated[
        bool | None,
        typer.Option(
            "--include-worktrees/--no-include-worktrees",
            help="Also read commits that exist only in a detached linked worktree.",
            show_default=False,
        ),
    ] = None,
    dedupe: Annotated[
        str | None,
        typer.Option(
            "--dedupe",
            help="How copies of one commit are matched: key (author, author time, subject) "
            "or patch-id (also the same change).",
            show_default=False,
        ),
    ] = None,
    jira_project: Annotated[
        list[str] | None,
        typer.Option(
            "--jira-project",
            help="A Jira project key, like PROJ, whose keys count as tickets (repeatable).",
        ),
    ] = None,
    template: Annotated[
        Path | None,
        typer.Option(
            "--template",
            help="Render the report with your own Jinja2 template instead of the built-in one.",
        ),
    ] = None,
    allow_template_links: Annotated[
        bool,
        typer.Option(
            "--allow-template-links",
            help="Let your --template write its own links, images and HTML. The template can "
            "then send your report's data to any address: only for a template you trust.",
        ),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Summarise the commits you authored in a period, as Markdown.

    It needs the email addresses you commit with, a folder to search (or a repository) and a
    period: give them as flags, or set them once in the config file (`sijl config show` names
    it). A flag replaces the file's value for one run.

    Example: sijl report --author-email you@example.com --root ~/code --last 6m --out review.md"""
    loaded = _load(
        config,
        _scan_flags(root=root, repo=repo, author_email=author_email, since=since, until=until,
                    last=last, quarter=quarter, half=half, year=year,
                    include_worktrees=include_worktrees, dedupe=dedupe,
                    jira_project=jira_project),
    )  # fmt: skip
    custom = _template(template) if template is not None else None  # before any scan
    summary = _summary(loaded)
    target = out.expanduser() if out is not None else None
    scanned = places([rc.repo for rc in summary.collection.repos], GitRunner()) if target else []
    if target is not None:  # never into a scanned repository unless exactly given
        _out(lambda: check_out(target, scanned, cwd=Path.cwd()), target)
    try:
        rendered = render_report(summary, custom, allow_links=allow_template_links)
    except TemplateProblem as exc:  # the user's template: a misspelt field, a refused operation
        _fail(str(exc), EXIT_USAGE)
    if target is None:
        typer.echo(rendered.text, nl=False)
        return
    # decided again on the very folder written into: a link swapped in meanwhile changes nothing
    _out(lambda: write_out(target, rendered.text, scanned, cwd=Path.cwd()), target)
    typer.echo(printable(f"Wrote {out}"), err=True)


def _out(step: Callable[[], None], target: Path) -> None:
    """Check or write `--out`: a refusal or a path that cannot be written is a usage error."""
    try:
        step()
    except OutRefused as exc:
        _fail(str(exc), EXIT_USAGE)
    except OSError as exc:
        _fail(f"cannot write {target}: {exc.strerror or exc}", EXIT_USAGE)


def _template(path: Path) -> CustomTemplate:
    """The user's `--template`, read and compiled before any repository is scanned: a syntax
    error or a refused construct is a usage error at once, with the template's line."""
    try:
        custom = read_template(path.expanduser())
        check(custom)
    except TemplateProblem as exc:
        _fail(str(exc), EXIT_USAGE)
    return custom


# The options of a command that reads your work, shared with `report` (whose own declarations
# predate them).
SinceOption = Annotated[
    str | None, typer.Option("--since", help="First day of the period, YYYY-MM-DD.")
]
UntilOption = Annotated[
    str | None,
    typer.Option("--until", help="Last day of the period, YYYY-MM-DD (default: today)."),
]
LastOption = Annotated[
    str | None,
    typer.Option("--last", help="A period that ends today, today included: 30d, 6w, 6m or 1y."),
]
QuarterOption = Annotated[
    str | None,
    typer.Option(
        "--quarter", help="A calendar quarter, e.g. 2026Q3; one still running stops today."
    ),
]
HalfOption = Annotated[
    str | None,
    typer.Option(
        "--half", help="A half year: 2026H1 (January to June) or 2026H2 (July to December)."
    ),
]
YearOption = Annotated[
    int | None,
    typer.Option("--year", help="A calendar year, e.g. 2026; one still running stops today."),
]
RootOption = Annotated[
    list[Path] | None,
    typer.Option("--root", help="A folder to search for repositories (repeatable)."),
]
RepoOption = Annotated[
    list[Path] | None, typer.Option("--repo", help="A repository to include (repeatable).")
]
EmailOption = Annotated[
    list[str] | None,
    typer.Option("--author-email", help="An email address you commit with (repeatable)."),
]
WorktreesOption = Annotated[
    bool | None,
    typer.Option(
        "--include-worktrees/--no-include-worktrees",
        help="Also read commits that exist only in a detached linked worktree.",
        show_default=False,
    ),
]
DedupeOption = Annotated[
    str | None,
    typer.Option(
        "--dedupe",
        help="How copies of one commit are matched: key (author, author time, subject) "
        "or patch-id (also the same change).",
        show_default=False,
    ),
]
JiraOption = Annotated[
    list[str] | None,
    typer.Option(
        "--jira-project",
        help="A Jira project key, like PROJ, whose keys count as tickets (repeatable).",
    ),
]


@app.command("digest")
@_help
def digest_command(
    since: SinceOption = None,
    until: UntilOption = None,
    last: LastOption = None,
    quarter: QuarterOption = None,
    half: HalfOption = None,
    year: YearOption = None,
    root: RootOption = None,
    repo: RepoOption = None,
    author_email: EmailOption = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write the digest to this file instead of stdout."),
    ] = None,
    with_prompt: Annotated[
        bool,
        typer.Option(
            "--with-prompt",
            help="Begin with instructions for Claude: answer only from the digest, cite its "
            'ids, and say "not in the data" otherwise.',
        ),
    ] = False,
    max_chars: Annotated[
        int | None,
        typer.Option(
            "--max-chars",
            min=1,
            help="Write at most this many characters (the instructions included); what does "
            "not fit is left out, and every cut says how much.",
        ),
    ] = None,
    no_redact: Annotated[
        bool,
        typer.Option(
            "--no-redact",
            help="Keep colleagues' names and email addresses as written (by default only yours "
            "are kept).",
        ),
    ] = False,
    include_worktrees: WorktreesOption = None,
    dedupe: DedupeOption = None,
    jira_project: JiraOption = None,
    config: ConfigOption = None,
) -> None:
    """Write your period's evidence as compact text with ids to cite.

    For asking Claude Code, or any AI chat you give the file to, about your work (Tier 1).
    Colleagues' names and email addresses are replaced unless you pass --no-redact. Nothing is
    sent anywhere."""
    loaded = _load(
        config,
        _scan_flags(root=root, repo=repo, author_email=author_email, since=since, until=until,
                    last=last, quarter=quarter, half=half, year=year,
                    include_worktrees=include_worktrees, dedupe=dedupe,
                    jira_project=jira_project),
    )  # fmt: skip
    summary = _summary(loaded)
    target = out.expanduser() if out is not None else None
    scanned = places([rc.repo for rc in summary.collection.repos], GitRunner()) if target else []
    if target is not None:  # report's --out rules: never into a scanned repository unless
        _out(lambda: check_out(target, scanned, cwd=Path.cwd()), target)  # exactly given
    try:  # everyone in the scanned repositories' history, so their names can be found
        redactor = None if no_redact else digests.redactor_for(summary, git=GitRunner())
    except GitError as exc:
        _fail(str(exc), EXIT_GIT)
    prompt = len(digests.PROMPT) + 1 if with_prompt else 0  # with_prompt() adds a blank line
    budget = None if max_chars is None else max(max_chars - prompt, 1)  # what the digest may use
    try:
        built = digests.build(summary, redactor=redactor, max_chars=budget)
    except digests.BudgetTooSmall as exc:
        what = "the smallest digest that fits" + (", with the instructions," if with_prompt else "")
        raise typer.BadParameter(
            f"--max-chars {max_chars:,} is too small: {what} takes {exc.smallest + prompt:,} "
            "characters",
            param_hint="'--max-chars'",
        ) from None
    text = digests.with_prompt(built.text) if with_prompt else built.text
    if target is None:
        typer.echo(text, nl=False)
    else:  # decided again on the very folder written into, as report does
        _out(lambda: write_out(target, text, scanned, cwd=Path.cwd()), target)
    known = redactor.known if redactor is not None else 0
    note = _digest_note(built, text, known=known, out=out, max_chars=max_chars)
    typer.echo(printable_block(note), err=True)


def _digest_note(
    built: digests.Digest, text: str, *, known: int, out: Path | None, max_chars: int | None
) -> str:
    """What `digest` tells the user on stderr: the size and a local token estimate, the
    redaction, what a budget left out — and, with --out, where it went and how to ask."""

    def count(n: int, noun: str, plural: str = "") -> str:
        return f"{n:,} {noun if n == 1 else plural or noun + 's'}"

    # Tickets are the keys of your Jira projects the text holds; any other key-shaped word a
    # subject or title writes (UTF-8, a key of another project) is citable, but no ticket.
    tickets = sum(key.rsplit("-", 1)[0] in built.projects for key in built.tickets)
    other_keys = len(built.tickets) - tickets
    if built.projects:
        kinds = count(tickets, "ticket")
        other = "other key-like id"
    else:
        kinds = "no tickets (no Jira projects are set)"
        other = "key-like id"
    if other_keys:
        kinds += f" and {count(other_keys, other)} written in subjects or titles"
    lines = [
        f"Digest: {count(len(text), 'character')}, about {count(ai.estimate_tokens(text), 'token')}"
        f" (estimated on this computer: nothing was sent), {count(len(built.ids), 'id')} to cite"
        f" — {count(len(built.shas), 'commit')}, {count(len(built.requests), 'request')}, "
        f"{kinds}."
    ]
    if not built.redaction:
        lines.append("Redaction: off (--no-redact) — names and email addresses are as written.")
    else:
        lines.append(
            f"Redaction: on — {count(built.redacted_names, 'name')} and "
            f"{count(built.redacted_emails, 'email address', 'email addresses')} "
            f"replaced (Sijl knows {count(known, 'name')} and user names of other people "
            "from your repositories' history); only yours are kept (--no-redact keeps them all)."
        )
    if built.complete:
        lines.append("Nothing was omitted.")
    else:
        o, n = built.omitted, built.listed
        lines.append(
            f"Omitted to fit --max-chars {max_chars:,}: {o.commits:,} of {n.commits:,} commits, "
            f"{o.requests:,} of {n.requests:,} requests and {o.tickets:,} of {n.tickets:,} "
            "tickets; every cut says so in the text."
        )
    if out is not None:
        lines += [
            f"Wrote {out}",
            f"Ask Claude Code about it: claude {shlex.quote(f'Read {out} and answer: …')}",
        ]
    return "\n".join(lines)


@app.command()
@_help
def repos(
    root: Annotated[
        list[Path] | None,
        typer.Option("--root", help="A folder to search for repositories (repeatable)."),
    ] = None,
    repo: Annotated[
        list[Path] | None,
        typer.Option("--repo", help="A repository to include (repeatable)."),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print JSON, in a fixed order, instead of text.")
    ] = False,
    config: ConfigOption = None,
) -> None:
    """List the repositories Sijl finds, and every path it did not scan.

    Each repository is listed once, with its linked worktrees; each path that was not scanned
    comes with the reason."""
    named_sources = bool(root) or bool(repo)  # as for report: the flags say exactly where to look
    loaded = _load(
        config,
        {
            "roots": [str(p) for p in root or []] if named_sources else None,
            "repos": [str(p) for p in repo or []] if named_sources else None,
        },
    )
    settings = loaded.settings
    if not settings.roots and not settings.repos:
        _missing("a folder to search or a repository", "--root", "scan.roots", loaded)
    git = GitRunner()
    try:
        git.require_version()
        found = discover(
            [Path(p).expanduser() for p in settings.roots],
            [Path(p).expanduser() for p in settings.repos],
            git,
            max_depth=settings.max_depth,
            with_worktrees=True,
        )
    except FileNotFoundError:
        _fail("git is not installed, or not on PATH", EXIT_GIT)
    except GitError as exc:
        _fail(str(exc), EXIT_GIT)
    typer.echo(listing_json(found) if as_json else render_listing(found), nl=False)
    if not found.repos:  # the listing above says why
        _fail("no git repository found", EXIT_NO_REPOS)


@config_app.command("show")
@_help
def config_show(config: ConfigOption = None) -> None:
    """Show every setting, its value and where it came from.

    The source is the default, the config file, the environment or a flag. The config file read
    is named first; the period the settings choose today comes last."""
    typer.echo(printable_block(render_sources(_load(config, {}), today=_today())), nl=False)


# --------------------------------------------------------------------------------- sijl ai


def _connect(loaded: Loaded) -> tuple[ai.Status, ai.Sender | None]:
    """The feature flag and, when on, the one client this command sends with — built before the
    preview, locally, so the preview names where the request will go. Tests replace it."""
    return ai.connect(enabled=loaded.settings.ai_enabled, disabled_by=_disabled_by(loaded))


def _ask(question: str) -> bool:
    """The one confirmation of a command: asked on stderr, read from stdin; only "y" or "yes"
    is a yes, and no answer (end of input, no stdin) is a no. Not `typer.confirm()`: it writes
    the prompt's last character to stdout, which must carry the answer only."""
    typer.echo(f"{question} [y/N] ", err=True, nl=False)
    stdin = sys.stdin
    answer = stdin.readline() if stdin is not None else ""
    if stdin is None or not stdin.isatty() or not answer.endswith("\n"):
        typer.echo("", err=True)  # no terminal echoed the answer: end the prompt's line
    return answer.strip().lower() in ("y", "yes")


def _disabled_by(loaded: Loaded) -> str:
    """Where `ai.enabled = false` came from, as the off reason names it."""
    source = loaded.sources["ai.enabled"]
    if source.startswith("flag "):
        return "--no-ai"
    if source.startswith("env "):
        return f"SIJL_AI={os.environ.get('SIJL_AI', '').strip()}"
    return f"ai.enabled = false in {loaded.path}"


def _ai_failed(
    message: str, question: str = "", code: int = EXIT_AI, flags: list[str] | None = None
) -> NoReturn:
    """An AI command that cannot give an answer: why, then the Tier 1 route — with the user's
    question and period flags filled in, when there are some — never a bare error. Exit 4, or
    `code` (5 once an earlier answer was not verified)."""
    route = ai.tier1_route(question, flags)
    typer.echo(printable_block(f"sijl: {message}\n{route}"), err=True, nl=False)
    raise typer.Exit(code)


def _to_stderr(text: str) -> None:
    """How AI commands show a preview: on stderr, so stdout carries the answer only."""
    typer.echo(text, err=True, nl=not text.endswith("\n"))


@ai_app.command("status")
@_help
def ai_status(no_ai: NoAiOption = False, config: ConfigOption = None) -> None:
    """Say whether Sijl's AI features are on, and why.

    When they are on: the model, where requests go and how sending is confirmed. When they are
    off: how to ask Claude about your work without them."""
    loaded = _load(config, {"ai_enabled": False if no_ai else None})
    settings = loaded.settings
    state = ai.status(enabled=settings.ai_enabled, disabled_by=_disabled_by(loaded))
    if not state.on:
        typer.echo(printable_block(f"AI: off — {state.reason}\n{ai.tier1_route()}"), nl=False)
        return
    asks = "asks first"
    if not settings.ai_confirm:
        source = loaded.sources["ai.confirm"]
        where = (
            "SIJL_AI_CONFIRM=false"
            if source.startswith("env ")
            else f"ai.confirm = false in {loaded.path}"
        )
        asks = f"does not ask first ({where})"
    redacts = (
        "colleagues' names and emails are redacted"
        if settings.ai_redact
        else "names and emails are sent as they are (ai.redact = false)"
    )
    lines = [
        f"AI: on — {state.reason}",
        f"Model: {settings.ai_model} · effort {ai.EFFORT} · requests go to {state.destination}",
        f"Before anything is sent, Sijl shows all of it and {asks}; {redacts}.",
        "Nothing has been sent: `sijl ai check` sends one test line, after asking.",
    ]
    typer.echo(printable_block("\n".join(lines)))


@ai_app.command("check")
@_help
def ai_check(
    no_ai: NoAiOption = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Send without asking (the preview is still shown).")
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Send one fixed line to Claude to check that your credentials work.

    Shows exactly what will be sent, with a cost estimate, and asks first."""
    loaded = _load(
        config, {"ai_enabled": False if no_ai else None, "ai_confirm": False if yes else None}
    )
    settings = loaded.settings
    state, claude = _connect(loaded)
    if claude is None:
        _ai_failed(f"AI is off: {state.reason}.")
    try:
        redactor = Redactor(keep_emails=settings.emails, keep_names=[settings.name])
        payload = ai.build_payload(
            model=settings.ai_model,
            system="",
            digest="",
            question=ai.CHECK_QUESTION,
            redactor=redactor if settings.ai_redact else None,
            effort=ai.CHECK_EFFORT,
            max_tokens=ai.CHECK_MAX_TOKENS,
        )
        session = ai.Session(
            sender=claude, show=_to_stderr, confirm=_ask if settings.ai_confirm else None
        )
        reply = session.send(payload)
    except ai.AiError as exc:
        _ai_failed(str(exc))
    finally:
        claude.close()
    if reply is None:
        typer.echo("Nothing was sent to Claude.", err=True)
        return
    if not reply.complete:  # no answer, but the attempt is billed: say what it used
        typer.echo(ai.render_usage(reply), err=True)
        _ai_failed(reply.problem)
    typer.echo(ai.render_reply(reply), nl=False)
    typer.echo(ai.render_usage(reply), err=True)


# ------------------------------------------------------------------------------- sijl ask


class _PreviewFiles:
    """Where `ask` keeps a request too long for the terminal (`ai.Session`'s `save`): one private
    folder per command — `tempfile.mkdtemp()`, readable by the user alone (0700) — one file per
    request in it, created 0600 and never overwritten, and all of it removed when the command
    ends. The request holds the user's evidence: it never lingers, nor lands in a repository.

    "When the command ends" includes being killed while the question waits or while it
    writes, to a pipe whose reader is gone: from the
    folder's creation to its removal, SIGHUP, SIGTERM, SIGQUIT (Ctrl-\\) and SIGPIPE raise
    SystemExit (exit 128 + the signal), so the command's `finally` removes it — Ctrl-C already
    raises — and an `atexit` hook is the fallback; the previous handlers come back afterwards.
    A signal ignored when the folder is made stays ignored: it cannot end the command (`nohup`
    ignores SIGHUP; Python, unless told otherwise, SIGPIPE — a closed pipe is then an OSError,
    which the `finally` sees too). A folder that cannot be made or written is an AiError:
    nothing is sent, and the command says why."""

    SIGNALS = tuple(
        getattr(signal, name)
        for name in ("SIGHUP", "SIGTERM", "SIGQUIT", "SIGPIPE")
        if hasattr(signal, name)
    )

    def __init__(self) -> None:
        self._folder: Path | None = None
        self._count = 0
        self._handlers: dict[int, object] = {}

    def __call__(self, text: str) -> str:
        try:
            if self._folder is None:
                self._guard()
                self._folder = Path(tempfile.mkdtemp(prefix="sijl-ask-"))
            self._count += 1
            path = self._folder / f"request-{self._count}.txt"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(text.encode("utf-8"))  # the bytes the summary's sha256 is of
        except OSError as exc:
            where = os.environ.get("TMPDIR") or tempfile.tempdir or "the temporary folder"
            raise ai.AiError(
                f"could not save the request for you to read ({exc.strerror or exc}, in {where}):"
                " nothing was sent — set TMPDIR to a folder you can write"
            ) from exc
        return f"{path}  (deleted when this command ends)"

    def _guard(self) -> None:
        def stop(signum: int, frame: object) -> None:
            raise SystemExit(128 + signum)

        for signum in self.SIGNALS:
            if signal.getsignal(signum) == signal.SIG_IGN:
                continue  # it cannot end the command: leave it ignored
            # not the main thread: ValueError — the finally and atexit still remove the folder
            with contextlib.suppress(ValueError):
                self._handlers[signum] = signal.signal(signum, stop)
        atexit.register(self.remove)

    def remove(self) -> None:
        if self._folder is not None:
            shutil.rmtree(self._folder, ignore_errors=True)
            self._folder = None
        for signum, handler in self._handlers.items():
            signal.signal(signum, handler)  # type: ignore[arg-type]
        self._handlers.clear()
        atexit.unregister(self.remove)


def _next_question() -> str | None:
    """The next question of an interactive session, read like `_ask()` reads its answer: asked on
    stderr, one line from stdin. An empty line or the end of input ends the session (None)."""
    typer.echo("Your question (an empty line ends): ", err=True, nl=False)
    stdin = sys.stdin
    line = stdin.readline() if stdin is not None else ""
    if stdin is None or not stdin.isatty() or not line.endswith("\n"):
        typer.echo("", err=True)  # no terminal echoed the line: end the prompt's line
    return line.strip() or None


def _evidence_about(built: digests.Digest, max_chars: int | None) -> str:
    """The preview's Evidence line: what the digest covers, and what --max-chars left out."""
    if built.complete:
        return built.about
    o, n = built.omitted, built.listed
    return (
        f"{built.about}; to fit --max-chars {max_chars:,}: {o.commits:,} of {n.commits:,} "
        f"commits, {o.requests:,} of {n.requests:,} requests and {o.tickets:,} of "
        f"{n.tickets:,} tickets left out"
    )


def _unverified(reading: citations.Reading) -> str:
    """The warning under an answer with an unknown citation detected (it then exits 5)."""
    check = reading.check
    if check.unverified:
        return (
            f"sijl: could not check the ids in this answer ({check.unverified}): treat every "
            "one Sijl did not verify as unverified."
        )
    unknown = len(reading.unknown_citations) + len(check.ids(citations.UNKNOWN))
    ids = "1 id that is" if unknown == 1 else f"{unknown:,} ids that are"
    return (
        f"sijl: this answer cites {ids} NOT in your evidence (marked in it): check it before "
        "you use the answer."
    )


def _scripted() -> bool:
    """Are the questions read from a pipe or a file — no one at a terminal to ask again?"""
    return sys.stdin is None or not sys.stdin.isatty()


def _converse(
    chat: asking.Conversation,
    question: str | None,
    *,
    interactive: bool,
    flags: list[str] | None = None,
) -> int:
    """Ask `question` — or, without one, the first question read — and in interactive mode every
    next one until an empty line. A complete answer goes to stdout with its check; an
    incomplete one ends a one-shot command (exit 4) and is reported in interactive mode, where
    the user may ask again; an AiError ends the command (exit 4). A no to sending ends it too.
    Returns the exit code (`ask_command`'s docstring): 5 when an answer shown cites an id the
    digest does not hold, else 4 when questions read from a pipe got no complete answer, else 0."""
    verified, asked_count, unanswered = True, 0, 0
    asked = question if question is not None else _next_question()
    while asked is not None:
        try:
            answer = chat.ask(asked)
        except ai.AiError as exc:
            if verified:
                _ai_failed(str(exc), asked, flags=flags)
            _ai_failed(  # exit 5 wins: an answer the user was shown cites ids not verified
                f"{exc}\nAn earlier answer cites ids that are NOT in your evidence (marked in it)",
                asked,
                EXIT_UNVERIFIED,
                flags,
            )
        if answer is None:
            typer.echo("Nothing was sent to Claude.", err=True)
            break
        asked_count += 1
        if answer.reading is not None:
            terminal = sys.stdout is not None and sys.stdout.isatty()
            typer.echo(answer.render(terminal=terminal), nl=False)
        typer.echo(ai.render_usage(answer.reply), err=True)  # billed, complete or not
        if answer.reading is not None and not answer.reading.ok:
            verified = False
            typer.echo(printable(_unverified(answer.reading)), err=True)
        if answer.reading is None:
            unanswered += 1
            problem = answer.reply.problem
            if answer.reply.stop_reason == "max_tokens":
                problem += " Allow a longer answer with --answer-limit (or ai.answer_limit)."
            if not interactive:
                _ai_failed(problem, asked, flags=flags)
            typer.echo(printable_block(f"sijl: {problem}"), err=True)
        if not interactive:
            break
        asked = _next_question()
    if unanswered and interactive and _scripted():
        typer.echo(
            f"sijl: {unanswered:,} of {asked_count:,} questions got no complete answer.",
            err=True,
        )
    if not verified:
        return EXIT_UNVERIFIED
    return EXIT_AI if unanswered and interactive and _scripted() else 0


@app.command("ask")
@_help
def ask_command(
    question: Annotated[
        str | None,
        typer.Argument(
            help="Your question, in quotes. Without one, Sijl asks for questions until an "
            "empty line."
        ),
    ] = None,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive",
            "-i",
            help="After the first answer, keep asking follow-up questions (one yes for all).",
        ),
    ] = False,
    since: SinceOption = None,
    until: UntilOption = None,
    last: LastOption = None,
    quarter: QuarterOption = None,
    half: HalfOption = None,
    year: YearOption = None,
    root: RootOption = None,
    repo: RepoOption = None,
    author_email: EmailOption = None,
    max_chars: Annotated[
        int | None,
        typer.Option(
            "--max-chars",
            min=1,
            help="Send at most this many characters of evidence; what does not fit is left out, "
            "and every cut says how much.",
        ),
    ] = None,
    answer_limit: Annotated[
        int | None,
        typer.Option(
            "--answer-limit",
            help="The longest answer Claude may give, in tokens, its thinking included "
            f"(default {ai.ANSWER_LIMIT:,}; ai.answer_limit).",
            show_default=False,
        ),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Send without asking (the preview is still shown).")
    ] = False,
    no_ai: NoAiOption = False,
    include_worktrees: WorktreesOption = None,
    dedupe: DedupeOption = None,
    jira_project: JiraOption = None,
    config: ConfigOption = None,
) -> None:
    """Ask Claude about your period, and check the ids its answer cites.

    Claude answers from your digest and cites its ids; Sijl checks every id it cites. It shows
    exactly what will be sent, with a cost estimate, and asks first.

    Exit codes, the first that applies wins: 5 when an answer shown cites an id that is not in
    your evidence (marked in it), even if Claude failed later; 4 when Claude could not be used,
    or a question got no complete answer (in interactive mode only when the questions come from
    a pipe or a file); 0 otherwise."""
    flags = _scan_flags(root=root, repo=repo, author_email=author_email, since=since,
                        until=until, last=last, quarter=quarter, half=half, year=year,
                        include_worktrees=include_worktrees, dedupe=dedupe,
                        jira_project=jira_project)  # fmt: skip
    flags |= {
        "ai_enabled": False if no_ai else None,
        "ai_confirm": False if yes else None,
        "ai_answer_limit": answer_limit,
    }
    loaded = _load(config, flags)
    settings = loaded.settings
    if question is not None and not question.strip():
        raise typer.BadParameter("the question is empty", param_hint="'QUESTION'")
    given = _given_flags(since=since, until=until, last=last, quarter=quarter, half=half,
                         year=year, root=root, repo=repo, author_email=author_email,
                         include_worktrees=include_worktrees, dedupe=dedupe,
                         jira_project=jira_project, config=config)  # fmt: skip
    state, claude = _connect(loaded)
    if claude is None:
        _ai_failed(f"AI is off: {state.reason}.", question or "", flags=given)
    previews = _PreviewFiles()
    try:
        summary = _summary(loaded)
        try:  # everyone in the scanned repositories' history, as `digest` does
            redactor = (
                digests.redactor_for(summary, git=GitRunner()) if settings.ai_redact else None
            )
        except GitError as exc:
            _fail(str(exc), EXIT_GIT)
        try:
            built = digests.build(summary, redactor=redactor, max_chars=max_chars)
        except digests.BudgetTooSmall as exc:
            raise typer.BadParameter(
                f"--max-chars {max_chars:,} is too small: the smallest digest that fits takes "
                f"{exc.smallest:,} characters",
                param_hint="'--max-chars'",
            ) from None
        session = ai.Session(
            sender=claude,
            show=_to_stderr,
            confirm=_ask if settings.ai_confirm else None,
            save=previews,
        )
        chat = asking.Conversation(
            session=session,
            evidence=built,
            model=settings.ai_model,
            redactor=redactor,
            max_tokens=settings.ai_answer_limit,
            about=_evidence_about(built, max_chars),
        )
        interactive = interactive or question is None
        if question is None:
            typer.echo(printable(f"Asking about: {built.about}"), err=True)
        code = _converse(chat, question, interactive=interactive, flags=given)
    finally:
        claude.close()
        previews.remove()
    if code:  # 5 fails closed: the answer was shown, every unknown id marked
        raise typer.Exit(code)
