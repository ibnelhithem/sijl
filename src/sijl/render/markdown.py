"""The Markdown report, rendered from Jinja2 templates.

The built-in templates live in `templates/` — package data, read with `importlib.resources`, so
they work from an installed wheel as from the source tree. `report.md.j2` is the whole report; it
includes one template per section (`SECTIONS`), which a user's own template can include too.

`render_report(report, template)` builds the values once (`context.py`), renders the built-in
report — or the user's `CustomTemplate` (`--template`) — in the narrowed sandbox (`safety.py`),
and passes every line of the result through `text.printable()`: names, paths and subjects can
carry terminal controls, and so could a template's own text.

A custom template is the user's whole report, with one guarantee kept: every report says what it
left out and that its figures are evidence, not a score. So a
custom template never prints the Caveats or the footer — `caveats` and `footer` are not values
for it, and the built-in sections print nothing when it includes them — and Sijl appends its
own Caveats section and footer after the template's output, always (a template could otherwise
hide them in an HTML comment).

A custom template is code from somewhere: it could put the report's data into an image address a
Markdown viewer loads on its own. So its output is parsed (markdown-it, CommonMark and tables)
before anything is written, and every link, image, autolink and piece of HTML in it must be one
Sijl built (`context.built_urls()`); anything else is a `TemplateProblem` — unless the user
passes `allow_links` (`--allow-template-links`) for a template they trust.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from jinja2 import BaseLoader, Environment, Template, TemplateNotFound
from markdown_it import MarkdownIt

from sijl.render.context import built_urls, context
from sijl.render.safety import ReportEnvironment, TemplateProblem, masked, problem
from sijl.report import Report
from sijl.text import printable_block

REPORT = "report.md.j2"
SECTIONS = (
    "header.md.j2",
    "summary.md.j2",
    "highlights.md.j2",
    "per_repository.md.j2",
    "tickets.md.j2",
    "months.md.j2",
    "kinds.md.j2",
    "delivered.md.j2",
    "not_pushed.md.j2",
    "caveats.md.j2",
    "appendix.md.j2",
    "footer.md.j2",
)
BUILT_IN = (REPORT, *SECTIONS)
_CUSTOM = "<your template>"  # the user's template's name: no built-in name starts with "<"
# A custom template's limits (a loop over loops, or a huge repeat, must end as a usage error,
# never as a hang or a full disk): its size, its output, its running time.
MAX_TEMPLATE_CHARS = 1_000_000
MAX_OUTPUT_CHARS = 50_000_000
TEMPLATE_SECONDS = 60.0


class _TooLong(Exception):
    """The template ran past `TEMPLATE_SECONDS`."""


@contextmanager
def _deadline(seconds: float) -> Iterator[None]:
    """Stop the template after `seconds`: SIGALRM raises inside whatever Jinja2 is doing. Only on
    the main thread, where signals arrive, and where `setitimer` exists (Linux, macOS)."""
    if threading.current_thread() is not threading.main_thread() or not hasattr(
        signal, "setitimer"
    ):
        yield
        return

    def stop(signum: int, frame: object) -> None:
        raise _TooLong

    previous = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@dataclass(frozen=True)
class CustomTemplate:
    """The user's template (`--template`): its text, and the path it was read from — the name
    every message about it uses."""

    source: str
    path: str


class _Loader(BaseLoader):
    """The built-in templates by name, and the user's one template: nothing else can be read."""

    def __init__(self, custom: CustomTemplate | None) -> None:
        self.custom = custom

    def get_source(
        self, environment: Environment, template: str
    ) -> tuple[str, str, Callable[[], bool]]:
        if type(template) is not str:  # a name must be the template's own text
            raise TemplateNotFound(repr(template))
        if template == _CUSTOM and self.custom is not None:
            return self.custom.source, self.custom.path, lambda: True
        if template in BUILT_IN:
            text = files(__package__).joinpath("templates", template).read_text(encoding="utf-8")
            return text, f"built-in {template}", lambda: True
        raise TemplateNotFound(template)


def environment(custom: CustomTemplate | None = None) -> ReportEnvironment:
    return ReportEnvironment(_Loader(custom))


def read_template(path: Path) -> CustomTemplate:
    """The user's template file, as UTF-8 text; `TemplateProblem` when it cannot be read."""
    try:
        if path.stat().st_size > 4 * MAX_TEMPLATE_CHARS:  # never read a huge file into memory
            raise TemplateProblem(
                str(path), None, f"it is larger than {MAX_TEMPLATE_CHARS:,} characters"
            )
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise TemplateProblem(str(path), None, "it is not UTF-8 text") from None
    except OSError as exc:
        raise TemplateProblem(str(path), None, f"it cannot be read ({exc.strerror})") from None
    return CustomTemplate(source, str(path))


