"""sijl.discover: what the walk searches. Hidden, dependency and build folders are pruned
and named; a folder that is itself a repository is never pruned; symbolic links are never
followed, so loops end; a folder that cannot be entered is a caveat, never a crash; a checked-out
submodule is a repository of its own; linked worktrees are listed with their repository."""

import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from sijl.discover import (
    DEPENDENCY_FOLDER,
    GIT_DIR_ELSEWHERE,
    HIDDEN_FOLDER,
    LINK_NOWHERE,
    LINK_TO_FOLDER,
    SUBMODULE_GIT_COMPONENT,
    SUBMODULE_NOT_CHECKED_OUT,
    SUBMODULE_OUTSIDE,
    discover,
)
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.report import build_report, caveats
from tests.filesystem import require_raw_byte_names
from tests.gitfactory import ME, REVIEW_PERIOD, RepoBuilder, run_git

AT = "2026-05-04T10:00:00+02:00"


def names(found) -> list[str]:
    return sorted(repo.name for repo in found.repos)


def test_folders_pruned_by_rule_are_named_not_dropped(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: a", at=AT)
    make_repo("node_modules/left-pad")  # an npm dependency installed from git
    make_repo(".venv/src/editable-pkg")  # `pip install -e git+…` clones into .venv/src
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True)  # Claude Code's worktrees and settings
    found = discover([tmp_path], [], GitRunner())
    assert names(found) == ["app"]
    assert [(s.path, s.reason) for s in found.pruned] == [
        (tmp_path / ".claude", HIDDEN_FOLDER),
        (tmp_path / ".venv", HIDDEN_FOLDER),
        (tmp_path / "node_modules", DEPENDENCY_FOLDER),
    ]
    assert found.skipped == ()  # pruned is its own list: these are rules, not failures


def test_a_repository_is_never_pruned_by_its_name(make_repo, tmp_path) -> None:
    make_repo("dist").commit("feat: a project that happens to be called dist", at=AT)
    make_repo(".dotfiles").commit("chore: a hidden repository of your own", at=AT)
    found = discover([tmp_path], [], GitRunner())
    assert names(found) == [".dotfiles", "dist"]
    assert found.pruned == ()


