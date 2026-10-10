"""sijl.collect: exactly your commits, in the period by author date, from every branch and
remote-tracking branch — and never from the stash or a detached scratch worktree."""

from dataclasses import replace
from datetime import date
from pathlib import Path

from sijl.collect import LOOKBACK, collect
from sijl.discover import discover
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.window import Window
from tests.gitfactory import COLLEAGUE, LOOKALIKE, ME, ME_ALT_NAME, REVIEW_PERIOD

Q2 = REVIEW_PERIOD
MINE = IdentitySet.from_emails([ME.email])


class RecordingGit(GitRunner):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, ...]] = []

    def run(self, cwd, *args, stdin=None):
        self.calls.append(args)
        return super().run(cwd, *args, stdin=stdin)


def collect_under(root: Path, *, git: GitRunner | None = None, window: Window = Q2):
    git = git or GitRunner()
    return collect(discover([root], [], git).repos, MINE, window, git)


def subjects(result) -> set[str]:
    """Every commit collected as yours: counted, or kept apart as not pushed."""
    return {c.subject for c in (*result.commits, *result.local_only)}


def test_the_author_date_decides_not_the_committer_date(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: written in June, rebased in July", at="2026-06-20T10:00:00+02:00",
                committed_at="2026-07-15T09:00:00+02:00")  # fmt: skip
    repo.commit("fix: written in March, rebased in April", at="2026-03-20T10:00:00+01:00",
                committed_at="2026-04-10T09:00:00+02:00")  # fmt: skip
    assert subjects(collect_under(tmp_path)) == {"feat: written in June, rebased in July"}


