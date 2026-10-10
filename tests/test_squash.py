"""sijl.squash, the stage: GitHub squash merges never double-count and never lose work.

A squash merge of yours on a mainline is a delivery. When the commits of its branch are still
here they are the code — linked by tree, then subject, then ticket — and the squash is not counted
again; those only on your laptop are delivered via the pull request, so they leave *Not pushed
yet*. When they are gone the squash itself is counted as code, with its lines — unless pushed
commits of yours that it could not be linked to were written since your previous delivery, which
it might hold. A trailing `(#N)` alone is just an issue reference."""

import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from sijl.collect import Collection, RepoCommits, collect
from sijl.deliveries import find_deliveries
from sijl.discover import discover
from sijl.git import NUMSTAT_FORMAT, GitError, GitRunner
from sijl.identity import IdentitySet
from sijl.models import AUTHOR, GITHUB, LINKED, PROMOTED, UNLINKED, Lines, Reach, Repo
from sijl.normalize import collapse
from sijl.remotes import Forges, HostResolver, link_commits
from sijl.squash import link_squashes
from sijl.window import Window
from tests.gitfactory import (
    COLLEAGUE,
    GITHUB_WEB,
    ME,
    ME_GITHUB,
    REVIEW_PERIOD,
    RepoBuilder,
    raw_commit,
    run_git,
)
from tests.test_collect import RecordingGit
from tests.test_deliveries import at, started

MINE = IdentitySet.from_emails([ME.email, ME_GITHUB.email])


def branch(repo: RepoBuilder, name: str, *days: int, start: str = "main", who=ME) -> list[str]:
    """`name` from `start` with one commit of yours per day, each writing its own file; back on
    main. Returns the commits' SHAs."""
    repo.branch(name, start)
    repo.switch(name)
    shas = [
        repo.commit(f"feat: {name} {day}", at=at(day), who=who, files={f"{name}-{day}.txt": "x\n"})
        for day in days
    ]
    repo.switch("main")
    return shas


def remote(repo: RepoBuilder, tmp_path: Path) -> Path:
    return tmp_path / "remotes" / f"{repo.path.name}.git"


def run(*paths: Path, git: GitRunner | None = None, tickets_of=None, window=REVIEW_PERIOD):
    git = git or GitRunner()
    found = collect(discover([], list(paths), git).repos, MINE, window, git)
    found = find_deliveries(collapse(found, git), MINE, window, git)
    extra = {"tickets_of": tickets_of} if tickets_of else {}
    return link_squashes(found, window, git, **extra)


def squashes(rc) -> list:
    return [d for d in rc.deliveries if d.squash]


# --------------------------------------------------------------------------- the main cases


def test_a_squash_whose_branch_commits_are_pushed_counts_once_as_a_delivery(make_repo, tmp_path):
    repo = started(make_repo)
    one, two = branch(repo, "fix/typo", 4, 5)
    sha = repo.github_squash("fix/typo", number=12, title="Fix a typo", at=at(7))
    repo.publish(remote(repo, tmp_path), "--all")  # the branch is still on the remote
    result = run(repo.path)
    [rc] = result.repos
    assert {c.sha for c in rc.commits} == {one, two}  # the work, once: never the squash as well
    [d] = rc.deliveries
    assert (d.forge, d.ref, d.title, d.merged_on.isoformat(), d.merge.sha) == (
        GITHUB,
        "#12",
        "Fix a typo",
        "2026-05-07",
        sha,
    )
    assert (d.role, d.squash, d.commits, d.yours, d.lines) == (AUTHOR, LINKED, 2, 2, None)
    assert {c: v.merge.sha for c, v in rc.via.items()} == {one: sha, two: sha}
    tally = result.squashes
    assert (tally.found, tally.linked, tally.promoted, tally.unlinked) == (1, 1, 0, 0)
    # The tip by its tree; the other by the item GitHub listed for it.
    assert (tally.commits_linked, tally.by_tree, tally.by_subject, tally.relabelled) == (2, 1, 1, 0)


