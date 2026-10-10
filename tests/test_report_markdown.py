"""The Markdown report and text it did not write (link injection): a commit subject, a
folder name or git's words must never become a link, an image or an HTML tag in a Markdown viewer
— only links Sijl builds itself may. Characters that start markup are escaped outside code
spans, so the rendered text still reads exactly as written; paths are code spans."""

import random
import re
import time
from datetime import date
from pathlib import Path

import pytest
from jinja2 import Undefined
from typer.testing import CliRunner

from sijl.classify import Conventional
from sijl.cli import app
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.models import Repo
from sijl.render.context import context
from sijl.render.safety import Markdown, Text
from sijl.report import _code, _link, _md, build_report, render_markdown
from tests.gitfactory import ME, PERIOD_ARGS, REVIEW_PERIOD
from tests.test_render_markdown import walk

ATTACK = "see [docs](javascript:alert(1)) and ![x](data:text/html,y) <a href=javascript:z>"


def report_of(*paths: Path, **extra) -> str:
    return render_markdown(
        build_report(
            roots=[],
            paths=list(paths),
            identities=IdentitySet.from_emails([ME.email]),
            window=REVIEW_PERIOD,
            git=GitRunner(),
            generated_on=date(2026, 10, 8),
            **extra,
        )
    )


def test_markup_is_escaped_outside_code_spans() -> None:
    assert _md(ATTACK) == (
        r"see \[docs](javascript:alert(1)) and !\[x](data:text/html,y) \<a href=javascript:z>"
    )
    assert _md(r"C:\temp") == r"C:\\temp"


def test_code_spans_are_kept_as_written() -> None:
    assert _md("fix `a[0]` and `<b>`") == "fix `a[0]` and `<b>`"
    assert _md("a ``x`[y]`` [z]") == r"a ``x`[y]`` \[z]"
    assert _md("a lone ` then [x](y)") == r"a lone \` then \[x](y)"  # closes nothing: escaped
    assert _md("``a`") == r"\`\`a\`"
    assert _md("dry-run -> approve -> MR") == "dry-run -> approve -> MR"  # nothing to escape


def test_code_puts_any_text_in_one_inert_span() -> None:
    assert _code("/Users/alex/app") == "`/Users/alex/app`"
    assert _code("a`b") == "``a`b``"
    assert _code("`x") == "`` `x ``"
    assert _code("") == "(empty)"  # a bare "``" would pair with the next piece's backticks


def test_a_link_s_text_cannot_end_it_early_and_only_a_built_url_is_linked() -> None:
    url = "https://github.com/acme/app/commit/" + "ab" * 20
    assert _link("x](javascript:y) [z", url) == rf"[x\](javascript:y) \[z]({url})"
    assert _link("`a]b`", url) == f"[`a]b`]({url})"  # a code span keeps its "]"
    for bad in ("javascript:alert(1)", "data:text/html,x", "/relative", "http://github.com/a"):
        assert _link("text", bad) == "text"
    assert _link("t", "https://github.com/a/b)](javascript:x") == "t"


def test_a_subject_cannot_become_a_link(make_repo) -> None:
    repo = make_repo("app")
    repo.commit(ATTACK, at="2026-05-04T10:00:00+02:00")
    repo.commit("fix `a[0]` | edge", at="2026-05-05T10:00:00+02:00")
    text = report_of(repo.path)
    assert not re.search(r"(?<!\\)\[(docs|x)\]\(", text)  # no "[" that is not escaped
    assert not re.search(r"(?<!\\)<a href", text)
    assert r"\[docs](javascript:alert(1))" in text and r"\<a href=javascript:z>" in text
    assert r"| fix `a[0]` \| edge |" in text


def test_a_folder_name_cannot_become_a_link(make_repo, tmp_path) -> None:
    repo = make_repo("[x](javascript:alert(1))")
    repo.commit("feat: mine", at="2026-05-04T10:00:00+02:00")
    text = report_of(repo.path)
    assert not re.search(r"(?<!\\)\[x\]\(", text.replace(f"`{repo.path}`", ""))
    assert r"### \[x](javascript:alert(1)) — 1 commit" in text
    assert r"most in \[x](javascript:alert(1)) (1)" in text


def test_a_path_with_a_backtick_stays_one_code_span(make_repo, tmp_path) -> None:
    repo = make_repo("tick`[x](javascript:y)")
    repo.commit("feat: mine", at="2026-05-04T10:00:00+02:00")
    text = report_of(repo.path)
    assert f"``{repo.path}``" in text


