"""Find the repositories under the folders a user names — each exactly once — and explain every
path that was not scanned.

A repository is identified by its *common git dir* (`git rev-parse --git-common-dir`): a main
checkout and all its linked worktrees share one, so they count once; two separate clones have
two, so they count twice (their shared commits are deduplicated later, by SHA). The walk goes
down to `max_depth` levels below each root, never into a repository, never through a symbolic
link, and not into hidden, dependency or build folders (pruned, and named); a checked-out
submodule is found through its superproject. Every path that could hold a repository but was not
scanned is a `Skipped` with a reason. Only `git rev-parse`, `git config --file
.gitmodules` and `git worktree list` run; nothing here writes to disk.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from sijl.git import GitError, GitRunner, parse_worktrees_z
from sijl.models import Repo, Skipped, Worktree
from sijl.text import json_text, printable

DEFAULT_MAX_DEPTH = 4
# The prune list: folders that hold installed dependencies or build output, not projects. Hidden
# folders (.venv, .cache, .claude — whose worktrees/ holds Claude Code's linked worktrees) are
# pruned by their leading dot. Either rule only stops the search going *into* a folder: a folder
# that is itself a repository is never pruned.
PRUNED_NAMES = frozenset({"node_modules", "venv", "__pycache__", "site-packages", "dist"})
HIDDEN_FOLDER = "hidden folder (tools and settings) — not searched"
DEPENDENCY_FOLDER = "dependency or build folder — not searched"
# Symbolic links are never followed: one can loop back to its own parent or lead anywhere on disk.
LINK_TO_FOLDER = "symbolic link — not followed (name its target with --root or --repo)"
LINK_NOWHERE = "symbolic link that leads nowhere (broken, or a loop) — not followed"
SUBMODULE_NOT_CHECKED_OUT = "submodule not checked out — it has no history on this machine"
SUBMODULE_OUTSIDE = "submodule path leads outside its repository — not followed"
SUBMODULE_GIT_COMPONENT = "submodule path names a .git folder — not followed"
GIT_DIR_ELSEWHERE = (
    "a git directory whose work tree is elsewhere — name the work tree with --repo to scan it"
)
# Refusals git makes for safety: reported, never worked around.
DUBIOUS = (
    "owned by another user — git does not trust it (safe.directory), and Sijl never overrides that"
)
BARE_REFUSED = (
    "a bare repository your git configuration only opens when named explicitly "
    "(safe.bareRepository) — Sijl does not override that"
)


@dataclass(frozen=True)
class Discovery:
    repos: tuple[Repo, ...]
    skipped: tuple[Skipped, ...]  # could hold a repository, but was not scanned: always a caveat
    pruned: tuple[Skipped, ...] = ()  # not searched by rule (hidden, dependency, build folders)


def looks_like_repo(path: Path) -> bool:
    """A checkout (`.git` folder, or a `.git` file for a worktree) or a bare repository."""
    try:
        if (path / ".git").exists():
            return True
        return (
            (path / "HEAD").is_file() and (path / "objects").is_dir() and (path / "refs").is_dir()
        )
    except OSError:  # Python 3.12's Path.exists() raises for an unreadable folder
        return False  # the walk then reports it as "cannot be read"


class NotHere(Exception):
    """git answered for a repository other than the folder probed; `reason` says which."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class GitDirOnly(Exception):
    """The folder is the git dir of a non-bare repository (a `--separate-git-dir` target, or a
    `.git` folder named directly): its repository is read from its work tree, not from here."""

    def __init__(self, common_dir: Path) -> None:
        super().__init__(str(common_dir))
        self.common_dir = common_dir


def _around(where: Path) -> str:
    return f"opens the repository around it ({where}) — its own .git is broken or missing"


def probe(git: GitRunner, path: Path, *, here_only: bool = False) -> Repo:
    """The repository at `path`. With `here_only`, git may not look above `path` for one, and the
    answer is checked: the work tree (or, for a bare repository, the git dir) must be `path`
    itself — otherwise `NotHere`. A broken `.git` is never the repository around it, and a git
    dir whose work tree is elsewhere (core.worktree) is never read from here."""
    return _probe(git, path, here_only=here_only)[0]


