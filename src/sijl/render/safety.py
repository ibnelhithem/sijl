"""What a report template can see and do.

A report is Markdown, so Jinja2 runs with autoescape off — its escaping is HTML's and would mangle
Markdown. Escaping is made the only way repository text reaches the page instead:

- **Text Sijl did not write** — names, subjects, titles, paths, ticket keys, git's words — is
  a `Text`. It is opaque: no string methods, no indexing, no iteration, and `str()` refuses. It is
  printed only by `finalize` (every `{{ … }}`), which makes it **inert wherever it stands**
  (`inert()`: every character that can open or close Markdown structure escaped, no code span
  kept), or by the `md`, `cell` and `code` filters (`report._md()`, `_cell()`, `_code()`), which
  keep a subject's code spans as written and are right in running text and table cells — the
  built-in templates use them there. Whatever a template does, the text reaches the page escaped.
- **Markdown Sijl wrote** — a sentence holding escaped names, a link `_link()` built — is a
  `Markdown`, printed as it is and just as opaque: cutting it could unbalance a code span and turn
  the escaped text inside into live markup.
- **Everything else** a template reaches is a `View` (named fields), a tuple, an int, a bool or
  None. A `View` refuses a plain str, so no raw text is reachable at all.
- **Every value is one line.** Line breaks and other controls inside a value — `\\n`, `\\r`,
  U+2028, U+2029, U+0085 — become visible escapes (`text.printable()`) as the value is printed or
  wrapped, never after the page is put together: splitting the page on its newlines first would
  turn a newline inside a folder name into a real line break.

`ReportEnvironment` is Jinja2's `SandboxedEnvironment`, narrowed: the only attributes are a View's
fields and a loop's counters; `%` and `str.format` are gone (a template's only strings are its own
literals: Text and Markdown cannot be indexed, sliced or iterated at all); the filters are
Sijl's and a few list-only built-ins; there are no globals (`range`, `dict`, `namespace`, …);
nothing is callable but `loop.cycle()` and `loop.changed()`; and the constructs that capture
rendered output into a value a template could take apart — `{% set %}` blocks, `{% filter %}`,
`{% macro %}`, `{% call %}`, `{% import %}` — are refused when a template is read
(`{% include %}` and `{% extends %}` stay: they only print). Calling a recursive loop, `super()`
or `self.<block>()` would hand back rendered output too, and is refused as a call.
`FieldUndefined` (strict) makes a misspelt field fail loudly; `problem()` names the template and
line. The loader is the caller's: the report's serves only the built-in templates and the one file
the user named, so no template can read any other file.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from types import MethodType, TracebackType
from typing import Any

from jinja2 import BaseLoader, StrictUndefined, TemplateSyntaxError, Undefined, nodes
from jinja2.exceptions import SecurityError, TemplateNotFound, TemplateRuntimeError
from jinja2.runtime import LoopContext
from jinja2.sandbox import SandboxedEnvironment

from sijl.report import _cell, _code, _count, _md, share
from sijl.text import printable

# Built-in filters that only count, pick or order: none turns text into other text. `join` is
# replaced by Sijl's own (`join` below), which escapes every item.
_KEPT_FILTERS = (
    "default", "d", "length", "first", "last", "list", "map", "select", "reject", "selectattr",
    "rejectattr", "sort", "sum", "min", "max", "reverse",
)  # fmt: skip
# A loop's counters and neighbours; `cycle` and `changed` are its only methods a template calls.
_LOOP_FIELDS = frozenset(
    {"index", "index0", "revindex", "revindex0", "first", "last", "length", "depth", "depth0",
     "previtem", "nextitem", "cycle", "changed"}
)  # fmt: skip
_LOOP_METHODS = frozenset({"cycle", "changed"})
# Constructs that turn rendered output into a value: refused when a template is read.
_CAPTURES: tuple[tuple[type[nodes.Node], str], ...] = (
    (nodes.AssignBlock, "{% set %} … {% endset %}"),
    (nodes.FilterBlock, "{% filter %}"),
    (nodes.Macro, "{% macro %}"),
    (nodes.CallBlock, "{% call %}"),
    (nodes.Import, "{% import %}"),  # the module's str() is the template's rendered output
    (nodes.FromImport, "{% from … import %}"),
)
_HOW_TO_PRINT = "print it as {{ value }}, or with the md, cell or code filter"
MAX_REPEAT = 10_000  # `"-" * 3` is fine; a string or list repeated past this many items is not
# Every character that can open, close or cancel inline Markdown — a code span, a link or image,
# an autolink or HTML, a table cell — escaped wherever it stands (inside a template's own code
# span a backslash means nothing, so `_md()`'s kept code spans could close it).
_INERT = re.compile(r"([\\`\[\]<>!|])")
# What turns a line into a heading, list item, thematic break or setext underline: escaped when
# the text starts with it (`# x`, `- x`, `1. x`, `===`).
_INERT_START = re.compile(r"\A(?:([#+*=_-])|([0-9]{1,9})([.)]))")


class Text:
    """Text Sijl did not write: printable only escaped (module docstring). True when not
    empty; equal only to itself — never to a str, never to another Text with the same words —
    and hashed by identity, so no comparison can reveal what it says (`==` with the template's
    own strings and `shorten()` could recover a subject character by character)."""

    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError(f"Text holds a str, not {type(text).__name__}")
        self._text = text

    def __str__(self) -> str:
        raise SecurityError(f"repository text cannot be turned into a string: {_HOW_TO_PRINT}")

    def __format__(self, spec: str) -> str:
        raise SecurityError(f"repository text cannot be formatted: {_HOW_TO_PRINT}")

    def __repr__(self) -> str:
        return "Text(…)"  # never the content: a repr can reach a page

    def __bool__(self) -> bool:
        return bool(self._text)


class Markdown:
    """Markdown Sijl wrote, every piece of repository text in it already escaped: printed as
    it is, and never taken apart (module docstring). It is one line, always: `printable()` shows
    every line break and control character in it as a visible escape (`\\x0a`), so a name or a
    path inside it can never end the line, code span or table row it stands in."""

    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError(f"Markdown holds a str, not {type(text).__name__}")
        self._text = printable(text)

    def __str__(self) -> str:
        raise SecurityError("Sijl's Markdown cannot be turned into a string: print it")

    def __format__(self, spec: str) -> str:
        raise SecurityError("Sijl's Markdown cannot be formatted: print it")

    def __repr__(self) -> str:
        return "Markdown(…)"

    def __bool__(self) -> bool:
        return bool(self._text)  # equal only to itself, like a Text


def _check(value: object) -> None:
    """A value a View may hold: Text, Markdown, a number, a bool, None, a View, or a tuple of
    those — never a str, and never a list (it could change after this check)."""
    if value is None or isinstance(value, Text | Markdown | int | View):  # bool is an int
        return
    if isinstance(value, tuple):
        for item in value:
            _check(item)
        return
    kind = type(value).__name__
    raise TypeError(f"a View holds Text, Markdown, numbers, views and tuples of them, not {kind}")


class View:
    """Named fields for templates (module docstring): `View(name=Text(…), commits=3)`. Its only
    attributes are its fields; `fields()` lists them for Python code."""

    __slots__ = ("_fields",)

    def __init__(self, **values: object) -> None:
        for name, value in values.items():
            if name.startswith("_"):
                raise TypeError(f"a field name cannot start with '_': {name}")
            _check(value)
        object.__setattr__(self, "_fields", values)

    def __getattr__(self, name: str) -> object:
        try:
            return self._fields[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("a View cannot be changed")

    def __repr__(self) -> str:
        return f"View({', '.join(self._fields)})"  # the field names, never their values


def fields(view: View) -> dict[str, object]:
    """A View's fields, for Python code (templates read them as attributes)."""
    return dict(view._fields)


