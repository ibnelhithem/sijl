"""Collapse rebased, cherry-picked and amended copies of one commit.

A rebase, a cherry-pick or an amend after a push writes a new commit — a new SHA and a new
committer date — that keeps the author, the author date and the message: one piece of work, two
commits. Copies share one key (`copy_key`): your email as written in the commit, the author
instant and the subject with its spacing normalised. They are matched inside one logical
repository: one clone, or several clones that share a root commit — checked only when a key turns
up in more than one clone, so unrelated projects never merge even when the same words were
committed in the same second.

Of each set of copies the one kept is a counted copy if there is one (collapsing never moves work
out of the totals), then the best reached (mainline > pushed > local > worktree), then
the earliest committed; the others are listed beside it (`RepoCommits.copies`) and counted for
Caveats. With `dedupe="patch-id"` copies must also share `git patch-id --stable`, which splits a
set whose changes differ. A partial clone is never asked for patch-ids — diff-tree would fetch
missing blobs into it — and neither is a clone git fails on: their sets are matched by the key
alone, and counted.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from sijl.collect import Collection, RepoCommits
from sijl.git import GitError, GitRunner
from sijl.models import RawCommit, Reach

Key = tuple[str, datetime, str]


def normalise_subject(subject: str) -> str:
    """Runs of whitespace become one space; case and words still count."""
    return " ".join(subject.split())


def copy_key(commit: RawCommit) -> Key:
    """What a rebase, a cherry-pick and an amend all keep: the email as written (a `.mailmap` may
    exist in one clone only), the author instant (aware datetimes compare across offsets), the
    subject."""
    email = commit.author_email_raw.lower()
    return (email, commit.author_date, normalise_subject(commit.subject))


@dataclass(frozen=True)
class _Copy:
    clone: int  # index into Collection.repos
    commit: RawCommit
    reach: Reach
    counted: bool  # in the totals, not under Not pushed yet


def _preference(copy: _Copy) -> tuple[bool, int, datetime, str]:
    """A counted copy first, then the best reached, then the earliest committed (the original),
    then the SHA, so the choice is stable."""
    return (not copy.counted, -copy.reach, copy.commit.committer_date, copy.commit.sha)


class Projects:
    """Which clones are one project: they share a root commit. Roots are read lazily, one
    `rev-list` per clone, and only for clones that a key spans — or, for squash.py, that a
    squash merge and a commit it may hold sit in."""

    def __init__(self, git: GitRunner, repos: Sequence[RepoCommits]) -> None:
        self._git, self._repos = git, repos
        self._roots: dict[int, frozenset[str]] = {}
        self.unread: set[int] = set()  # clones whose roots git could not read

    def roots(self, clone: int) -> frozenset[str]:
        if clone not in self._roots:
            path = self._repos[clone].repo.path
            try:
                out = self._git.run(path, "rev-list", "--max-parents=0", "--branches", "--remotes")
                self._roots[clone] = frozenset(out.decode().split())
            except GitError:  # roots unknown: this clone is never merged with another
                self._roots[clone] = frozenset()
                self.unread.add(clone)
        return self._roots[clone]

    def same(self, a: int, b: int) -> bool:
        """Are clones `a` and `b` one project? A clone always is itself; two clones are when
        they share a root commit (one whose roots git cannot read never is)."""
        return a == b or bool(self.roots(a) & self.roots(b))

    def split(self, same: list[_Copy]) -> list[list[_Copy]]:
        """`same` grouped by project; one clone is always one project."""
        clones = sorted({m.clone for m in same})
        if len(clones) == 1:
            return [same]
        projects: list[tuple[set[int], set[str]]] = []
        for clone in clones:
            members, roots = {clone}, set(self.roots(clone))
            for project in [p for p in projects if p[1] & roots]:
                projects.remove(project)
                members |= project[0]
                roots |= project[1]
            projects.append((members, roots))
        return [[m for m in same if m.clone in members] for members, _ in projects]


def _config(git: GitRunner, path: Path, pattern: str, *types: str) -> list[str]:
    """The values of the repository's own keys matching `pattern`, as git itself reads them."""
    try:
        out = git.run(path, "config", "--local", "--null", *types, "--get-regexp", pattern)
    except GitError as exc:
        if exc.returncode == 1:  # no such key
            return []
        raise
    return [entry.partition("\n")[2] for entry in out.decode().split("\0") if entry]


