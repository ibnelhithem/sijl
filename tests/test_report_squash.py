"""The report with GitHub squash merges: each one of yours is a row under *Delivered* — its
number, title, date, link — counted once: as its linked branch commits (marked *delivered via #N*
under *Per repository*, out of *Not pushed yet*), or as itself with its lines when they are gone,
or not at all when pushed work may be its own; *Caveats* says how many of each, and why."""

from datetime import date
from pathlib import Path

from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.report import Report, build_report, caveats, render_markdown, summary_paragraph
from tests.gitfactory import COLLEAGUE, ME, ME_GITHUB, REVIEW_PERIOD, RepoBuilder
from tests.test_deliveries import at, started
from tests.test_squash import Q3, RootsFail, branch, late_branch, two_clones

MINE = IdentitySet.from_emails([ME.email, ME_GITHUB.email])
APP = "https://github.com/alexgh/app"


def build(*paths: Path) -> Report:
    return build_report(
        roots=[],
        paths=list(paths),
        identities=MINE,
        window=REVIEW_PERIOD,
        git=GitRunner(),
        generated_on=date(2026, 10, 8),
    )


def on_github(repo: RepoBuilder, tmp_path: Path, *refspecs: str) -> None:
    repo.publish(tmp_path / "remotes" / "app.git", *refspecs)
    repo.git("remote", "set-url", "origin", "git@github.com:alexgh/app.git")


def three_squashes(make_repo, tmp_path: Path) -> tuple[RepoBuilder, dict[str, str]]:
    """#12 linked (its branch only on your laptop), #13 promoted (its branch gone), #14 not linked
    (an edited description, a branch not up to date, and its pushed commit kept)."""
    repo = started(make_repo)
    shas = dict(zip(("one", "two"), branch(repo, "fix/typo", 4, 5), strict=True))
    shas["linked"] = repo.github_squash("fix/typo", number=12, title="Fix a typo", at=at(7))
    branch(repo, "fix/gone", 8)
    shas["promoted"] = repo.github_squash("fix/gone", number=13, title="Gone", at=at(9))
    repo.git("branch", "-D", "fix/gone")
    [shas["kept"]] = branch(repo, "feature/x", 10)
    repo.commit("fix: a colleague's on main", at=at(11), who=COLLEAGUE, files={"c.txt": "x"})
    shas["unlinked"] = repo.github_squash("feature/x", number=14, title="X", at=at(12),
                                          body="Edited.")  # fmt: skip
    on_github(repo, tmp_path, "main", "feature/x")
    return repo, shas


def test_each_squash_merge_is_a_delivered_row_counted_once(make_repo, tmp_path) -> None:
    text = render_markdown(build(three_squashes(make_repo, tmp_path)[0].path))
    section = text[text.index("## Delivered") : text.index("## Caveats")]
    assert (
        "**3 merge requests and pull requests** of yours were merged into a mainline, all as "
        "GitHub squash merges (git does not record by whom)."
    ) in section
    assert "A squash merge is counted once too" in section
    assert (
        f"| 2026-05-12 | [#14]({APP}/pull/14) | X | the squash, not linked: +1 -0 lines | "
        "not recorded (squash) |"
    ) in section
    assert (
        f"| 2026-05-09 | [#13]({APP}/pull/13) | Gone | the squash: +1 -0 lines | "
        "not recorded (squash) |"
    ) in section
    assert (
        f"| 2026-05-07 | [#12]({APP}/pull/12) | Fix a typo | 2 linked | not recorded (squash) |"
        in section
    )


def test_each_squash_merge_keeps_the_commits_it_counts(make_repo, tmp_path) -> None:
    """For the digest: a linked squash holds the SHAs of its linked commits; one counted as
    code, itself — so `your_commits` is always the commits `yours` counts."""
    repo, shas = three_squashes(make_repo, tmp_path)
    deliveries = {d.number: d for d in build(repo.path).collection.repos[0].deliveries}
    assert deliveries[12].your_commits == {shas["one"], shas["two"]}
    assert deliveries[13].your_commits == {shas["promoted"]}
    assert deliveries[14].your_commits == {shas["unlinked"]}
    assert all(len(d.your_commits) == d.yours for d in deliveries.values())