def _probe(git: GitRunner, path: Path, *, here_only: bool) -> tuple[Repo, Path]:
    """`probe()`, plus the git dir that `path` opened (`--absolute-git-dir`)."""
    run = git.run_here if here_only else git.run_quick
    out = run(
        path,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
        "--absolute-git-dir",
        "--is-bare-repository",
        "--is-inside-git-dir",
    )
    common_line, git_dir_line, bare_line, inside_line = _lines(out)[:4]
    common, git_dir = Path(common_line).resolve(), Path(git_dir_line).resolve()
    bare = bare_line == "true"
    if not bare and inside_line == "true":  # it has HEAD, objects and refs, but is not bare
        raise GitDirOnly(common)
    here = path.resolve()
    walked_up = git_dir not in (here, here / ".git") and here.is_relative_to(git_dir)
    if bare and here_only and not (path / ".git").is_file() and walked_up:
        raise NotHere(_around(git_dir))  # a bare layout (or core.bare .git) must be its git dir
    top = None
    if not bare and (here_only or common.name != ".git"):
        top = Path(_lines(run(path, "rev-parse", "--show-toplevel"))[0]).resolve()
        if here_only and top != here:
            if here.is_relative_to(top):  # git walked up to `top`
                raise NotHere(_around(top))
            raise NotHere(f"its work tree is elsewhere ({top}) — not read from here")
    if bare and (path / ".git").is_file():
        # The ".bare" layout: `app/.git` says `gitdir: ./.bare`. The project is the folder git
        # was opened from — where it opens even when safe.bareRepository wants bare
        # repositories named explicitly.
        main = here
    elif bare:
        main = common
    elif common.name == ".git":
        main = common.parent
    else:  # a separate git dir: the top of the working tree
        main = top or here
    return Repo(name=_name(main, bare), path=main, common_dir=common, bare=bare), git_dir


def _not_regular(path: Path) -> str:
    """Why git must not be started on `path`, or "". git opens HEAD, config and commondir first;
    a pipe or a device in their place makes it wait for a writer until the timeout — per folder.
    Checked without opening them (`os.stat` never blocks; it follows a symbolic link, so an old
    git's symlinked HEAD is fine when it leads to a regular file). Behind a `.git` file the git
    dir is git's to find: the probe's short timeout guards that case."""
    dot_git = path / ".git"
    try:
        if dot_git.is_file():
            return ""
        folder = dot_git if dot_git.is_dir() else path  # a .git folder, or a bare layout
        for name in ("HEAD", "config", "commondir"):
            try:
                mode = os.stat(folder / name).st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(mode):
                return f"its {name} is not a regular file (a pipe or a device?) — not opened"
    except OSError as exc:
        return f"cannot be read ({exc.strerror})"
    return ""


def _lines(out: bytes) -> list[str]:
    """git's output lines, decoded as file names are (os.fsdecode): a folder name that is not
    UTF-8 keeps its bytes as surrogates, so the path still opens and prints escaped. Split on
    "\n" only and nothing stripped: a folder may end in a space, U+3000 or \x1c, which
    `strip()` would eat and `splitlines()` would split on — naming another folder."""
    return os.fsdecode(out).removesuffix("\n").split("\n")


def linked_worktrees(git: GitRunner, repo: Repo) -> tuple[Worktree, ...]:
    """The linked worktrees of `repo`, in path order. git's own list, so it includes worktrees
    outside every root, inside folders the walk prunes (`.claude/worktrees`), locked ones and
    ones whose folder was deleted ("prunable" — listed, never pruned: that would write)."""
    records = parse_worktrees_z(git.run_quick(repo.path, "worktree", "list", "--porcelain", "-z"))
    linked = [
        Worktree(
            path=Path(record["worktree"]),
            head=record.get("HEAD", "") if record.get("HEAD", "").strip("0") else "",
            branch=record.get("branch", "").removeprefix("refs/heads/"),
            locked="locked" in record,
            prunable="prunable" in record,
        )
        for record in records[1:]  # the first is the main worktree, or the bare repository
    ]
    return tuple(sorted(linked, key=lambda w: w.path.parts))


