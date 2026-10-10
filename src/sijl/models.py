"""The data Sijl passes between its stages. Frozen dataclasses, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum
from pathlib import Path

DEDUPE_MODES = ("key", "patch-id")  # how copies of one commit are matched
# A host name or an ssh alias, as remotes and the forge settings may name one:
# letters, digits, dots, hyphens, underscores — never a leading "-", which ssh would read as an
# option, and nothing else (no spaces, "=", "/", "%", "@", brackets or controls). Use fullmatch.
HOST_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}")
# The forges links are built for and merge requests / pull requests are read from.
GITHUB, GITLAB = "github", "gitlab"
# The committer of every commit GitHub's website writes: web merges, squash merges, web edits.
GITHUB_WEB_EMAIL = "noreply@github.com"
# A delivery's role: it merged commits you wrote, you merged it, or both.
AUTHOR, MERGER, BOTH = "author", "merger", "both"
# What a GitHub squash merge of yours delivered: linked to branch commits that are
# counted instead; counted as code itself, its branch commits gone; or counted as code itself
# although it could not be linked and commits of yours may be its own (never taken out: work
# that may be counted twice is disclosed, work taken out could be lost).
LINKED, PROMOTED, UNLINKED = "linked", "promoted", "unlinked"
# A group, project or owner name in a project path: what GitHub and GitLab allow, no more.
PATH_SEGMENT = re.compile(r"[A-Za-z0-9_.~+-]+")


class Reach(IntEnum):
    """How far a commit travelled; a higher value is better reached (of copies, the best one is
    kept)."""

    WORKTREE = 1  # only in a detached linked worktree (read with --include-worktrees)
    LOCAL = 2  # on a local branch, a tag or the main checkout, but on no remote branch
    PUSHED = 3  # on a remote-tracking branch
    MAINLINE = 4  # on a remote's default branch


@dataclass(frozen=True)
class RawCommit:
    """One commit exactly as `git log` reported it (pass 1)."""

    sha: str
    parents: tuple[str, ...]
    tree: str
    author_name_raw: str  # %an — as written in the commit
    author_email_raw: str  # %ae
    author_name: str  # %aN — after the repository's .mailmap
    author_email: str  # %aE
    author_date: datetime  # %aI — aware, in the author's own UTC offset
    committer_name: str
    committer_email: str
    committer_date: datetime  # %cI — reset by every rebase or cherry-pick
    trailers: tuple[tuple[str, str], ...]  # (lower-cased key, value)
    subject: str
    body: str  # the full message: subject, blank line, body

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    @property
    def author_day(self) -> date:
        """The calendar day the author wrote this, in their own timezone.

        `.date()` of an aware datetime keeps its own offset: 23:30 on 31 March in New York stays
        31 March although it is already 1 April in UTC. Every stage uses this one rule.
        """
        return self.author_date.date()


@dataclass(frozen=True)
class GraphNode:
    """One commit as deliveries walk the history: its parents and its author's emails."""

    sha: str
    parents: tuple[str, ...]
    author_email_raw: str  # %ae
    author_email: str  # %aE — after the repository's .mailmap


@dataclass(frozen=True)
class Lines:
    """The lines one commit changed against its parent, as `git log --numstat` counts them:
    a renamed file only by what changed in it; a binary file has no lines and is counted apart."""

    added: int
    removed: int
    binary: int = 0  # files git reports as binary