def test_the_numbers_count_the_work_once(make_repo, tmp_path) -> None:
    report = build(three_squashes(make_repo, tmp_path)[0].path)
    # two linked branch commits (from your laptop), the promoted squash, the pushed commit kept
    # unlinked and the squash it could not be linked to (both counted: never lose work)
    assert (report.total, report.collection.local_only) == (5, [])
    assert summary_paragraph(report).startswith(
        "Between 1 April 2026 and 30 June 2026 you authored **5 commits** on **5 active days**"
    )
    assert "## Not pushed yet" not in render_markdown(report)


def test_per_repository_marks_what_a_squash_delivered_and_a_promoted_squashs_lines(
    make_repo, tmp_path
) -> None:
    repo, shas = three_squashes(make_repo, tmp_path)
    text = render_markdown(build(repo.path))
    rows = text[text.index("## Appendix") :]  # every counted commit is in the Appendix
    assert f"| 2026-05-04 | `{shas['one'][:8]}` (delivered via [#12]({APP}/pull/12)) |" in rows
    assert (
        f"| 2026-05-09 | [`{shas['promoted'][:8]}`]({APP}/commit/{shas['promoted']}) "
        "(squash merge, +1 -0 lines) | Gone (#13) |"
    ) in rows
    assert shas["linked"][:8] not in rows
    assert f"[`{shas['unlinked'][:8]}`]({APP}/commit/{shas['unlinked']}) (squash merge, " in rows


def test_caveats_explain_every_move(make_repo, tmp_path) -> None:
    lines = caveats(build(three_squashes(make_repo, tmp_path)[0].path))
    assert (
        "3 GitHub squash merges of yours (one-parent commits GitHub committed, the subject ending "
        "`(#N)`) are listed under *Delivered* and counted once: 1 was linked to the commits of "
        "yours on its branch, which are counted instead of the squash; 1 whose branch commits are "
        "gone — none of your commits written since your previous delivery is left unlinked — is "
        "counted as code itself, with its lines; 1 could not be linked to branch commits and is "
        "counted as code itself, with its lines — of your commits written since your previous "
        "delivery that are linked to no squash and may be its branch, 1 pushed and counted in "
        "this period, where that work may be counted twice."
    ) in lines
    assert (
        "2 of your commits were linked to a squash merge — 1 by tree (a branch tip with the "
        "squash's tree), 1 by subject (the squash's title, or an item GitHub listed in it) — and "
        "are marked *delivered via #N*; 2 of them are on no remote branch and are counted, not "
        "listed under *Not pushed yet*."
    ) in lines
    assert (
        "Lines of a squash merge counted as code are its own change against the commit before it "
        "(`git log --numstat`: a renamed file counts what changed in it, a binary file has no "
        "lines)."
    ) in lines
    assert (
        "Who merged a squash merge is not in git — GitHub is its committer, the pull request's "
        "author its author — so *Merged by* says *not recorded*."
    ) in lines