def masked(value: object) -> object:
    """`value` with every text replaced by "x" (or by nothing when it was empty) — the same views,
    lists, numbers and truth values otherwise. A template rendered over masked values writes only
    its own text: what is left of an address there, the template wrote (`render.markdown`)."""
    if isinstance(value, Text):
        return Text("x" if value._text else "")
    if isinstance(value, Markdown):
        return Markdown("x" if value._text else "")
    if isinstance(value, View):
        return View(**{name: masked(item) for name, item in value._fields.items()})
    if isinstance(value, tuple):
        return tuple(masked(item) for item in value)
    return value


def _kind(value: object) -> str:
    if isinstance(value, View):
        return f"a group of fields ({', '.join(fields(value))}): print one of them"
    if isinstance(value, tuple | list):
        return "a list: loop over it, or join it with the join filter"
    return f"a {type(value).__name__}"


def inert(text: str) -> str:
    """`text` as plain text in any Markdown position — running text, the template's own code span,
    link text, a table cell, a heading — with every structural character backslash-escaped (inside
    a code span the template opened, the backslashes show, but nothing can escape it)."""
    escaped = _INERT.sub(r"\\\1", text)
    return _INERT_START.sub(lambda m: f"\\{m[1]}" if m[1] else f"{m[2]}\\{m[3]}", escaped)


