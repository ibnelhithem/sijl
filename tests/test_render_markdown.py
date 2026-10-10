"""sijl.render.markdown and .context: the report rendered from Jinja2 templates shipped as
package data; the values templates read hold no plain string anywhere — every piece of text
Sijl did not write is a `Text` — and the loader serves the built-in templates and nothing
else."""

from datetime import date
from importlib.resources import files

import pytest
from jinja2 import TemplateNotFound

from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.render.context import context
from sijl.render.markdown import BUILT_IN, environment
from sijl.render.safety import Markdown, Text, View, fields
from sijl.report import build_report, render_markdown
from tests.gitfactory import ME, REVIEW_PERIOD


def test_every_built_in_template_is_package_data_and_every_file_is_built_in() -> None:
    folder = files("sijl.render").joinpath("templates")
    shipped = {entry.name for entry in folder.iterdir() if entry.name.endswith(".j2")}
    assert shipped == set(BUILT_IN)


@pytest.mark.parametrize("name", ["../markdown.py", "/etc/passwd", "templates/report.md.j2", ""])
def test_the_loader_serves_built_in_names_only(name: str) -> None:
    with pytest.raises(TemplateNotFound):
        environment().get_template(name)


def walk(value: object, path: str = "") -> list[tuple[str, object]]:
    """Every leaf a template can reach, with its path: `repositories[0].rows[1].subject`."""
    if isinstance(value, View):
        return [
            leaf for name, item in fields(value).items() for leaf in walk(item, f"{path}.{name}")
        ]
    if isinstance(value, tuple):
        return [leaf for i, item in enumerate(value) for leaf in walk(item, f"{path}[{i}]")]
    return [(path, value)]


def report_of(make_repo, tmp_path):
    repo = make_repo("app")
    repo.commit("feat(PROJ-1): [x](javascript:y)", at="2026-05-04T10:00:00+02:00")
    repo.commit("fix: QC-4 <b>", at="2026-05-05T10:00:00+02:00")
    return build_report(
        roots=[tmp_path], paths=[], identities=IdentitySet.from_emails([ME.email]),
        window=REVIEW_PERIOD, git=GitRunner(), generated_on=date(2026, 10, 8),
        jira_projects=["PROJ"],
    )  # fmt: skip


def test_no_value_a_template_reads_is_a_plain_string(make_repo, tmp_path) -> None:
    values = context(report_of(make_repo, tmp_path))
    leaves = [leaf for name, value in values.items() for leaf in walk(value, name)]
    kinds = {type(value) for _, value in leaves}
    assert kinds <= {Text, Markdown, int, bool, type(None)}, kinds
    texts = {path for path, value in leaves if isinstance(value, Text)}
    assert "repositories[0].rows[0].subject" in texts and "person" in texts  # repository text


def test_rendering_twice_gives_the_same_report(make_repo, tmp_path) -> None:
    report = report_of(make_repo, tmp_path)
    assert render_markdown(report) == render_markdown(report)
    assert render_markdown(report).endswith(
        "---\n\n*These figures are evidence of activity, not a performance score.*\n"
    )


def test_every_link_of_the_built_in_report_is_one_sijl_built(tmp_path) -> None:
    """The allow-list a custom template is held to (`built_urls`) holds every link the built-in
    report makes — on the golden scenario: commits, requests, projects, *delivered via #N*."""
    from sijl.render.context import built_urls
    from sijl.render.markdown import foreign_markup
    from tests.test_report_golden import MINE, scenario

    scenario(tmp_path)
    report = build_report(
        roots=[tmp_path / "repos"], paths=[], identities=MINE, window=REVIEW_PERIOD,
        git=GitRunner(), generated_on=date(2026, 10, 8), jira_projects=["PROJ"],
    )  # fmt: skip
    text = render_markdown(report)
    assert text.count("](https://") > 20
    assert foreign_markup(text, built_urls(report)) == []


CUSTOM_POSITIONS = [
    "`{{ v }}`", "[{{ v }}](https://intranet.example/x)", "![{{ v }}](https://intranet.example/i)",
    "[t]({{ v }})", "![t]({{ v }})", "[t](<{{ v }}>)", "<{{ v }}>", "<!-- {{ v }} -->",
    "| a | {{ v }} |\n|---|---|\n| {{ v }} | `{{ v }}` |", "# {{ v }}", "- {{ v }}",
    "**{{ v }}**", "see {{ v }} and `{{ v }}`",
]  # fmt: skip


def test_no_custom_template_writes_a_link_sijl_did_not_build(make_repo, tmp_path) -> None:
    """Every position, every way of printing, hostile subjects — through the custom
    template path, each render is either refused or has no link, image or HTML but Sijl's."""
    pytest.importorskip("markdown_it")
    from sijl.render.markdown import CustomTemplate, foreign_markup, render_report
    from sijl.render.safety import TemplateProblem
    from tests.test_render_safety import NASTY

    repo = make_repo("app")
    for n, subject in enumerate(s for s in NASTY if "\n" not in s):
        repo.commit(subject, at=f"2026-05-{n % 28 + 1:02d}T{n % 24:02d}:00:00+02:00")
    report = build_report(
        roots=[], paths=[repo.path], identities=IdentitySet.from_emails([ME.email]),
        window=REVIEW_PERIOD, git=GitRunner(), generated_on=date(2026, 10, 8),
    )  # fmt: skip
    refused = 0
    for position in CUSTOM_POSITIONS:
        for how in ("v", "v|md", "v|cell", "v|code", "v|shorten(9)"):
            body = position.replace("{{ v }}", "{{ " + how + " }}")
            loop = "{% for c in repositories[0].rows %}{% set v = c.subject %}"
            source = f"{loop}{body}\n\n{{% endfor %}}"
            try:
                text = render_report(report, CustomTemplate(source, "fuzz.md.j2")).text
            except TemplateProblem:
                refused += 1
                continue
            assert foreign_markup(text, frozenset()) == [], (body, text)
    assert refused  # the template's own links, its HTML comment, a link built from a value


def test_a_template_that_includes_the_built_in_report_passes_the_link_check(
    make_repo, tmp_path
) -> None:
    """The link check refuses what a template writes, never what the repositories hold: the
    built-in report — golden scenario and the hostile one, subjects full of addresses — passes
    when a custom template includes it."""
    from sijl.render.markdown import CustomTemplate, render_report
    from tests.test_report_golden import MINE, scenario
    from tests.test_report_markdown import hostile_repositories

    scenario(tmp_path / "golden")
    hostile = hostile_repositories(make_repo, tmp_path)
    for roots, paths in (([tmp_path / "golden" / "repos"], []), ([], hostile)):
        report = build_report(
            roots=roots, paths=paths, identities=MINE, window=REVIEW_PERIOD, git=GitRunner(),
            generated_on=date(2026, 10, 8), jira_projects=["PROJ"],
        )  # fmt: skip
        mine = render_report(report, CustomTemplate('{% include "report.md.j2" %}\n', "t")).text
        assert "## Appendix" in mine and "## Caveats" in mine