def _name(main: Path, bare: bool) -> str:
    """The folder name; a bare `mirror.git` is "mirror", and a bare repository kept in its
    project's own `.git` or `.bare` folder is named after that project."""
    if not bare:
        return main.name
    if main.name in (".git", ".bare"):
        return main.parent.name
    return main.name.removesuffix(".git") or main.name


_LINK_TARGET = re.compile(r"not a git repository: (.+)")  # a .git file naming a missing gitdir


def skip_reason(exc: GitError, *, looked_like_repo: bool) -> str:
    """Why git could not open a path as a repository, in words a reader can act on.

    Read from git's own message (`LC_ALL=C` keeps it English). A refusal git makes for safety —
    another user's repository, a bare repository the user's config keeps closed — is reported and
    never worked around: Sijl adds no `safe.directory` and no `--git-dir`."""
    text = exc.stderr
    if exc.returncode == -1:  # GitRunner's timeout
        return f"git gave no answer ({exc.summary}) — a pipe, a device or a hung disk? Not scanned"
    if "dubious ownership" in text:
        return DUBIOUS
    if "safe.bareRepository" in text:
        return BARE_REFUSED
    if "invalid gitfile format" in text:
        return "corrupt: its .git file does not point to a repository"
    link = _LINK_TARGET.search(text)
    if link:
        return (
            f"broken link: its .git file points to {link.group(1).strip()}, which is not a "
            "repository (moved or deleted)"
        )
    if "not a git repository" in text:
        if looked_like_repo:  # a .git entry, or a bare layout, that git cannot open
            return "corrupt or incomplete: git cannot open it as a repository"
        return "not a git repository"
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    detail = exc.summary
    if detail.endswith(":") and len(lines) > 1:  # "unknown repository extension found:" + name
        detail = f"{detail} {lines[1]}"
    return f"cannot be opened: {detail}"