def finalize(value: object) -> str:
    """What every `{{ … }}` prints: repository text inert (`inert()`), Sijl's Markdown as it
    is, numbers with thousands separators, the template's own strings as written, None as
    nothing — and anything else refused, so no repr (of a list, a view, a template module) reaches
    the page."""
    if isinstance(value, Text):
        return printable(inert(value._text))  # inert wherever it stands, and one line
    if isinstance(value, Markdown):
        return value._text
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, str):  # the template's own text: a literal, or `loop.cycle("a", "b")`
        return value
    if value is None:
        return ""
    if isinstance(value, Undefined):
        return str(value)  # strict: raises, naming what is missing
    raise TemplateRuntimeError(f"cannot print {_kind(value)}")


# ------------------------------------------------------------------------------------ filters


def _raw(value: object, filter_name: str) -> str:
    """The text a filter escapes: repository text, the template's own string, or a number, a
    yes/no or nothing, as printing writes them."""
    if isinstance(value, Text):
        return value._text
    if isinstance(value, str | int) or value is None:  # bool is an int
        return finalize(value)
    raise TemplateRuntimeError(f"the {filter_name} filter takes text, not {_kind(value)}")


def md_filter(value: object) -> Markdown:
    """`{{ x|md }}`: `_md()` — what printing does anyway; Markdown passes as it is."""
    return value if isinstance(value, Markdown) else Markdown(_md(_raw(value, "md")))


def cell_filter(value: object) -> Markdown:
    """`{{ x|cell }}`: a table cell, `_cell()` — `|` escaped too; Markdown gets only that."""
    if isinstance(value, Markdown):
        return Markdown(value._text.replace("|", "\\|"))
    return Markdown(_cell(_raw(value, "cell")))


def code_filter(value: object) -> Markdown:
    """`{{ x|code }}`: one inert code span, `_code()`."""
    return Markdown(_code(_raw(value, "code")))


def plural_filter(n: object, noun: object, plural: object = "") -> Markdown:
    """`{{ n|plural("commit") }}` → "1 commit" / "1,183 commits"; `plural` for irregular nouns."""
    if not isinstance(n, int) or isinstance(n, bool):
        raise TemplateRuntimeError(f"the plural filter counts a number, not {_kind(n)}")
    if not isinstance(noun, str) or not isinstance(plural, str):
        raise TemplateRuntimeError("the plural filter's nouns are the template's own words")
    return Markdown(_md(_count(n, noun, plural)))


def share_filter(n: object, total: object) -> Markdown:
    """`{{ n|share(total) }}` → "13%", "<1%", ">99%" — `report.share()`."""
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (n, total)):
        raise TemplateRuntimeError("the share filter takes two numbers")
    if total <= 0 or not 0 <= n <= total:  # type: ignore[operator]
        raise TemplateRuntimeError(f"no share of {n} in {total}")
    return Markdown(share(n, total))  # type: ignore[arg-type]


def join_filter(items: object, separator: object = "") -> Markdown:
    """`{{ names|join(", ") }}`: every item printed as `{{ … }}` prints it — repository text
    escaped, each a self-contained piece — between the template's own separators."""
    if isinstance(items, str | Text | Markdown) or not isinstance(items, Iterable):
        raise TemplateRuntimeError(f"the join filter joins a list, not {_kind(items)}")
    if not isinstance(separator, str):
        raise TemplateRuntimeError("the join filter's separator is the template's own text")
    return Markdown(separator.join(finalize(item) for item in items))


def shorten_filter(value: object, width: object) -> Text:
    """`{{ subject|shorten(60) }}`: at most `width` characters, the last one "…" when cut. The
    raw text is cut and stays a `Text`, so it is escaped after the cut — cutting escaped text
    could leave half a code span, and live markup after it."""
    if not isinstance(value, Text):
        raise TemplateRuntimeError(f"the shorten filter takes repository text, not {_kind(value)}")
    if not isinstance(width, int) or isinstance(width, bool) or width < 2:
        raise TemplateRuntimeError("the shorten filter needs a width of at least 2")
    text = value._text
    return Text(text if len(text) <= width else text[: width - 1] + "…")  # always a new one


FILTERS = {
    "md": md_filter,
    "cell": cell_filter,
    "code": code_filter,
    "plural": plural_filter,
    "share": share_filter,
    "join": join_filter,
    "shorten": shorten_filter,
}


# -------------------------------------------------------------------------------- environment


class FieldUndefined(StrictUndefined):
    """Strict: printing, testing or looping over something missing fails — and for a View's
    field, the message lists the fields there are and the closest one."""

    __slots__ = ()

    @property
    def _undefined_message(self) -> str:
        holder = self._undefined_obj
        if isinstance(holder, View) and self._undefined_hint is None:
            names = sorted(fields(holder))
            close = difflib.get_close_matches(str(self._undefined_name), names, n=1)
            hint = f" — did you mean {close[0]!r}?" if close else ""
            return (
                f"{self._undefined_name!r} is not a field here{hint} (the fields are: "
                f"{', '.join(names)})"
            )
        return super()._undefined_message