def test_pruned_folders_are_named_in_the_report_caveats(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: a", at=AT)
    (tmp_path / ".claude").mkdir()
    (tmp_path / "node_modules" / "left-pad").mkdir(parents=True)
    report = build_report(
        roots=[tmp_path],
        paths=[],
        identities=IdentitySet.from_emails([ME.email]),
        window=REVIEW_PERIOD,
        git=GitRunner(),
        generated_on=date(2026, 10, 6),
    )
    assert (
        f"2 folders were not searched, by rule — hidden, dependency and build folders hold "
        f"tools, not projects: `{tmp_path / '.claude'}`, `{tmp_path / 'node_modules'}`."
    ) in caveats(report)


def test_a_long_list_of_pruned_folders_is_capped_and_says_so(make_repo, tmp_path) -> None:
    make_repo("app").commit("feat: a", at=AT)
    for n in range(12):
        (tmp_path / f".tool-{n:02}").mkdir()
    report = build_report(
        roots=[tmp_path],
        paths=[],
        identities=IdentitySet.from_emails([ME.email]),
        window=REVIEW_PERIOD,
        git=GitRunner(),
        generated_on=date(2026, 10, 6),
    )
    [line] = [c for c in caveats(report) if "by rule" in c]
    assert line.startswith("12 folders were not searched, by rule")
    assert f"`{tmp_path / '.tool-09'}` and 2 more (`sijl repos` lists them all)." in line
    assert ".tool-10" not in line


def test_symbolic_links_are_never_followed_so_loops_end(make_repo, tmp_path) -> None:
    make_repo("group/app").commit("feat: a", at=AT)
    group = tmp_path / "group"
    (group / "up").symlink_to(tmp_path)  # a loop back to the root
    (group / "self").symlink_to(group / "self")  # a link to itself
    (group / "dangling").symlink_to(tmp_path / "nowhere")
    (tmp_path / "shortcut").symlink_to(group / "app")  # a second way to a repository
    (tmp_path / "notes.txt").write_text("not a folder\n")
    (tmp_path / "readme").symlink_to(tmp_path / "notes.txt")  # a link to a file is just a file
    found = discover([tmp_path], [], GitRunner(), max_depth=1000)  # the loop, not the depth, ends
    assert names(found) == ["app"]
    assert sorted((str(s.path), s.reason) for s in found.skipped) == [
        (str(group / "dangling"), LINK_NOWHERE),
        (str(group / "self"), LINK_NOWHERE),
        (str(group / "up"), LINK_TO_FOLDER),
        (str(tmp_path / "shortcut"), LINK_TO_FOLDER),
    ]


def test_a_root_the_user_names_is_followed_even_through_a_link(make_repo, tmp_path) -> None:
    make_repo("real/app").commit("feat: a", at=AT)
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    (tmp_path / "file.txt").write_text("x\n")
    found = discover(
        [tmp_path / "alias", tmp_path / "missing", tmp_path / "file.txt"], [], GitRunner()
    )
    assert names(found) == ["app"]
    assert [(s.path.name, s.reason) for s in found.skipped] == [
        ("file.txt", "not a folder"),
        ("missing", "does not exist"),
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can enter any folder")
def test_a_folder_that_can_be_listed_but_not_entered_is_a_caveat_not_a_crash(make_repo, tmp_path):
    make_repo("fine").commit("feat: a", at=AT)
    sealed = tmp_path / "sealed"
    (sealed / "inner").mkdir(parents=True)
    sealed.chmod(0o444)  # its names can be read, nothing inside it can be looked at
    try:
        found = discover([tmp_path], [], GitRunner())
    finally:
        sealed.chmod(0o755)
    assert names(found) == ["fine"]
    assert [(s.path, s.reason) for s in found.skipped] == [
        (sealed / "inner", "cannot be read (Permission denied)")
    ]  # and `sealed` is not also called empty: what it holds could not be checked


def add_submodule(superproject: RepoBuilder, upstream: RepoBuilder, where: str) -> None:
    """`git submodule add` from a local path: file:// transport must be allowed for the clone."""
    superproject.git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
                     str(upstream.path), where)  # fmt: skip


def test_a_checked_out_submodule_is_a_repository_of_its_own(make_repo, tmp_path) -> None:
    lib = make_repo("upstream/lib")
    lib.commit("feat: the library", at=AT)
    app = make_repo("work/app")
    app.commit("feat: the app", at=AT)
    add_submodule(app, lib, "vendor/lib")
    app.commit("chore: vendor the library", at=AT)
    found = discover([tmp_path / "work"], [], GitRunner())
    assert names(found) == ["app", "lib"]
    [sub] = [repo for repo in found.repos if repo.name == "lib"]
    assert sub.path == (app.path / "vendor" / "lib").resolve()
    assert sub.common_dir == (app.path / ".git" / "modules" / "vendor" / "lib").resolve()
    assert sub.superproject == app.path.resolve()
    assert found.skipped == ()


def test_nested_submodules_are_found_through_each_superproject(make_repo, tmp_path) -> None:
    core = make_repo("upstream/core")
    core.commit("feat: core", at=AT)
    lib = make_repo("upstream/lib")
    lib.commit("feat: lib", at=AT)
    add_submodule(lib, core, "deps/core")
    lib.commit("chore: core inside lib", at=AT)
    app = make_repo("work/app")
    app.commit("feat: app", at=AT)
    add_submodule(app, lib, "vendor/lib")
    app.git(
        "-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init", "--recursive"
    )
    found = discover([tmp_path / "work"], [], GitRunner())
    assert names(found) == ["app", "core", "lib"]
    by_name = {repo.name: repo for repo in found.repos}
    assert by_name["core"].superproject == by_name["lib"].path


def test_a_submodule_that_is_not_checked_out_is_named(make_repo, tmp_path) -> None:
    lib, other = make_repo("upstream/lib"), make_repo("upstream/other")
    lib.commit("feat: lib", at=AT)
    other.commit("feat: other", at=AT)
    app = make_repo("work/app")
    app.commit("feat: app", at=AT)
    add_submodule(app, lib, "vendor/lib")
    add_submodule(app, other, "vendor/other")
    app.commit("chore: two submodules", at=AT)
    app.git("submodule", "deinit", "-q", "-f", "vendor/other")  # its folder stays, empty
    found = discover([tmp_path / "work"], [], GitRunner())
    assert names(found) == ["app", "lib"]
    assert [(s.path, s.reason) for s in found.skipped] == [
        (app.path / "vendor" / "other", SUBMODULE_NOT_CHECKED_OUT)
    ]


def test_a_submodule_path_that_leads_outside_its_repository_is_not_followed(make_repo, tmp_path):
    make_repo("elsewhere").commit("feat: not under the root", at=AT)
    app = make_repo("work/app")
    app.commit("feat: app", at=AT)
    (app.path / ".gitmodules").write_text(
        '[submodule "x"]\n\tpath = ../../elsewhere\n\turl = ./x\n'  # a crafted .gitmodules
    )
    found = discover([tmp_path / "work"], [], GitRunner())
    assert names(found) == ["app"]
    assert [(s.path, s.reason) for s in found.skipped] == [
        (app.path / "../../elsewhere", SUBMODULE_OUTSIDE)
    ]


def test_a_bare_repository_behind_a_git_file_is_listed_at_its_project_folder(
    hermetic_git, make_repo, tmp_path
) -> None:
    # The ".bare" layout: app/.bare is a bare clone, app/.git says "gitdir: ./.bare", and the
    # worktrees sit beside it (app/main). The project is "app", not ".bare".
    upstream = make_repo("upstream/app")
    upstream.commit("feat: a", at=AT)
    app = tmp_path / "work" / "app"
    run_git(tmp_path, "clone", "-q", "--bare", str(upstream.path), str(app / ".bare"))
    (app / ".git").write_text("gitdir: ./.bare\n")
    run_git(app, "worktree", "add", "-q", "main", "main")
    [repo] = discover([tmp_path / "work"], [], GitRunner()).repos
    assert (repo.name, repo.path, repo.bare) == ("app", app.resolve(), True)
    assert repo.common_dir == (app / ".bare").resolve()
    hermetic_git.write_text("[safe]\n\tbareRepository = explicit\n")
    assert discover([tmp_path / "work"], [], GitRunner()).repos == (repo,)  # opened via .git


def test_a_bare_repository_kept_in_a_git_folder_takes_its_folder_s_name(tmp_path) -> None:
    (tmp_path / "dots").mkdir()
    run_git(tmp_path / "dots", "init", "-q", "--bare", "-b", "main", ".git")
    [repo] = discover([tmp_path], [], GitRunner()).repos
    assert (repo.name, repo.bare) == ("dots", True)


def test_linked_worktrees_are_listed_with_their_repository_once(make_repo, tmp_path) -> None:
    app = make_repo("work/app")
    head = app.commit("feat: a", at=AT)
    app.add_worktree(tmp_path / "work" / "app-feature", branch="feature/a")  # walked: same repo
    app.add_worktree(app.path / ".claude" / "worktrees" / "agent-1")  # Claude Code's, detached
    app.add_worktree(tmp_path / "elsewhere" / "review", branch="review")  # outside the root
    app.git("worktree", "lock", "--reason", "on a USB disk", str(tmp_path / "elsewhere" / "review"))
    gone = app.add_worktree(tmp_path / "elsewhere" / "gone")
    shutil.rmtree(gone.path)  # deleted by hand: git still lists it, as prunable
    found = discover([tmp_path / "work"], [], GitRunner(), with_worktrees=True)
    [repo] = found.repos
    assert [(w.path, w.branch, w.locked, w.prunable) for w in repo.worktrees] == [
        ((tmp_path / "elsewhere" / "gone").resolve(), "", False, True),
        ((tmp_path / "elsewhere" / "review").resolve(), "review", True, False),
        ((app.path / ".claude" / "worktrees" / "agent-1").resolve(), "", False, False),
        ((tmp_path / "work" / "app-feature").resolve(), "feature/a", False, False),
    ]
    assert {w.head for w in repo.worktrees} == {head}
    assert found.skipped == () and found.pruned == ()
    assert discover([tmp_path / "work"], [], GitRunner()).repos[0].worktrees == ()  # on request


def test_a_bare_repository_lists_only_its_linked_worktrees(make_repo, tmp_path) -> None:
    upstream = make_repo("upstream")
    upstream.commit("feat: a", at=AT)
    run_git(tmp_path, "clone", "-q", "--bare", str(upstream.path), str(tmp_path / "work" / "m.git"))
    run_git(tmp_path / "work" / "m.git", "worktree", "add", "-q", str(tmp_path / "wt"), "main")
    [repo] = discover([tmp_path / "work"], [], GitRunner(), with_worktrees=True).repos
    assert [(w.path, w.branch) for w in repo.worktrees] == [((tmp_path / "wt").resolve(), "main")]


def under(base, name: bytes):
    """A path below `base` whose last part is raw bytes: Linux allows any but / and NUL."""
    return Path(os.fsdecode(os.fsencode(base) + b"/" + name))


def test_a_repository_whose_folder_name_is_not_utf8_is_found(tmp_path) -> None:
    require_raw_byte_names(tmp_path)  # macOS cannot have such a folder
    repo = RepoBuilder(under(tmp_path, b"r\xff"))
    repo.commit("feat: a", at=AT)
    repo.add_worktree(under(tmp_path, b"wt\xfe"), branch="feature/x")
    found = discover([tmp_path], [], GitRunner(), with_worktrees=True)
    [r] = found.repos
    assert os.fsencode(r.path) == os.fsencode(tmp_path) + b"/r\xff"  # the exact bytes
    assert r.name == os.fsdecode(b"r\xff")
    assert [os.fsencode(w.path) for w in r.worktrees] == [os.fsencode(tmp_path) + b"/wt\xfe"]
    assert found.skipped == ()


@pytest.mark.parametrize("tail", [b" ", b"\xe3\x80\x80", b"\x1c", b"\xc2\x85"])
def test_a_folder_name_ending_in_whitespace_or_a_separator_is_its_own_repository(
    tmp_path, tail: bytes
) -> None:
    # git's own --separate-git-dir: `proj<tail>/.git` is a file naming a git dir elsewhere, so
    # the folder comes back from `rev-parse --show-toplevel`; `plain<tail>` is an ordinary one.
    RepoBuilder(tmp_path / "root" / "proj").commit("feat: the other proj", at=AT)
    odd = under(tmp_path / "root", b"proj" + tail)
    run_git(tmp_path, "init", "-q", "-b", "main", f"--separate-git-dir={tmp_path / 'gd'}", str(odd))
    RepoBuilder(under(tmp_path / "root", b"plain" + tail))
    found = discover([tmp_path / "root"], [], GitRunner())
    assert sorted(os.fsencode(r.path) for r in found.repos) == sorted(
        os.fsencode(tmp_path / "root") + b"/" + name
        for name in (b"proj", b"proj" + tail, b"plain" + tail)
    )
    assert found.skipped == ()


def test_a_git_dir_whose_work_tree_is_elsewhere_is_not_read_from_here(make_repo, tmp_path):
    # `root/hijack/.git` names a git dir whose core.worktree is someone else's checkout.
    victim = make_repo("outside/victim")
    victim.commit("feat: the victim's work", at=AT)
    crafted = tmp_path / "craft-gd"
    run_git(tmp_path, "init", "-q", f"--separate-git-dir={crafted}", str(tmp_path / "scratch"))
    run_git(tmp_path, f"--git-dir={crafted}", "config", "core.worktree", str(victim.path))
    (tmp_path / "root" / "hijack").mkdir(parents=True)
    (tmp_path / "root" / "hijack" / ".git").write_text(f"gitdir: {crafted}\n")
    found = discover([tmp_path / "root"], [], GitRunner())
    assert found.repos == ()
    assert [(s.path, s.reason) for s in found.skipped] == [
        (
            tmp_path / "root" / "hijack",
            f"its work tree is elsewhere ({victim.path}) — not read from here",
        )
    ]


def test_a_git_entry_that_is_a_symbolic_link_is_never_followed(make_repo, tmp_path) -> None:
    secret = make_repo("outside/secret")
    secret.commit("feat: secret", at=AT)
    (tmp_path / "root" / "linkgit").mkdir(parents=True)
    (tmp_path / "root" / "linkgit" / ".git").symlink_to(secret.path / ".git")
    found = discover([tmp_path / "root"], [], GitRunner())
    assert found.repos == ()
    assert [(s.path, s.reason) for s in found.skipped] == [
        (tmp_path / "root" / "linkgit" / ".git", LINK_TO_FOLDER)
    ]


def test_a_submodule_whose_git_dir_is_another_repository_never_relabels_it(make_repo, tmp_path):
    # The .gitmodules of zzz-evil names `inner`, whose .git file leads to aaa-real's .git, and
    # `inner2`, whose .git is a link to a repository outside the root.
    secret = make_repo("outside/secret")
    secret.commit("feat: secret", at=AT)
    make_repo("root/aaa-real").commit("feat: real", at=AT)
    evil = make_repo("root/zzz-evil")
    evil.commit("feat: evil", at=AT)
    (evil.path / ".gitmodules").write_text(
        '[submodule "x"]\n\tpath = inner\n[submodule "y"]\n\tpath = inner2\n'
    )
    (evil.path / "inner").mkdir()
    (evil.path / "inner" / ".git").write_text("gitdir: ../../aaa-real/.git\n")
    (evil.path / "inner2").mkdir()
    (evil.path / "inner2" / ".git").symlink_to(secret.path / ".git")
    found = discover([tmp_path / "root"], [], GitRunner())
    assert [(r.name, r.superproject) for r in found.repos] == [
        ("aaa-real", None),
        ("zzz-evil", None),
    ]
    real_git = (tmp_path / "root" / "aaa-real" / ".git").resolve()
    assert [(s.path, s.reason) for s in found.skipped] == [
        (
            evil.path / "inner",
            f"submodule whose git dir is not its superproject's ({real_git}) — not followed",
        ),
        (evil.path / "inner2" / ".git", LINK_TO_FOLDER),
    ]


def test_a_submodule_path_naming_the_git_folder_or_the_repository_itself_is_refused(make_repo):
    # `path = .git` would make the superproject a submodule of itself; `path = .` too.
    sup = make_repo("root/sup")
    sup.commit("feat: sup", at=AT)
    (sup.path / ".gitmodules").write_text(
        '[submodule "g"]\n\tpath = .\n[submodule "h"]\n\tpath = .git\n'
        '[submodule "k"]\n\tpath = vendor/.GIT/x\n'
    )
    found = discover([sup.path.parent], [], GitRunner())
    assert [(r.name, r.superproject) for r in found.repos] == [("sup", None)]
    assert sorted((str(s.path), s.reason) for s in found.skipped) == [
        (str(sup.path), SUBMODULE_OUTSIDE),
        (str(sup.path / ".git"), SUBMODULE_GIT_COMPONENT),
        (str(sup.path / "vendor/.GIT/x"), SUBMODULE_GIT_COMPONENT),
    ]


def test_a_separate_git_dir_is_not_reported_when_its_repository_is_found(tmp_path) -> None:
    # `git init --separate-git-dir=root/proj-git root/proj`: proj-git holds HEAD, objects and
    # refs like a bare repository, but it is the git dir of `proj`, which is scanned.
    root = tmp_path / "root"
    run_git(tmp_path, "init", "-q", "-b", "main", f"--separate-git-dir={root / 'proj-git'}",
            str(root / "proj"))  # fmt: skip
    found = discover([root], [], GitRunner())
    assert [r.path for r in found.repos] == [root / "proj"]
    assert found.skipped == ()


def test_a_separate_git_dir_whose_work_tree_was_not_found_says_so(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    run_git(tmp_path, "init", "-q", "-b", "main", f"--separate-git-dir={root / 'gd'}",
            str(tmp_path / "elsewhere"))  # fmt: skip
    found = discover([root], [], GitRunner())
    assert found.repos == ()
    assert [(s.path, s.reason) for s in found.skipped] == [(root / "gd", GIT_DIR_ELSEWHERE)]


def test_naming_a_dot_git_folder_means_its_repository(make_repo) -> None:
    app = make_repo("app")
    app.commit("feat: a", at=AT)
    found = discover([], [app.path / ".git"], GitRunner())
    assert [r.path for r in found.repos] == [app.path.resolve()]
    assert found.skipped == ()


def bare_with_worktree(upstream: RepoBuilder, bare: Path, worktree: Path) -> None:
    run_git(upstream.path.parent, "clone", "-q", "--bare", str(upstream.path), str(bare))
    run_git(bare, "worktree", "add", "-q", "-b", "feat", str(worktree))


def test_the_same_bare_repository_is_described_the_same_whatever_the_order(make_repo, tmp_path):
    # A bare repository in B with a linked worktree in A. Whichever path is met first, the
    # entry is the bare repository itself — the path whose git dir IS the common dir.
    upstream = make_repo("upstream")
    upstream.commit("feat: a", at=AT)
    bare_with_worktree(upstream, tmp_path / "B" / "app.git", tmp_path / "A" / "feat")
    ab = discover([tmp_path / "A", tmp_path / "B"], [], GitRunner(), with_worktrees=True)
    ba = discover([tmp_path / "B", tmp_path / "A"], [], GitRunner(), with_worktrees=True)
    assert ab == ba
    [repo] = ab.repos
    assert (repo.name, repo.path, repo.bare) == (
        "app",
        (tmp_path / "B" / "app.git").resolve(),
        True,
    )
    assert [w.path for w in repo.worktrees] == [(tmp_path / "A" / "feat").resolve()]


def test_within_one_root_the_bare_repository_wins_over_a_worktree_met_first(make_repo, tmp_path):
    upstream = make_repo("upstream")
    upstream.commit("feat: a", at=AT)
    bare_with_worktree(upstream, tmp_path / "C" / "z.git", tmp_path / "C" / "a-feat")
    [repo] = discover([tmp_path / "C"], [], GitRunner()).repos
    assert (repo.name, repo.bare) == ("z", True)
