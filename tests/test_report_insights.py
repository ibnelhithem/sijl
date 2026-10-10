"""The report and Insights: one model computed once in build_report() feeds the Markdown —
the kinds-of-work and per-month sections, the streak in the summary, and the Caveats about kind
"other" and a failing review_category()."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from sijl.aggregate import aggregate
from sijl.classify import REVIEW_CATEGORIES, Conventional
from sijl.collect import Collection
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.models import Repo
from sijl.report import (
    CATEGORY_LABELS,
    build_report,
    caveats,
    month_label,
    render_markdown,
    share,
)
from sijl.window import Window
from tests.gitfactory import ME, REVIEW_PERIOD
from tests.test_aggregate import API, by_kind, collection, commit, repo_commits
from tests.test_report_pushed import section


def scenario(make_repo, tmp_path: Path) -> None:
    """Two repositories. app has a remote: two pushed commits, a cherry-picked copy of one on a
    local branch (collapsed) and a commit never pushed. tools has none, so all three count.

         May 2026: Fri 1 · Sat 2 · Sun 3 · Mon 4 · Tue 5 · Wed 6 · … · Sat 9 · … Mon 1 June
    """
    app = make_repo("app")
    base = app.commit("feat(PROJ-1): billing flow", at="2026-05-01T09:00:00+02:00")  # Fri
    fix = app.commit("fix(api)!: handle 404", at="2026-05-04T09:00:00+02:00")  # Mon
    app.publish(tmp_path / "remotes" / "app.git", "main")
    app.branch("topic", base)
    app.switch("topic")
    app.cherry_pick(fix, at="2026-05-06T09:00:00+02:00")  # a copy: collapsed into `fix`
    app.commit("docs: never pushed", at="2026-05-05T09:00:00+02:00")  # Tue, local only
    app.switch("main")
    tools = make_repo("tools")
    tools.commit("Add a script", at="2026-05-06T09:00:00+02:00")  # Wed, no prefix
    tools.commit("ui: new header", at="2026-05-09T09:00:00+02:00")  # Sat, a team prefix
    tools.commit("chore: bump deps", at="2026-06-01T09:00:00+02:00",  # Mon
                 body="BREAKING CHANGE: needs Python 3.12")  # fmt: skip


def build(root: Path, *, categorise=by_kind, window: Window = REVIEW_PERIOD):
    return build_report(
        roots=[root],
        paths=[],
        identities=IdentitySet.from_emails([ME.email]),
        window=window,
        git=GitRunner(),
        generated_on=date(2026, 10, 8),
        categorise=categorise,
    )


def test_the_report_carries_the_one_model_and_its_totals_come_from_it(make_repo, tmp_path):
    scenario(make_repo, tmp_path)
    report = build(tmp_path)
    assert report.insights == aggregate(report.collection, REVIEW_PERIOD, categorise=by_kind)
    # Counted: billing flow, handle 404 (app); script, header, bump deps (tools). Not counted:
    # the cherry-picked copy (collapsed) and "never pushed" (Tue 5 May, local only).
    assert (report.total, report.active_days) == (report.insights.total, 5)
    assert report.insights.total == 5
    assert [rc.repo.name for rc in report.active_repos] == ["tools", "app"]
    assert [(repo.name, n) for repo, n in report.insights.per_repo] == [("tools", 3), ("app", 2)]


def test_rendering_reads_the_model_and_never_categorises_again(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)
    calls: list[str] = []

    def counting(commit: Conventional, repo: Repo) -> str:
        calls.append(commit.description)
        return by_kind(commit, repo)

    report = build(tmp_path, categorise=counting)
    assert len(calls) == 5  # once per counted commit, in build_report()
    render_markdown(report)
    render_markdown(report)
    assert len(calls) == 5


def test_kinds_of_work_shows_categories_kinds_and_breaking_changes(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)
    kinds = section(render_markdown(build(tmp_path)), "Kinds of work")
    assert (
        "| Category | Commits | Share |\n"
        "|---|---|---|\n"
        "| Features | 1 | 20% |\n"
        "| Fixes | 1 | 20% |\n"
        "| Tooling | 1 | 20% |\n"
        "| Other | 2 | 40% |\n"
    ) in kinds  # empty categories (quality, docs) are left out
    assert (
        "| Kind | Commits | Share |\n"
        "|---|---|---|\n"
        "| `feat` | 1 | 20% |\n"
        "| `fix` | 1 | 20% |\n"
        "| `chore` | 1 | 20% |\n"
        "| other | 2 | 40% |\n"
    ) in kinds
    assert "2 commits are marked as breaking changes (`!` or a `BREAKING CHANGE` footer)." in kinds


def test_per_month_lists_every_month_of_the_period(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)
    months = section(render_markdown(build(tmp_path)), "Per month")
    assert (
        "| Month | Commits | Active days |\n"
        "|---|---|---|\n"
        "| April 2026 | 0 | 0 |\n"
        "| May 2026 | 4 | 4 |\n"
        "| June 2026 | 1 | 1 |\n"
    ) in months


def test_the_summary_names_the_longest_streak(make_repo, tmp_path) -> None:
    """Fri 1 → Mon 4 May across the weekend; the local-only Tue 5 May does not join Wed 6."""
    scenario(make_repo, tmp_path)
    summary = section(render_markdown(build(tmp_path)), "Summary")
    assert (
        "Your longest streak was **2 weekdays** in a row with commits, from 1 May 2026 to "
        "4 May 2026 (weekends neither break nor extend a streak; a weekday without a commit — a "
        "holiday or leave too — ends it)."
    ) in summary


def test_the_sections_read_in_the_report_s_order(make_repo, tmp_path):
    scenario(make_repo, tmp_path)
    text = render_markdown(build(tmp_path))
    order = ["## Summary", "## Per repository", "## Per month", "## Kinds of work",
             "## Not pushed yet", "## Caveats", "## Appendix"]  # fmt: skip  # the report's order
    positions = [text.index(line) for line in order]
    assert positions == sorted(positions)


def test_caveats_say_what_kind_other_holds(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)
    assert (
        "Kind *other* holds 1 commit without a conventional prefix (`type: …`) and 1 commit "
        "whose prefix is not a standard kind: ui (1)."
    ) in caveats(build(tmp_path))


@pytest.mark.parametrize(
    ("prefixes", "listed"),
    [
        (["p01", "p01", *(f"p{i:02d}" for i in range(2, 13))],
         "13 commits whose prefix is not a standard kind: p01 (2), p02 (1), p03 (1), p04 (1), "
         "p05 (1), p06 (1), p07 (1), p08 (1), p09 (1), p10 (1), plus 2 more prefixes (2 commits)."),
        ([f"p{i:02d}" for i in range(1, 12)],
         "11 commits whose prefix is not a standard kind: p01 (1), p02 (1), p03 (1), p04 (1), "
         "p05 (1), p06 (1), p07 (1), p08 (1), p09 (1), p10 (1), plus 1 more prefix (1 commit)."),
    ],
)  # fmt: skip
def test_a_long_list_of_prefixes_shows_ten_and_counts_the_rest(
    make_repo, tmp_path, prefixes: list[str], listed: str
) -> None:
    make_repo("app").commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    commits = [commit(n, "2026-05-04T10:00:00+02:00", f"{p}: x") for n, p in enumerate(prefixes)]
    insights = aggregate(collection(repo_commits(API, commits)), REVIEW_PERIOD)
    lines = caveats(replace(build(tmp_path), insights=insights))
    assert f"Kind *other* holds {listed}" in lines


def test_caveats_disclose_a_failing_review_category(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)

    def broken(commit: Conventional, repo: Repo) -> str:
        if commit.kind == "fix":
            raise KeyError("fix")
        return "bugs" if commit.kind == "feat" else by_kind(commit, repo)

    report = build(tmp_path, categorise=broken)
    # app's commits are read newest first: the fix (4 May) fails before the feature (1 May)
    assert dict(report.insights.per_category)["other"] == 4  # 2 of kind other + 2 failures
    lines = caveats(report)
    assert (
        "`review_category()` failed on 2 commits (the first time it raised KeyError: 'fix'); "
        "they are counted under *other*."
    ) in lines


def test_nothing_failed_nothing_said(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    lines = " ".join(caveats(build(tmp_path)))
    assert "review_category" not in lines and "Kind *other*" not in lines


def test_an_empty_period_has_no_kinds_or_months(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: long ago", at="2025-01-01T09:00:00+01:00")
    text = render_markdown(build(tmp_path))
    assert "## Kinds of work" not in text and "## Per month" not in text
    assert "longest streak" not in text


@pytest.mark.parametrize(
    ("window", "labels"),
    [
        (Window(date(2026, 4, 6), date(2026, 6, 17)),
         ["April 2026 (from 6 April)", "May 2026", "June 2026 (to 17 June)"]),
        (Window(date(2026, 4, 6), date(2026, 4, 17)), ["April 2026 (6 to 17 April)"]),
        (Window(date(2026, 2, 1), date(2026, 2, 28)), ["February 2026"]),
        (Window(date(9999, 12, 1), date(9999, 12, 31)), ["December 9999"]),  # no overflow
        (Window(date(9999, 11, 1), date(9999, 12, 30)),
         ["November 9999", "December 9999 (to 30 December)"]),
    ],
)  # fmt: skip
def test_a_month_cut_by_the_period_says_so(window: Window, labels: list[str]) -> None:
    nothing = Collection(repos=(), skipped=(), merges_left_out=0, seen_in_other_clones=0)
    months = [m.month for m in aggregate(nothing, window).per_month]
    assert [month_label(m, window) for m in months] == labels


@pytest.mark.parametrize(
    ("n", "total", "text"),
    [(1, 5, "20%"), (1, 8, "13%"), (3, 8, "38%"), (2, 3, "67%"), (7, 7, "100%"),
     (1, 300, "<1%"), (199, 200, ">99%")],
)  # fmt: skip
def test_shares_round_half_up_but_never_to_none_or_all(n, total, text) -> None:
    assert share(n, total) == text


def test_every_category_has_a_label() -> None:
    assert tuple(CATEGORY_LABELS) == REVIEW_CATEGORIES


def test_a_period_ending_on_9999_12_31_renders(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    report = build(tmp_path, window=Window(date(2026, 4, 1), date(9999, 12, 31)))
    months = section(render_markdown(report), "Per month")
    assert "| April 2026 | 0 | 0 |\n| May 2026 | 1 | 1 |" in months  # nothing after today
    assert "October 2026" in months and "November 2026" not in months  # generated 8 October


def test_a_long_period_folds_empty_months_before_and_after_the_work(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    report = build(tmp_path, window=Window(date(1970, 1, 1), date(2026, 10, 8)))
    assert len(report.insights.per_month) == 56 * 12 + 10  # the model keeps every month
    months = section(render_markdown(report), "Per month")
    assert (
        "| Month | Commits | Active days |\n"
        "|---|---|---|\n"
        "| No commits from January 1970 to April 2026 | 0 | 0 |\n"
        "| May 2026 | 1 | 1 |\n"
        "| No commits from June 2026 to October 2026 (to 8 October) | 0 | 0 |\n"
    ) in months


def test_a_short_period_keeps_every_month_row(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    window = Window(date(2025, 1, 1), date(2026, 12, 31))  # 24 months: no folding
    months = section(render_markdown(build(tmp_path, window=window)), "Per month")
    assert "No commits from" not in months and "| January 2025 | 0 | 0 |" in months


@pytest.mark.parametrize("period", [["--until", "9999-12-31"], ["--since", "1970-01-01"]])
def test_the_report_command_takes_the_widest_periods(make_repo, period) -> None:
    from typer.testing import CliRunner

    from sijl.cli import app

    repo = make_repo("app")
    repo.commit("feat: shipped it", at="2026-05-04T10:00:00+02:00")
    since = [] if "--since" in period else ["--since", "2026-04-01"]
    args = ["report", "--repo", str(repo.path), "--author-email", ME.email, *since, *period]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "| May 2026 | 1 | 1 |" in result.stdout


@pytest.mark.parametrize(
    ("days", "sentence"),
    [
        (["2026-05-06T09:00:00+02:00"],  # Wednesday
         "Your longest streak was **1 weekday** with commits, on 6 May 2026 (weekends neither "
         "break nor extend a streak; a weekday without a commit — a holiday or leave too — ends "
         "it)."),
        (["2026-05-09T09:00:00+02:00", "2026-05-10T09:00:00+02:00"],  # Saturday and Sunday
         "Every commit was made on a Saturday or a Sunday, so there is no streak of weekdays "
         "(weekends neither break nor extend a streak)."),
    ],
)  # fmt: skip
def test_the_streak_sentence_always_states_the_rule(make_repo, tmp_path, days, sentence) -> None:
    repo = make_repo("app")
    for n, at in enumerate(days):
        repo.commit(f"feat: {n}", at=at)
    assert sentence in section(render_markdown(build(tmp_path)), "Summary")


def test_every_count_the_markdown_prints_is_read_from_the_model(make_repo, tmp_path) -> None:
    """Not pushed yet, the deliveries and the quiet repositories are counted once, in Insights:
    a model with other numbers renders those numbers, every time, in every place."""
    scenario(make_repo, tmp_path)
    report = build(tmp_path)
    ghost = Repo("ghost", tmp_path / "ghost", tmp_path / "ghost" / ".git", False)
    numbers = replace(report.insights, not_pushed=77, delivered=5, delivered_by_you=2,
                      delivered_squashed=0, merged_for_others=3, quiet_repos=(ghost,))  # fmt: skip
    doctored = replace(report, insights=numbers)
    for _ in range(2):
        text = render_markdown(doctored)
        summary = section(text, "Summary")
        assert "77 more commits are not pushed yet" in summary
        assert (
            "5 merge requests and pull requests of yours were merged, and you merged 3" in summary
        )
        assert "77 commits of yours are on no remote branch" in section(text, "Not pushed yet")
        assert "77 commits on no remote branch are listed under *Not pushed yet*" in text
        assert "No commits of yours in this period: ghost." in text
        assert "by you, 3 by colleagues" in section(text, "Delivered")


def test_the_model_counts_what_the_collection_holds(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)
    make_repo("quiet").commit("feat: long ago", at="2025-01-01T09:00:00+01:00")
    insights = build(tmp_path).insights
    assert (insights.not_pushed, insights.delivered, insights.merged_for_others) == (1, 0, 0)
    # quiet: the 2025-only repository, and app's bare remote (its commits count in the clone)
    assert [repo.name for repo in insights.quiet_repos] == ["app", "quiet"]


def test_kinds_of_work_says_its_shares_are_rounded(make_repo, tmp_path) -> None:
    scenario(make_repo, tmp_path)
    kinds = section(render_markdown(build(tmp_path)), "Kinds of work")
    assert (
        "Shares are rounded to whole percents, so a table's shares can add up to a few points "
        "more or less than 100%."
    ) in kinds
