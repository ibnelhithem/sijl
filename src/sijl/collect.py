"""Collect your commits in the period, repository by repository (pass 1).

Which history is read: local branches, remote-tracking branches, tags and the main checkout's
HEAD — so work that lives only on a remote, only locally or only under a tag all counts — and
nothing else: never the stash, notes, `refs/original`, `refs/prefetch` or any other namespace,
and never the detached HEAD of a linked worktree unless `include_worktrees` asks for it. One
`git log` per repository reads all of it, with the tips on stdin.

Which commits count: authored by one of your exact emails (identity.py), with an author date
inside the period (window.py). git gets only `--since` (a committer-date prefilter: the
lookback's 90 days before the period, then two days of slack; none at all for a period reaching
before 1970, which git misreads) and never `--until` or `--author`; the real decisions are made
here. Your commits in the lookback are kept apart (`lookback`), never counted: squash.py
links them to the squash merges that hold them.
Merge commits are never counted as commits. They are kept for deliveries (deliveries.py):
your own merges in the period, every merge — anyone's — on a remote's default branch from the
period's first day on (`Mainline`), and every other merge from that day on (`side_merges`), all
found in what pass 1 already read. So are your one-parent commits GitHub committed on those
first-parent chains (`github_commits`), where squash.py finds your squash merges.

How far each commit travelled (`Reach`): mainline (a remote's default branch) >
pushed (any remote-tracking branch) > local > worktree. In a repository with remote-tracking
branches, local and worktree commits are kept apart (`local_only`), outside the totals; in a
repository without any they are counted, as there is nothing to push to. A commit found in
several clones is listed once, in the clone where it is best reached.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from sijl.git import (
    PASS1_FORMAT,
    PEOPLE_FORMAT,
    GitError,
    GitRunner,
    parse_log_z,
    parse_people_z,
    parse_worktrees_z,
)
from sijl.identity import IdentitySet
from sijl.models import (
    GITHUB_WEB_EMAIL,
    Delivery,
    Lines,
    RawCommit,
    Reach,
    Repo,
    Skipped,
    SquashTally,
)
from sijl.window import Window

TIP_NAMESPACES = ("refs/heads", "refs/remotes", "refs/tags")
# How far before the period pass 1 reads: a pull request squash-merged in the
# period may hold branch commits written before it, counted in their own period — found here,
# the squash is a delivery, never code a second time. They are read for linking only.
LOOKBACK = timedelta(days=90)
DEFAULT_BRANCH_FALLBACKS = ("main", "master")  # when a remote has no HEAD (`git remote add`)
_REF_FORMAT = (
    "%(objectname)%00%(objecttype)%00%(*objectname)%00%(*objecttype)%00%(symref)%00%(refname)"
)


@dataclass(frozen=True)
class Tips:
    """The commits history is read from, grouped by what each says about how far work went."""

    kept: tuple[str, ...] = ()  # local branches, tags, the main checkout's detached HEAD
    remotes: tuple[str, ...] = ()  # remote-tracking branches
    mainline: tuple[str, ...] = ()  # each remote's default branch (also in `remotes`)
    mainline_names: tuple[str, ...] = ()  # those branches' names, e.g. "main" (sync merges)
    worktrees: tuple[str, ...] = ()  # detached HEADs of linked worktrees
    tags_ignored: int = 0  # tags that point at a tree, a blob or another tag
    # Each remote-tracking ref and its commit, by ref name: which remote holds what (for links).
    remote_refs: tuple[tuple[str, str], ...] = ()

    def to_read(self, include_worktrees: bool) -> list[str]:
        worktrees = self.worktrees if include_worktrees else ()
        return sorted({*self.kept, *self.remotes, *worktrees})


@dataclass(frozen=True)
class Mainline:
    """The merges on one remote's default branch — its first-parent chain, the line a merge
    button extends — dated (author date) on the period's first day or later, newest first;
    anyone's. Deliveries are read from them."""

    merges: tuple[RawCommit, ...]
    base: str  # the oldest one's first parent: what it reaches was on the mainline before