class _Search:
    def __init__(self, git: GitRunner, max_depth: int, prune: frozenset[str]) -> None:
        self.git, self.max_depth, self.prune = git, max_depth, prune
        self.found: dict[Path, Repo] = {}
        self.git_dirs: dict[Path, Path] = {}  # common dir → the git dir it was opened through
        self.git_dir_only: dict[Path, Path] = {}  # git dirs met without their work tree
        self.skipped: list[Skipped] = []
        self.pruned: list[Skipped] = []

    def consider(self, path: Path, *, superproject: Repo | None = None) -> None:
        # A .git that is a symbolic link is a link like any other: never followed.
        git_entry = path / ".git"
        try:
            if git_entry.is_symlink():
                link = LINK_TO_FOLDER if git_entry.is_dir() else LINK_NOWHERE
                self.skipped.append(Skipped(git_entry, link))
                return
        except OSError as exc:
            self.skipped.append(Skipped(path, f"cannot be read ({exc.strerror})"))
            return
        trap = _not_regular(path)
        if trap:
            self.skipped.append(Skipped(path, trap))
            return
        # A folder with a .git (or a bare layout) of its own is probed right there; a plain folder
        # the user named may still lead up to the repository it sits in (`--repo app/src`).
        here = looks_like_repo(path)
        try:
            repo, git_dir = _probe(self.git, path, here_only=here)
        except GitError as exc:
            self.skipped.append(Skipped(path, skip_reason(exc, looked_like_repo=here)))
            return
        except NotHere as exc:
            self.skipped.append(Skipped(path, exc.reason))
            return
        except GitDirOnly as exc:
            if path.name == ".git" and superproject is None:  # `--repo app/.git`: means `app`
                self.consider(path.parent)
            else:  # reported at the end only if its repository was not found another way
                self.git_dir_only.setdefault(exc.common_dir, path)
            return
        if superproject is not None and not self._owns(superproject, path, git_dir):
            reason = f"submodule whose git dir is not its superproject's ({git_dir}) — not followed"
            self.skipped.append(Skipped(path, reason))
            return  # and a repository found elsewhere is never relabelled as its submodule
        parent = superproject.path if superproject is not None else None
        common = repo.common_dir
        known = self.found.get(common)
        if known is not None:  # one entry per repository, however reached
            parent = known.superproject or parent  # named with --repo, then met as a submodule
            if _rank(repo, git_dir) >= _rank(known, self.git_dirs[common]):
                self.found[common] = replace(known, superproject=parent)
                return
        # The entry that describes the repository does not depend on which path was met first:
        # the one whose git dir IS the common dir (the bare repository, the main checkout) wins,
        # then the first by path. Submodules are looked for once the walk is over.
        self.found[common] = replace(repo, superproject=parent)
        self.git_dirs[common] = git_dir

    def all_submodules(self) -> None:
        """Look for submodules in every repository found — and in the submodules found — once
        each, after the walk, so they come from the entry that finally describes it."""
        searched: set[Path] = set()
        while pending := sorted(set(self.found) - searched, key=lambda c: c.parts):
            for common in pending:
                searched.add(common)
                if not self.found[common].bare:
                    self.submodules(self.found[common])

    def _owns(self, superproject: Repo, path: Path, git_dir: Path) -> bool:
        """A submodule's git dir is inside its superproject's `<git dir>/modules/` (absorbed, as
        `git submodule` makes it) or is the submodule's own `.git` folder (a clone added in
        place). Anything else — a .git file leading to another repository — is not its own."""
        modules = self.git_dirs[superproject.common_dir] / "modules"
        own = path / ".git"
        return git_dir.is_relative_to(modules) or (own.is_dir() and git_dir == own.resolve())

    def submodules(self, repo: Repo) -> None:
        """A checked-out submodule is a repository of its own — its commits are not the
        superproject's — inside a folder the walk never enters. Its path comes from `.gitmodules`,
        read by git (`git config --file`, includes off), never parsed here; a path that leads
        outside the repository is refused, as `.gitmodules` is just a file anyone can write."""
        listing = repo.path / ".gitmodules"
        try:
            if listing.is_symlink():
                self.skipped.append(Skipped(listing, "a symbolic link — submodules not read"))
                return
            if not listing.is_file():
                return  # no submodules
        except OSError as exc:
            self.skipped.append(Skipped(listing, f"cannot be read ({exc.strerror})"))
            return
        pattern = r"^submodule\..*\.path$"
        try:
            out = self.git.run_quick(
                repo.path, "config", "--file", ".gitmodules", "--no-includes", "--null",
                "--get-regexp", pattern,
            )  # fmt: skip
        except GitError as exc:
            if exc.returncode != 1:  # 1: no submodule has a path
                reason = f"submodules could not be listed: {exc.summary}"
                self.skipped.append(Skipped(listing, reason))
            return
        top = repo.path.resolve()
        for entry in os.fsdecode(out).split("\0"):  # paths keep bytes that are not UTF-8
            if not entry:
                continue
            relative = entry.partition("\n")[2]  # "key\nvalue" with --null
            target = repo.path / relative
            if any(part.casefold() == ".git" for part in Path(relative).parts):
                self.skipped.append(Skipped(target, SUBMODULE_GIT_COMPONENT))
                continue
            try:
                real = target.resolve()
                inside = real != top and real.is_relative_to(top)
            except (OSError, RuntimeError):  # a symbolic link loop on the way
                inside = False
            if not inside:
                self.skipped.append(Skipped(target, SUBMODULE_OUTSIDE))
            elif not looks_like_repo(target):
                self.skipped.append(Skipped(target, SUBMODULE_NOT_CHECKED_OUT))
            else:
                self.consider(target, superproject=repo)

    def with_worktrees(self, repo: Repo) -> Repo:
        try:
            return replace(repo, worktrees=linked_worktrees(self.git, repo))
        except GitError as exc:
            reason = f"its linked worktrees could not be listed: {exc.summary}"
            self.skipped.append(Skipped(repo.path, reason))
            return repo

    def _rule(self, folder: Path) -> str:
        """Why `folder` is pruned, or "" when it is searched."""
        if folder.name.startswith("."):
            return HIDDEN_FOLDER
        if folder.name in self.prune:
            return DEPENDENCY_FOLDER
        return ""

    def children(self, directory: Path) -> list[Path] | None:
        """Sub-folders worth searching: no symbolic links, nothing the prune rules exclude — both
        recorded, so nothing disappears silently. None when the folder itself cannot be read
        (recorded under Caveats); an entry that cannot be looked at is recorded and passed over."""
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            self.skipped.append(Skipped(directory, f"cannot be read ({exc.strerror})"))
            return None
        result = []
        for entry in entries:
            try:  # a folder readable but not enterable (r--) lists names whose stat then fails
                if entry.is_symlink():
                    if entry.is_dir():  # follows the link: False for a loop or a broken link
                        self.skipped.append(Skipped(entry, LINK_TO_FOLDER))
                    elif not entry.exists():
                        self.skipped.append(Skipped(entry, LINK_NOWHERE))
                    continue  # a link to a file is just a file
                if not entry.is_dir():
                    continue  # a file
                rule = self._rule(entry)
                if rule and not looks_like_repo(entry):  # a repository is never pruned by name
                    self.pruned.append(Skipped(entry, rule))
                    continue
            except OSError as exc:
                self.skipped.append(Skipped(entry, f"cannot be read ({exc.strerror})"))
                continue
            result.append(entry)
        return result

    def walk(self, directory: Path, depth: int) -> bool:
        """Search `directory`. True when something was met *or already disclosed* — a repository,
        an unreadable folder or entry, a symbolic link, the depth limit — so the caller does not
        also call it empty."""
        if looks_like_repo(directory):
            self.consider(directory)
            return True  # never search inside a repository
        before = len(self.skipped)
        children = self.children(directory)
        if children is None:
            return True  # unreadable: reported by children()
        disclosed = len(self.skipped) > before  # a link or an unreadable entry was named in it
        if depth >= self.max_depth:
            if not children:
                return disclosed
            self.skipped.append(
                Skipped(
                    directory,
                    f"not searched deeper than {self.max_depth} levels — name a repository "
                    "below it with --repo",
                )
            )
            return True
        held = disclosed
        for child in children:
            held |= self.walk(child, depth + 1)
        return held