def test_branch_commits_left_on_your_laptop_are_delivered_via_the_pull_request(make_repo, tmp_path):
    # GitHub deleted the branch after the merge; your local branch still holds the commits.
    # Left under Not pushed yet while the squash was counted, the work would look unfinished.
    repo = started(make_repo)
    one, two = branch(repo, "fix/typo", 4, 5)
    repo.github_squash("fix/typo", number=12, title="Fix a typo", at=at(7))
    repo.publish(remote(repo, tmp_path), "main")
    result = run(repo.path)
    [rc] = result.repos
    assert [c.sha for c in rc.commits] == [two, one] and rc.local_only == ()
    assert {rc.via[one].ref, rc.via[two].ref} == {"#12"}
    assert result.squashes.relabelled == 2


def test_a_squash_whose_branch_commits_are_gone_counts_as_code_with_its_lines(make_repo, tmp_path):
    repo = started(make_repo)
    branch(repo, "fix/typo", 4, 5)
    sha = repo.github_squash("fix/typo", number=12, title="Fix a typo", at=at(7))
    repo.git("branch", "-D", "fix/typo")  # deleted everywhere: only the squash is left
    repo.publish(remote(repo, tmp_path), "main")
    result = run(repo.path)
    [rc] = result.repos
    assert [c.sha for c in rc.commits] == [sha]
    [d] = rc.deliveries
    assert (d.squash, d.commits, d.yours, d.lines) == (PROMOTED, 1, 1, Lines(2, 0, 0))
    assert rc.lines == {sha: Lines(2, 0, 0)}  # each branch commit wrote a one-line file
    assert (result.squashes.found, result.squashes.promoted) == (1, 1)


