"""sijl.render.safety: what a report template can see and do.

Text Sijl did not write reaches a template only as an opaque `Text`, which prints escaped
(`report._md()`) and cannot be cut, joined, formatted or iterated into anything else; Sijl's
own Markdown is an opaque `Markdown`; everything else is a `View` of named fields, a tuple, a
number or a bool — never a plain str. The sandbox leaves templates no route to the raw text: no
string methods, no indexing or slicing a string, no `%` or `str.format`, only Sijl's filters
and a few list-only built-ins, no globals, nothing callable but `loop.cycle()`/`loop.changed()`,
and no construct that captures rendered output (`{% set %}` blocks, `{% filter %}`, macros, call
blocks, imports). A misspelt field fails loudly (StrictUndefined), naming the field."""

import pytest
from jinja2 import FunctionLoader, TemplateError, TemplateNotFound, TemplateSyntaxError

from sijl.render.safety import (
    Markdown,
    ReportEnvironment,
    TemplateProblem,
    Text,
    View,
    fields,
    problem,
)

HOSTILE = "[x](javascript:alert(1)) `a [y](https://evil.example)` <img src=z>"
# `{{ value }}` makes repository text inert wherever it stands: every structural character escaped
ESCAPED = r"\[x\](javascript:alert(1)) \`a \[y\](https://evil.example)\` \<img src=z\>"
# the md filter keeps a subject's code spans as written: for running text, where it is safe
RICH = r"\[x](javascript:alert(1)) `a [y](https://evil.example)` \<img src=z>"


def environment(**sources: str) -> ReportEnvironment:
    """Templates by name; each one's file name is its name, as the report's loader gives them."""
    sources["base"] = "{% block b %}[{{ t }}]{% endblock %}"
    return ReportEnvironment(
        FunctionLoader(
            lambda name: (sources[name], name, lambda: True) if name in sources else None
        )
    )


def render(source: str, **context: object) -> str:
    return environment(t=source).get_template("t").render(context)


def hostile() -> dict[str, object]:
    t = Text(HOSTILE)
    return {
        "t": t,
        "m": Markdown(r"\[ok]"),
        "v": View(name=t, n=1234, flag=True, rows=(View(subject=t),), nothing=None),
        "xs": (t, t),
    }


def test_text_prints_escaped_and_markdown_prints_as_written() -> None:
    assert render("{{ t }}", **hostile()) == ESCAPED
    assert render("{{ t|md }}", **hostile()) == RICH
    assert render("{{ m }}", **hostile()) == r"\[ok]"
    assert render("{{ v.name }} {{ v.rows[0].subject }}", **hostile()) == f"{ESCAPED} {ESCAPED}"


def test_numbers_print_with_thousands_and_nothing_prints_empty() -> None:
    assert render("{{ v.n }}|{{ v.flag }}|{{ v.nothing }}|{{ 7 }}", **hostile()) == "1,234|True||7"


def test_the_markdown_filters_are_the_report_s_own_escaping() -> None:
    ctx = {"t": Text("a | b [c](d)"), "p": Text("/x/`y`")}
    assert render("{{ t|md }}", **ctx) == r"a | b \[c](d)"
    assert render("{{ t|cell }}", **ctx) == r"a \| b \[c](d)"
    assert render("{{ p|code }}", **ctx) == "`` /x/`y` ``"
    assert render("{{ 'a|b'|cell }} {{ 3|plural('commit') }} {{ 1|plural('copy', 'copies') }}") == (
        r"a\|b 3 commits 1 copy"
    )
    assert render("{{ 1|share(8) }} {{ 0|share(8) }}") == "13% <1%"


def test_join_escapes_every_item_and_shorten_cuts_before_escaping() -> None:
    assert render("{{ xs|join(', ') }}", **hostile()) == f"{ESCAPED}, {ESCAPED}"
    # The cut falls inside the code span: escaped after the cut, its backtick and the link-shaped
    # text that was code are inert — cutting the escaped text would have left a live link.
    assert render("{{ t|shorten(42)|md }}", t=Text(HOSTILE)) == (
        r"\[x](javascript:alert(1)) \`a \[y](https://e…"
    )