def discover(
    roots: Sequence[Path],
    paths: Sequence[Path],
    git: GitRunner,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    prune: frozenset[str] = PRUNED_NAMES,
    with_worktrees: bool = False,
) -> Discovery:
    """Repositories from explicit `paths` and from the folders under `roots`; with
    `with_worktrees`, each one's linked worktrees too (one more git call per repository)."""
    search = _Search(git, max_depth, prune)
    for path in paths:
        try:
            exists = path.exists()
        except OSError as exc:
            search.skipped.append(Skipped(path, f"cannot be read ({exc.strerror})"))
            continue
        if exists:
            search.consider(path)
        else:
            search.skipped.append(Skipped(path, "does not exist"))
    for root in roots:  # a root the user names is followed even when it is a symbolic link
        try:
            is_folder = root.is_dir()
            exists = is_folder or root.exists()
        except OSError as exc:
            search.skipped.append(Skipped(root, f"cannot be read ({exc.strerror})"))
            continue
        if not exists:
            search.skipped.append(Skipped(root, "does not exist"))
        elif not is_folder:
            search.skipped.append(Skipped(root, "not a folder"))
        elif looks_like_repo(root):
            search.consider(root)
        else:
            before = len(search.skipped)
            children = search.children(root)
            if children is None:
                continue  # the root itself is unreadable: already reported
            disclosed = len(search.skipped) > before
            empty = [child for child in children if not search.walk(child, 1)]
            if len(empty) == len(children) and not disclosed:  # nothing anywhere under the root
                search.skipped.append(Skipped(root, "no git repository inside"))
            else:  # name each top-level folder that held nothing, e.g. a copy without .git
                search.skipped.extend(Skipped(c, "no git repository inside") for c in empty)
    search.all_submodules()
    search.skipped.extend(
        Skipped(path, GIT_DIR_ELSEWHERE)
        for common, path in search.git_dir_only.items()
        if common not in search.found
    )
    repos = tuple(sorted(search.found.values(), key=lambda r: (r.name, str(r.path))))
    if with_worktrees:
        repos = tuple(search.with_worktrees(repo) for repo in repos)
    return Discovery(repos=repos, skipped=_once(search.skipped), pruned=_once(search.pruned))