def test_a_trailing_issue_reference_is_said_to_be_one(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    repo.commit("fix: the typo (#12)", at=at(4), files={"t.txt": "x"})
    on_github(repo, tmp_path, "main")
    report = build(repo.path)
    assert report.total == 1 and "### Yours" not in render_markdown(report)
    assert any(
        "a subject ending `(#N)` in any other commit is just an issue reference" in line
        for line in caveats(report)
    )
    assert not any("GitHub squash merges of yours" in line for line in caveats(report))


def test_squash_merges_no_longer_count_as_work_without_a_request(make_repo, tmp_path) -> None:
    lines = caveats(build(three_squashes(make_repo, tmp_path)[0].path))
    assert (
        "Merge requests and pull requests merged by fast-forward or rebase — or squashed anywhere "
        "but on GitHub's website — leave no merge commit git can recognise, so git alone cannot "
        "list them as deliveries (the GitLab and GitHub APIs can; a future version may ask them)."
    ) in lines


def test_lines_not_read_and_squashes_off_the_mainline_are_counted(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "fix/gone", 4)
    repo.github_squash("fix/gone", number=13, title="Gone", at=at(5))
    repo.git("branch", "-D", "fix/gone")
    branch(repo, "feature/base", 6)
    branch(repo, "feature/top", 7, start="feature/base")
    repo.switch("feature/base")
    repo.github_squash("feature/top", number=50, title="Top", at=at(8))
    repo.switch("main")
    on_github(repo, tmp_path, "--all")
    repo.git("config", "remote.origin.promisor", "true")  # read as a partial clone
    report = build(repo.path)
    lines = caveats(report)
    assert any(
        line.endswith(
            "Lines could not be read for 1 of them (a partial clone, where reading a change would "
            "fetch file contents into it, or a git error)."
        )
        for line in lines
    )
    assert (
        "1 squash merge of yours is on no mainline's own line (a pull request squashed into "
        "another branch): counted as an ordinary commit, not linked."
    ) in lines
    assert "| the squash (lines not read) |" in render_markdown(report)


def test_the_headline_names_each_way_a_request_was_merged(make_repo, tmp_path) -> None:
    # Two ways and nothing merged for colleagues: one dash opens the list, a full stop ends it
    # (an earlier sentence ended "— 1 by you, 1 by a colleague —." in that case).
    repo = started(make_repo)
    branch(repo, "fix/typo", 4)
    repo.github_pr_merge("fix/typo", number=11, title="Typo", at=at(5))
    branch(repo, "fix/gone", 6)
    repo.github_squash("fix/gone", number=13, title="Gone", at=at(7))
    on_github(repo, tmp_path, "--all")
    text = render_markdown(build(repo.path))
    assert (
        "**2 merge requests and pull requests** of yours were merged into a mainline — 1 by you, "
        "1 as a GitHub squash merge. Each is dated"
    ) in text


def test_roots_that_cannot_be_read_are_disclosed(make_repo, tmp_path) -> None:
    dev, fresh, _, _ = two_clones(tmp_path, delete_branch=True)
    report = build_report(roots=[], paths=[fresh, dev], identities=MINE, window=REVIEW_PERIOD,
                          git=RootsFail(), generated_on=date(2026, 10, 8))  # fmt: skip
    assert (
        "Root commits could not be read in 2 repositories while linking squash merges: a squash "
        "merge there is linked only to commits in the same clone, never in another clone of the "
        "same project."
    ) in caveats(report)


def test_the_lookback_before_the_period_is_named_where_it_mattered(make_repo, tmp_path) -> None:
    def q3(repo: RepoBuilder) -> list[str]:
        on_github(repo, tmp_path / repo.path.parent.name, "--all")
        return caveats(build_report(roots=[], paths=[repo.path], identities=MINE, window=Q3,
                                    git=GitRunner(), generated_on=date(2026, 10, 8)))  # fmt: skip

    days = ("2026-06-20T10:00:00+02:00", "2026-06-21T10:00:00+02:00")
    held = q3(late_branch(lambda name: RepoBuilder(tmp_path / "held" / name), *days, keep="r"))
    assert (
        "1 squash merge in this period holds commits written before it (read in the 90 days "
        "before the period): that work counts in the period it was written; here the squash is a "
        "delivery, not code."
    ) in held
    old = q3(late_branch(lambda name: RepoBuilder(tmp_path / "old" / name),
                         "2026-03-20T10:00:00+01:00", keep="r"))  # fmt: skip
    assert (
        "For 1 squash merge of yours counted as code, no earlier delivery of yours was found "
        "within the 90 days read before the period: branch commits written before that are not "
        "read, so it may hold work already counted in an earlier period."
    ) in old