def partial_clone(git: GitRunner, path: Path) -> bool:
    """A partial clone fetches missing objects on demand, writing into the repository — with the
    upload-pack program its own config names. git's own test: extensions.partialClone is set, or
    some remote.<name>.promisor is true, read by git's boolean parser (yes, on, 1 and a bare key
    are true too; a value git cannot read raises, and the caller treats the clone as unread).
    Public: squash.py asks it too before reading a squash merge's lines."""
    if _config(git, path, r"^extensions\.partialclone$"):
        return True
    return "true" in _config(git, path, r"^remote\..*\.promisor$", "--type=bool")


def _patch_ids(
    git: GitRunner, repos: Sequence[RepoCommits], wanted: list[_Copy]
) -> dict[str, str | None]:
    """`git patch-id --stable` of each wanted commit: two calls per clone. An empty change has
    the id ""; None means unread — a partial clone, or a clone git fails on."""
    by_clone: dict[int, list[str]] = defaultdict(list)
    for m in wanted:
        by_clone[m.clone].append(m.commit.sha)
    ids: dict[str, str | None] = {}
    for clone, shas in by_clone.items():
        path = repos[clone].repo.path
        # --full-index: a binary change hashes its blob ids, which clones abbreviate differently
        args = ("diff-tree", "--stdin", "-p", "--root", "--full-index", "--no-ext-diff")
        try:
            if partial_clone(git, path):
                raise GitError(("config",), 0, "a partial clone: diff-tree would fetch blobs")
            stdin = ("\n".join(shas) + "\n").encode()
            diffs = git.run(path, *args, "--no-textconv", "--no-color", stdin=stdin)
            out = git.run(path, "patch-id", "--stable", stdin=diffs)
        except GitError:
            ids.update(dict.fromkeys(shas))
            continue
        ids.update(dict.fromkeys(shas, ""))
        for line in out.decode().splitlines():
            patch, _, sha = line.partition(" ")
            ids[sha] = patch
    return ids


def _without(rc: RepoCommits, dropped: set[str], copies: dict[str, tuple[str, ...]]) -> RepoCommits:
    return replace(
        rc,
        commits=tuple(c for c in rc.commits if c.sha not in dropped),
        local_only=tuple(c for c in rc.local_only if c.sha not in dropped),
        reach={sha: level for sha, level in rc.reach.items() if sha not in dropped},
        copies={sha: copies[sha] for sha in rc.reach if sha in copies},
    )


def collapse(collection: Collection, git: GitRunner, *, dedupe: str = "key") -> Collection:
    """The same collection with every set of copies reduced to its best-reached commit."""
    by_key: dict[Key, list[_Copy]] = defaultdict(list)
    for index, rc in enumerate(collection.repos):
        for commit, counted in [
            *((c, True) for c in rc.commits),
            *((c, False) for c in rc.local_only),
        ]:
            by_key[copy_key(commit)].append(_Copy(index, commit, rc.reach[commit.sha], counted))
    projects = Projects(git, collection.repos)
    sets = [p for same in by_key.values() if len(same) > 1 for p in projects.split(same)]
    sets = [s for s in sets if len(s) > 1]

    splits = unread = 0
    if dedupe == "patch-id" and sets:
        ids = _patch_ids(git, collection.repos, [m for s in sets for m in s])
        parted: list[list[_Copy]] = []
        for same in sets:
            if any(ids[m.commit.sha] is None for m in same):  # cannot tell: the key decides
                unread += len(same)
                parted.append(same)
                continue
            by_patch: dict[str | None, list[_Copy]] = defaultdict(list)
            for m in same:
                by_patch[ids[m.commit.sha]].append(m)
            splits += len(by_patch) - 1
            parted += [p for p in by_patch.values() if len(p) > 1]
        sets = parted

    copies: dict[str, tuple[str, ...]] = {}
    for same in sets:
        keep, *rest = sorted(same, key=_preference)
        copies[keep.commit.sha] = tuple(m.commit.sha for m in rest)
    dropped = {sha for shas in copies.values() for sha in shas}
    return replace(
        collection,
        repos=tuple(_without(rc, dropped, copies) for rc in collection.repos),
        copies_collapsed=len(dropped),
        patch_splits=splits,
        dedupe=dedupe,
        patch_unread=unread,
        roots_unread=len(projects.unread),
    )
