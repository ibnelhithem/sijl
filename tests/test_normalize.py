"""sijl.normalize: rebased, cherry-picked and amended copies of one commit count once —
the best-reached copy is kept and the others are listed beside it; unrelated work never merges."""

from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl.cli import app
from sijl.collect import collect
from sijl.discover import discover
from sijl.git import GitError, GitRunner
from sijl.identity import IdentitySet
from sijl.normalize import collapse, copy_key, partial_clone
from sijl.report import build_report, caveats, render_markdown
from tests.gitfactory import (
    ME,
    ME_GITHUB,
    PERIOD_ARGS,
    REVIEW_PERIOD,
    RepoBuilder,
    raw_commit,
    run_git,
)

MINE = IdentitySet.from_emails([ME.email])
runner = CliRunner()


def at(day: int) -> str:
    return f"2026-05-{day:02d}T10:00:00+02:00"


class FailingOn(GitRunner):
    """git as it behaves on a corrupt or unreachable object: one command always fails."""

    def __init__(self, *command: str) -> None:
        super().__init__()
        self._command = command

    def run(self, cwd: Path, *args: str, stdin: bytes | None = None) -> bytes:
        if args[: len(self._command)] == self._command:
            raise GitError(args, 128, "fatal: simulated failure")
        return super().run(cwd, *args, stdin=stdin)


def run(*paths: Path, dedupe: str = "key", git: GitRunner | None = None, identities=MINE):
    git = git or GitRunner()
    repos = discover([], list(paths), git).repos
    return collapse(collect(repos, identities, REVIEW_PERIOD, git), git, dedupe=dedupe)


def build(*paths: Path, dedupe: str = "key", git: GitRunner | None = None):
    return build_report(
        roots=[],
        paths=list(paths),
        identities=MINE,
        window=REVIEW_PERIOD,
        git=git or GitRunner(),
        generated_on=date(2026, 10, 8),
        dedupe=dedupe,
    )


def listed(result) -> list[str]:
    """Every commit still listed — counted or not pushed yet — by subject."""
    return sorted(c.subject for c in (*result.commits, *result.local_only))


def cherry_picked(make_repo, tmp_path: Path) -> tuple[RepoBuilder, str, str]:
    """A fix made on a local topic branch, cherry-picked onto main, and main pushed."""
    repo = make_repo("app")
    repo.commit("chore: start", at=at(4), files={"main.txt": "1\n"})
    repo.branch("topic")
    repo.switch("topic")
    original = repo.commit("fix: the bug", at=at(5), files={"fix.txt": "fixed\n"})
    repo.switch("main")
    picked = repo.cherry_pick(original, at=at(6))
    repo.publish(tmp_path / "remotes" / "app.git", "main")
    return repo, original, picked


def test_a_rebased_branch_whose_old_copy_survives_on_a_remote_counts_once(
    make_repo, tmp_path
) -> None:
    remote = tmp_path / "remotes" / "app.git"
    repo = make_repo("app")
    repo.commit("chore: start", at=at(4), files={"main.txt": "1\n"})
    repo.publish(remote, "main")
    repo.branch("feature")
    repo.switch("feature")
    old = repo.commit("feat: the feature", at=at(5), files={"feature.txt": "f\n"})
    repo.publish(remote, "feature")
    repo.switch("main")
    repo.commit("fix: on main meanwhile", at=at(6), files={"main.txt": "2\n"})
    repo.publish(remote, "main")
    repo.switch("feature")
    new = repo.rebase("main", at=at(7))
    assert new != old

    result = run(repo.path)
    assert listed(result) == ["chore: start", "feat: the feature", "fix: on main meanwhile"]
    rc = result.repos[0]
    assert [c.sha for c in rc.commits if c.subject == "feat: the feature"] == [old]  # pushed
    assert rc.copies == {old: (new,)}
    assert result.local_only == []  # the rebased copy is not unpushed work
    assert result.copies_collapsed == 1


def test_a_cherry_pick_counts_once_and_both_shas_are_listed(make_repo, tmp_path) -> None:
    repo, original, picked = cherry_picked(make_repo, tmp_path)
    report = build(repo.path)
    assert report.total == 2
    text = render_markdown(report)
    row = next(line for line in text.splitlines() if "fix: the bug" in line)
    assert picked[:8] in row and original[:8] in row  # the mainline copy first
    assert row.index(picked[:8]) < row.index(original[:8])
    assert "## Not pushed yet" not in text  # the topic-branch copy is not unpushed work


def test_different_subjects_at_the_same_time_stay_separate(make_repo) -> None:
    repo = make_repo("app")
    repo.commit("feat: one", at=at(5))
    repo.commit("feat: two", at=at(5))
    result = run(repo.path)
    assert listed(result) == ["feat: one", "feat: two"]
    assert result.copies_collapsed == 0