ROUTES_TO_RAW_TEXT = [
    "{{ t ~ '' }}",  # concatenation calls str()
    "{{ t + '' }}",
    "{{ '%s' % t }}",
    "{{ '%s' % 'x' }}",  # no %-formatting at all
    "{{ '{}'.format(t) }}",
    "{{ t.upper() }}",
    "{{ t._text }}",
    "{{ t['_text'] }}",
    "{{ t|attr('_text') }}",
    "{{ t[1:] }}",
    "{% for c in t %}{{ c }}{% endfor %}",
    "{{ t|string }}",
    "{{ t|replace('[', '') }}",
    "{{ t|truncate(9) }}",
    "{{ t|e }}",
    "{{ t|safe }}",
    "{{ t|urlize }}",
    "{{ t|list|join }}",
    "{{ t|join }}",
    "{{ m|join }}",
    "{{ xs }}",  # a list's repr
    "{{ v }}",  # a view's repr
    "{{ m ~ '' }}",
    "{{ m[1:] }}",
    "{{ t.__class__ }}",
    "{{ v.__class__.__mro__ }}",
    "{{ v._fields }}",
    "{{ t|code|string }}",
    "{{ t|pprint }}",
    "{{ t|tojson }}",
    "{{ t|format }}",
]


@pytest.mark.parametrize("source", ROUTES_TO_RAW_TEXT)
def test_no_route_leads_from_text_to_its_raw_form(source: str) -> None:
    with pytest.raises((TemplateError, TypeError)):
        render(source, **hostile())


@pytest.mark.parametrize(
    ("source", "construct"),
    [
        ("a\n{% set x %}{{ t }}{% endset %}{{ x[1:] }}", "{% set %}"),
        ("a\n{% filter upper %}{{ t }}{% endfilter %}", "{% filter %}"),
        ("a\n{% macro m() %}{{ t }}{% endmacro %}{{ m() }}", "{% macro %}"),
        ("a\n{% call x() %}{{ t }}{% endcall %}", "{% call %}"),
        ("a\n{% import 'base' as b %}{{ (b ~ '')[1:] }}", "{% import %}"),
        ("a\n{% from 'base' import x %}", "{% from … import %}"),
    ],
)
def test_no_construct_can_capture_rendered_output(source: str, construct: str) -> None:
    with pytest.raises(TemplateSyntaxError) as raised:
        render(source, **hostile())
    assert raised.value.lineno == 2 and construct in raised.value.message


@pytest.mark.parametrize(
    "source",
    [
        "{% for x in xs recursive %}{{ loop(xs) }}{% endfor %}",  # a recursive loop's output
        "{% extends 'base' %}{% block b %}{{ super() }}{% endblock %}",  # the parent's output
        "{% extends 'base' %}{% block b %}{{ self.b() }}{% endblock %}",
    ],
)
def test_no_call_returns_rendered_output(source: str) -> None:
    with pytest.raises(TemplateError):
        render(source, **hostile())


@pytest.mark.parametrize("name", ["range", "dict", "namespace", "lipsum", "cycler", "joiner"])
def test_there_are_no_globals(name: str) -> None:
    with pytest.raises(TemplateError):
        render(f"{{{{ {name}() }}}}")


@pytest.mark.parametrize("source", ["{% include '/etc/passwd' %}", "{% extends '../x' %}"])
def test_no_template_reads_a_file_the_loader_does_not_serve(source: str) -> None:
    with pytest.raises(TemplateNotFound):
        render(source)


def test_loops_lists_and_comparisons_still_work() -> None:
    source = (
        "{% for r in v.rows %}{{ loop.index }}/{{ loop.length }} {{ loop.cycle('a', 'b') }} "
        "{{ r.subject|shorten(4) }}{% endfor %} {{ xs|length }} "
        "{{ v.rows|selectattr('subject')|list|length }} {% if t %}yes{% endif %} "
        "{% if t == 'other' %}no{% endif %}{% if v.name == v.rows[0].subject %}same{% endif %} "
        "{{ v.nothing|default('none', true) }}"
    )
    assert render(source, **hostile()) == r"1/1 a \[x\]… 2 1 yes same none"


def test_a_misspelt_field_fails_loudly_and_names_the_fields() -> None:
    with pytest.raises(TemplateError) as raised:
        render("{{ v.nme }}", **hostile())
    message = str(raised.value)
    assert "'nme' is not a field here" in message and "did you mean 'name'" in message
    with pytest.raises(TemplateError, match="is undefined"):
        render("{{ nobody }}")


