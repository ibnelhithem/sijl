"""The citation check reads the answer as Markdown shows it too.
`sijl ask … > answer.md` is rendered by whatever opens it: `cafe**babe**`, `` `cafe`babe ``,
`cafe<b></b>babe`, `cafe&#x200b;babe` or `PROJ-<!-- -->99` show "cafebabe" and "PROJ-99" while
the text as written holds no such token. The check also reads a Markdown view of the answer —
markup removed, entities decoded, links reduced to their text — and every unknown id it shows is
marked in the answer, after the construct, and counted. The check fails closed: an answer too
long to check, a check that takes too long or fails, is "not verified" — never "all found" — and
every pattern and loop is linear in the answer's length."""

import itertools
import random
import re
import time

import pytest
from markdown_it import MarkdownIt
from typer.testing import CliRunner

from sijl import citations, cli, ids, text
from sijl.citations import KNOWN, UNKNOWN
from sijl.cli import app
from sijl.text import printable_block
from tests.scaling import growth_problem
from tests.test_citations import evidence
from tests.test_cli_ask import KEY, QUESTION, args, claude, repos  # noqa: F401  (fixtures)
from tests.test_regex_budget import PATTERNS

runner = CliRunner()


def cited(answer: str) -> list[tuple[str, str, bool]]:
    return [(c.id, c.status, c.rendered) for c in citations.check(answer, evidence()).cited]


@pytest.mark.parametrize(
    ("answer", "shown_id", "marked"),
    [
        ("Fixed in cafe**babe** for PROJ-12.", "cafebabe", "cafe**babe**"),
        ("Fixed in `cafe`babe.", "cafebabe", "`cafe`babe"),
        ("Fixed in cafe<b></b>babe.", "cafebabe", "cafe<b></b>babe"),
        ("Fixed in cafe&ZeroWidthSpace;babe.", "cafe\u200bbabe", "cafe&ZeroWidthSpace;babe"),
        ("Fixed in cafe&#x200b;babe.", "cafe\u200bbabe", "cafe&#x200b;babe"),
        ("Ticket PROJ-<!-- -->99.", "PROJ-99", "PROJ-<!-- -->99"),
        ("Ticket PROJ&#x2d;99.", "PROJ-99", "PROJ&#x2d;99"),
        ("See [cafe](https://x.test/y)babe.", "cafebabe", "[cafe](https://x.test/y)babe"),
        ("See ![cafe](x.png)babe.", "cafebabe", "![cafe](x.png)babe"),
        ("See [cafe][ref]babe.", "cafebabe", "[cafe][ref]babe"),
        ("See dead**beef**.", "deadbeef", "dead**beef**"),  # a known SHA, split by markup
        # autolinks: shown as their address, decoded as markdown-it decodes it
        ("See <ab:cafe>babe.", "cafebabe", "<ab:cafe>babe"),
        ("Mail <x@cafe>babe.", "cafebabe", "<x@cafe>babe"),
        ("See <ab:caf%65>babe.", "cafebabe", "<ab:caf%65>babe"),
        ("See <http://xn--cafe->babe.", "cafebabe", "<http://xn--cafe->babe"),
        ("Ticket <ab:PROJ->99.", "PROJ-99", "<ab:PROJ->99"),
    ],
)
def test_what_markdown_shows_is_checked_and_marked_after_the_construct(
    answer: str, shown_id: str, marked: str
) -> None:
    check = citations.check(answer, evidence())
    assert not check.ok
    assert (shown_id, UNKNOWN, True) in cited(answer)
    assert f"{marked}{citations.MARK}" in citations.mark(answer, check)
    assert "NOT in your evidence, marked in the answer above" in citations.note(
        citations.read(answer, evidence())
    )


def test_a_construct_already_marked_as_written_is_not_marked_twice() -> None:
    """`PROJ&#45;98` holds `#45`, an unknown request as written; that mark covers the
    rendered PROJ-98 over the same characters."""
    answer = "Ticket PROJ&#45;98."
    check = citations.check(answer, evidence())
    assert not check.ok
    assert citations.mark(answer, check) == f"Ticket PROJ&#45{citations.MARK};98."


def test_ids_markdown_shows_as_written_are_verified_once() -> None:
    answer = "**PROJ-12**, `deadbeef`, [webapp!42](https://x.test/y) and *app#12*."
    assert cited(answer) == [
        ("PROJ-12", KNOWN, False),
        ("deadbeef", KNOWN, False),
        ("webapp!42", KNOWN, False),
        ("app#12", KNOWN, False),
    ]