class ReportEnvironment(SandboxedEnvironment):
    """Jinja2's sandbox, narrowed to what a report template needs (module docstring). Markdown
    output: no autoescape; blocks on their own lines leave no blank lines behind (`trim_blocks`,
    `lstrip_blocks`); a template's final newline is kept."""

    intercepted_binops = frozenset({"%", "**", "*"})

    def __init__(self, loader: BaseLoader) -> None:
        super().__init__(
            loader=loader,
            autoescape=False,
            undefined=FieldUndefined,
            finalize=finalize,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            auto_reload=False,
            optimized=False,  # no constant folding: `{{ 7 ** 30000000 }}` would run while compiling
        )
        self.globals.clear()  # no range, dict, namespace, lipsum, cycler or joiner
        self.filters = {name: self.filters[name] for name in _KEPT_FILTERS} | FILTERS

    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        if attr.startswith("_"):
            return False
        if isinstance(obj, View):
            return attr in fields(obj)
        if isinstance(obj, LoopContext):
            return attr in _LOOP_FIELDS
        return False  # no str method, no list method, nothing of a template or a module

    def is_safe_callable(self, obj: Any) -> bool:
        return (
            isinstance(obj, MethodType)
            and isinstance(obj.__self__, LoopContext)
            and obj.__name__ in _LOOP_METHODS
        )

    def wrap_str_format(self, value: Any) -> None:
        return None  # `str.format` is a str attribute like any other: refused

    def call_binop(self, context: Any, operator: str, left: Any, right: Any) -> Any:
        """`%` only on numbers; no `**` (one power can take minutes); `*` repeats a string or list
        to at most `MAX_REPEAT` items (no denial of service)."""
        if operator == "%" and not isinstance(left, int | float):
            raise SecurityError("% formatting is not available in a report template")
        if operator == "**":
            raise SecurityError("** is not available in a report template")
        if operator == "*":
            for items, times in ((left, right), (right, left)):
                repeat = isinstance(items, str | tuple | list) and isinstance(times, int)
                if repeat and len(items) * max(times, 0) > MAX_REPEAT:
                    raise SecurityError(f"a repeat past {MAX_REPEAT:,} items is not available")
        return super().call_binop(context, operator, left, right)

    def _parse(self, source: str, name: str | None, filename: str | None) -> nodes.Template:
        """Jinja2's parse, then the capture refusal (module docstring) — with the line, so the
        user can find it."""
        tree = super()._parse(source, name, filename)
        for kind, label in _CAPTURES:
            for node in tree.find_all(kind):
                raise TemplateSyntaxError(
                    f"{label} is not available in a report template: it would capture rendered "
                    "text, which a template only prints",
                    node.lineno,
                    name,
                    filename,
                )
        return tree


# --------------------------------------------------------------------------------- problems


class TemplateProblem(Exception):
    """A template that cannot be read or rendered: which, where and why — for a usage error."""

    def __init__(self, template: str, line: int | None, message: str) -> None:
        super().__init__(message)
        self.template, self.line, self.message = template, line, message

    def __str__(self) -> str:
        where = f"template {self.template}" + (f", line {self.line}" if self.line else "")
        return f"{where}: {self.message}"


def _innermost_template_frame(tb: TracebackType | None) -> tuple[str, int] | None:
    """The file and line of the last template frame in a traceback Jinja2 rewrote: the place in
    the template where it went wrong (Jinja2 marks those frames with `__jinja_exception__`)."""
    found = None
    while tb is not None:
        frame_globals = tb.tb_frame.f_globals
        if "__jinja_exception__" in frame_globals:
            found = (str(frame_globals.get("__file__", "")), tb.tb_lineno)
        tb = tb.tb_next
    return found


def problem(exc: BaseException) -> TemplateProblem:
    """What went wrong with a template, as the user can act on it: the template's file (or
    built-in name) and line, and the reason."""
    if isinstance(exc, TemplateSyntaxError):
        return TemplateProblem(str(exc.filename or exc.name or "?"), exc.lineno, exc.message or "")
    if isinstance(exc, TemplateNotFound):
        message = f"there is no built-in template named {exc.name!r}"
    elif isinstance(exc, TemplateRuntimeError | SecurityError):
        message = str(exc)
    else:
        message = f"{type(exc).__name__}: {exc}"
    where = _innermost_template_frame(exc.__traceback__)
    return (
        TemplateProblem(where[0], where[1], message)
        if where
        else TemplateProblem("?", None, message)
    )