def check(custom: CustomTemplate) -> None:
    """Read and compile the user's template before any repository is scanned, so that a syntax
    error or a refused construct fails at once. Raises `TemplateProblem`."""
    if len(custom.source) > MAX_TEMPLATE_CHARS:
        raise TemplateProblem(
            custom.path, None, f"it is larger than {MAX_TEMPLATE_CHARS:,} characters"
        )
    try:
        environment(custom).get_template(_CUSTOM)
    except Exception as exc:  # whatever is wrong with a user's template is a usage error
        raise problem(exc) from None


@dataclass(frozen=True)
class Rendered:
    text: str


def _with_caveats(text: str, report: Report) -> str:
    """A custom report, then Sijl's own Caveats section and footer — always."""
    values = context(report)
    built_in = environment()
    added = "".join(
        built_in.get_template(name).render(values) for name in ("caveats.md.j2", "footer.md.j2")
    )
    return text.rstrip("\n") + "\n\n" + added


# Schemes a renderer may turn into a link, or that address something by themselves.
_SCHEMES = frozenset(
    {"http", "https", "ftp", "ftps", "sftp", "mailto", "file", "data", "javascript", "vbscript",
     "tel", "sms", "irc", "ircs", "ssh", "git", "news", "nntp", "xmpp", "ws", "wss"}
)  # fmt: skip
# What can stand around an address in Markdown: the scan splits a line on these.
_AROUND = str.maketrans({char: " " for char in "<>()[]{}\"'`|*~,;!"})
_HITS_KEPT = 20


def _parser() -> MarkdownIt:
    """markdown-it as close to GitHub's GFM as it gets: tables, strikethrough, and bare addresses
    (`www.`, `http(s)://`, e-mail) made links (linkify)."""
    return MarkdownIt("gfm-like")


def foreign_markup(
    text: str, allowed: frozenset[str], *, bare: bool = False
) -> list[tuple[int, str]]:
    """HTML, and links and images Sijl did not build, in `text` — each as (its line, what it
    is). Links a renderer makes of bare addresses count only with `bare` (in a report's real
    output they may be repository text; see `_carriers()`)."""
    parser = _parser()
    own = {parser.normalizeLink(url) for url in allowed}
    found = []
    for block in parser.parse(text):
        line = block.map[0] + 1 if block.map else 0
        for token in (block, *(block.children or [])):
            if token.type in ("html_block", "html_inline"):
                found.append((line, f"HTML ({_short(token.content.strip(), 60)})"))
            elif token.type in ("link_open", "image"):
                if token.markup == "linkify" and not bare:
                    continue
                where = str(token.attrGet("href") or token.attrGet("src") or "")
                if where not in own:
                    kind = "an image" if token.type == "image" else "a link"
                    found.append((line, f"{kind} to {_short(where, 80)}"))
    return found


def _is_domain(host: str) -> bool:
    """`evil.example`, `a.b.example:8080`: two labels or more, the last a word of letters."""
    labels = host.partition(":")[0].split(".")
    return (
        len(labels) >= 2
        and all(label and all(c.isalnum() or c == "-" for c in label) for label in labels)
        and labels[-1].isalpha()
        and len(labels[-1]) >= 2
    )


def _address(token: str) -> bool:
    """Whether a word could be an address a renderer links to, or a mail program writes to."""
    low = token.lower().lstrip(".:")
    scheme, colon, _ = low.partition(":")
    if "://" in low or low.startswith("//") or (colon and scheme in _SCHEMES):
        return True
    if low.startswith("www.") and len(low) > 4:
        return True
    local, at, domain = low.rpartition("@")  # the last @: `a@b@evil.example` mails evil.example
    if at and local and _is_domain(domain.rstrip(".")):
        return True
    cut = min((low.find(c) for c in "/?#" if c in low), default=-1)
    return cut > 0 and _is_domain(low[:cut])


