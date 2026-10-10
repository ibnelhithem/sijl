"""Where `report --out` may write, and how it writes.

Sijl never writes into a repository it reads, and the report file is
the one thing it writes. So `--out` is checked against every scanned repository — its git dir and
its work trees, the main one and every linked worktree — and the rules are:

- **Git's own files** — a git dir (`.git`, a bare repository, the common dir that holds every
  worktree's own git dir) and the `.git` entry at the top of every work tree (a linked worktree's
  is a file pointing at its git dir) — are never written to: a report has no business there, and
  a file there can change what git does, or cut a worktree off from its repository. No file named
  `.git` is ever written either.
- **A work tree** is written to only when the path is **exactly given**: written in full on the
  command line — absolute, or from `~` — with **no symbolic link anywhere on the way**, the file
  itself included. A relative path (the folder the command runs in decided where it lands), a
  path a link leads into a repository, and a link inside the repository (one it committed, say:
  `reports/review.md` → another repository's hook) are refused, with the full path to give.

How it is decided and written: the folder the file goes into is **opened
one component at a time with `O_NOFOLLOW`**, from `/` down, so no link is followed unnoticed; each
folder opened is identified by `(st_dev, st_ino)` and compared with the scanned repositories'
folders by identity, never by spelling — on a case-insensitive file system `.GIT` is `.git`, and
a name may come in another Unicode normalisation. The file is then written **into that opened
folder**: a new temporary file (`O_CREAT | O_EXCL | O_NOFOLLOW`, mode 0600 while written, 0644
when done, flushed to disk) moved over the name with `os.replace()` — which replaces a link or a
hard link instead of writing through it. The CLI checks before rendering (`check()`, so a refusal
costs nothing) and `write()` decides again on the folders it opens, so a link swapped in between
cannot redirect the file.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from sijl.discover import linked_worktrees
from sijl.git import GitError, GitRunner
from sijl.models import Repo

Identity = tuple[int, int]  # (st_dev, st_ino): what a file system calls the same file


class OutRefused(Exception):
    """`--out` may not be written: why, and what to give instead."""


@dataclass(frozen=True)
class Place:
    """A scanned repository, as `--out` sees it: its name and paths (for messages) and the
    identities of its work trees and of git's own files."""

    name: str
    work_trees: tuple[Path, ...]  # the main worktree (none for a bare repository) and linked ones
    git_dirs: tuple[Path, ...]  # the common git dir, which holds every worktree's own git dir
    tree_ids: frozenset[Identity] = field(default_factory=frozenset)
    git_ids: frozenset[Identity] = field(default_factory=frozenset)  # git dirs, each tree's .git


def _identity(path: Path, *, follow: bool = True) -> Identity | None:
    try:
        found = os.stat(path) if follow else os.lstat(path)
    except OSError:
        return None
    return (found.st_dev, found.st_ino)


def places(repos: Sequence[Repo], git: GitRunner) -> list[Place]:
    """Every scanned repository's work trees and git's own files, by path and by identity."""
    found = []
    for repo in repos:
        trees = [] if repo.bare else [repo.path]
        with suppress(GitError):  # unlisted: the main work tree and the git dir still count
            trees += [worktree.path for worktree in linked_worktrees(git, repo)]
        git_dirs = [repo.common_dir, *([repo.path] if repo.bare else [])]
        own = [_identity(d) for d in git_dirs] + [
            _identity(tree / ".git", follow=False) for tree in trees
        ]
        found.append(
            Place(
                repo.name,
                tuple(dict.fromkeys(Path(os.path.realpath(tree)) for tree in trees)),
                tuple(dict.fromkeys(Path(os.path.realpath(d)) for d in git_dirs)),
                frozenset(i for i in (_identity(tree) for tree in trees) if i is not None),
                frozenset(i for i in own if i is not None),
            )
        )
    return found


class _Link(Exception):
    """A component of the path is a symbolic link; `opened` are the folders before it."""

    def __init__(self, opened: list[Identity]) -> None:
        self.opened = opened