def tokens_of(markdown: str) -> list:
    """Every token a CommonMark + GFM-tables renderer makes of `markdown`, inline ones too."""
    from markdown_it import MarkdownIt

    found = []
    for token in MarkdownIt("commonmark").enable("table").parse(markdown):
        found += [token, *(token.children or [])]
    return found


def test_pieces_joined_on_one_line_cannot_pair_their_backticks() -> None:
    # Each piece is rendered alone; a lone backtick in one must not open a span another closes.
    line = " ".join(
        [_md("a`"), _md("b`[click me](https://evil.example/phish)`"), _code(""), _code("x`")]
    )
    assert not [t for t in tokens_of(line) if t.type in ("link_open", "html_inline", "image")]


IMG = "<img src=https://evil.example/n.png onerror=alert(1)>"
NEWLINES = ("\n\n", "\r\n", "\r", "\u2028", "\u2029", "\x85")


def hostile_repositories(make_repo, tmp_path: Path) -> list[Path]:
    """The hostile scenario below, built once for both whole-report tests: one repository with
    hostile subjects, ticket keys, request titles, branch names, remote names and submodule paths,
    two whose folder names carry lone backticks and a link, and line breaks of
    every kind — \n, \r\n, \r, U+2028, U+2029, U+0085 — in subjects, titles, folder names and
    submodule paths, each followed by HTML: a value must never end its line, its code span or its
    table row."""
    hostile_subjects = [
        "fix `unclosed [x](https://evil.example/a)",
        "``[y](javascript:alert(1))",
        "<script>alert(1)</script> and <img src=https://evil.example/i.png>",
        "a`b` <b>c</b> ` [z](data:text/html,x)",
        # a team prefix becomes an area named in Caveats; markup is never prefix-shaped
        "ui-x_y: an area",
        "x`<img src=https://evil.example/i.png>`[p](https://evil.example/p): not an area",
    ]
    app = make_repo("app")
    for day, subject in enumerate(hostile_subjects, start=4):
        app.commit(subject, at=f"2026-05-{day:02d}T10:00:00+02:00")
    for day, newline in enumerate(NEWLINES, start=18):  # line breaks in subjects
        at = f"2026-05-{day:02d}T10:00:00+02:00"
        app.commit(f"fix: `a{newline}{IMG}` b{newline}{IMG}", at=at)
    # A committed .gitmodules names paths with line breaks; they reach Caveats
    gitmodules = (
        f'[submodule "s0"]\n\tpath = "vendor0\\n\\n{IMG}"\n\turl = https://example.invalid/x\n'
        f'[submodule "s1"]\n\tpath = "vendor1\u2028{IMG}"\n\turl = https://example.invalid/x\n'
    )
    app.commit(
        "chore: submodules", at="2026-05-25T10:00:00+02:00", files={".gitmodules": gitmodules}
    )
    # keys of a named project, a suggested prefix and refused ids, each beside markup
    hostile_tickets = [
        "feat(PROJ-1`<img src=x>`): [PROJ-2](https://evil.example/t) <b>bold</b>",
        "fix: TC-UI-07`[x](https://evil.example/z)` and UTF-8<img src=y>",
        "QC-4`[d](https://evil.example/d)`: a suggested prefix",
    ]
    for day, subject in enumerate(hostile_tickets, start=4):
        app.commit(subject, at=f"2026-05-{day:02d}T15:00:00+02:00")
    # Deliveries: the title and the branch come from the merge commit's message, which
    # anyone can write; one pull request links (its project holds it), one merge request names
    # another project and stays text.
    hostile_titles = {
        "pull": "`[x](https://evil.example/pr) <img src=x onerror=alert(1)> ``\u2028" + IMG,
        "gitlab": "<b>bold</b> ``[y](javascript:alert(2))` ![i](https://evil.example/i.png)\r"
        + IMG,
    }
    for day, (name, title) in enumerate(hostile_titles.items(), start=12):
        app.branch(name)
        app.switch(name)
        app.commit(f"feat: {name}", at=f"2026-05-{day:02d}T10:00:00+02:00")
        app.switch("main")
        branch = (
            "b`[z](https://evil.example/b)<img`" if name == "pull" else f"{name}/PROJ-3`<a href=x>"
        )
        message = (
            f"Merge pull request #5 from evil/{branch}\n\n{title}\n"
            if name == "pull"
            else f"Merge branch '{branch}' into 'main'\n\n{title}\n\nSee merge request "
            "evil/other!6\n"
        )
        app.merge(name, message_text=message, at=f"2026-05-{day:02d}T12:00:00+02:00")
    app.publish(tmp_path / "remotes" / "app.git", "main")
    app.git("remote", "set-url", "origin", "git@github.com:acme/app.git")
    with (app.path / ".git" / "config").open("a") as config:
        config.write(
            '[remote ""]\n\turl = nonsense:\n'
            '[remote "[click me](https://evil.example/phish)`"]\n\turl = nonsense:\n'
            '[remote "<img src=https://evil.example/t.png>`"]\n\turl = nonsense:\n'
        )
    folders = [make_repo("a`"), make_repo("b`[click me](https://evil.example/phish)`")]
    folders += [make_repo(f"c{i}{newline}{IMG}") for i, newline in enumerate(NEWLINES)]
    for day, repo in enumerate(folders, start=10):
        repo.commit(f"feat: in {repo.path.name}", at=f"2026-05-{day:02d}T10:00:00+02:00")
    return [app.path, *(r.path for r in folders)]