def addresses(text: str, allowed: frozenset[str]) -> list[tuple[int, str]]:
    """Everything address- or link-like in `text`, by plain reading, whatever a renderer would
    make of it — (line, what) — except an address exactly equal to one Sijl built: a scheme or
    `//host`, `www.`, a domain with a path or query, an e-mail, a `<` that opens a tag, comment
    or autolink, a link or image `](…)`, a reference definition `[…]:`. Deliberately broad: it
    reads text no renderer has to agree on (GitHub links `www.anything`; markdown-it only known
    top-level domains)."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.split("\n"), start=1):
        if len(found) >= _HITS_KEPT:  # enough to refuse and to say where; the rest is not needed
            break
        start = line.find("](")
        while start != -1:
            # at most 300 characters read: a longer target is no link Sijl built anyway
            target = line[start + 2 : start + 302].split(")", 1)[0].strip().strip("<>")
            target = target.split(" ", 1)[0]
            if target not in allowed:
                found.append((number, f"a link or image to {_short(target, 60) or '(nothing)'}"))
                break
            start = line.find("](", start + 2)
        stripped = line.lstrip(" ")
        if len(line) - len(stripped) <= 3 and stripped.startswith("[") and "]:" in stripped:
            found.append((number, "a link reference definition"))
        tag = line.find("<")
        while tag != -1:
            after = line[tag + 1 : tag + 2]
            if after.isalpha() or after in ("/", "!", "?"):
                found.append((number, "HTML, a comment or an autolink (<…)"))
                break
            tag = line.find("<", tag + 1)
        for word in line.translate(_AROUND).split():
            if _address(word) and word.rstrip(".,:;?!") not in allowed:
                found.append((number, f"an address ({_short(word, 60)})"))
                break
    return found


def _source_line(source: str, needle: str) -> int | None:
    """The first line of the template's own text holding `needle` — where it wrote it."""
    for number, line in enumerate(source.split("\n"), start=1):
        if needle and needle in line:
            return number
    return None


def _carriers(template: CustomTemplate, rendered: str, report: Report) -> None:
    """Refuse a custom template's output that could carry the report's data to an address
    Sijl did not build (fail closed, whatever renders it).

    The real output is parsed GFM-like: any HTML, and any written link or image Sijl did not
    build, is refused. Then the template renders again over masked values (`safety.masked()`):
    whatever address- or link-like is left there, the template wrote — parsed GFM-like, bare
    addresses included, and read plainly by `addresses()`. Repository text never trips it: a
    subject full of addresses is masked; but a template's own `https://…?d=` beside a value is
    refused even where markdown-it would see no link, as GitHub or GitLab may."""
    allowed = built_urls(report)
    env = environment(template)
    values = context(report, custom=True)
    again = _limited(env.get_template(_CUSTOM), {k: masked(v) for k, v in values.items()})
    found = addresses(again, allowed) + foreign_markup(again, allowed, bare=True)
    found += foreign_markup(rendered, allowed)
    if not found:
        return
    line, what = found[0]
    shown = what.partition("(")[2].rstrip(")") if "(" in what else what.rpartition(" ")[2]
    # The template's own text in it: the longest piece between masked values ("x").
    pieces = sorted((p for p in shown.split("x") if len(p) >= 3), key=len, reverse=True)
    where = None
    for candidate in (*pieces, "](", "<", "]:"):
        where = _source_line(template.source, candidate)
        if where:
            break
    more = f", and {len(found) - 1:,} more" if len(found) > 1 else ""
    raise TemplateProblem(
        template.path,
        where,
        f"it writes {what}{more} that Sijl did not build (line {line:,} of its output) — a "
        "template could send your report's data to any address. Pass --allow-template-links "
        "only for a template you trust",
    )


def render_report(
    report: Report, template: CustomTemplate | None = None, *, allow_links: bool = False
) -> Rendered:
    """The report rendered with the built-in templates, or with the user's `template` — whose
    failures (a misspelt field, a refused operation, a link Sijl did not build unless
    `allow_links`) raise `TemplateProblem`."""
    if template is None:
        return Rendered(printable_block(environment().get_template(REPORT).render(context(report))))
    check(template)
    try:
        text = _limited(environment(template).get_template(_CUSTOM), context(report, custom=True))
    except _TooLong:
        message = f"it ran longer than {TEMPLATE_SECONDS:g} s; nothing was written"
        raise TemplateProblem(template.path, None, message) from None
    except TemplateProblem:
        raise
    except Exception as exc:  # the user's template: any failure is theirs to fix, by its line
        raise problem(exc) from None
    if not allow_links:
        try:
            _carriers(template, text, report)
        except _TooLong:
            message = f"it ran longer than {TEMPLATE_SECONDS:g} s; nothing was written"
            raise TemplateProblem(template.path, None, message) from None
    return Rendered(printable_block(_with_caveats(text, report)))


def _limited(template: Template, values: dict[str, object]) -> str:
    """The template's output, produced piece by piece within `TEMPLATE_SECONDS` and up to
    `MAX_OUTPUT_CHARS`."""
    pieces, size = [], 0
    with _deadline(TEMPLATE_SECONDS):
        for piece in template.generate(values):
            size += len(piece)
            if size > MAX_OUTPUT_CHARS:
                raise TemplateProblem(
                    template.filename or "?",
                    None,
                    f"its output passed {MAX_OUTPUT_CHARS:,} characters; nothing was written",
                )
            pieces.append(piece)
    return "".join(pieces)


def _short(text: str, limit: int = 100) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render(report: Report, template: CustomTemplate | None = None) -> str:
    return render_report(report, template).text
