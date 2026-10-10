"""sijl.deliveries, the stage: merge requests and pull requests merged into a mainline in
the period, each with its number, title and date, and whose they are — yours when it merged a
commit you wrote (whoever clicked merge), merged for others when you merged it and wrote none of
it. Sync merges are never deliveries, and merges are never commits."""

import re
from pathlib import Path

from sijl.collect import collect
from sijl.deliveries import find_deliveries
from sijl.discover import discover
from sijl.git import GRAPH_FORMAT, GitError, GitRunner
from sijl.identity import IdentitySet
from sijl.models import AUTHOR, BOTH, GITHUB, GITLAB, MERGER
from sijl.normalize import collapse
from tests.gitfactory import (
    COLLEAGUE,
    LOOKALIKE,
    ME,
    ME_GITHUB,
    REVIEW_PERIOD,
    RepoBuilder,
    run_git,
)
from tests.test_collect import RecordingGit

MINE = IdentitySet.from_emails([ME.email, ME_GITHUB.email])


def at(day: int, month: int = 5) -> str:
    return f"2026-{month:02d}-{day:02d}T10:00:00+02:00"


def started(make_repo, name: str = "app") -> RepoBuilder:
    repo = make_repo(name)
    repo.commit("chore: start", at="2026-03-01T09:00:00+01:00", who=COLLEAGUE)
    return repo


def branch(repo: RepoBuilder, name: str, *commits, start: str = "main") -> None:
    """`name` from `start` with one commit per (day, who); back on main. Each commit writes its
    own file, so merges never conflict."""
    repo.branch(name, start)
    repo.switch(name)
    for day, who in commits:
        repo.commit(f"feat: {name} {day}", at=at(day), who=who, files={f"{name}-{day}.txt": "x"})
    repo.switch("main")


def ship(repo: RepoBuilder, tmp_path: Path) -> None:
    """Push main (and every branch) to `origin`, so main is a mainline."""
    repo.publish(tmp_path / "remotes" / f"{repo.path.name}.git", "--all")


def run(*paths: Path, git: GitRunner | None = None, tickets_of=None):
    git = git or GitRunner()
    collection = collapse(
        collect(discover([], list(paths), git).repos, MINE, REVIEW_PERIOD, git), git
    )
    extra = {"tickets_of": tickets_of} if tickets_of else {}
    return find_deliveries(collection, MINE, REVIEW_PERIOD, git, **extra)