@dataclass(frozen=True)
class RepoCommits:
    repo: Repo
    commits: tuple[RawCommit, ...]  # yours, not merges, inside the period, counted; newest first
    local_only: tuple[RawCommit, ...] = ()  # yours, on no remote branch although it has some
    reach: Mapping[str, Reach] = field(default_factory=dict)  # sha → reach, for both lists
    has_remote: bool = False  # the repository has remote-tracking branches
    copies: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # kept sha → copies
    remote_refs: tuple[tuple[str, str], ...] = ()  # (refs/remotes/<remote>/<branch>, sha)
    merges: tuple[RawCommit, ...] = ()  # your merge commits in the period, anywhere; newest first
    mainlines: tuple[Mainline, ...] = ()  # one per remote default branch with merges
    # Every other merge from the period's first day on, anyone's, newest first (a request
    # merged into another long-lived branch is delivered once it reaches a mainline).
    side_merges: tuple[RawCommit, ...] = ()
    mainline_names: tuple[str, ...] = ()  # the default branches' names, "main"
    # Your one-parent commits GitHub committed at one instant on a mainline's first-parent chain,
    # from the lookback's first day on, newest first: squash merges and web edits.
    github_commits: tuple[RawCommit, ...] = ()
    # Your commits, merges too, written in the lookback before the period: never counted,
    # only linked to squash merges; the non-merges' reach is in `reach` too.
    lookback: tuple[RawCommit, ...] = ()
    deliveries: tuple[Delivery, ...] = ()  # yours and merged for others; newest first
    delivery_error: str = ""  # git could not read what the merges brought in: git's words
    via: Mapping[str, Delivery] = field(default_factory=dict)  # sha → the squash merge holding it
    # A squash merge counted as code → its lines; None when they could not be read
    lines: Mapping[str, Lines | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Collection:
    repos: tuple[RepoCommits, ...]
    skipped: tuple[Skipped, ...]  # repositories whose history could not be read
    merges_left_out: int  # your merge commits in the period, never counted as commits
    seen_in_other_clones: int  # commits listed once although several clones hold them
    tags_ignored: int = 0
    include_worktrees: bool = False
    copies_collapsed: int = 0  # rebased / cherry-picked copies not counted again (normalize.py)
    patch_splits: int = 0  # commits kept apart by --dedupe patch-id although their key matched
    dedupe: str = "key"
    patch_unread: int = 0  # commits matched by key alone: their patch-id could not be read
    roots_unread: int = 0  # clones whose root commits could not be read (never matched across)
    # Filled by deliveries.find_deliveries(): what your merges were, and what git cannot show.
    merges_delivered: int = 0  # of merges_left_out: listed as deliveries
    merges_sync: int = 0  # of merges_left_out: a mainline (or the same branch) merged in; ignored
    merges_elsewhere: int = 0  # of merges_left_out: a request merged into a branch, not a mainline
    merges_unread: int = 0  # of merges_left_out: on a mainline whose merged commits were not read
    landed_without_request: int = 0  # your counted mainline commits no request merge brought in
    deliveries_in_other_clones: int = 0  # deliveries listed once although several clones hold them
    lookback_start: date | None = None  # the first day read before the period
    squashes: SquashTally = field(default_factory=SquashTally)  # squash.py's counts
    # (name, email) of every author pass 1 read — anyone's, as written and after .mailmap — and
    # of every co-author a `Co-authored-by` trailer names ("" when it gives no address); sorted.
    # Never counted: the people the digest's redactor knows by name.
    authors: tuple[tuple[str, str], ...] = ()

    @property
    def commits(self) -> list[RawCommit]:
        """Every counted commit — the one set the report's name, totals and days come from."""
        return [commit for rc in self.repos for commit in rc.commits]

    @property
    def local_only(self) -> list[RawCommit]:
        """Your commits on no remote branch, kept out of the totals."""
        return [commit for rc in self.repos for commit in rc.local_only]

    @property
    def repos_without_remote(self) -> list[RepoCommits]:
        """Repositories whose commits are counted although nothing shows they were pushed."""
        return [rc for rc in self.repos if not rc.has_remote]


def worktree_heads(git: GitRunner, repo: Repo) -> tuple[str, list[str]]:
    """The main checkout's HEAD if it is detached, and the detached HEADs of linked worktrees.

    A HEAD on a branch is skipped: the branch is already a tip.
    """
    records = parse_worktrees_z(git.run(repo.path, "worktree", "list", "--porcelain", "-z"))
    detached = [r.get("HEAD", "") if "detached" in r else "" for r in records]
    detached = [sha if sha.strip("0") else "" for sha in detached]  # all zeros: nothing yet
    main = detached[0] if detached else ""
    return main, [sha for sha in detached[1:] if sha]


def ref_tips(git: GitRunner, repo: Repo) -> Tips:
    """Branches, remote-tracking branches and tags (peeled to their commit), plus HEADs."""
    out = git.run(repo.path, "for-each-ref", f"--format={_REF_FORMAT}", *TIP_NAMESPACES)
    kept: set[str] = set()
    remotes: set[str] = set()
    by_name: dict[str, str] = {}
    default_ref: dict[str, str] = {}  # "refs/remotes/origin" → the ref origin/HEAD points at
    ignored = 0
    for line in out.decode().splitlines():
        sha, kind, peeled, peeled_kind, symref, name = line.split("\0")
        if symref:  # origin/HEAD only names the default branch, which is listed itself
            if name.startswith("refs/remotes/") and name.endswith("/HEAD"):
                default_ref[name.removesuffix("/HEAD")] = symref
            continue
        commit = sha if kind == "commit" else peeled if peeled_kind == "commit" else ""
        if not commit:
            ignored += 1
            continue
        by_name[name] = commit
        (remotes if name.startswith("refs/remotes/") else kept).add(commit)

    mainline: set[str] = set()
    names: set[str] = set()
    prefixes = {"/".join(n.split("/")[:3]) for n in by_name if n.startswith("refs/remotes/")}
    for prefix in sorted(prefixes):
        fallbacks = [f"{prefix}/{branch}" for branch in DEFAULT_BRANCH_FALLBACKS]
        for ref in [default_ref.get(prefix, ""), *fallbacks]:
            if ref in by_name:
                mainline.add(by_name[ref])
                names.add(ref.removeprefix(f"{prefix}/"))
                break

    main_head, linked = worktree_heads(git, repo)
    if main_head:
        kept.add(main_head)
    return Tips(
        kept=tuple(sorted(kept)),
        remotes=tuple(sorted(remotes)),
        mainline=tuple(sorted(mainline)),
        mainline_names=tuple(sorted(names)),
        worktrees=tuple(sorted(set(linked))),
        tags_ignored=ignored,
        remote_refs=tuple(
            sorted((n, c) for n, c in by_name.items() if n.startswith("refs/remotes/"))
        ),
    )


def read_window(git: GitRunner, repo: Repo, tips: Sequence[str], window: Window) -> list[RawCommit]:
    if not tips:
        return []  # a repository without branches yet
    since = window.git_since()  # None before 1970: git misreads it, so read all history
    out = git.run(
        repo.path,
        "log",
        "--stdin",
        "-z",
        "--no-color",
        "--no-decorate",
        *([f"--since={since}"] if since else []),
        f"--format={PASS1_FORMAT}",
        stdin=("\n".join(tips) + "\n").encode(),
    )
    return parse_log_z(out)


def mainline_merges(
    history: Sequence[RawCommit], heads: Sequence[str], window: Window
) -> tuple[Mainline, ...]:
    """The merges on each default branch's first-parent chain, from the period's first day on,
    read from pass 1's history (all authors): each chain is followed from its tip through first
    parents until it leaves what pass 1 read. Tips with the same merges give one `Mainline`."""
    by_sha = {commit.sha: commit for commit in history}
    found: dict[tuple[str, ...], Mainline] = {}
    for head in heads:
        merges, sha = [], head
        while sha in by_sha:
            commit = by_sha[sha]
            if commit.is_merge and commit.author_day >= window.start:
                merges.append(commit)
            sha = commit.parents[0] if commit.parents else ""
        if merges:
            key = tuple(m.sha for m in merges)
            found.setdefault(key, Mainline(tuple(merges), merges[-1].parents[0]))
    return tuple(found.values())


def github_commits(
    history: Sequence[RawCommit], heads: Sequence[str], since: date, identities: IdentitySet
) -> tuple[RawCommit, ...]:
    """Your one-parent commits that GitHub committed (`noreply@github.com`) at one instant on each
    default branch's first-parent chain — where "Squash and merge" puts them — dated on `since`
    (the lookback's first day) or later, newest first, from pass 1's history. Later ones
    too: a branch written in the period may be squash-merged after it; and earlier ones, which
    hold the commits written in the lookback. A chain is followed until it leaves what pass 1
    read or meets a commit another chain already passed."""
    by_sha = {commit.sha: commit for commit in history}
    seen: set[str] = set()
    found: list[RawCommit] = []
    for head in heads:
        sha = head
        while sha in by_sha and sha not in seen:
            seen.add(sha)
            commit = by_sha[sha]
            if (
                len(commit.parents) == 1
                and commit.committer_email.casefold() == GITHUB_WEB_EMAIL
                and commit.author_date == commit.committer_date  # stamped at merge time
                and commit.author_day >= since
                and identities.matches(commit)
            ):
                found.append(commit)
            sha = commit.parents[0] if commit.parents else ""
    return tuple(sorted(found, key=lambda c: (c.author_date, c.sha), reverse=True))


def authors_of(history: Iterable[RawCommit]) -> set[tuple[str, str]]:
    """(name, email) of each commit's author, as written and after .mailmap, and of each person a
    `Co-authored-by: Name <address>` trailer names — the address "" when it has none."""
    found: set[tuple[str, str]] = set()
    for commit in history:
        found.add((commit.author_name_raw, commit.author_email_raw))
        found.add((commit.author_name, commit.author_email))
        for key, value in commit.trailers:
            if key == "co-authored-by":
                name, _, rest = value.partition("<")
                found.add((name.strip(), rest.partition(">")[0].strip()))
    return found


def read_people(git: GitRunner, repo: Repo) -> set[tuple[str, str]]:
    """Everyone in the repository's whole history — every ref, any date, anyone's — for the
    digest's redactor: authors and committers as written and after .mailmap, and every
    person a trailer names (`git.parse_people_z()`). One `git log`; commit objects only, so a
    partial clone fetches nothing. Never counted."""
    out = git.run(
        repo.path, "log", "--all", "-z", "--no-color", "--no-decorate", f"--format={PEOPLE_FORMAT}"
    )
    return parse_people_z(out)


def not_reached_from(
    git: GitRunner, repo: Repo, shas: Sequence[str], excluded: Sequence[str]
) -> set[str]:
    """Which of `shas` no `excluded` tip reaches: `rev-list` from them, with those tips negated."""
    if not excluded:
        return set(shas)
    lines = [*shas, *(f"^{tip}" for tip in excluded)]
    out = git.run(repo.path, "rev-list", "--stdin", stdin=("\n".join(lines) + "\n").encode())
    return set(out.decode().split()) & set(shas)


def reach_of(
    git: GitRunner, repo: Repo, tips: Tips, shas: Sequence[str], include_worktrees: bool
) -> dict[str, Reach]:
    """How far each commit travelled — at most three `rev-list` calls per repository."""
    if not shas:
        return {}
    not_pushed = not_reached_from(git, repo, shas, tips.remotes)
    not_mainline = not_reached_from(git, repo, shas, tips.mainline)
    worktree_only: set[str] = set()
    if include_worktrees and tips.worktrees and not_pushed:
        worktree_only = not_reached_from(git, repo, sorted(not_pushed), tips.to_read(False))
    levels = {}
    for sha in shas:
        if sha not in not_mainline:
            levels[sha] = Reach.MAINLINE
        elif sha not in not_pushed:
            levels[sha] = Reach.PUSHED
        elif sha in worktree_only:
            levels[sha] = Reach.WORKTREE
        else:
            levels[sha] = Reach.LOCAL
    return levels


@dataclass
class _Read:
    repo: Repo
    has_remote: bool
    mine: list[RawCommit]
    reach: dict[str, Reach]
    tips: Tips
    merges: list[RawCommit]  # yours, in the period
    mainlines: tuple[Mainline, ...]
    side_merges: tuple[RawCommit, ...]
    github_commits: tuple[RawCommit, ...]
    lookback: tuple[RawCommit, ...]


def collect(
    repos: Sequence[Repo],
    identities: IdentitySet,
    window: Window,
    git: GitRunner,
    *,
    include_worktrees: bool = False,
    lookback: timedelta = LOOKBACK,
) -> Collection:
    first = window.start - lookback  # pass 1 reads from here: the period and its lookback
    read: list[_Read] = []
    skipped: list[Skipped] = []
    merges: set[str] = set()  # a merge in two clones is one merge
    tags_ignored = 0
    people: set[tuple[str, str]] = set()  # every author and co-author read
    for repo in repos:
        try:
            tips = ref_tips(git, repo)
            reading = replace(window, start=first)
            history = read_window(git, repo, tips.to_read(include_worktrees), reading)
            yours = [c for c in history if identities.matches(c) and window.includes(c.author_day)]
            mine = [c for c in yours if not c.is_merge]
            before = [
                c for c in history if identities.matches(c) and first <= c.author_day < window.start
            ]
            earlier = [c.sha for c in before if not c.is_merge]
            reach = reach_of(git, repo, tips, [c.sha for c in mine] + earlier, include_worktrees)
            mainlines = mainline_merges(history, tips.mainline, window)
            on_chain = {m.sha for mainline in mainlines for m in mainline.merges}
            side = [
                c
                for c in history
                if c.is_merge and c.author_day >= window.start and c.sha not in on_chain
            ]
            side.sort(key=lambda c: c.author_date, reverse=True)
            github = github_commits(history, tips.mainline, first, identities)
        except GitError as exc:  # includes a timeout: one hung repository never stops the rest
            skipped.append(Skipped(repo.path, f"history could not be read: {exc.summary}"))
            continue
        except ValueError as exc:  # a record git printed that the parser cannot trust
            skipped.append(Skipped(repo.path, f"history could not be parsed: {exc}"))
            continue
        tags_ignored += tips.tags_ignored
        people.update(authors_of(history))
        own_merges = sorted((c for c in yours if c.is_merge), key=lambda c: c.author_date)[::-1]
        merges.update(c.sha for c in own_merges)
        read.append(
            _Read(
                repo,
                bool(tips.remotes),
                mine,
                reach,
                tips,
                own_merges,
                mainlines,
                tuple(side),
                github,
                tuple(sorted(before, key=lambda c: c.author_date, reverse=True)),
            )
        )

    owner: dict[str, int] = {}  # sha → the clone where it is best reached (the first on a tie)
    for index, r in enumerate(read):
        for commit in r.mine:
            best = owner.get(commit.sha)
            if best is None or r.reach[commit.sha] > read[best].reach[commit.sha]:
                owner[commit.sha] = index
    copies = sum(len(r.mine) for r in read)

    found: list[RepoCommits] = []
    for index, r in enumerate(read):
        own = [c for c in r.mine if owner[c.sha] == index]
        own.sort(key=lambda c: c.author_date, reverse=True)
        apart = {c.sha for c in own if r.has_remote and r.reach[c.sha] < Reach.PUSHED}
        found.append(
            RepoCommits(
                repo=r.repo,
                commits=tuple(c for c in own if c.sha not in apart),
                local_only=tuple(c for c in own if c.sha in apart),
                reach={c.sha: r.reach[c.sha] for c in (*own, *r.lookback) if c.sha in r.reach},
                has_remote=r.has_remote,
                remote_refs=r.tips.remote_refs,
                merges=tuple(r.merges),
                mainlines=r.mainlines,
                side_merges=r.side_merges,
                mainline_names=r.tips.mainline_names,
                github_commits=r.github_commits,
                lookback=r.lookback,
            )
        )
    return Collection(
        repos=tuple(found),
        skipped=tuple(skipped),
        merges_left_out=len(merges),
        seen_in_other_clones=copies - len(owner),
        tags_ignored=tags_ignored,
        include_worktrees=include_worktrees,
        lookback_start=first,
        authors=tuple(sorted(people)),
    )