def test_caveats_count_the_collapsed_copies(make_repo, tmp_path) -> None:
    repo, _, _ = cherry_picked(make_repo, tmp_path)
    lines = caveats(build(repo.path))
    assert any(line.startswith("1 rebased or cherry-picked copy ") for line in lines), lines
    lonely = make_repo("lonely")
    lonely.commit("feat: alone", at=at(5))
    lines = caveats(build(lonely.path))
    assert any(line.startswith("No rebased or cherry-picked copies") for line in lines), lines


def test_the_copy_key_ignores_spacing_and_the_offset_but_not_the_words() -> None:
    a = raw_commit(subject="feat:  add   login ", at="2026-05-04T10:00:00+02:00")
    b = raw_commit(subject="feat: add login", at="2026-05-04T08:00:00+00:00")  # same instant
    c = raw_commit(subject="feat: add Login", at="2026-05-04T10:00:00+02:00")
    d = raw_commit(subject="feat: add login", at="2026-05-04T10:00:01+02:00")
    assert copy_key(a) == copy_key(b)
    assert copy_key(a) != copy_key(c)
    assert copy_key(a) != copy_key(d)


def test_copies_in_two_clones_of_one_project_count_once(make_repo, tmp_path) -> None:
    original = make_repo("app")
    original.commit("chore: start", at=at(3))
    first = original.commit("feat: shared", at=at(4))
    clone = original.clone(tmp_path / "app-copy")
    clone.git("remote", "remove", "origin")  # the clone alone holds its copy
    amended = clone.amend(at=at(6))
    assert amended != first

    result = run(original.path, clone.path)
    assert listed(result) == ["chore: start", "feat: shared"]
    assert result.copies_collapsed == 1
    kept = next(rc for rc in result.repos if rc.copies)
    assert kept.copies == {first: (amended,)}  # tie on reach: the earliest committed is kept


def test_the_same_words_at_the_same_time_in_unrelated_repositories_stay_apart(make_repo) -> None:
    api = make_repo("api")
    api.commit("chore: api starts", at=at(3))
    api.commit("ci: pin actions", at=at(5), files={"ci.yml": "api\n"})
    web = make_repo("web")
    web.commit("chore: web starts", at=at(3))
    web.commit("ci: pin actions", at=at(5), files={"ci.yml": "web\n"})
    result = run(api.path, web.path)
    assert len(result.commits) == 4
    assert result.copies_collapsed == 0


def test_patch_id_keeps_same_time_and_subject_apart_when_the_changes_differ(make_repo) -> None:
    repo = make_repo("app")
    repo.commit("chore: start", at=at(4), files={"a.txt": "1\n"})
    repo.branch("other")
    repo.commit("feat: same words", at=at(5), files={"a.txt": "2\n"})
    repo.switch("other")
    repo.commit("feat: same words", at=at(5), files={"b.txt": "different\n"})
    assert len(run(repo.path).commits) == 2
    split = run(repo.path, dedupe="patch-id")
    assert len(split.commits) == 3
    assert split.patch_splits == 1
    lines = caveats(build(repo.path, dedupe="patch-id"))
    assert any("kept as separate work" in line for line in lines), lines


def test_patch_id_still_collapses_a_true_cherry_pick(make_repo, tmp_path) -> None:
    repo, _, _ = cherry_picked(make_repo, tmp_path)
    result = run(repo.path, dedupe="patch-id")
    assert result.copies_collapsed == 1
    assert result.patch_splits == 0


def test_the_dedupe_flag(make_repo, tmp_path) -> None:
    repo, _, _ = cherry_picked(make_repo, tmp_path)
    args = ["report", "--repo", str(repo.path), "--author-email", ME.email, *PERIOD_ARGS]
    result = runner.invoke(app, [*args, "--dedupe", "patch-id"])
    assert result.exit_code == 0, result.output
    assert "fix: the bug" in result.stdout
    wrong = runner.invoke(app, [*args, "--dedupe", "sha"])
    assert wrong.exit_code == 2
    assert "scan.dedupe" in wrong.output


def two_clones(make_repo, tmp_path: Path) -> tuple[RepoBuilder, RepoBuilder]:
    """One project in two clones, each holding its own copy of "feat: shared"."""
    original = make_repo("app")
    original.commit("chore: start", at=at(3))
    original.commit("feat: shared", at=at(4))
    clone = original.clone(tmp_path / "app-copy")
    clone.git("remote", "remove", "origin")
    clone.amend(at=at(6))
    return original, clone