def _walk(folder: Path) -> tuple[int, list[Identity]]:
    """`folder` (absolute) opened from `/` down, one component at a time, never through a link:
    its file descriptor, and the identity of every folder on the way, itself included."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    opened = [_fd_identity(fd)]
    try:
        for part in folder.parts[1:]:
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR) and _is_link(fd, part):
                    raise _Link(opened) from None
                raise
            os.close(fd)
            fd = nxt
            opened.append(_fd_identity(fd))
    except BaseException:
        os.close(fd)
        raise
    return fd, opened


def _fd_identity(fd: int) -> Identity:
    found = os.fstat(fd)
    return (found.st_dev, found.st_ino)


def _is_link(fd: int, name: str) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(name, dir_fd=fd).st_mode)
    except OSError:
        return False


def _decide(given: Path, places: Sequence[Place], *, cwd: Path) -> tuple[int, str]:
    """The opened folder `--out given` would be written into, and the file's name there — or
    `OutRefused` (module docstring). The caller closes the descriptor."""
    typed = Path(os.path.abspath(cwd / given))  # `..` folded by name; links not followed
    name = typed.name
    real = Path(os.path.realpath(typed.parent)) / name
    trees = {i: place for place in places for i in place.tree_ids}
    own = {i: place for place in places for i in place.git_ids}
    if not name:
        raise OutRefused(f"--out {given} names a folder, not a file")
    if name.casefold() == ".git":  # any spelling: `.GIT` is `.git` where case does not count
        raise OutRefused(f"--out {given} is in git's own files (a .git): Sijl never writes there")
    via_link = False
    try:
        fd, opened = _walk(typed.parent)
    except _Link as link:
        inside = next((trees[i] for i in link.opened if i in trees), None)
        if inside is not None:  # a link inside the repository: it decides where the file lands
            raise OutRefused(_into(given, inside, real)) from None
        via_link = True
        fd, opened = _walk(real.parent)  # where the links lead, itself opened without links
    try:
        entry = os.lstat(name, dir_fd=fd) if _exists(fd, name) else None
        ids = [*opened, *([(entry.st_dev, entry.st_ino)] if entry is not None else [])]
        mine = next((own[i] for i in ids if i in own), None)
        if mine is not None:
            raise OutRefused(
                f"--out {given} is in git's own files of the scanned repository {mine.name}: "
                "Sijl never writes there"
            )
        inside = next((trees[i] for i in opened if i in trees), None)
        linked = via_link or (entry is not None and stat.S_ISLNK(entry.st_mode))
        if inside is not None and (not given.is_absolute() or linked):
            raise OutRefused(_into(given, inside, real))
    except BaseException:
        os.close(fd)
        raise
    return fd, name


def _exists(fd: int, name: str) -> bool:
    try:
        os.lstat(name, dir_fd=fd)
    except FileNotFoundError:
        return False
    return True


def _into(given: Path, place: Place, real: Path) -> str:
    where = f" ({place.work_trees[0]})" if place.work_trees else ""
    return (
        f"--out {given} would write into the scanned repository {place.name}{where}, and "
        "Sijl does not write into a repository it reads unless you give the file's full "
        f"path, with no symbolic link on the way: --out {real}"
    )


def check(given: Path, places: Sequence[Place], *, cwd: Path) -> None:
    """Whether `--out given` may be written — `OutRefused` if not; nothing is written."""
    fd, _ = _decide(given, places, cwd=cwd)
    os.close(fd)


def write(given: Path, text: str, places: Sequence[Place], *, cwd: Path) -> None:
    """Write the report at `--out given`, deciding again on the folder it is written into
    (module docstring): a new file there, moved over the name — never through a link."""
    fd, name = _decide(given, places, cwd=cwd)
    temporary = f".{name}.sijl-{secrets.token_hex(6)}"
    try:
        out = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        try:
            with os.fdopen(out, "w", encoding="utf-8") as file:
                file.write(text)
                file.flush()
                os.fsync(file.fileno())
                os.fchmod(file.fileno(), 0o644)
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary, dir_fd=fd)
            raise
    finally:
        os.close(fd)