def test_the_whole_report_has_no_link_or_html_but_sijl_s_own(make_repo, tmp_path) -> None:
    """Hostile remote names (empty, a link, an <img>), folder names with lone backticks, subjects
    with unbalanced backticks, links and tags, and merge request and pull request titles
    and branch names carrying the same — rendered as one report and parsed the way a Markdown
    viewer would: the only links are Sijl's own https forge links, and there is no raw HTML
    and no image anywhere."""
    pytest.importorskip("markdown_it")
    text = report_of(*hostile_repositories(make_repo, tmp_path), jira_projects=["PROJ"])
    tokens = tokens_of(text)
    assert not [t for t in tokens if t.type in ("html_inline", "html_block", "image")]
    hrefs = [t.attrGet("href") for t in tokens if t.type == "link_open"]
    assert hrefs and all(h.startswith("https://github.com/acme/app") for h in hrefs), hrefs
    assert "https://github.com/acme/app/pull/5" in hrefs  # the delivery's own link is there
    assert "| !6 |" in text  # another project's merge request: its number, no link
    assert "(empty)" in text
    assert "## Kinds of work" in text and "ui-x_y (1)" in text  # the area, named and inert
    assert "| PROJ-1 |" in text and "| PROJ-2 |" in text and "| QC | 1 | 0 | QC-4 |" in text
    assert "TC-UI-07 (1)" in text and "UTF-8 (1)" in text
    assert "| PROJ-3 | 0 | 1 |" in text and "| PROJ-3 |\n" in text  # from the request


def every_field_template(values: dict[str, object]) -> str:
    """A template that prints every value a template can reach — built from the values
    themselves — each on its own line, then through every filter that takes it, then all of them
    side by side on one line and in one table row (pieces that share a line must stay
    self-contained), every list joined, and the built-in report after it all."""
    leaves = [leaf for name, value in values.items() for leaf in walk(value, name)]
    leaves = [(path, value) for path, value in leaves if not isinstance(value, Undefined)]
    lines = []
    for path, value in leaves:
        lines.append(f"{{{{ {path} }}}}")
        if isinstance(value, Text):
            lines += [f"{{{{ {path}|{f} }}}}" for f in ("md", "cell", "code", "shorten(9)")]
        elif isinstance(value, Markdown):
            lines.append(f"{{{{ {path}|cell }}}}")
    lists = sorted({path.rpartition("[")[0] for path, _ in leaves if path.endswith("]")})
    lines += [f"{{{{ {path}|join(' ') }}}}" for path in lists]
    lines.append(" ".join(f"{{{{ {path} }}}}" for path, _ in leaves))
    lines.append("| " + " | ".join(f"{{{{ {path}|cell }}}}" for path, _ in leaves) + " |")
    return "\n\n".join([*lines, '{% include "report.md.j2" %}']) + "\n"


