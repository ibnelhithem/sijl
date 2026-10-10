"""The whole Markdown report against a golden file.

`scenario()` builds, with pinned identities and dates, repositories that exercise every section of
the report: merge requests merged by you and by a colleague, one you merged for a colleague, a
GitHub pull request and two GitHub squash merges (one linked to branch commits only on your
laptop, one counted as code), Jira tickets with a suggested prefix and a refused id, a breaking
change, a commit without a prefix and a team prefix, a cherry-picked copy, unpushed work, a
repository with no remote, one with only a colleague's commits, a folder with no repository and a
hidden folder. The report is rendered as `sijl report` renders it; the folder the test ran in
becomes `<tmp>` and Sijl's version `<version>`, so the file is the same on every machine.

When the report changes on purpose, regenerate the file and review its diff like code:

    UPDATE_GOLDEN=1 uv run pytest tests/test_report_golden.py

(`UPDATE_GOLDEN`, not `SIJL_…`: the hermetic fixture removes every `SIJL_` variable, and
it is read once, when this module is imported.)
"""

import os
from datetime import date
from pathlib import Path

from sijl import __version__
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.report import build_report, render_markdown
from tests.gitfactory import COLLEAGUE, ME, ME_GITHUB, REVIEW_PERIOD, RepoBuilder

GOLDEN = Path(__file__).parent / "golden" / "report.md"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
MINE = IdentitySet.from_emails([ME.email, ME_GITHUB.email])


def at(month: int, day: int, hour: int = 10) -> str:
    return f"2026-{month:02d}-{day:02d}T{hour:02d}:00:00+02:00"


def on_branch(repo: RepoBuilder, name: str, *commits: tuple[str, str, str], start: str = "main"):
    """`name` from `start` with one commit per (subject, day, file), then back on main."""
    repo.branch(name, start)
    repo.switch(name)
    shas = [
        repo.commit(subject, at=day, files={file: f"{subject}\n"}) for subject, day, file in commits
    ]
    repo.switch("main")
    return shas


def gitlab_app(root: Path) -> None:
    """GitLab: your merge request merged by a colleague, one merged by you (a breaking fix), a
    colleague's you merged; direct commits of every kind; a cherry-picked copy and an idea on a
    local branch."""
    app = RepoBuilder(root / "repos" / "app")
    start = app.commit("chore: start", at=at(3, 2), who=COLLEAGUE)
    on_branch(app, "feature/PROJ-12-export",
              ("feat(PROJ-12): export the report", at(4, 6), "export.py"),
              ("test(PROJ-12): cover the export", at(4, 7), "test_export.py"))  # fmt: skip
    app.gitlab_mr_merge("feature/PROJ-12-export", number=42, title="Export the report (PROJ-12)",
                        at=at(4, 8), who=COLLEAGUE)  # fmt: skip
    on_branch(app, "fix/retry", ("fix(api)!: retry on 503 | not on 500", at(5, 4), "retry.py"))
    app.gitlab_mr_merge("fix/retry", number=150, title="Retry on 503", at=at(5, 5))
    app.branch("feature/theirs")
    app.switch("feature/theirs")
    app.commit("feat: their feature", at=at(5, 6), who=COLLEAGUE, files={"theirs.py": "x\n"})
    app.switch("main")
    app.gitlab_mr_merge("feature/theirs", number=151, title="Their feature", at=at(5, 7))
    docs = app.commit("docs: explain the export", at=at(5, 11), files={"README.md": "x\n"})
    app.commit("Tidy the build", at=at(5, 12), files={"build.txt": "x\n"})
    app.commit("ui: a new header", at=at(5, 13), files={"header.txt": "x\n"})
    app.commit("fix: flaky QC-4 test on UTF-8 names", at=at(6, 1), files={"qc.txt": "x\n"})
    app.publish(root / "remotes" / "app.git", "--all")
    app.git("remote", "set-url", "origin", "git@gitlab.com:acme/platform/app.git")
    app.branch("local/try", start)
    app.switch("local/try")
    app.cherry_pick(docs, at=at(6, 2, 12))  # a copy of a pushed commit: counted once
    app.commit("feat: an idea not pushed yet", at=at(6, 2), files={"idea.txt": "x\n"})
    app.switch("main")