def _markdown_it_visible(source: str, *, gfm: bool = False) -> str:
    """What markdown-it renders as text: text, code, image alt text and raw HTML's text —
    CommonMark's, or with `gfm` GitHub's strikethrough too (`~~x~~` shows `x`)."""
    out = []
    markdown = MarkdownIt("commonmark", {"html": True})
    for token in (markdown.enable("strikethrough") if gfm else markdown).parse(source):
        if token.type == "inline":
            for child in token.children or []:
                if child.type in ("text", "code_inline", "image"):
                    out.append(child.content)
                elif child.type in ("softbreak", "hardbreak"):
                    out.append("\n")
        elif token.type in ("code_block", "fence", "html_block"):
            out.append(token.content)
        out.append("\n")
    return "".join(out)


def snippets(seed: int = 24, count: int = 600) -> list[str]:
    """Ids split and wrapped by every Markdown and HTML construct, at random."""
    rnd = random.Random(seed)
    pieces = ["cafebabe", "deadbeef", "PROJ-99", "PROJ-12", "acme!123", "app#12", "1a2b3c4d"]
    wraps = ["**{}**", "*{}*", "`{}`", "_{}_", "<b>{}</b>", "<!-- x -->{}", "[{}](u)", "![{}](u)",
             "[{}][r]", "{}<br>", "&#x200b;{}", "\\{}", "~~{}~~", "<span>{}</span>",
             "<ab:{}>", "<https://x.test/{}>", "<x@{}>", "<{}@x.test>", "<ab:%{}>",
             "<http://xn--{}->"]  # fmt: skip
    made = []
    for _ in range(count):
        token = rnd.choice(pieces)
        cut = rnd.randint(1, len(token) - 1)
        left, right = token[:cut], token[cut:]
        made.append(f"x {_wrap(rnd.choice(wraps), left)}{_wrap(rnd.choice(wraps), right)} y")
    return made


def _wrap(wrap: str, piece: str) -> str:
    """`piece` in `wrap`; `%{}` percent-encodes its first character, as a URL may."""
    if "%{}" in wrap:
        return wrap.replace("%{}", f"%{ord(piece[0]):02X}{piece[1:]}")
    return wrap.format(piece)


# The label `[r]` of the snippets, defined: then `[x][r]` is a link, showing `x` alone.
DEFINED = "\n\n[r]: https://x.test/r\n"


def test_whatever_markdown_it_renders_unknown_is_never_verified() -> None:
    """The security property, over 600 random snippets: whenever markdown-it shows an
    id-shaped token the digest does not hold there, as written, the check is not ok — with the
    snippet's reference label defined or not."""
    evidence_ = evidence()
    for source in snippets():
        for rendered, gfm in itertools.product((source, source + DEFINED), (False, True)):
            visible = _markdown_it_visible(rendered, gfm=gfm)
            shown = [m for m in ids.CANDIDATE.finditer(visible)]
            unknown = [m.group() for m in shown if m.group() not in evidence_.ids]
            if unknown:
                assert not citations.check(rendered, evidence_).ok, (rendered, unknown)


def test_the_markdown_view_shows_every_token_commonmark_shows() -> None:
    """Every token markdown-it shows is in the view or in the text as written — but where `_`
    is involved (emphasis, or a name's own): the view drops it wherever a renderer may. The
    view drops the brackets of `[x][r]` as if `r` were defined (it may be, anywhere in the
    answer) and `~~` as GitHub does, so markdown-it renders the snippet so too."""
    for source in snippets():
        if "_" in source:
            continue
        visible = _markdown_it_visible(source + DEFINED, gfm=True)
        rendered = {m.group() for m in ids.CANDIDATE.finditer(visible)}
        seen = {m.group() for m in ids.CANDIDATE.finditer(citations.markdown_view(source)[0])}
        seen |= {m.group() for m in ids.CANDIDATE.finditer(source)}  # the text as written
        assert rendered <= seen, (source, rendered - seen)


# ------------------------------------------------------------------- failing closed, linear