def test_collapsing_never_moves_counted_work_out_of_the_totals(make_repo, tmp_path) -> None:
    lonely = make_repo("app")  # no remote: its commits are counted
    lonely.commit("chore: start", at=at(3))
    lonely.commit("feat: shared", at=at(5))
    other = lonely.clone(tmp_path / "app-copy")
    other.git("remote", "remove", "origin")
    other.publish(tmp_path / "remotes" / "app.git", "HEAD~1:refs/heads/main")  # without the feature
    other.amend(at=at(4))  # its copy is on no remote branch, and was committed earlier
    result = run(lonely.path, other.path)
    assert sorted(c.subject for c in result.commits) == ["chore: start", "feat: shared"]
    assert result.local_only == []
    assert result.copies_collapsed == 1


def test_a_mailmap_in_one_clone_does_not_stop_copies_matching(make_repo, tmp_path) -> None:
    original, clone = two_clones(make_repo, tmp_path)
    (clone.path / ".mailmap").write_text(f"{ME_GITHUB.name} <{ME_GITHUB.email}> <{ME.email}>\n")
    both = IdentitySet.from_emails([ME.email, ME_GITHUB.email])
    result = run(original.path, clone.path, identities=both)
    assert result.copies_collapsed == 1


def test_patch_id_reads_a_binary_change_the_same_in_every_clone(make_repo, tmp_path) -> None:
    original = make_repo("app")
    original.commit("chore: start", at=at(3))
    original.commit("feat: new logo", at=at(4), files={"logo.png": b"\x89PNG\r\n\x1a\n\x00\x01"})
    clone = original.clone(tmp_path / "app-copy")
    clone.git("remote", "remove", "origin")
    clone.git("config", "core.abbrev", "20")  # this clone abbreviates object ids differently
    clone.amend(at=at(6))
    result = run(original.path, clone.path, dedupe="patch-id")
    assert result.copies_collapsed == 1
    assert result.patch_splits == 0


def test_patch_id_never_fetches_into_a_partial_clone(make_repo, tmp_path) -> None:
    origin = make_repo("app")
    origin.commit("chore: start", at=at(3), files={"a.txt": "1\n"})
    origin.commit("feat: shared", at=at(4), files={"a.txt": "2\n"})
    origin.git("config", "uploadpack.allowfilter", "true")
    partial = tmp_path / "partial"
    run_git(tmp_path, "clone", "-q", "--filter=blob:none", f"file://{origin.path}", str(partial))
    RepoBuilder.existing(partial).amend(at=at(6))  # a local copy of the pushed commit
    objects = sorted(str(p) for p in (partial / ".git" / "objects").rglob("*"))
    result = run(partial, dedupe="patch-id")
    assert sorted(str(p) for p in (partial / ".git" / "objects").rglob("*")) == objects
    assert result.copies_collapsed == 1  # matched by the key alone
    assert result.patch_unread == 2


def test_a_patch_id_git_cannot_read_falls_back_to_the_key_and_says_so(make_repo, tmp_path):
    repo, _, _ = cherry_picked(make_repo, tmp_path)
    failing = FailingOn("patch-id")
    result = run(repo.path, dedupe="patch-id", git=failing)
    assert (result.copies_collapsed, result.patch_splits, result.patch_unread) == (1, 0, 2)
    lines = caveats(build(repo.path, dedupe="patch-id", git=failing))
    assert any("author, author time and subject alone" in line for line in lines), lines
    assert not any("the changes differ" in line for line in lines), lines


def test_root_commits_git_cannot_read_are_disclosed(make_repo, tmp_path) -> None:
    original, clone = two_clones(make_repo, tmp_path)
    failing = FailingOn("rev-list", "--max-parents=0")
    result = run(original.path, clone.path, git=failing)
    assert (result.copies_collapsed, result.roots_unread) == (0, 2)
    lines = caveats(build(original.path, clone.path, git=failing))
    assert any(
        line.startswith("Root commits could not be read in 2 repositories") for line in lines
    )


@pytest.mark.parametrize("value", ["true", "yes", "on", "1", "TRUE", None])
def test_a_partial_clone_is_recognised_as_git_recognises_it(make_repo, value) -> None:
    repo = make_repo("app")
    repo.commit("feat: something", at=at(4))
    config = repo.path / ".git" / "config"
    line = "\tpromisor\n" if value is None else f"\tpromisor = {value}\n"  # a bare key is true
    config.write_text(config.read_text() + '[remote "origin"]\n' + line)
    assert partial_clone(GitRunner(), repo.path)


def test_a_full_clone_is_not_partial(make_repo, tmp_path) -> None:
    original = make_repo("app")
    original.commit("feat: something", at=at(4))
    clone = original.clone(tmp_path / "copy")
    assert not partial_clone(GitRunner(), clone.path)