def test_a_template_that_prints_every_field_has_no_link_or_html_but_sijl_s_own(
    make_repo, tmp_path, monkeypatch
) -> None:
    """The same hostile repositories through `--template`, with a template that prints every
    value a template can reach, every way it can: still no raw HTML, no image, and no link but
    Sijl's own."""
    pytest.importorskip("markdown_it")
    paths = hostile_repositories(make_repo, tmp_path)
    values = context(
        build_report(
            roots=[], paths=paths, identities=IdentitySet.from_emails([ME.email]),
            window=REVIEW_PERIOD, git=GitRunner(), generated_on=date(2026, 10, 8),
            jira_projects=["PROJ"],
        ),
        custom=True,  # a custom template's values: no Caveats, no footer (Sijl adds them)
    )  # fmt: skip
    template = tmp_path / "every-field.md.j2"
    template.write_text(every_field_template(values), encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text("[forges]\nssh_aliases = false\n")  # as the in-process build: no ssh
    monkeypatch.setattr("sijl.cli._today", lambda: date(2026, 10, 8))
    args = ["report", *(f"--repo={path}" for path in paths), "--author-email", ME.email]
    options = ["--jira-project", "PROJ", "--template", str(template), "--config", str(config)]
    result = CliRunner().invoke(app, [*args, *PERIOD_ARGS, *options])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    tokens = tokens_of(result.stdout)
    assert not [t for t in tokens if t.type in ("html_inline", "html_block", "image")]
    hrefs = [t.attrGet("href") for t in tokens if t.type == "link_open"]
    assert hrefs and all(h.startswith("https://github.com/acme/app") for h in hrefs), hrefs
    assert "https://github.com/acme/app/pull/5" in hrefs
    assert len(result.stdout) > 2 * len(render_markdown(build_report(
        roots=[], paths=paths, identities=IdentitySet.from_emails([ME.email]),
        window=REVIEW_PERIOD, git=GitRunner(), generated_on=date(2026, 10, 8),
        jira_projects=["PROJ"],
    )))  # fmt: skip  # every field, then the whole report: more than twice its length


def test_what_the_category_function_raised_cannot_become_a_link(make_repo) -> None:
    """review_category() is the user's code; if it raises with a commit's own words, the
    Caveats line that quotes it must still be inert Markdown."""
    pytest.importorskip("markdown_it")
    repo = make_repo("app")
    repo.commit(
        "fix: see [x](https://evil.example/r) <img src=y> `a", at="2026-05-04T10:00:00+02:00"
    )

    def raising(commit: Conventional, repo: Repo) -> str:
        raise ValueError(commit.description)

    report = build_report(
        roots=[], paths=[repo.path], identities=IdentitySet.from_emails([ME.email]),
        window=REVIEW_PERIOD, git=GitRunner(), generated_on=date(2026, 10, 8), categorise=raising,
    )  # fmt: skip
    text = render_markdown(report)
    assert "`review_category()` failed on 1 commit" in text
    tokens = tokens_of(text)
    assert not [t for t in tokens if t.type in ("html_inline", "html_block", "image", "link_open")]


def _md_by_rescanning(text: str) -> str:
    """The first `_md()`: for each backtick run, a scan of the rest of the text for its closer —
    kept here as the reference the faster one must agree with."""
    markup = re.compile(r"([\\\[<])")
    ticks_re = re.compile(r"`+")
    out, pos = [], 0
    while (opening := ticks_re.search(text, pos)) is not None:
        out.append(markup.sub(r"\\\1", text[pos : opening.start()]))
        ticks = len(opening.group())
        closing = next(
            (m for m in ticks_re.finditer(text, opening.end()) if len(m.group()) == ticks), None
        )
        if closing is None:
            out.append("\\`" * ticks)
            pos = opening.end()
        else:
            out.append(text[opening.start() : closing.end()])
            pos = closing.end()
    out.append(markup.sub(r"\\\1", text[pos:]))
    return "".join(out)


def test_code_spans_are_found_exactly_as_before_on_any_text() -> None:
    rng = random.Random(20)
    for _ in range(3000):
        text = "".join(rng.choice("```ab [<\\]") for _ in range(rng.randrange(40)))
        assert _md(text) == _md_by_rescanning(text), text


def test_many_unclosed_backtick_runs_are_escaped_in_linear_time() -> None:
    # Runs of 2, 3, 4, … backticks close nothing; scanning the rest of the text for each one's
    # closer, past a hundred thousand single backticks, was O(n^1.5): 4.7 s for 200 KB, about a
    # minute for a 1 MB subject.
    runs, length = [], 2
    while sum(len(r) for r in runs) < 500_000:
        runs.append("`" * length + "x")
        length += 1
    text = "".join(runs) + "`a" * 250_000
    started = time.perf_counter()
    rendered = _md(text)
    assert time.perf_counter() - started < 1.0
    assert rendered.startswith("\\`\\`x\\`\\`\\`x") and rendered.endswith("`a`a")
