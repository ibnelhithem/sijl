"""The `discovery_tree` scenario: one folder holding every shape discovery must handle.

`work/` is the folder handed to `--root`; `outside/` and `upstream/` sit beside it and are not
searched. What is in `work/`:

- `app/` — a repository with five linked worktrees: listed once, with all five. They are
  `app-feature/` (on a branch, walked: no new entry), `app/.claude/worktrees/agent-1/` (inside
  the repository, never searched), `.claude/worktrees/agent-2/` (in a hidden folder, pruned),
  and `../outside/review/` (locked) and `../outside/gone/` (deleted by hand: prunable).
- `shortcut` — a symbolic link to `app`: not followed.
- `mirror.git/` — a bare clone: found, named `mirror`.
- `proj/` — the `.bare` layout (`proj/.git` says `gitdir: ./.bare`, worktree `proj/main`):
  found, named `proj`.
- `super/` — a superproject with `vendor/lib` checked out and `vendor/other` not: `super` and
  `lib` found, `vendor/other` named.
- `loop`, `self`, `dangling` — links to an ancestor, to themselves, to nothing: named, never
  followed.
- `node_modules/left-pad/` — a repository inside a dependency folder: `node_modules` pruned.
- `notes/drafts/` — plain folders: "no git repository inside".
- `garbage/`, `orphan/`, `hollow/`, `badcfg/` — a bad `.git` file, a worktree whose repository
  is gone, an empty `.git` folder, a bad config: each skipped with its reason.
- `deep/1/2/3/buried/` — a repository below the depth limit: `deep/1/2/3` named.

Dubious ownership needs another user (or git's test switch, injected per call), so it is tested
in `tests/test_discover_skips.py` rather than here.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from tests.gitfactory import RepoBuilder, run_git

AT = "2026-05-04T10:00:00+02:00"
FILE_TRANSPORT = ("-c", "protocol.file.allow=always")  # `submodule add` from a local path


@dataclass(frozen=True)
class DiscoveryTree:
    base: Path  # everything the scenario wrote: the "nothing changed" check snapshots all of it
    root: Path  # base/work, the folder handed to --root
    outside: Path  # base/outside, beside the root
    app: RepoBuilder  # the repository with many linked worktrees
    worktrees: tuple[Path, ...]  # app's linked worktrees, in path order


def build_discovery_tree(base: Path) -> DiscoveryTree:
    work, outside = base / "work", base / "outside"

    app = RepoBuilder(work / "app")
    app.commit("feat: the app", at=AT)
    app.commit("fix: the app", at="2026-05-05T10:00:00+02:00")
    worktrees = (
        app.add_worktree(outside / "gone").path,
        app.add_worktree(outside / "review", branch="review").path,
        app.add_worktree(work / ".claude" / "worktrees" / "agent-2").path,
        app.add_worktree(app.path / ".claude" / "worktrees" / "agent-1").path,
        app.add_worktree(work / "app-feature", branch="feature/a").path,
    )
    app.git("worktree", "lock", "--reason", "on a USB disk", str(outside / "review"))
    shutil.rmtree(outside / "gone")  # deleted by hand: git still lists it, as prunable
    (work / "shortcut").symlink_to(app.path)

    run_git(base, "clone", "-q", "--bare", str(app.path), str(work / "mirror.git"))
    proj = work / "proj"
    run_git(base, "clone", "-q", "--bare", str(app.path), str(proj / ".bare"))
    (proj / ".git").write_text("gitdir: ./.bare\n")
    run_git(proj, "worktree", "add", "-q", "main", "main")

    lib = RepoBuilder(base / "upstream" / "lib")
    lib.commit("feat: the library", at=AT)
    other = RepoBuilder(base / "upstream" / "other")
    other.commit("feat: another library", at=AT)
    sup = RepoBuilder(work / "super")
    sup.commit("feat: the superproject", at=AT)
    sup.git(*FILE_TRANSPORT, "submodule", "add", "-q", str(lib.path), "vendor/lib")
    sup.git(*FILE_TRANSPORT, "submodule", "add", "-q", str(other.path), "vendor/other")
    sup.commit("chore: two submodules", at=AT)
    sup.git("submodule", "deinit", "-q", "-f", "vendor/other")  # its folder stays, empty

    (work / "loop").symlink_to(work)
    (work / "self").symlink_to(work / "self")
    (work / "dangling").symlink_to(base / "nowhere")

    RepoBuilder(work / "node_modules" / "left-pad")
    (work / "notes" / "drafts").mkdir(parents=True)
    (work / "garbage").mkdir()
    (work / "garbage" / ".git").write_text("nonsense\n")
    (work / "orphan").mkdir()
    (work / "orphan" / ".git").write_text(
        f"gitdir: {base / 'deleted' / '.git' / 'worktrees' / 'x'}\n"
    )
    (work / "hollow" / ".git").mkdir(parents=True)
    badcfg = RepoBuilder(work / "badcfg")
    with (badcfg.path / ".git" / "config").open("a") as config:
        config.write("[core\n")
    RepoBuilder(work / "deep" / "1" / "2" / "3" / "buried")

    return DiscoveryTree(base=base, root=work, outside=outside, app=app, worktrees=worktrees)