def test_a_view_holds_no_plain_string() -> None:
    with pytest.raises(TypeError):
        View(name="raw text")
    with pytest.raises(TypeError):
        View(rows=(Text("a"), "raw"))
    with pytest.raises(TypeError):
        View(rows=[Text("a")])  # tuples only: a list could be changed after the check
    with pytest.raises(TypeError):
        View(_hidden=1)
    x = Text("x")
    assert fields(View(a=1, b=x)) == {"a": 1, "b": x}


def test_text_and_markdown_never_show_their_content_in_a_repr() -> None:
    assert HOSTILE not in repr(Text(HOSTILE)) and "ok" not in repr(Markdown("ok"))
    with pytest.raises(TemplateError):
        str(Text(HOSTILE))


def test_a_problem_names_the_template_and_line() -> None:
    env = environment(**{"mine.md.j2": "a\nb\n{{ v.nme }}\n", "bad.md.j2": "{% if %}"})
    try:
        env.get_template("mine.md.j2").render(hostile())
    except Exception as exc:  # the problem() under test takes any exception
        found = problem(exc)
    assert isinstance(found, TemplateProblem)
    assert (found.template, found.line) == ("mine.md.j2", 3)
    assert str(found).startswith("template mine.md.j2, line 3: 'nme' is not a field here")
    with pytest.raises(TemplateSyntaxError) as raised:
        env.get_template("bad.md.j2")
    assert str(problem(raised.value)).startswith("template bad.md.j2, line 1: ")


LINE_BREAKS = ("\n", "\r\n", "\r", "\u2028", "\u2029", "\x85", "\x0b", "\x0c")


@pytest.mark.parametrize("newline", LINE_BREAKS)
def test_no_value_can_break_its_line(newline: str) -> None:
    """A value is escaped as one piece, so a line break inside it would end
    the paragraph, the code span or the table row it stands in, and what follows would be live
    Markdown. Every value — repository text, Sijl's own Markdown, every filter — shows its
    line breaks as visible escapes (`\\x0a`), as the report did before templates."""
    text = Text(f"a`{newline}<img src=x>`{newline}[y](https://evil.example)")
    out = render(
        "{{ t }}|{{ t|md }}|{{ t|cell }}|{{ t|code }}|{{ t|shorten(30) }}|{{ xs|join }}|{{ m }}",
        t=text, xs=(text,), m=Markdown(f"b{newline}<img src=y>"),
    )  # fmt: skip
    assert not any(char in out for char in LINE_BREAKS), repr(out)
    assert "\\x0a" in out or "\\x0d" in out or "\\u202" in out or "\\x85" in out or "\\x0" in out


# `{{ value }}` must be inert wherever a template puts it — inside the
# template's own code span or link text too, where `_md()`'s balanced code spans and unescaped `]`
# turned repository text into links. Positions a template can give a value, with the links and
# images the template itself writes there:
POSITIONS = {
    "code span": ("`{{ v }}`", ()),
    "double code span": ("``{{ v }}`` and `{{ v }}`", ()),
    "link text": ("[{{ v }}](https://intranet.example/x)", ("https://intranet.example/x",)),
    "image alt": (
        "![{{ v }}](https://intranet.example/i.png)",
        ("https://intranet.example/i.png",),
    ),
    "link title": ('[t](https://intranet.example/x "{{ v }}")', ("https://intranet.example/x",)),
    "table cell": ("| a | b |\n|---|---|\n| {{ v }} | `{{ v }}` |", ()),
    "heading": ("# {{ v }}\n\n## `{{ v }}`", ()),
    "list item": ("- {{ v }}\n- `{{ v }}`\n1. {{ v }}", ()),
    "emphasis": ("**{{ v }}** _{{ v }}_ *`{{ v }}`*", ()),
    "block start": ("{{ v }}\n\n> {{ v }}", ()),
    "running text": ("see {{ v }} and {{ v }}, then `{{ v }}`", ()),
}
NASTY = [
    "[x](https://evil.example/a)", "`[x](https://evil.example/a)`",
    "x `[y](https://evil.example/a)` z",
    "a](https://evil.example/b) and", "]](https://evil.example/d)", "<img src=https://evil.example/i>",
    "`<img src=x onerror=alert(1)>`", "<https://evil.example/c>", "![i](https://evil.example/i.png)",
    "[r]: https://evil.example/ref", "[x][r]", "``", "`", "\\", "\\`", "<!-- x -->", "-->",
    "a\n\n<img src=x>", "# [h](https://evil.example/h)", "1. [l](https://evil.example/l)",
    "|[c](https://evil.example/c)|", '") [t](https://evil.example/t "', "&lt;img src=x&gt;",
]  # fmt: skip
PIECES = ["[", "]", "(", ")", "`", "``", "<", ">", "!", "\\", "|", "*", "#", '"', " ", "a",
          "https://evil.example/z", "img src=x", "\n", "-->", "&lt;"]  # fmt: skip