def _rank(repo: Repo, git_dir: Path) -> tuple[bool, tuple[str, ...]]:
    """Which of several paths to one repository describes it: the one whose git dir is the common
    dir (a bare repository, or the main checkout) before a linked worktree, then by path."""
    return (git_dir != repo.common_dir, repo.path.parts)


def _once(items: list[Skipped]) -> tuple[Skipped, ...]:
    """Each (path, reason) once, in path order: a folder reached from two overlapping roots is
    one caveat, and the order never depends on the order the roots were given in."""
    return tuple(sorted(set(items), key=lambda s: (s.path.parts, s.reason)))


# ------------------------------------------------------------------------- `sijl repos`


def listing(found: Discovery) -> dict[str, object]:
    """What `sijl repos --json` prints. Every list is already in a fixed order — repositories
    by name then path, worktrees and skipped paths by path — so two runs over the same folders
    print the same bytes, whatever the order of the roots."""

    def entry(s: Skipped) -> dict[str, str]:
        return {"path": str(s.path), "reason": s.reason}

    return {
        "repositories": [
            {
                "name": repo.name,
                "path": str(repo.path),
                "common_dir": str(repo.common_dir),
                "bare": repo.bare,
                "superproject": str(repo.superproject) if repo.superproject else None,
                "worktrees": [
                    {
                        "path": str(w.path),
                        "head": w.head or None,
                        "branch": w.branch or None,
                        "locked": w.locked,
                        "prunable": w.prunable,
                    }
                    for w in repo.worktrees
                ],
            }
            for repo in found.repos
        ],
        "skipped": [entry(s) for s in found.skipped],
        "pruned": [entry(s) for s in found.pruned],
    }


def listing_json(found: Discovery) -> str:
    """Exact values (a folder name round-trips), with terminal controls escaped (text.py)."""
    return json_text(listing(found))


def _worktree_line(w: Worktree) -> str:
    where = f"branch {w.branch}" if w.branch else f"detached at {w.head[:8]}" if w.head else ""
    notes = [where or "no commit yet"]
    if w.locked:
        notes.append("locked")
    if w.prunable:
        notes.append("its folder is gone")
    return f"  - {w.path} — {' — '.join(notes)}"


def render_listing(found: Discovery) -> str:
    """The plain-text listing: repositories with their linked worktrees, then every path not
    scanned and every folder pruned, each with its reason. Every section is always there, so an
    empty one says "none" rather than vanishing. Each line goes through `printable()`: names,
    paths and git's own words may carry terminal controls."""
    out = [f"Repositories ({len(found.repos)})"]
    for repo in found.repos:
        line = f"- {repo.name} — {repo.path}"
        if repo.bare:
            line += " — bare"
        if repo.superproject:
            line += f" — submodule of {repo.superproject}"
        out.append(line)
        if repo.worktrees:
            n = len(repo.worktrees)
            out.append(f"  {n} linked worktree{'' if n == 1 else 's'}:")
            out += [_worktree_line(w) for w in repo.worktrees]
    for title, items in (("Not scanned", found.skipped), ("Not searched, by rule", found.pruned)):
        out += ["", f"{title} ({len(items)})"]
        out += [f"- {s.path} — {s.reason}" for s in items] or ["- none"]
    if not found.repos:
        out.insert(1, "- none")
    return "\n".join(printable(line) for line in out) + "\n"