@dataclass(frozen=True)
class Delivery:
    """A merge request (GitLab) or pull request (GitHub) merged into a mainline, read from its
    merge commit. Yours when it merged a commit you wrote, whoever merged it;
    merged for others when you merged it and wrote none of its commits."""

    forge: str  # GITLAB or GITHUB: the shape of the merge commit's message
    number: int
    title: str  # line 3 of the message; "" when there is none
    branch: str  # the source branch the subject names; "" when it names none
    project: tuple[str, ...]  # GitLab: the project `See merge request` names; () otherwise
    merge: RawCommit  # the merge commit: its author merged it, its author date is the date
    role: str  # AUTHOR, MERGER or BOTH
    commits: int  # the commits it merged (merge^1..merge^2), merge commits not counted
    yours: int  # of those, the ones you wrote
    tickets: frozenset[str] = frozenset()  # keys in its branch, title and message (`tickets_of`)
    squash: str = ""  # a GitHub squash merge: LINKED, PROMOTED or UNLINKED; "" for a merge
    lines: Lines | None = None  # a counted squash's lines (PROMOTED, UNLINKED); None: not read
    # The SHAs of the commits `yours` counts (the digest lists them under the request): the
    # ones of yours it merged, whenever written; a squash's linked commits, or the squash itself
    # when it is counted as code. Empty for a request merged for others.
    your_commits: frozenset[str] = frozenset()

    @property
    def merged_on(self) -> date:
        """The day it was merged: the merge commit's author day, in the merger's timezone."""
        return self.merge.author_day

    @property
    def ref(self) -> str:
        """How the forge writes it: `!42` on GitLab, `#12` on GitHub."""
        return f"#{self.number}" if self.forge == GITHUB else f"!{self.number}"

    @property
    def is_yours(self) -> bool:
        return self.role in (AUTHOR, BOTH)


@dataclass(frozen=True)
class SquashTally:
    """What squash.py found and did, for the report's Caveats. `found` splits into
    `linked`, `promoted` and `unlinked`."""

    found: int = 0  # your squash merges on a mainline in the period: listed under Delivered
    linked: int = 0  # linked to commits of yours, which are counted; the squash is not
    promoted: int = 0  # nothing to link and nothing that may be its own: counted as code
    unlinked: int = 0  # nothing to link, but commits of yours may be its own: counted as code too
    unlinked_pushed: int = 0  # those commits, pushed (counted): the same work may count twice
    unlinked_other: int = 0  # those not counted here: only on your laptop, or before the period
    lookback_limited: int = 0  # promoted or unlinked with no earlier delivery within the lookback
    linked_earlier: int = 0  # linked ones holding commits written in the lookback before the period
    commits_linked: int = 0  # your commits in the period linked to a squash merge, whenever merged
    by_tree: int = 0  # of commits_linked: a branch tip with the squash's tree
    by_subject: int = 0  # its title or a `* ` item of its message
    by_branch: int = 0  # below a tree-linked tip, on its branch
    by_ticket: int = 0  # a ticket key both name (only with an extractor)
    relabelled: int = 0  # of commits_linked: on no remote branch, counted as delivered via it
    after_period: int = 0  # squash merges after the period that linked commits written in it
    lines_unread: int = 0  # promoted squashes whose lines could not be read
    off_mainline: int = 0  # squash-shaped commits of yours on no mainline's line: plain commits
    roots_unread: int = 0  # clones whose roots git could not read: linked within themselves only


@dataclass(frozen=True)
class Worktree:
    """A linked worktree of a repository, as `git worktree list` reports it."""

    path: Path
    head: str  # the commit checked out there; "" before its first commit
    branch: str  # "feature/x", or "" when its HEAD is detached
    locked: bool = False  # `git worktree lock`: kept even when its folder is unreachable
    prunable: bool = False  # its folder is gone; git would remove the record on `worktree prune`


@dataclass(frozen=True)
class Repo:
    """A repository found on disk, once, however many worktrees or paths lead to it."""

    name: str
    path: Path  # the main worktree, or the bare repository itself
    common_dir: Path  # resolved `git rev-parse --git-common-dir`: the identity of the repository
    bare: bool
    superproject: Path | None = None  # set for a submodule found through its superproject
    worktrees: tuple[Worktree, ...] = ()  # linked ones, when discover(with_worktrees=True)


@dataclass(frozen=True)
class Skipped:
    """A path that was not scanned, and why — always shown under the report's Caveats."""

    path: Path
    reason: str