def github_site(root: Path) -> None:
    """GitHub: a pull request merged with a merge commit; a squash merge whose branch commits are
    only on your laptop (linked), and one whose branch is gone (counted as code)."""
    site = RepoBuilder(root / "repos" / "site")
    site.commit("chore: start", at=at(3, 2), who=COLLEAGUE)
    on_branch(site, "fix/typo", ("fix: a typo in the footer", at(4, 14), "footer.html"))
    site.github_pr_merge("fix/typo", number=12, title="Fix a typo", at=at(4, 15))
    on_branch(site, "feat/search", ("feat: a search box", at(4, 20), "search.html"),
              ("feat: search results", at(4, 21), "results.html"))  # fmt: skip
    site.github_squash("feat/search", number=13, title="Search", at=at(4, 22))
    on_branch(site, "fix/gone", ("fix: a broken link", at(5, 18), "links.html"))
    site.github_squash("fix/gone", number=14, title="Fix a broken link", at=at(5, 19))
    site.git("branch", "-D", "fix/gone", "fix/typo")
    site.publish(root / "remotes" / "site.git", "main")  # feat/search stays on this laptop
    site.git("remote", "set-url", "origin", "git@github.com:alexgh/site.git")


def scenario(root: Path) -> None:
    gitlab_app(root)
    github_site(root)
    tools = RepoBuilder(root / "repos" / "tools")  # no remote: counted, unchecked
    tools.commit("chore: bump the dependencies", at=at(6, 3))
    tools.commit("build: pin uv", at=at(6, 4))
    RepoBuilder(root / "repos" / "quiet").commit("feat: theirs", at=at(5, 4), who=COLLEAGUE)
    (root / "repos" / "notes").mkdir()  # no repository inside
    (root / "repos" / ".cache").mkdir()  # hidden: not searched, by rule


def rendered(tmp_path: Path) -> str:
    scenario(tmp_path)
    report = build_report(
        roots=[tmp_path / "repos"], paths=[], identities=MINE, window=REVIEW_PERIOD,
        git=GitRunner(), generated_on=date(2026, 10, 8), jira_projects=["PROJ"],
    )  # fmt: skip
    text = render_markdown(report)
    for folder in {str(tmp_path.resolve()), str(tmp_path)}:
        text = text.replace(folder, "<tmp>")
    return text.replace(f"Sijl {__version__}", "Sijl <version>")


def test_the_full_report_matches_the_golden_file(tmp_path) -> None:
    text = rendered(tmp_path)
    if UPDATE:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8"), (
        "the report changed: if on purpose, run UPDATE_GOLDEN=1 uv run pytest "
        "tests/test_report_golden.py and review the diff of tests/golden/report.md"
    )


def test_the_golden_report_exercises_every_section() -> None:
    text = GOLDEN.read_text(encoding="utf-8")
    for piece in (
        "# Work summary — Alex Example", "## Summary", "## Highlights", "- **Busiest month:**",
        "- **Largest merge requests and pull requests**", "- **Most active tickets**",
        "## Per repository", "No commits of yours in this period: quiet.", "## Tickets",
        "Other prefixes that look like Jira project keys", "## Per month", "## Kinds of work",
        "breaking change", "## Delivered", "### Yours", "### Merged for others",
        "the squash: ", "linked", "## Not pushed yet", "## Caveats",
        "## Appendix: every counted commit", "(delivered via [#13]", "(copy `",
        "*These figures are evidence of activity, not a performance score.*",
    ):  # fmt: skip
        assert piece in text, piece


def test_caveats_list_every_skip_and_filter_with_counts() -> None:
    caveats = GOLDEN.read_text(encoding="utf-8").split("## Caveats")[1].split("\n## ")[0]
    for disclosure in (
        "Scanned 4 repositories under `<tmp>/repos`.",
        "Not scanned: `<tmp>/repos/notes` — no git repository inside.",
        "1 folder was not searched, by rule",
        "merge commits you made are not counted as commits:",
        "1 rebased or cherry-picked copy of a commit (same author, author time and subject) is "
        "not counted again",
        "1 commit on no remote branch is listed under *Not pushed yet*",
        "No remote-tracking branches in tools",
        "2 GitHub squash merges of yours",
        "keys of projects not in `jira.projects` — QC (1)",
        "ids that only look like ticket keys",
        "UTF-8 (1)",
        "Kind *other* holds 2 commits without a conventional prefix",  # Tidy…, the squash #14
        "ui (1)",
    ):  # fmt: skip
        assert disclosure in caveats, disclosure