def test_an_answer_too_long_to_check_is_not_verified() -> None:
    long = "PROJ-12 " * (citations.CHECK_MAX_CHARS // 8 + 1)
    started = time.perf_counter()
    check = citations.check(long, evidence())
    assert time.perf_counter() - started < 1
    assert not check.ok and check.unverified
    note = citations.note(citations.read(long, evidence()))
    assert "- The rest of the answer could NOT be checked: the answer is " in note
    assert "Treat every other id in it as unverified." in note


def test_a_failure_inside_the_check_is_not_verified(monkeypatch) -> None:
    def broken(shown, evidence, deadline):
        raise RuntimeError("boom")

    monkeypatch.setattr(citations, "_check", broken)
    check = citations.check("PROJ-12", evidence())
    assert not check.ok and check.unverified == "the check failed (RuntimeError)"


def test_a_check_that_takes_too_long_is_not_verified(monkeypatch) -> None:
    monkeypatch.setattr(citations, "CHECK_SECONDS", -1.0)
    check = citations.check("PROJ-12 " * 10_000, evidence())
    assert not check.ok and check.unverified == "checking it took too long"


def test_a_failing_check_makes_ask_exit_5(claude, repos, network, monkeypatch) -> None:  # noqa: F811
    def broken(shown, evidence, deadline):
        raise RuntimeError("boom")

    monkeypatch.setattr(citations, "_check", broken)
    network.consent()
    claude.replies = [("You delivered PROJ-12.", "end_turn", 0, 0)]
    result = runner.invoke(app, args(repos, QUESTION, "--yes"), env=KEY)
    assert result.exit_code == cli.EXIT_UNVERIFIED, result.output
    assert "could NOT be checked: the check failed (RuntimeError)" in result.stdout
    assert "could not check the ids in this answer" in result.stderr


HOSTILE = {
    "id runs": lambda n: ("deadbeef PROJ-99 acme!12 " * (n // 25 + 1))[:n],
    "invisible every other char": lambda n: ("d\u034f" * (n // 2 + 1))[:n],
    "escaped invisibles": lambda n: printable_block("b\u200b" * (n // 2 + 1))[:n],
    "markdown nesting": lambda n: ("![[`*_<b>&#x2d;" * (n // 15 + 1))[:n],
    "open tags": lambda n: ("<a" * (n // 2 + 1))[:n],
    "link destinations": lambda n: ("[a](" * (n // 4 + 1))[:n],
    "entities": lambda n: ("&#x200b;" * (n // 8 + 1))[:n],
    "look-alikes": lambda n: ("PROJ-l\u039f1-" * (n // 9 + 1))[:n],
    "autolinks": lambda n: ("<ab:cafe>babe<x@y>" * (n // 18 + 1))[:n],
    "brackets before one closer": lambda n: ("<1" * (n // 2 + 1))[: n - 1] + ">",
    "one long autolink": lambda n: "<http://xn--" + ("a%41." * n)[: n - 13] + ">",
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_check_and_mark_are_linear_in_the_answer(name: str) -> None:
    make = HOSTILE[name]
    evidence_ = evidence()

    def check_and_mark(answer: str) -> None:
        citations.mark(answer, citations.check(answer, evidence_))

    sizes = (25_000, 50_000, 100_000, citations.CHECK_MAX_CHARS)
    assert not growth_problem(check_and_mark, make, sizes), name


def test_a_hostile_megabyte_is_answered_in_under_two_seconds() -> None:
    """1 MB: id-shaped runs, invisible characters every other character, 50,000 candidate
    tokens, deep Markdown nesting — over the limit, so not verified, at once."""
    parts = [
        ("deadbeef PROJ-99 " * 30_000),  # 60,000 candidates
        "x\u034f" * 100_000,
        "![[`*_<b>" * 30_000,
    ]
    answer = "".join(parts)[:1_000_000]
    started = time.perf_counter()
    check = citations.check(answer, evidence())
    citations.mark(answer, check)
    assert time.perf_counter() - started < 2
    assert not check.ok and check.unverified


def test_the_regex_budget_guards_every_pattern_of_the_check() -> None:
    """tests/test_regex_budget.py finds every module-level pattern (each under the first name it
    meets): the check's own are among them, so their growth on hostile text is judged."""
    watched = set(PATTERNS.values())
    assert {
        citations._ESCAPE,
        citations._ASCII_LOOKALIKE,
        citations._ENTITY,
        ids.CANDIDATE,
        ids.ID_PATTERN,
        ids.ID,
        text._IGNORABLE,
    } <= watched


@pytest.mark.parametrize("at", [0, 10, 1023, 1025, -1])
def test_an_unclosed_comment_anywhere_ends_the_check_at_once(at: int) -> None:
    """An unclosed `<!--` once sent the Markdown view back two characters, for
    ever, with unbounded memory — and the deadline only looked at the clock when the position was
    a multiple of 1,024. A comment without `-->` runs to the end of the text; every loop of the
    check must move forward, and the deadline is checked on elapsed time."""
    filler = "PROJ-12 and cafebabe " * 100
    answer = filler[:at] + "<!--" + filler[at:] if at >= 0 else filler + "<!--"
    started = time.perf_counter()
    check = citations.check(answer, evidence())
    assert time.perf_counter() - started < 1
    assert not check.unverified
    assert citations.markdown_view("ab<!--cd")[0] == "ab"


def test_a_loop_that_stops_advancing_fails_closed(monkeypatch) -> None:
    """Defence in depth: a scanning loop that does not move forward raises inside the check, and
    the check is then "not verified" — never a hang, never "all found"."""
    monkeypatch.setattr(citations, "_ENTITY", re.compile(r"(?=&)"))  # an empty match at `&`
    monkeypatch.setattr(citations.html, "unescape", lambda entity: "x")  # "decoded" to x
    check = citations.check("a &amp; b PROJ-12", evidence())
    assert check.unverified.startswith("the check failed")
