"""sijl.report: the summary report — header, a summary paragraph built from the numbers,
commits per repository, and a Caveats section that is always there."""

from datetime import date
from pathlib import Path

from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.report import build_report, caveats, render_markdown
from sijl.window import Window
from tests.gitfactory import COLLEAGUE, ME, REVIEW_PERIOD
from tests.test_collect import BrokenLog

Q2 = REVIEW_PERIOD


def scenario(make_repo, root: Path) -> None:
    webapp = make_repo("web-tests")
    webapp.commit("test(PROJ-1): first spec", at="2026-04-02T09:00:00+02:00")
    webapp.commit("fix: a | pipe in the subject", at="2026-04-02T15:00:00+02:00")
    webapp.commit("feat: a colleague's", at="2026-04-03T09:00:00+02:00", who=COLLEAGUE)
    webapp.branch("feature/x")
    webapp.switch("feature/x")
    webapp.commit("feat: on a branch", at="2026-05-10T09:00:00+02:00")
    webapp.switch("main")
    webapp.merge("feature/x", message_text="Merge branch 'feature/x' into 'main'\n",
                 at="2026-05-11T09:00:00+02:00")  # fmt: skip
    make_repo("api-tests").commit("feat: tier1 data", at="2026-06-30T23:00:00+02:00")
    make_repo("sandbox").commit("chore: someone else", at="2026-05-01T09:00:00+02:00",
                                 who=COLLEAGUE)  # fmt: skip
    (root / "notes").mkdir()


def build(root: Path, git: GitRunner | None = None, window: Window = Q2):
    return build_report(
        roots=[root],
        paths=[],
        identities=IdentitySet.from_emails([ME.email]),
        window=window,
        git=git or GitRunner(),
        generated_on=date(2026, 10, 6),
    )


def test_the_numbers(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)
    report = build(tmp_path)
    assert report.person == ME.name
    assert (report.total, report.active_days, len(report.active_repos)) == (4, 3, 2)
    # the same numbers are the Insights' — one model, no second count
    insights = report.insights
    assert (insights.total, insights.active_days, len(insights.per_repo)) == (4, 3, 2)


def test_the_markdown_reads_top_to_bottom(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)
    text = render_markdown(build(tmp_path))
    order = [  # overview first, the evidence in the Appendix, the footer last
        f"# Work summary — {ME.name}",
        "**Period:** 1 April 2026 to 30 June 2026",
        "## Summary",
        "you authored **4 commits** on **3 active days** across **2 repositories**",
        "## Highlights",
        "## Per repository",
        "| web-tests | 3 | 75% |",
        "| api-tests | 1 | 25% |",
        "No commits of yours in this period: sandbox.",
        "## Per month",
        "## Kinds of work",
        "## Caveats",
        "## Appendix: every counted commit",
        "### web-tests — 3 commits",
        "### api-tests — 1 commit",
        "*These figures are evidence of activity, not a performance score.*",
    ]
    positions = [text.index(line) for line in order]
    assert positions == sorted(positions)


def test_commit_rows_are_safe_markdown(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)
    text = render_markdown(build(tmp_path))
    assert "| 2026-04-02 | `" in text
    assert "fix: a \\| pipe in the subject |" in text  # a pipe would split the table cell
    assert "feat: a colleague's" not in text


def test_caveats_disclose_every_gap(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)
    text = render_markdown(build(tmp_path))
    caveats = text[text.index("## Caveats") :]
    assert "Scanned 3 repositories" in caveats
    assert "no git repository inside" in caveats and "notes" in caveats
    assert (  # a branch merged locally, with no merge request: not a commit, not a delivery
        "1 merge commit you made is not counted as a commit: 1 merged a branch with no merge "
        "request or pull request (not listed)."
    ) in caveats
    assert "No rebased or cherry-picked copies of a commit were found" in caveats
    assert "stash" in caveats


def test_an_empty_period_says_so(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: long ago", at="2025-01-01T09:00:00+01:00")
    text = render_markdown(build(tmp_path))
    assert f"No commits by {ME.email} were found between 1 April 2026 and 30 June 2026" in text
    assert "## Caveats" in text


def test_a_repository_that_could_not_be_read_is_not_counted_as_scanned(make_repo, tmp_path):
    make_repo("broken").commit("feat: unreadable", at="2026-05-04T10:00:00+02:00")
    make_repo("fine").commit("feat: fine", at="2026-05-04T10:00:00+02:00")
    report = build(tmp_path, BrokenLog())
    assert report.repos_scanned == 1
    caveats = render_markdown(report).split("## Caveats")[1]
    assert "Scanned 1 repository" in caveats and "could not be parsed" in caveats


# ------------------------------------------------------------ the window, everywhere

Q4_TO_DATE = Window(date(2026, 10, 1), date(2026, 10, 8), name="Q4 2026", to_date=True)
LAST_6M = Window(date(2026, 4, 9), date(2026, 10, 8), name="the last 6 months")


def test_the_header_states_the_window_its_preset_and_the_counting_rule(make_repo, tmp_path):
    make_repo("app").commit("feat: this quarter", at="2026-10-02T09:00:00+02:00")
    text = render_markdown(build(tmp_path, window=Q4_TO_DATE))
    assert (
        "**Period:** 1 October 2026 to 8 October 2026 — Q4 2026 to date (inclusive; by author "
        "date, in each commit's own timezone)  " in text
    )
    plain = render_markdown(build(tmp_path))  # --since/--until: the days alone, no preset name
    assert (
        "**Period:** 1 April 2026 to 30 June 2026 (inclusive; by author date, in each "
        "commit's own timezone)  " in plain
    )


def test_the_summary_names_the_preset(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: this quarter", at="2026-10-02T09:00:00+02:00")
    text = render_markdown(build(tmp_path, window=Q4_TO_DATE))
    assert "Between 1 October 2026 and 8 October 2026 (Q4 2026 to date) you authored" in text
    q1 = Window(date(2026, 1, 1), date(2026, 3, 31), name="Q1 2026")
    empty = render_markdown(build(tmp_path, window=q1))
    assert "were found between 1 January 2026 and 31 March 2026 (Q1 2026) in the" in empty


def test_caveats_always_state_the_window_and_how_it_counts(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: in Q2", at="2026-05-04T10:00:00+02:00")
    first = caveats(build(tmp_path))[0]
    assert first == (
        "Commits are counted by author date — the day each was written, in its author's own "
        "timezone — from 1 April 2026 to 30 June 2026, both days included; rebasing or pushing "
        "a commit later does not move it."
    )
    running = caveats(build(tmp_path, window=Q4_TO_DATE))[0]
    assert running.endswith(
        "Q4 2026 is not over yet, so the period stops at today, 8 October 2026."
    )
    assert "not over" not in caveats(build(tmp_path, window=LAST_6M))[0]