def foreign(markdown: str, own: tuple[str, ...]) -> list[str]:
    """Links, images and HTML in `markdown` that the template did not write itself."""
    from markdown_it import MarkdownIt

    found = []
    for token in MarkdownIt("commonmark").enable("table").parse(markdown):
        for t in (token, *(token.children or [])):
            if t.type in ("html_inline", "html_block"):
                found.append(t.content)
            elif t.type in ("link_open", "image"):
                where = t.attrGet("href") or t.attrGet("src")
                if where not in own:
                    found.append(where)
    return found


def test_a_value_is_inert_in_every_position_a_template_can_give_it() -> None:
    pytest.importorskip("markdown_it")
    import random

    rng = random.Random(16)
    values = NASTY + ["".join(rng.choice(PIECES) for _ in range(rng.randrange(1, 12)))
                      for _ in range(300)]  # fmt: skip
    for name, (source, own) in POSITIONS.items():
        env = environment(t=source)
        for value in values:
            out = env.get_template("t").render(v=Text(value))
            assert not foreign(out, own), (name, value, out)


def test_repository_text_is_no_oracle() -> None:
    """With `==` against the template's own strings and `shorten()`, a template
    recovered a subject character by character (`shorten(2) == '[…'`, then `shorten(3) == '[x…'`)
    and printed it through its own literals — live. Text equals only itself: never a str, never
    another Text with the same words; its hash is its identity; `shorten()` always returns a new
    Text, so `sameas` cannot measure its length either."""
    t = Text("[x](https://evil.example/a)")
    oracle = (
        "{% if t|shorten(2) == '[…' %}[{% endif %}{% if t|shorten(3) == '[x…' %}x{% endif %}"
        "{% if t == '[x](https://evil.example/a)' %}whole{% endif %}"
        "{% if t|shorten(99) is sameas t %}short{% endif %}"
        "{{ xs|select('equalto', '[x](https://evil.example/a)')|list|length }}"
        "{% if '[x](https://evil.example/a)' in xs %}in{% endif %}"
        "{% if t == t %}itself{% endif %}{% if t == u %}twin{% endif %}"
    )
    assert render(oracle, t=t, u=Text("[x](https://evil.example/a)"), xs=(t,)) == "0itself"
    assert Text("a") != Text("a") and Text("a") != "a" and hash(Text("a")) != hash("a")
    assert Markdown("a") != Markdown("a")


@pytest.mark.parametrize(
    "source",
    [
        "{{ 7 ** 30000000 > 1 }}",  # folded while compiling: it once took 57 s
        "{{ 2 ** 10 }}",  # no ** at all
        "{{ 'x' * 100000000 }}",
        "{{ xs * 100000000 }}",
        "{{ 100000000 * 'x' }}",
    ],
)
def test_no_operator_can_make_a_template_run_or_grow_without_end(source: str) -> None:
    """Templates compile without constant folding, `**` is refused and
    repeating a string or a list is capped, so each of these fails at once."""
    import time

    started = time.perf_counter()
    with pytest.raises(TemplateError):
        render(source, xs=(1, 2, 3))
    assert time.perf_counter() - started < 2.0


def test_small_arithmetic_and_short_repeats_still_work() -> None:
    assert render("{{ 6 * 7 }} {{ '-' * 3 }} {{ 7 % 3 }} {{ 2 + 2 }}") == "42 --- 1 4"
