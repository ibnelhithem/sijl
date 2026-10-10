"""One model with GitHub squash merges: Insights counts exactly the commits the
report lists under *Per repository* — relabelled laptop-only commits (delivered via #N) and
promoted squashes included, a linked squash and the lookback before the period never — and every
*Delivered* row once."""

from datetime import date

from sijl.git import GitRunner
from sijl.report import build_report, render_markdown
from tests.test_report_pushed import section
from tests.test_report_squash import MINE, build, three_squashes
from tests.test_squash import Q3, late_branch


def test_insights_reconcile_with_linked_promoted_and_relabelled_work(make_repo, tmp_path) -> None:
    repo, shas = three_squashes(make_repo, tmp_path)
    report = build(repo.path)
    insights = report.insights
    counted = [c for rc in report.collection.repos for c in rc.commits]
    counted_shas = {c.sha for c in counted}
    # #12's two branch commits were only on your laptop: relabelled, counted; #12 itself is not.
    # #13 promoted (its branch gone) and #14 unlinked are counted as code, and so is #14's
    # pushed branch commit: never lose work.
    assert {shas["one"], shas["two"], shas["promoted"], shas["unlinked"], shas["kept"]} == (
        counted_shas
    )
    assert shas["linked"] not in counted_shas
    assert insights.total == len(counted) == sum(n for _, n in insights.per_repo) == 5
    assert sum(n for _, n in insights.per_kind) == insights.total
    assert sum(n for _, n in insights.per_category) == insights.total
    assert sum(m.commits for m in insights.per_month) == insights.total
    assert insights.active_days == len({c.author_day for c in counted})
    assert insights.not_pushed == len(report.collection.local_only) == 0  # after the relabel
    rows = [d for rc in report.collection.repos for d in rc.deliveries]
    assert insights.delivered + insights.merged_for_others == len(rows) == 3
    assert (insights.delivered_squashed, insights.delivered_by_you) == (3, 0)
    text = render_markdown(report)
    assert f"### app — {insights.total} commits" in section(text, "Appendix")
    assert "**5 commits**" in section(text, "Summary")


def test_work_written_in_the_lookback_counts_in_no_month_day_or_streak(make_repo, tmp_path):
    """#40 is merged in Q3; its branch commits were written in May, in the 90-day lookback that
    pass 1 reads only to link them. Q3 gains a delivery and not a single commit."""
    repo = late_branch(
        make_repo, "2026-05-04T10:00:00+02:00", "2026-05-05T10:00:00+02:00", keep="laptop"
    )
    repo.publish(tmp_path / "remotes" / "app.git", "main")  # main is a mainline; the branch is not
    report = build_report(
        roots=[], paths=[repo.path], identities=MINE, window=Q3, git=GitRunner(),
        generated_on=date(2026, 10, 8),
    )  # fmt: skip
    insights = report.insights
    assert (insights.total, insights.active_days, insights.longest_streak.weekdays) == (0, 0, 0)
    assert [m.month for m in insights.per_month] == ["2026-07", "2026-08", "2026-09"]
    assert {m.commits for m in insights.per_month} == {0}
    assert insights.delivered == 1  # #40, linked to commits counted in their own period