def test_git_is_never_asked_to_filter_by_until_or_author(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: a", at="2026-05-04T10:00:00+02:00")
    git = RecordingGit()
    collect_under(tmp_path, git=git)
    log_calls = [call for call in git.calls if call[0] == "log"]
    since = replace(Q2, start=Q2.start - LOOKBACK).git_since()  # the lookback before it
    assert log_calls and all(arg == f"--since={since}" or not arg.startswith("--since")
                             for call in log_calls for arg in call)  # fmt: skip
    assert not any(a.startswith(("--until", "--before", "--author")) for c in log_calls for a in c)


def test_a_window_reaching_before_1970_reads_all_history(make_repo, tmp_path) -> None:
    # git 2.43 misreads a --since before the Unix epoch (1969-12-30 becomes 2012-12-29), so
    # git gets no --since at all and the author-date filter alone decides.
    repo = make_repo("app")
    repo.commit("feat: in 1971", at="1971-05-04T10:00:00+01:00")
    repo.commit("feat: in 2026", at="2026-05-04T10:00:00+02:00")
    git = RecordingGit()
    found = collect_under(tmp_path, git=git, window=Window(date(1926, 10, 9), date(2026, 10, 8)))
    assert subjects(found) == {"feat: in 1971", "feat: in 2026"}
    log_calls = [call for call in git.calls if call[0] == "log"]
    assert log_calls and not any(a.startswith("--since") for c in log_calls for a in c)


def test_only_your_exact_email_counts(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: mine", at="2026-05-04T10:00:00+02:00")
    repo.commit("feat: mine under another name", at="2026-05-05T10:00:00+02:00", who=ME_ALT_NAME)
    repo.commit("feat: a look-alike's", at="2026-05-06T10:00:00+02:00", who=LOOKALIKE)
    repo.commit("feat: a colleague's", at="2026-05-07T10:00:00+02:00", who=COLLEAGUE)
    repo.commit("docs: mine, committed by a colleague", at="2026-05-08T10:00:00+02:00",
                committer=COLLEAGUE)  # fmt: skip
    assert subjects(collect_under(tmp_path)) == {
        "feat: mine",
        "feat: mine under another name",
        "docs: mine, committed by a colleague",
    }


def test_remote_only_branches_count_and_the_stash_never_does(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: on main", at="2026-05-04T10:00:00+02:00")
    repo.branch("feature/pushed")
    repo.switch("feature/pushed")
    repo.commit("feat: only on the remote now", at="2026-05-05T10:00:00+02:00")
    repo.publish(tmp_path / "remotes" / "app.git", "feature/pushed")
    repo.switch("main")
    repo.git("branch", "-D", "feature/pushed")  # the local branch is gone; origin/... remains
    repo.stash("WIP on main: half done", files={"history.txt": "changed\n"},
               at="2026-05-06T10:00:00+02:00")  # fmt: skip
    result = collect(discover([], [repo.path], GitRunner()).repos, MINE, Q2, GitRunner())
    assert subjects(result) == {"feat: on main", "feat: only on the remote now"}


def test_detached_worktree_commits_are_left_out(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: on main", at="2026-05-04T10:00:00+02:00")
    scratch = repo.add_worktree(tmp_path / "scratch")
    scratch.commit("feat: experiment in a detached worktree", at="2026-05-05T10:00:00+02:00")
    assert subjects(collect_under(tmp_path)) == {"feat: on main"}


def test_merge_commits_are_counted_but_not_listed(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: base", at="2026-05-04T10:00:00+02:00")
    repo.branch("feature/x")
    repo.switch("feature/x")
    repo.commit("feat: on the branch", at="2026-05-05T10:00:00+02:00")
    repo.switch("main")
    repo.merge("feature/x", message_text="Merge branch 'feature/x' into 'main'\n",
               at="2026-05-06T10:00:00+02:00")  # fmt: skip
    result = collect_under(tmp_path)
    assert subjects(result) == {"feat: base", "feat: on the branch"}
    assert result.merges_left_out == 1


def test_a_commit_in_two_clones_counts_once(make_repo, tmp_path) -> None:
    original = make_repo("app")
    original.commit("feat: shared", at="2026-05-04T10:00:00+02:00")
    clone = original.clone(tmp_path / "app-copy")
    clone.commit("feat: only in the copy", at="2026-05-05T10:00:00+02:00")
    result = collect_under(tmp_path)
    listed = [c.subject for c in (*result.commits, *result.local_only)]
    assert sorted(listed) == ["feat: only in the copy", "feat: shared"]
    assert result.seen_in_other_clones == 1


def test_commits_are_newest_first_within_each_repository(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    for day in (4, 6, 5):
        repo.commit(f"feat: day {day}", at=f"2026-05-0{day}T10:00:00+02:00")
    [app] = collect_under(tmp_path).repos
    assert [c.subject for c in app.commits] == ["feat: day 6", "feat: day 5", "feat: day 4"]


def test_a_repository_with_no_branches_yet_is_simply_empty(make_repo, tmp_path) -> None:
    make_repo("empty")
    [empty] = collect_under(tmp_path).repos
    assert empty.commits == ()


class BrokenLog(GitRunner):
    """A git whose log for one repository is garbage, as if a record could not be parsed."""

    def run(self, cwd, *args, stdin=None):
        if args[0] == "log" and Path(cwd).name == "broken":
            return b"not\x1fa record\x00"
        return super().run(cwd, *args, stdin=stdin)


def test_a_repository_whose_history_cannot_be_parsed_is_skipped_not_fatal(make_repo, tmp_path):
    make_repo("broken").commit("feat: unreadable", at="2026-05-04T10:00:00+02:00")
    make_repo("fine").commit("feat: fine", at="2026-05-04T10:00:00+02:00")
    git = BrokenLog()
    result = collect(discover([tmp_path], [], git).repos, MINE, Q2, git)
    assert subjects(result) == {"feat: fine"}
    [skipped] = result.skipped
    assert skipped.path.name == "broken" and "could not be parsed" in skipped.reason


def test_every_author_and_co_author_read_is_kept_for_redaction(make_repo, tmp_path) -> None:
    """The digest's redactor must know colleagues' names to find them in free text — every
    author pass 1 read, anyone's, the lookback's too, as written and after .mailmap, and every
    co-author a trailer names. Kept apart from every count."""
    repo = make_repo("app")
    repo.commit("chore: early", at="2026-02-10T10:00:00+01:00", who=LOOKALIKE)  # the lookback
    repo.commit("feat: pair", at="2026-05-04T10:00:00+02:00",
                trailers=["Co-authored-by: Jane Pair <jane.pair@acme.test>",
                          "Co-authored-by: Kim Noaddress"])  # fmt: skip
    repo.commit("fix: theirs", at="2026-05-05T10:00:00+02:00", who=COLLEAGUE)
    (repo.path / ".mailmap").write_text(f"Sam C <{COLLEAGUE.email}>\n")
    repo.commit("docs: mailmap", at="2026-05-06T10:00:00+02:00")
    result = collect_under(tmp_path)
    people = {
        (LOOKALIKE.name, LOOKALIKE.email),
        (ME.name, ME.email),
        ("Jane Pair", "jane.pair@acme.test"),
        ("Kim Noaddress", ""),
        ("Sam C", COLLEAGUE.email),  # after .mailmap
        (COLLEAGUE.name, COLLEAGUE.email),  # as written
    }
    assert result.authors == tuple(sorted(people))  # sorted: the same on every run
    assert len(result.commits) == 2