def test_a_gitlab_merge_request_is_one_delivery(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/PROJ-12-report", (4, ME), (5, ME))
    sha = repo.gitlab_mr_merge("feature/PROJ-12-report", number=42, title="Add the x report",
                               at="2026-05-06T23:30:00-04:00")  # fmt: skip
    ship(repo, tmp_path)
    [rc] = run(repo.path).repos
    [d] = rc.deliveries
    assert (d.forge, d.number, d.ref, d.title) == (GITLAB, 42, "!42", "Add the x report")
    assert d.merged_on.isoformat() == "2026-05-06"  # the merge's author date, in its own timezone
    assert (d.merge.sha, d.branch, d.project) == (
        sha,
        "feature/PROJ-12-report",
        ("acme", "platform", "app"),
    )
    assert (d.role, d.commits, d.yours) == (BOTH, 2, 2)


def test_a_github_pull_request_is_one_delivery(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "fix/typo", (4, ME))
    repo.github_pr_merge("fix/typo", number=12, title="Fix a typo", at=at(7))  # by ME_GITHUB
    ship(repo, tmp_path)
    [d] = run(repo.path).repos[0].deliveries
    assert (d.forge, d.ref, d.title, d.merged_on.isoformat()) == (
        GITHUB,
        "#12",
        "Fix a typo",
        "2026-05-07",
    )
    assert (d.role, d.commits, d.yours, d.branch) == (BOTH, 1, 1, "fix/typo")


def test_a_pull_request_merged_with_its_title_as_the_message_is_one_delivery(make_repo, tmp_path):
    # A repository can set GitHub's merge message to the pull request's title: `Title (#N)`,
    # still a merge commit GitHub committed. It is read like `Merge pull request #N`.
    repo = started(make_repo)
    branch(repo, "fix/typo", (4, ME), (5, COLLEAGUE))
    repo.github_title_merge("fix/typo", number=12, title="Fix a typo", at=at(7))
    ship(repo, tmp_path)
    result = run(repo.path)
    [d] = result.repos[0].deliveries
    assert (d.forge, d.ref, d.title, d.branch, d.merged_on.isoformat()) == (
        GITHUB,
        "#12",
        "Fix a typo",
        "",
        "2026-05-07",
    )
    assert (d.role, d.commits, d.yours) == (BOTH, 2, 1)
    assert (result.merges_left_out, result.merges_delivered) == (1, 1)


def test_your_merge_request_merged_by_a_colleague_is_still_yours(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=1, title="X", at=at(6), who=COLLEAGUE)
    ship(repo, tmp_path)
    [d] = run(repo.path).repos[0].deliveries
    assert (d.role, d.yours) == (AUTHOR, 1)


def test_merging_a_colleagues_merge_request_is_merged_for_others(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/theirs", (4, COLLEAGUE), (5, LOOKALIKE))  # a look-alike is not you
    repo.gitlab_mr_merge("feature/theirs", number=2, title="Theirs", at=at(6))
    ship(repo, tmp_path)
    [d] = run(repo.path).repos[0].deliveries
    assert (d.role, d.commits, d.yours) == (MERGER, 2, 0)


def test_colleagues_merging_their_own_work_is_not_listed(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/theirs", (4, COLLEAGUE))
    repo.github_pr_merge("feature/theirs", number=3, title="Theirs", at=at(6), who=LOOKALIKE)
    ship(repo, tmp_path)
    assert run(repo.path).repos[0].deliveries == ()


def test_a_shared_merge_request_counts_your_share(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/pair", (4, COLLEAGUE), (5, ME), (6, COLLEAGUE))
    repo.gitlab_mr_merge("feature/pair", number=4, title="Pair", at=at(7), who=COLLEAGUE)
    ship(repo, tmp_path)
    [d] = run(repo.path).repos[0].deliveries
    assert (d.role, d.commits, d.yours) == (AUTHOR, 3, 1)


def test_a_delivery_keeps_which_of_its_commits_are_yours(make_repo, tmp_path) -> None:
    """The digest lists a request's commits under it — by SHA, exactly the `yours` it
    counts, a commit written before the period too; none for a request merged for others."""
    repo = started(make_repo)
    repo.branch("feature/pair", "main")
    repo.switch("feature/pair")
    early = repo.commit("feat: begun in February", at="2026-02-10T09:00:00+01:00",
                        files={"p.txt": "x"})  # fmt: skip
    repo.commit("feat: theirs", at=at(4), who=COLLEAGUE, files={"q.txt": "x"})
    late = repo.commit("feat: mine", at=at(5), who=ME_GITHUB, files={"r.txt": "x"})
    repo.switch("main")
    repo.gitlab_mr_merge("feature/pair", number=4, title="Pair", at=at(7), who=COLLEAGUE)
    branch(repo, "feature/theirs", (8, COLLEAGUE))
    repo.gitlab_mr_merge("feature/theirs", number=5, title="Theirs", at=at(9))
    ship(repo, tmp_path)
    theirs, pair = run(repo.path).repos[0].deliveries
    assert (pair.yours, pair.your_commits) == (2, frozenset({early, late}))
    assert (theirs.role, theirs.your_commits) == (MERGER, frozenset())


def test_sync_merges_are_ignored(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/x", (4, ME))
    repo.commit("fix: a colleague's on main", at=at(5), who=COLLEAGUE, files={"main.txt": "x"})
    repo.switch("feature/x")
    repo.merge("main", message_text="Merge branch 'main' into feature/x\n", at=at(6))
    repo.switch("main")
    repo.gitlab_mr_merge("feature/x", number=5, title="X", at=at(7), who=COLLEAGUE)
    ship(repo, tmp_path)
    result = run(repo.path)
    [d] = result.repos[0].deliveries
    # Only your commit: neither the sync merge nor the colleague's commit it brought in counts.
    assert (d.number, d.role, d.commits, d.yours) == (5, AUTHOR, 1, 1)
    assert (result.merges_left_out, result.merges_sync, result.merges_delivered) == (1, 1, 0)


def test_a_pull_onto_the_mainline_is_a_sync_merge_not_a_delivery(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "upstream", (4, COLLEAGUE))
    repo.commit("feat: mine on main", at=at(5), files={"mine.txt": "x"})
    repo.merge("upstream", message_text="Merge branch 'main' of github.com:acme/app\n", at=at(6))
    ship(repo, tmp_path)
    result = run(repo.path)
    assert result.repos[0].deliveries == ()
    assert (result.merges_left_out, result.merges_sync) == (1, 1)


def test_stacked_branches_each_deliver_only_their_own_commits(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/a", (4, ME))
    branch(repo, "feature/b", (5, ME), start="feature/a")  # stacked on a, not on main
    repo.gitlab_mr_merge("feature/a", number=6, title="A", at=at(6))
    repo.gitlab_mr_merge("feature/b", number=7, title="B", at=at(7))
    ship(repo, tmp_path)
    deliveries = run(repo.path).repos[0].deliveries
    assert [(d.number, d.commits) for d in deliveries] == [(7, 1), (6, 1)]  # newest first


def test_commits_written_before_the_period_still_say_whose_it_is(make_repo, tmp_path) -> None:
    # The branch is older than anything pass 1 reads (two days before the period): the graph is
    # read from git, not from pass 1, so the merge request is still yours.
    repo = started(make_repo)
    repo.branch("feature/long", "main")
    repo.switch("feature/long")
    repo.commit("feat: begun in February", at="2026-02-10T09:00:00+01:00", files={"l.txt": "x"})
    repo.switch("main")
    repo.gitlab_mr_merge("feature/long", number=8, title="Long", at=at(6), who=COLLEAGUE)
    ship(repo, tmp_path)
    [d] = run(repo.path).repos[0].deliveries
    assert (d.role, d.yours) == (AUTHOR, 1)


def test_merges_outside_the_period_are_not_listed(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/early", (4, ME))
    repo.gitlab_mr_merge("feature/early", number=9, title="Early", at="2026-03-31T10:00:00+02:00")
    branch(repo, "feature/late", (20, ME))
    repo.gitlab_mr_merge("feature/late", number=10, title="Late", at=at(2, month=7))
    ship(repo, tmp_path)
    assert run(repo.path).repos[0].deliveries == ()


def test_work_on_a_mainline_without_a_request_merge_is_counted_for_caveats(make_repo, tmp_path):
    repo = started(make_repo)
    repo.commit("fix: pushed straight to main", at=at(3), files={"direct.txt": "x"})
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=11, title="X", at=at(6))
    branch(repo, "feature/later", (20, ME))  # written in the period, merged after it
    repo.gitlab_mr_merge("feature/later", number=12, title="Later", at=at(2, month=7))
    ship(repo, tmp_path)
    result = run(repo.path)
    assert [d.number for d in result.repos[0].deliveries] == [11]
    # Only the direct commit came with no request merge; the late one has its (later) merge.
    assert result.landed_without_request == 1


def test_a_squash_merge_is_its_own_request_not_work_that_landed_without_one(make_repo, tmp_path):
    # GitHub's squash merge is the pull request's own commit on the mainline; only the push
    # straight to main came with no request.
    repo = started(make_repo)
    repo.commit("fix: pushed straight to main", at=at(3), files={"direct.txt": "x"})
    branch(repo, "fix/typo", (4, ME))
    repo.github_squash("fix/typo", number=12, title="Fix a typo", at=at(6))
    repo.commit("fix: also straight, ending in an issue (#12)", at=at(7), files={"i.txt": "x"})
    ship(repo, tmp_path)
    assert run(repo.path).landed_without_request == 2


def test_one_git_call_reads_every_merged_commit(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    for n in range(3):
        branch(repo, f"feature/{n}", (4 + n, ME))
        repo.gitlab_mr_merge(f"feature/{n}", number=20 + n, title=f"N{n}", at=at(10 + n))
    ship(repo, tmp_path)
    quiet = started(make_repo, "quiet")
    quiet.commit("feat: no merges here", at=at(4), files={"q.txt": "x"})
    ship(quiet, tmp_path)
    git = RecordingGit()
    result = run(repo.path, quiet.path, git=git)
    assert len(result.repos[0].deliveries) == 3
    graph_calls = [c for c in git.calls if f"--format={GRAPH_FORMAT}" in c]
    assert len(graph_calls) == 1  # one for the repository with merges, none for the quiet one
    assert not any(a.startswith(("--since", "--until", "--author")) for a in graph_calls[0])


def test_a_delivery_in_two_clones_is_listed_once(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=13, title="X", at=at(6))
    ship(repo, tmp_path)
    copy = repo.clone(tmp_path / "app-copy")
    result = run(repo.path, copy.path)
    assert [len(rc.deliveries) for rc in result.repos] == [1, 0]
    assert result.deliveries_in_other_clones == 1


class GraphFails(GitRunner):
    def run(self, cwd, *args, stdin=None):
        if f"--format={GRAPH_FORMAT}" in args:
            raise GitError(args, 128, "fatal: bad object 0123abcd")
        return super().run(cwd, *args, stdin=stdin)


def test_a_graph_git_cannot_read_is_named_and_the_commits_still_count(make_repo, tmp_path):
    repo = started(make_repo)
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=14, title="X", at=at(6))
    branch(repo, "feature/y", (7, ME))
    repo.commit("fix: on main", at=at(7), who=COLLEAGUE, files={"main.txt": "x"})
    repo.switch("feature/y")
    repo.merge("main", message_text="Merge branch 'main' into feature/y\n", at=at(8))  # sync
    repo.switch("main")
    repo.merge("feature/y", message_text="Merge branch 'feature/y'\n", at=at(9))  # by hand
    ship(repo, tmp_path)
    result = run(repo.path, git=GraphFails())
    [rc] = result.repos
    assert rc.deliveries == () and rc.delivery_error == "bad object 0123abcd"
    assert len(rc.commits) == 2
    # Only the request could have been a delivery; the sync and the hand merge are still known.
    assert (result.merges_left_out, result.merges_unread, result.merges_sync) == (3, 1, 1)


def test_your_merges_are_told_apart(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/a", (4, COLLEAGUE))
    repo.gitlab_mr_merge("feature/a", number=15, title="A", at=at(6))  # merged for others
    branch(repo, "release/1", (5, COLLEAGUE))
    branch(repo, "feature/b", (7, COLLEAGUE), start="release/1")
    repo.switch("release/1")
    repo.gitlab_mr_merge("feature/b", number=16, title="B", at=at(8), into="release/1")
    branch(repo, "hotfix/main", (11, COLLEAGUE), start="release/1")  # "main" ends its name only
    repo.commit("fix: on main", at=at(11), who=COLLEAGUE, files={"main.txt": "x"})
    repo.switch("release/1")
    repo.gitlab_mr_merge("hotfix/main", number=17, title="H", at=at(12), into="release/1")
    repo.gitlab_mr_merge("main", number=18, title="Back", at=at(13), into="release/1")  # sync
    repo.switch("main")
    branch(repo, "feature/c", (9, ME))
    repo.merge("feature/c", message_text="Merge branch 'feature/c'\n", at=at(10))  # by hand
    ship(repo, tmp_path)
    result = run(repo.path)
    assert [d.number for d in result.repos[0].deliveries] == [15]
    assert result.merges_left_out == 5
    assert (result.merges_delivered, result.merges_elsewhere, result.merges_sync) == (1, 2, 1)


def test_tickets_come_from_the_branch_the_title_and_the_message(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/PROJ-1-x", (4, ME))
    repo.gitlab_mr_merge("feature/PROJ-1-x", number=17, title="PROJ-2 title", at=at(6),
                         description="Closes PROJ-3")  # fmt: skip
    ship(repo, tmp_path)
    seen: list[str] = []

    def tickets_of(text: str) -> set[str]:
        seen.append(text)
        return set(re.findall(r"PROJ-[0-9]+", text))

    [d] = run(repo.path, tickets_of=tickets_of).repos[0].deliveries
    assert seen[:2] == ["feature/PROJ-1-x", "PROJ-2 title"] and "Closes PROJ-3" in seen[2]
    assert d.tickets == {"PROJ-1", "PROJ-2", "PROJ-3"}
    assert run(repo.path).repos[0].deliveries[0].tickets == frozenset()  # default: none


def test_each_default_branch_is_traced_from_its_own_base(make_repo, tmp_path) -> None:
    # Two remotes' default branches; a merge dated before the period (a skewed clock) brought
    # upstream's in-period merge into origin's main below origin's oldest in-period merge. Each
    # default branch is read down to its own base, so upstream's merge is still traced.
    repo = started(make_repo)
    repo.branch("upstream-main", "main")
    branch(repo, "feature/x", (4, ME), start="upstream-main")
    repo.switch("upstream-main")
    repo.gitlab_mr_merge("feature/x", number=30, title="Upstream", at=at(5, month=4))
    repo.switch("main")
    repo.merge("upstream-main", message_text="Merge branch 'upstream-main'\n",
               at="2026-03-25T10:00:00+01:00")  # fmt: skip
    branch(repo, "feature/y", (9, ME))
    repo.gitlab_mr_merge("feature/y", number=31, title="Origin", at=at(10))
    repo.publish(tmp_path / "remotes" / "origin.git", "main")
    repo.publish(tmp_path / "remotes" / "upstream.git", "upstream-main:main", remote="upstream")
    deliveries = run(repo.path).repos[0].deliveries
    assert [(d.number, d.commits, d.yours) for d in deliveries] == [(31, 1, 1), (30, 1, 1)]


def test_a_pull_request_merged_upstream_is_yours_although_the_fork_merged_it_first(
    make_repo, tmp_path
) -> None:
    # origin is a company fork that merged your fix in March and keeps merging its own work;
    # upstream merged the same branch as your pull request #12 in May. The fork's base reaches
    # your fix, upstream's does not: what #12 merged is upstream's merge^1..merge^2, your fix.
    repo = started(make_repo)
    repo.branch("upstream-main", "main")
    repo.branch("feature/fix", "main")
    repo.switch("feature/fix")
    repo.commit("fix: the bug", at="2026-03-05T10:00:00+01:00", files={"fix.txt": "x"})
    repo.switch("main")
    repo.gitlab_mr_merge("feature/fix", number=3, title="Fix (fork)", who=COLLEAGUE,
                         at="2026-03-10T10:00:00+01:00", project="company/app")  # fmt: skip
    branch(repo, "feature/co", (8, COLLEAGUE))
    repo.gitlab_mr_merge("feature/co", number=4, title="Co", who=COLLEAGUE, at=at(10, 4),
                         project="company/app")  # fmt: skip
    repo.switch("upstream-main")
    repo.commit("feat: upstream work", at=at(2, 4), who=COLLEAGUE, files={"up.txt": "x"})
    repo.github_pr_merge("feature/fix", number=12, title="Fix the bug", at=at(15), who=COLLEAGUE)
    repo.switch("main")
    repo.publish(tmp_path / "remotes" / "origin.git", "main")
    repo.publish(tmp_path / "remotes" / "upstream.git", "upstream-main:main", remote="upstream")
    git = RecordingGit()
    [d] = run(repo.path, git=git).repos[0].deliveries
    assert (d.ref, d.role, d.commits, d.yours) == ("#12", AUTHOR, 1, 1)
    assert len([c for c in git.calls if f"--format={GRAPH_FORMAT}" in c]) == 2  # two bases


def test_a_lagging_copy_of_main_shares_its_base_and_its_graph_read(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "fix/a", (4, ME))
    repo.github_pr_merge("fix/a", number=1, title="A", at=at(6))
    repo.publish(tmp_path / "remotes" / "copy.git", "main")  # origin: a copy of main, behind
    branch(repo, "fix/b", (8, ME), (9, COLLEAGUE))
    repo.github_pr_merge("fix/b", number=2, title="B", at=at(10), who=COLLEAGUE)
    repo.publish(tmp_path / "remotes" / "github.git", "--all", remote="github")
    git = RecordingGit()
    result = run(repo.path, git=git)
    assert [(d.ref, d.commits, d.yours) for d in result.repos[0].deliveries] == [
        ("#2", 2, 1),
        ("#1", 1, 1),
    ]
    assert len([c for c in git.calls if f"--format={GRAPH_FORMAT}" in c]) == 1


def test_work_delivered_in_one_clone_is_not_counted_as_unrequested_in_another(
    make_repo, tmp_path
) -> None:
    # Two clones: upstream, where a maintainer merged your pull request, and a fork that pulled
    # upstream in (a sync merge, no request). Your commit is listed once — in the fork, first on
    # the tie — but the request that delivered it lives in upstream's clone.
    up = started(make_repo, "up")
    up.publish(tmp_path / "remotes" / "up.git", "main")
    fork = up.clone(tmp_path / "fork")
    run_git(tmp_path / "remotes", "init", "-q", "--bare", "-b", "main", "fork.git")
    fork.git("remote", "set-url", "origin", str(tmp_path / "remotes" / "fork.git"))
    fork.git("push", "-q", "origin", "main")
    branch(up, "fix", (4, ME))
    up.github_pr_merge("fix", number=12, title="Fix", at=at(6), who=COLLEAGUE)
    up.git("push", "-q", "origin", "main")
    up.git("fetch", "-q", "origin")
    fork.commit("feat: fork only", at=at(5), who=COLLEAGUE, files={"f.txt": "x"})
    fork.git("fetch", "-q", str(tmp_path / "remotes" / "up.git"), "main")
    fork.merge("FETCH_HEAD", message_text="Merge branch 'main' of up\n", at=at(7), who=COLLEAGUE)
    fork.git("push", "-q", "origin", "main")
    fork.git("fetch", "-q", "origin")
    result = run(fork.path, up.path)
    assert [d.ref for rc in result.repos for d in rc.deliveries] == ["#12"]
    assert result.landed_without_request == 0


def gitflow(make_repo, tmp_path: Path) -> RepoBuilder:
    """develop is the default branch, main is production: your hotfix merge request !20 into
    main (merged by a colleague), then a colleague's back-merge request !21, main into develop."""
    repo = started(make_repo)
    repo.branch("develop", "main")
    branch(repo, "hotfix/x", (4, ME))
    repo.gitlab_mr_merge("hotfix/x", number=20, title="Hotfix", at=at(5), who=COLLEAGUE)
    repo.switch("develop")
    repo.commit("feat: dev work", at=at(3), who=COLLEAGUE, files={"dev.txt": "x"})
    repo.gitlab_mr_merge("main", number=21, title="Back-merge main", at=at(6), who=COLLEAGUE,
                         into="develop")  # fmt: skip
    repo.switch("main")
    ship(repo, tmp_path)
    repo.git("remote", "set-head", "origin", "develop")
    return repo


def test_a_back_merge_request_is_never_a_delivery(make_repo, tmp_path) -> None:
    [rc] = run(gitflow(make_repo, tmp_path).path).repos
    assert rc.mainline_names == ("develop",)
    assert 21 not in [d.number for d in rc.deliveries]


def test_a_request_into_another_long_lived_branch_counts_once_it_reaches_the_mainline(
    make_repo, tmp_path
) -> None:
    [rc] = run(gitflow(make_repo, tmp_path).path).repos
    [d] = rc.deliveries  # !20 reached develop through the back-merge's second parent
    assert (d.number, d.role, d.commits, d.yours) == (20, AUTHOR, 1, 1)


def test_a_request_from_another_projects_main_into_main_is_a_delivery(make_repo, tmp_path):
    repo = started(make_repo)
    branch(repo, "their-main", (4, ME))  # a fork's main, merged into this project's main
    text = (
        "Merge branch 'main' into 'main'\n\nFrom the fork\n\n"
        "See merge request acme/platform/app!22\n"
    )
    repo.merge("their-main", message_text=text, at=at(6), who=COLLEAGUE)
    ship(repo, tmp_path)
    [d] = run(repo.path).repos[0].deliveries
    assert (d.number, d.role, d.branch, d.yours) == (22, AUTHOR, "main", 1)


def test_a_request_into_a_feature_branch_is_delivered_by_the_outer_request(make_repo, tmp_path):
    repo = started(make_repo)
    branch(repo, "feature/a", (4, ME))
    branch(repo, "feature/b", (5, ME), start="feature/a")
    repo.switch("feature/a")
    repo.gitlab_mr_merge("feature/b", number=40, title="Inner", at=at(6), into="feature/a")
    repo.switch("main")
    repo.gitlab_mr_merge("feature/a", number=41, title="Outer", at=at(7))
    ship(repo, tmp_path)
    deliveries = run(repo.path).repos[0].deliveries
    assert [(d.number, d.commits) for d in deliveries] == [(41, 2)]