def test_a_trailing_issue_reference_alone_is_not_a_squash(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    sha = repo.commit("fix: the typo (#12)", at=at(4), files={"t.txt": "x"})  # committed by you
    repo.publish(remote(repo, tmp_path), "main")
    result = run(repo.path)
    [rc] = result.repos
    assert [c.sha for c in rc.commits] == [sha] and rc.deliveries == ()
    assert result.squashes.found == 0


def test_a_squash_that_cannot_be_linked_stays_counted_and_the_doubt_is_disclosed(
    make_repo, tmp_path
):
    # The branch was not up to date (different tree) and the description was edited (no list of
    # its commits): nothing links. Your pushed commits since your previous delivery may be its
    # branch, but they may as well be another branch's: taking the squash out could lose the work
    # so it stays counted, with its lines, and Caveats count both.
    repo = started(make_repo)
    pushed = branch(repo, "feature/x", 4, 5)
    repo.commit("fix: a colleague's on main", at=at(6), who=COLLEAGUE, files={"c.txt": "x"})
    sha = repo.github_squash("feature/x", number=20, title="X", at=at(8), body="Edited.")
    repo.publish(remote(repo, tmp_path), "--all")
    result = run(repo.path)
    [rc] = result.repos
    assert {c.sha for c in rc.commits} == {*pushed, sha}  # nothing counted left the totals
    [d] = rc.deliveries
    assert (d.squash, d.merge.sha, d.commits, d.yours, d.lines) == (
        UNLINKED,
        sha,
        1,
        1,
        Lines(2, 0, 0),
    )
    tally = result.squashes
    assert (tally.found, tally.unlinked, tally.unlinked_pushed, tally.promoted) == (1, 1, 2, 0)


def test_a_web_edit_ending_in_an_issue_number_never_leaves_the_totals(make_repo, tmp_path):
    # GitHub's web editor also commits as GitHub, at one instant: the shape of a squash. Read as
    # one that links nothing, it stays counted.
    repo = started(make_repo)
    branch(repo, "spike", 3)
    web = repo.commit("docs: fix a broken link (#30)", at=at(4), who=ME, committer=GITHUB_WEB,
                      files={"README.md": "x\n"})  # fmt: skip
    repo.publish(remote(repo, tmp_path), "main", "spike")
    result = run(repo.path)
    assert web in {c.sha for c in result.repos[0].commits}
    assert result.squashes.unlinked == 1


def test_a_rebase_merge_of_a_commit_naming_an_issue_is_your_commit_not_a_squash(
    make_repo, tmp_path
):
    # GitHub's "Rebase and merge" commits your commit anew — committed by GitHub, one parent, the
    # subject still ending in the issue it references — but keeps its author date.
    repo = started(make_repo)
    branch(repo, "spike", 3)  # pushed and unlinked: once, it kept the "squash" out of the totals
    repo.branch("fix/x", "main")
    repo.switch("fix/x")
    mine = repo.commit("fix: handle empty input (#30)", at=at(4), files={"x.txt": "x\n"})
    repo.switch("main")
    repo.commit("chore: a colleague's", at=at(5), who=COLLEAGUE, files={"c.txt": "c\n"})
    rebased = repo.cherry_pick(mine, at=at(6), committer=GITHUB_WEB)
    repo.git("branch", "-D", "fix/x")
    repo.publish(remote(repo, tmp_path), "main", "spike")
    result = run(repo.path)
    [rc] = result.repos
    assert rebased in {c.sha for c in rc.commits} and rc.deliveries == ()
    assert result.squashes.found == 0


# ------------------------------------------------------------------------------- linking ranks


def test_a_tree_link_takes_the_whole_branch_below_the_tip(make_repo, tmp_path) -> None:
    # The description was edited, so only the tree says which branch it was; the commits of
    # yours below the tip, through your own sync merge, are its commits too.
    repo = started(make_repo)
    first, _ = branch(repo, "feature/x", 4, 5)
    repo.commit("fix: a colleague's on main", at=at(6), who=COLLEAGUE, files={"c.txt": "x"})
    repo.switch("feature/x")
    repo.merge("main", message_text="Merge branch 'main' into feature/x\n", at=at(7))
    tip = repo.commit("feat: after the sync", at=at(8), files={"tip.txt": "x"})
    repo.switch("main")
    repo.github_squash("feature/x", number=21, title="X", at=at(9), body="Edited.")
    repo.publish(remote(repo, tmp_path), "main")
    result = run(repo.path)
    [rc] = result.repos
    assert first in rc.via and tip in rc.via and len(rc.via) == 3
    tally = result.squashes
    assert (tally.by_tree, tally.by_branch, tally.relabelled) == (1, 2, 3)


def test_a_branch_walk_stops_at_commits_an_earlier_squash_names(make_repo, tmp_path) -> None:
    # feature/b was stacked on feature/a. #1 squashed feature/a (not up to date: no tree) and lists
    # its commits; #2 squashed feature/b, whose tip has #2's tree. Walking down from that tip
    # reaches feature/a's commits — but #1 names them, so they stay #1's: taking them would leave
    # #1 with nothing, counted as code beside the same work counted as commits.
    repo = started(make_repo)
    a1, a2 = branch(repo, "feature/a", 3, 4)
    repo.commit("fix: a colleague's", at=at(5), who=COLLEAGUE, files={"c.txt": "x"})
    repo.github_squash("feature/a", number=1, title="A", at=at(6))  # lists both commits
    [b] = branch(repo, "feature/b", 7, start="feature/a")
    repo.switch("feature/b")
    repo.merge("main", message_text="Merge branch 'main' into feature/b\n", at=at(8))
    tip = repo.commit("feat: b's tip", at=at(8), files={"tip.txt": "x"})  # #2's tree
    repo.switch("main")
    repo.github_squash("feature/b", number=2, title="B", at=at(9), body="Edited.")
    repo.publish(remote(repo, tmp_path), "main")
    result = run(repo.path)
    [rc] = result.repos
    refs = [rc.via[sha].ref for sha in (a1, a2, b, tip)]
    assert refs == ["#1", "#1", "#2", "#2"]
    assert {d.ref: d.squash for d in rc.deliveries} == {"#1": LINKED, "#2": LINKED}


def test_the_tree_wins_over_a_subject_another_squash_names(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    [z] = branch(repo, "feature/z", 3)
    repo.github_squash("feature/z", number=1, title="Z", at=at(6), body="* fix: x")
    repo.branch("fix/x", "main")  # up to date with main: its tip's tree becomes #2's tree
    repo.switch("fix/x")
    x = repo.commit("fix: x", at=at(5), files={"x.txt": "x"})  # written before #1 was merged
    repo.switch("main")
    repo.github_squash("fix/x", number=2, title="X", at=at(7), body="Edited.")
    repo.publish(remote(repo, tmp_path), "main")
    [rc] = run(repo.path).repos
    assert (rc.via[x].ref, rc.via[z].ref) == ("#2", "#1")


def test_a_subject_links_to_the_first_squash_after_it_and_each_item_names_one_commit(
    make_repo, tmp_path
):
    # Neither branch is up to date, so no tree matches. GitHub's list names each commit once:
    # "* wip" in #1 takes the older "wip", #2's the newer; one written after #2 stays unlinked.
    repo = started(make_repo)
    repo.branch("a", "main")
    repo.switch("a")
    wip_a = repo.commit("wip", at=at(3), files={"a.txt": "x"})
    repo.switch("main")
    repo.commit("fix: a colleague's", at=at(4), who=COLLEAGUE, files={"c1.txt": "x"})
    repo.github_squash("a", number=1, title="wip", at=at(5))  # one commit: titled after it
    repo.branch("b", "a")
    repo.switch("b")
    wip_b = repo.commit("wip", at=at(6), files={"b.txt": "x"})
    late = repo.commit("wip", at=at(12), files={"b2.txt": "x"})
    repo.switch("main")
    repo.commit("fix: another of a colleague's", at=at(7), who=COLLEAGUE, files={"c2.txt": "x"})
    repo.github_squash("b", number=2, title="B", at=at(8), body="* wip\n\n* other")
    repo.publish(remote(repo, tmp_path), "main")
    result = run(repo.path)
    [rc] = result.repos
    assert (rc.via[wip_a].ref, rc.via[wip_b].ref) == ("#1", "#2")  # the title, then an item
    assert late not in rc.via  # written after #2 was merged: never in it
    assert [c.sha for c in rc.local_only] == [late]
    assert result.squashes.by_subject == 2


def test_a_subject_links_a_pushed_commit_before_an_unpushed_one_and_then_the_latest(
    make_repo, tmp_path
):
    # An abandoned local "fix: lint" and an older pushed one, both written before PR #5 — titled
    # "fix: lint" after its one commit. The PR's commit is the pushed one written last before it
    # The stray stays under Not pushed yet, the old one stays counted, unlinked.
    repo = started(make_repo)
    repo.branch("old", "main")
    repo.switch("old")
    old = repo.commit("fix: lint", at=at(2), files={"old.txt": "x\n"})
    repo.switch("main")
    repo.branch("experiment", "main")
    repo.switch("experiment")
    stray = repo.commit("fix: lint", at=at(4, 5), files={"exp.txt": "x\n"})
    repo.switch("main")
    repo.branch("fix/lint", "main")
    repo.switch("fix/lint")
    real = repo.commit("fix: lint", at=at(3), files={"lint.txt": "x\n"})
    repo.switch("main")
    repo.commit("chore: a colleague's", at=at(5), who=COLLEAGUE, files={"c.txt": "c\n"})
    repo.github_squash("fix/lint", number=5, title="fix: lint", at=at(6))
    repo.publish(remote(repo, tmp_path), "main", "fix/lint", "old")
    [rc] = run(repo.path).repos
    assert set(rc.via) == {real}
    assert [c.sha for c in rc.local_only] == [stray] and old in {c.sha for c in rc.commits}


def test_tickets_link_only_through_the_extractor_given(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    repo.branch("feature/PROJ-7", "main")
    repo.switch("feature/PROJ-7")
    mine = repo.commit("feat: the report", at=at(4), body="Part of PROJ-7.", files={"r": "x"})
    repo.switch("main")
    repo.commit("fix: a colleague's", at=at(5), who=COLLEAGUE, files={"c.txt": "x"})
    repo.github_squash("feature/PROJ-7", number=9, title="PROJ-7 report", at=at(6), body="Done.")
    repo.publish(remote(repo, tmp_path), "main")
    assert mine not in run(repo.path).repos[0].via  # no extractor: tickets link nothing

    def tickets_of(text: str) -> set[str]:
        return set(re.findall(r"PROJ-[0-9]+", text))

    result = run(repo.path, tickets_of=tickets_of)
    assert result.repos[0].via[mine].ref == "#9" and result.squashes.by_ticket == 1
    assert result.repos[0].deliveries[0].tickets == {"PROJ-7"}


# ---------------------------------------------------------------------------- the promotion rule


def test_unpushed_unlinked_commits_leave_a_squash_counted_but_not_verified(make_repo, tmp_path):
    # An unlinked commit on your laptop may be the squash's branch, so its branch commits cannot
    # be said to be gone; but it is not in the totals, so counting the squash counts nothing
    # twice — and leaving the squash out would lose the pull request's work.
    repo = started(make_repo)
    branch(repo, "feature/x", 4, 5)
    repo.commit("fix: a colleague's on main", at=at(6), who=COLLEAGUE, files={"c.txt": "x"})
    sha = repo.github_squash("feature/x", number=20, title="X", at=at(8), body="Edited.")
    repo.publish(remote(repo, tmp_path), "main")  # the branch only on your laptop
    result = run(repo.path)
    [rc] = result.repos
    assert sha in {c.sha for c in rc.commits} and len(rc.local_only) == 2
    assert squashes(rc)[0].squash == UNLINKED
    assert (result.squashes.unlinked_pushed, result.squashes.unlinked_other) == (0, 2)


def test_only_unlinked_work_since_your_previous_delivery_casts_doubt(make_repo, tmp_path):
    repo = started(make_repo)
    [spike] = branch(repo, "spike", 2)  # pushed, never merged
    branch(repo, "fix/a", 3)
    repo.github_squash("fix/a", number=30, title="A", at=at(4))
    branch(repo, "fix/c", 8)
    c = repo.github_squash("fix/c", number=32, title="C", at=at(9))
    repo.git("branch", "-D", "fix/a", "fix/c")  # both branches gone
    repo.publish(remote(repo, tmp_path), "--all")
    result = run(repo.path)
    [rc] = result.repos
    # #30 links nothing, and `spike` — pushed, unlinked, written before it — may be its branch:
    # not linked, still counted. #32: nothing is unlinked since #30, your previous delivery.
    assert {d.ref: d.squash for d in rc.deliveries} == {"#30": UNLINKED, "#32": PROMOTED}
    a = next(d.merge.sha for d in rc.deliveries if d.ref == "#30")
    assert {spike, a, c} == {x.sha for x in rc.commits}
    assert result.squashes.unlinked_pushed == 1


def test_your_previous_delivery_counts_from_any_clone_of_the_project(make_repo, tmp_path):
    # A delivery is listed once, in the first clone holding it. A stale clone sorted first holds
    # your pull request #7; the fresh one holds it too, and squash #8 after it. "Since your
    # previous delivery" must still start at #7, so the spike written before it casts no doubt
    # on #8.
    work = started(make_repo, "work")
    origin = tmp_path / "remotes" / "app.git"
    branch(work, "feat", 4)
    work.github_pr_merge("feat", number=7, title="Feat", at=at(5))
    work.publish(origin, "main")
    run_git(tmp_path, "clone", "-q", str(origin), str(tmp_path / "0-stale"))
    branch(work, "spike", 3)  # pushed, never merged, written before #7
    branch(work, "c", 10)
    work.github_squash("c", number=8, title="C", at=at(11))
    work.git("branch", "-D", "c")
    work.git("push", "-q", "origin", "main", "spike")
    run_git(tmp_path, "clone", "-q", str(origin), str(tmp_path / "1-fresh"))
    for paths in ((tmp_path / "1-fresh",), (tmp_path / "0-stale", tmp_path / "1-fresh")):
        result = run(*paths)
        assert [d.squash for rc in result.repos for d in rc.deliveries if d.squash] == [PROMOTED]


# ------------------------------------------------------------------- periods side by side

Q2 = Window(date(2026, 4, 1), date(2026, 6, 30))
Q3 = Window(date(2026, 7, 1), date(2026, 9, 30))
BOTH = Window(date(2026, 4, 1), date(2026, 9, 30))
LATE = "2026-07-02T10:00:00+02:00"  # #40 is merged in Q3


def late_branch(make_repo, *days: str, keep: str) -> RepoBuilder:
    """Branch fix/late with one commit of yours on each of `days`, squash-merged as #40 in Q3;
    afterwards the branch is kept on the remote ("remote"), only on your laptop ("laptop") or
    deleted everywhere ("gone")."""
    repo = started(make_repo)
    repo.branch("fix/late", "main")
    repo.switch("fix/late")
    for n, day in enumerate(days):
        repo.commit(f"feat: late {n}", at=day, files={f"late-{n}.txt": "x\n"})
    repo.switch("main")
    repo.commit("fix: a colleague's", at="2026-06-01T09:00:00+02:00", who=COLLEAGUE,
                files={"c.txt": "c\n"})  # fmt: skip
    repo.github_squash("fix/late", number=40, title="Late", at=LATE)  # lists its commits
    return repo


@pytest.mark.parametrize("keep", ["remote", "laptop", "gone"])
@pytest.mark.parametrize(
    "days",
    [
        ("2026-06-27T10:00:00+02:00", "2026-06-28T10:00:00+02:00"),  # all before the squash's Q3
        ("2026-06-28T10:00:00+02:00", "2026-07-01T10:00:00+02:00"),  # across the boundary
    ],
)
def test_two_adjacent_periods_count_what_one_period_over_both_counts(
    make_repo, tmp_path, keep, days
):
    # Q2 counts the branch commits written in it — pushed, or delivered from your laptop by #40,
    # merged after Q2; Q3 finds #40's branch commits in the lookback before it, so #40 is a
    # delivery there, never code. A report over both counts the same commits.
    repo = late_branch(make_repo, *days, keep=keep)
    refs = {"remote": ["--all"], "laptop": ["main"], "gone": ["main"]}[keep]
    if keep == "gone":
        repo.git("branch", "-D", "fix/late")
    repo.publish(remote(repo, tmp_path), *refs)
    q2, q3, both = (run(repo.path, window=w) for w in (Q2, Q3, BOTH))
    counted = [{c.sha for c in result.commits} for result in (q2, q3, both)]
    assert counted[0].isdisjoint(counted[1]) and counted[0] | counted[1] == counted[2]
    assert [(d.ref, d.squash) for rc in q3.repos for d in rc.deliveries] == [
        ("#40", PROMOTED if keep == "gone" else LINKED)
    ]
    assert q2.repos[0].deliveries == ()


def test_a_squash_whose_branch_was_written_before_the_period_is_a_delivery_not_code(
    make_repo, tmp_path
):
    days = ("2026-06-20T10:00:00+02:00", "2026-06-21T10:00:00+02:00")  # listed by #40
    repo = late_branch(make_repo, *days, keep="remote")
    repo.publish(remote(repo, tmp_path), "--all")
    result = run(repo.path, window=Q3)
    [d] = result.repos[0].deliveries
    assert (d.squash, d.yours) == (LINKED, 2) and result.commits == []
    assert (result.squashes.linked, result.squashes.linked_earlier) == (1, 1)


def test_branch_commits_older_than_the_lookback_are_not_read_and_that_is_said(make_repo, tmp_path):
    # Written in March, merged in July: more than 90 days before Q3, so not read — #40 is counted
    # as code in Q3 (its March commits were counted in Q1) and Caveats name the limit.
    repo = late_branch(make_repo, "2026-03-20T10:00:00+01:00", keep="remote")
    repo.publish(remote(repo, tmp_path), "--all")
    result = run(repo.path, window=Q3)
    [d] = result.repos[0].deliveries
    assert d.squash == PROMOTED and len(result.commits) == 1
    assert result.squashes.lookback_limited == 1


# ------------------------------------------------------------------------------------- edges


def test_a_squash_after_the_period_still_delivers_commits_written_in_it(make_repo, tmp_path):
    repo = started(make_repo)
    [mine] = branch(repo, "fix/late", 28)
    repo.github_squash("fix/late", number=40, title="Late", at="2026-07-02T10:00:00+02:00")
    repo.publish(remote(repo, tmp_path), "main")
    result = run(repo.path)
    [rc] = result.repos
    assert [c.sha for c in rc.commits] == [mine] and rc.via[mine].ref == "#40"
    assert rc.deliveries == ()  # merged after the period: not listed
    assert (result.squashes.found, result.squashes.after_period, result.squashes.relabelled) == (
        0,
        1,
        1,
    )


def test_a_squash_into_another_branch_is_an_ordinary_commit(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/base", 4)
    branch(repo, "feature/top", 5, start="feature/base")
    repo.switch("feature/base")
    sha = repo.github_squash("feature/top", number=50, title="Top", at=at(6))
    repo.switch("main")
    repo.publish(remote(repo, tmp_path), "--all")
    result = run(repo.path)
    assert sha in {c.sha for c in result.repos[0].commits}
    assert (result.squashes.found, result.squashes.off_mainline) == (0, 1)


def test_a_colleagues_squash_is_not_yours(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "fix/theirs", 4, who=COLLEAGUE)
    repo.github_squash("fix/theirs", number=60, title="Theirs", at=at(5), who=COLLEAGUE)
    repo.publish(remote(repo, tmp_path), "main")
    result = run(repo.path)
    assert result.repos[0].deliveries == () and result.squashes.found == 0


def test_a_squash_in_two_clones_is_listed_once(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "fix/typo", 4)
    repo.github_squash("fix/typo", number=12, title="Fix a typo", at=at(7))
    repo.publish(remote(repo, tmp_path), "--all")
    copy = repo.clone(tmp_path / "app-copy")
    result = run(repo.path, copy.path)
    assert [len(squashes(rc)) for rc in result.repos] == [1, 0]
    assert result.deliveries_in_other_clones == 1 and len(result.commits) == 1


def two_clones(root: Path, *, delete_branch: bool) -> tuple[Path, Path, set, str]:
    """Your dev clone, where feature/x was written and pushed; GitHub squash-merges it as #12 (a
    scratch clone stands in for github.com); then a fresh clone of yours. The dev clone never
    fetched the merge; when GitHub deleted the branch, only the dev clone still knows it."""
    dev = started(lambda name: RepoBuilder(root / name), "a-dev")
    one, two = branch(dev, "feature/x", 4, 5)
    origin = root / "remotes" / "app.git"
    dev.publish(origin, "main", "feature/x")
    run_git(root, "clone", "-q", str(origin), str(root / "gh"))
    gh = RepoBuilder.existing(root / "gh")
    gh.git("branch", "feature/x", "origin/feature/x")
    gh.commit("fix: a colleague's", at=at(6), who=COLLEAGUE, files={"c.txt": "c\n"})
    sha = gh.github_squash("feature/x", number=12, title="X", at=at(7))
    gh.git("push", "-q", "origin", "main", *([":feature/x"] if delete_branch else []))
    run_git(root, "clone", "-q", str(origin), str(root / "z-fresh"))
    return dev.path, root / "z-fresh", {one, two}, sha


def test_a_squash_links_its_branch_commits_in_another_clone_of_the_project(make_repo, tmp_path):
    # The branch commits are listed in the dev clone (best reached there, or first), the squash
    # only in the fresh one: linked across the two, as they share a root.
    for delete_branch in (False, True):
        dev, fresh, branch_commits, sha = two_clones(
            tmp_path / str(delete_branch), delete_branch=delete_branch
        )
        for paths in ((dev, fresh), (fresh, dev)):
            result = run(*paths)
            assert {c.sha for c in result.commits} == branch_commits, (delete_branch, paths)
            [d] = [d for rc in result.repos for d in rc.deliveries]
            assert (d.merge.sha, d.squash, d.yours) == (sha, LINKED, 2)


class RootsFail(GitRunner):
    def run(self, cwd, *args, stdin=None):
        if args[:2] == ("rev-list", "--max-parents=0"):
            raise GitError(args, 128, "fatal: cannot read roots")
        return super().run(cwd, *args, stdin=stdin)


def test_clones_whose_roots_cannot_be_read_are_linked_within_themselves(make_repo, tmp_path):
    dev, fresh, branch_commits, sha = two_clones(tmp_path, delete_branch=True)
    result = run(fresh, dev, git=RootsFail())
    assert {c.sha for c in result.commits} == {*branch_commits, sha}  # counted, not lost
    assert result.squashes.roots_unread == 2


def test_clones_of_different_projects_are_never_linked(make_repo, tmp_path) -> None:
    api = make_repo("api")
    api.commit("chore: api starts", at="2026-03-01T09:00:00+01:00", who=COLLEAGUE)
    [mine] = branch(api, "fix/x", 4)
    api.publish(remote(api, tmp_path), "--all")
    web = make_repo("web")
    web.commit("chore: web starts", at="2026-03-01T09:00:00+01:00", who=COLLEAGUE)
    web.branch("fix/x", "main")
    web.switch("fix/x")
    web.commit("feat: fix/x 4", at=at(5), files={"w.txt": "x\n"})  # the same subject as api's
    web.switch("main")
    web.commit("fix: a colleague's", at=at(6), who=COLLEAGUE, files={"c.txt": "c\n"})
    web.github_squash("fix/x", number=3, title="X", at=at(7))
    web.git("branch", "-D", "fix/x")
    web.publish(remote(web, tmp_path), "main")
    result = run(api.path, web.path)
    assert mine not in result.repos[0].via


def test_lines_cost_one_git_call_per_repository_and_none_in_a_partial_clone(make_repo, tmp_path):
    repo = started(make_repo)
    for n, day in enumerate((4, 6), start=1):
        branch(repo, f"fix/{n}", day)
        repo.github_squash(f"fix/{n}", number=n, title=f"F{n}", at=at(day + 1))
        repo.git("branch", "-D", f"fix/{n}")
    repo.publish(remote(repo, tmp_path), "main")
    git = RecordingGit()
    result = run(repo.path, git=git)
    assert len([c for c in git.calls if f"--format={NUMSTAT_FORMAT}" in c]) == 1
    assert [d.lines for d in result.repos[0].deliveries] == [Lines(1, 0, 0), Lines(1, 0, 0)]
    # A partial clone would fetch the file contents a change needs, into itself: never asked.
    repo.git("config", "uploadpack.allowfilter", "true")
    partial = tmp_path / "partial"
    run_git(tmp_path, "clone", "-q", "--filter=blob:none", f"file://{repo.path}", str(partial))
    objects = sorted(str(p) for p in (partial / ".git" / "objects").rglob("*"))
    git = RecordingGit()
    result = run(partial, git=git)
    assert sorted(str(p) for p in (partial / ".git" / "objects").rglob("*")) == objects
    assert not [c for c in git.calls if f"--format={NUMSTAT_FORMAT}" in c]
    assert [d.lines for d in result.repos[0].deliveries] == [None, None]
    assert result.squashes.lines_unread == 2 and result.squashes.promoted == 2


def test_commits_delivered_from_your_laptop_get_no_link_and_are_not_missing_one(
    make_repo, tmp_path
) -> None:
    # Counted now, but on no remote branch: there is no page to link to, and that is not a
    # "pushed but unlinked" commit either. The squash merge links to its pull request.
    repo = started(make_repo)
    one, two = branch(repo, "fix/typo", 4, 5)
    repo.github_squash("fix/typo", number=12, title="Fix a typo", at=at(7))
    direct = repo.commit("fix: straight to main", at=at(8), files={"d.txt": "x"})
    repo.publish(remote(repo, tmp_path), "main")
    repo.git("remote", "set-url", "origin", "git@github.com:alexgh/app.git")
    git = RecordingGit()
    result = run(repo.path, git=git)
    found = link_commits(result.repos, git, Forges(), HostResolver(None))
    path = repo.path
    assert found.commit_url(path, direct).endswith(f"/commit/{direct}")
    assert found.commit_url(path, one) == found.commit_url(path, two) == ""
    assert found.of(path).unlinked == 0
    [d] = result.repos[0].deliveries
    assert found.request_url(path, d) == "https://github.com/alexgh/app/pull/12"


class NoGit(GitRunner):
    """Answers every call as git does when there is nothing to read: no config key, no lines."""

    def run(self, cwd, *args, stdin=None):
        raise GitError(args, 1, "")


def test_eight_thousand_squash_merges_take_well_under_a_second() -> None:
    # Each squash i is merged a minute after an unrelated pushed commit i of yours: nothing
    # links, every one is "could not be linked", every interval holds one commit (an outcome
    # loop that scanned every delivery and every commit per squash once took 6.8 s).
    start = datetime(2026, 4, 2, tzinfo=UTC)
    commits, squash_commits, reach = [], [], {}
    for i in range(8000):
        when = start + timedelta(minutes=2 * i)
        mine = raw_commit(sha=f"c{i:039x}", tree=f"d{i:039x}", subject=f"other {i}",
                          at=when.isoformat())  # fmt: skip
        squash = raw_commit(who=ME_GITHUB, sha=f"a{i:039x}", tree=f"b{i:039x}",
                            subject=f"S{i} (#{i + 1})", body=f"S{i} (#{i + 1})\n",
                            committer_email=GITHUB_WEB.email,
                            at=(when + timedelta(minutes=1)).isoformat())  # fmt: skip
        commits.append(mine)
        squash_commits.append(squash)
        reach[mine.sha], reach[squash.sha] = Reach.PUSHED, Reach.MAINLINE
    repo = Repo(name="x", path=Path("/nonexistent"), common_dir=Path("/nonexistent/.git"),
                bare=False)  # fmt: skip
    rc = RepoCommits(repo=repo, commits=tuple(commits + squash_commits), reach=reach,
                     has_remote=True, github_commits=tuple(squash_commits))  # fmt: skip
    collection = Collection(repos=(rc,), skipped=(), merges_left_out=0, seen_in_other_clones=0)
    started_at = time.perf_counter()
    result = link_squashes(collection, Window(date(2026, 4, 1), date(2026, 12, 31)), NoGit())
    assert time.perf_counter() - started_at < 1.0
    assert (result.squashes.unlinked, result.squashes.unlinked_pushed) == (8000, 8000)
