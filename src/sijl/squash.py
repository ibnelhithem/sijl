"""GitHub squash merges: never double-count, never lose work.

GitHub's "Squash and merge" turns a pull request's branch into one new commit on the mainline —
one parent, committed by GitHub at the instant it is authored, its subject the title and ` (#N)`
(`deliveries.parse_squash`). It is authored by the pull request's author, so a squash of yours is
counted as your commit; and the branch commits it squashed are often still here — on the remote
branch, or only on your laptop once GitHub deleted that — counted too, or listed under *Not pushed
yet*. One piece of work, counted twice, or delivered work that looks unfinished.

So each squash merge of yours on a mainline (`RepoCommits.github_commits`) is a delivery, and
`link()` looks for the commits of yours it squashed — on no mainline, written in the period or in
the lookback before it (`RepoCommits.lookback`), in any clone of the same project (clones sharing a
root commit, `normalize.Projects`) — by four signals, strongest first:

1. **tree**: the squash's tree is exactly a branch tip's tree when the branch was up to date;
2. **subject**: the commit's subject is the squash's title (GitHub's default for a one-commit pull
   request) or one of the `* subject` items GitHub lists in its default description — each line
   names one commit: a pushed one before one only on your laptop, then the latest written before
   the squash;
3. **branch**: below a tree-linked tip, the commits of yours on its branch are the pull request's
   too — down to a commit another squash took, so a branch stacked on one squashed earlier never
   takes what that squash's own message names;
4. **ticket**: a ticket key both name (`tickets_of`: `tickets.ticket_finder()` with Jira projects).

A commit is never in a squash merged before it was written. Then, per squash in the period:

- **linked** — commits were linked: they are the code; the squash is not counted again, and the
  linked ones only on your laptop are counted, delivered via the pull request (`RepoCommits.via`) —
  also when it was merged after their period (that keeps adjacent periods adding up);
- **promoted** — nothing linked, and none of your commits since your previous delivery (in any
  clone of the project, bounded by the lookback) is left unlinked: its branch commits are gone, so
  the squash itself is counted as code, with its lines (`git log --numstat`, one call per
  repository, never in a partial clone);
- **unlinked** — nothing linked, but such commits exist and may be its own. It is still counted as
  code, with its lines: they may as well be another branch's, and taking the squash out could
  lose the work. Caveats count them, pushed (the same work may count twice) and not.

A squash leaves the totals only when commits linked to it are counted instead.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from sijl.collect import Collection, RepoCommits
from sijl.deliveries import Request, TicketsOf, no_tickets, parse_squash
from sijl.git import NUMSTAT_ARGS, GitError, GitRunner, parse_numstat
from sijl.models import (
    AUTHOR,
    GITHUB,
    LINKED,
    PROMOTED,
    UNLINKED,
    Delivery,
    Lines,
    RawCommit,
    Reach,
    SquashTally,
)
from sijl.normalize import Projects, normalise_subject, partial_clone
from sijl.window import Window

# How a commit was linked, strongest first: a branch tip with the squash's tree; its subject named
# in the squash's message; below a tip on its branch; a ticket key both name.
TREE, SUBJECT, BRANCH, TICKET = "tree", "subject", "branch", "ticket"
_ITEM = "* "  # GitHub's default squash description lists each commit as `* <its message>`


@dataclass
class _Squash:
    """One squash merge of yours, and what was linked to it."""

    commit: RawCommit
    request: Request
    clones: set[int] = field(default_factory=set)  # clones whose mainline's line holds it
    # Subjects its message names, each as often as it may still link a commit: the title once,
    # a `* ` item as often as GitHub listed it.
    names: Counter[str] = field(default_factory=Counter)
    tickets: frozenset[str] = frozenset()
    linked: list[str] = field(default_factory=list)  # SHAs of the commits linked to it

    @property
    def when(self) -> datetime:
        return self.commit.author_date


@dataclass(frozen=True)
class _Candidate:
    """A commit of yours on no mainline: it may be a squashed branch's."""

    clone: int
    commit: RawCommit
    counted: bool  # in the totals (on a remote branch); False: Not pushed yet, or before the period
    reach: Reach = Reach.LOCAL
    in_period: bool = True  # False: written in the lookback before the period, linked only

    @property
    def pushed(self) -> bool:
        return self.reach >= Reach.PUSHED


class _Pool:
    """Candidates with one subject in one clone, pushed or not, oldest first. `latest(when)` is
    the latest one not taken yet written no later than `when`: a bisection, then a disjoint-set
    jump over the ones taken ("the free one at or before"), so each look-up is near constant."""

    def __init__(self, items: Iterable[_Candidate]) -> None:
        self.items = sorted(items, key=lambda c: (c.commit.author_date, c.commit.sha))
        self._dates = [c.commit.author_date for c in self.items]
        self._free = list(range(len(self.items)))  # i, or a lower index when i was taken
        self._index = {c.commit.sha: i for i, c in enumerate(self.items)}

    def _free_at_or_before(self, i: int) -> int:
        root = i
        while root >= 0 and self._free[root] != root:
            root = self._free[root]
        while i >= 0 and self._free[i] != i:  # path compression
            self._free[i], i = root, self._free[i]
        return root

    def latest(self, when: datetime) -> _Candidate | None:
        i = self._free_at_or_before(bisect_right(self._dates, when) - 1)
        return self.items[i] if i >= 0 else None

    def take(self, sha: str) -> None:
        i = self._index[sha]
        self._free[i] = i - 1


def message_names(commit: RawCommit, title: str) -> Counter[str]:
    """The subjects a squash's message names: its title once, and each `* ` item at the start of a
    line as often as GitHub listed it — whitespace normalised, as copies are matched."""
    names: Counter[str] = Counter()
    for line in commit.body.split("\n")[1:]:
        if line.startswith(_ITEM):
            names[normalise_subject(line[len(_ITEM) :])] += 1
    key = normalise_subject(title)
    names[key] = max(names[key], 1)
    return names


def link(
    squashes: Sequence[_Squash],
    candidates: Sequence[_Candidate],
    merges: Mapping[str, tuple[str, ...]],
    tickets_of: TicketsOf = no_tickets,
    same: Callable[[int, int], bool] = lambda a, b: a == b,
) -> dict[str, str]:
    """Link candidates to squashes — tree, subject, branch, ticket — and return how each linked
    commit was found (sha → TREE, SUBJECT, BRANCH or TICKET); each squash's `linked` lists its
    commits. A commit links to one squash at most, merged no earlier than it was written, in a
    clone of the same project (`same(clone, clone)`).

    For a subject, each squash — oldest first — takes, per line naming it, the commit a pull
    request most likely held: a **pushed** one before one only on your laptop, then the
    **latest** written before the squash (the oldest could be an abandoned local commit).

    `merges` maps your merge commits to their parents. The branch below a tree-linked tip is
    walked through candidates and your merges (a sync merge inside it) and ends at anything else —
    a colleague's commit, a commit already on a mainline — or at a commit linked to another
    squash: on a branch stacked on one squashed earlier, what that squash's own message names
    stays its own. That is why the walk comes after the subjects, not with the tip."""
    by_sha = {c.commit.sha: c for c in candidates}
    how: dict[str, str] = {}
    holder: dict[str, _Squash] = {}
    grouped: dict[tuple[int, str, bool], list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        name = normalise_subject(candidate.commit.subject)
        grouped[(candidate.clone, name, candidate.pushed)].append(candidate)
    pools = {key: _Pool(items) for key, items in grouped.items()}
    pool_of = {c.commit.sha: pool for pool in pools.values() for c in pool.items}
    clones_naming: dict[tuple[str, bool], list[int]] = defaultdict(list)
    for clone, name, pushed in sorted(pools):
        clones_naming[(name, pushed)].append(clone)

    def near(clone: int, squash: _Squash) -> bool:
        return any(same(clone, other) for other in sorted(squash.clones))

    def take(candidate: _Candidate, squash: _Squash, rank: str) -> None:
        how[candidate.commit.sha] = rank
        holder[candidate.commit.sha] = squash
        squash.linked.append(candidate.commit.sha)
        pool_of[candidate.commit.sha].take(candidate.commit.sha)
        name = normalise_subject(candidate.commit.subject)
        if squash.names[name] > 0:
            squash.names[name] -= 1  # a line names one commit

    def fits(candidate: _Candidate, squash: _Squash) -> bool:
        return (
            candidate.commit.sha not in how
            and candidate.commit.author_date <= squash.when
            and near(candidate.clone, squash)
        )

    oldest_first = sorted(squashes, key=lambda s: (s.when, s.commit.sha))
    tips: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        tips[candidate.commit.tree].append(candidate)
    linked_tips: list[tuple[_Squash, _Candidate]] = []
    for squash in oldest_first:  # 1. a branch tip with the squash's tree
        for tip in tips.get(squash.commit.tree, ()):
            if fits(tip, squash):
                take(tip, squash, TREE)
                linked_tips.append((squash, tip))

    for squash in oldest_first:  # 2. the subject: the title or a listed item, per line
        for name in list(squash.names):
            while squash.names[name] > 0:
                best = None
                for pushed in (True, False):  # a pushed commit before an unpushed one
                    found = [
                        pools[(clone, name, pushed)].latest(squash.when)
                        for clone in clones_naming.get((name, pushed), ())
                        if near(clone, squash)
                    ]
                    found = [c for c in found if c is not None]
                    if found:
                        best = max(found, key=lambda c: (c.commit.author_date, c.commit.sha))
                        break
                if best is None:
                    break
                take(best, squash, SUBJECT)

    for squash, tip in linked_tips:  # 3. below a tree-linked tip, on its branch
        stack, seen = list(tip.commit.parents), set()
        while stack:
            sha = stack.pop()
            if sha in seen or holder.get(sha, squash) is not squash:
                continue  # walked already, or another squash's: the branch below is that one's
            seen.add(sha)
            found = by_sha.get(sha)
            if found is not None:
                if fits(found, squash):
                    take(found, squash, BRANCH)
                stack.extend(found.commit.parents)
            elif sha in merges:
                stack.extend(merges[sha])

    naming: dict[str, list[_Squash]] = defaultdict(list)  # 4. a ticket key both name
    for squash in oldest_first:
        for key in squash.tickets:
            naming[key].append(squash)
    if naming:
        for candidate in sorted(candidates, key=lambda c: (c.commit.author_date, c.commit.sha)):
            if candidate.commit.sha in how:
                continue
            keys = frozenset(tickets_of(candidate.commit.body))
            named = {id(s): s for key in sorted(keys) for s in naming.get(key, ())}.values()
            for squash in sorted(named, key=lambda s: (s.when, s.commit.sha)):
                if fits(candidate, squash):
                    take(candidate, squash, TICKET)
                    break
    return how


def read_lines(git: GitRunner, path: Path, shas: Iterable[str]) -> dict[str, Lines] | None:
    """Each commit's lines against its parent: one `git log --numstat` for all of them. None when
    they cannot be read: a partial clone — reading changes would fetch missing file contents into
    it, through the upload-pack program its own config names — or an error from git."""
    try:
        if partial_clone(git, path):
            return None
        stdin = "".join(f"{sha}\n" for sha in shas).encode()
        return parse_numstat(git.run(path, *NUMSTAT_ARGS, stdin=stdin))
    except (GitError, ValueError):
        return None


def link_squashes(
    collection: Collection,
    window: Window,
    git: GitRunner,
    *,
    tickets_of: TicketsOf = no_tickets,
) -> Collection:
    """The collection with your squash merges linked, promoted or left unlinked (see the module's
    docstring): their deliveries listed — each once, in the first clone holding it — commits moved
    in and out of the totals, `via` and `lines` filled, and the counts Caveats need."""
    repos = collection.repos
    squashes: dict[str, _Squash] = {}
    for index, rc in enumerate(repos):
        for commit in rc.github_commits:
            request = parse_squash(commit)
            if request is None:
                continue  # a web edit, or an issue reference GitHub committed
            if commit.sha not in squashes:
                names = message_names(commit, request.title)
                squashes[commit.sha] = _Squash(
                    commit, request, names=names, tickets=frozenset(tickets_of(commit.body))
                )
            squashes[commit.sha].clones.add(index)
    off_mainline = len(
        {
            c.sha
            for rc in repos
            for c in (*rc.commits, *rc.local_only)
            if c.sha not in squashes and parse_squash(c) is not None
        }
    )
    if not squashes:
        return replace(collection, squashes=SquashTally(off_mainline=off_mainline))

    candidates = [
        _Candidate(index, commit, counted, rc.reach[commit.sha])
        for index, rc in enumerate(repos)
        for commits, counted in ((rc.commits, True), (rc.local_only, False))
        for commit in commits
        if rc.reach[commit.sha] < Reach.MAINLINE and commit.sha not in squashes
    ]
    earlier: dict[str, _Candidate] = {}  # the lookback's, each once: in its best-reached clone
    for index, rc in enumerate(repos):
        for commit in rc.lookback:
            level = rc.reach.get(commit.sha, Reach.MAINLINE)
            if commit.is_merge or level >= Reach.MAINLINE or commit.sha in squashes:
                continue
            if commit.sha not in earlier or level > earlier[commit.sha].reach:
                earlier[commit.sha] = _Candidate(index, commit, False, level, in_period=False)
    candidates += earlier.values()
    merges = {m.sha: m.parents for rc in repos for m in (*rc.merges, *rc.lookback) if m.is_merge}
    projects = Projects(git, repos)  # clones sharing a root commit: one project
    known: dict[tuple[int, int], bool] = {}

    def same(a: int, b: int) -> bool:
        if (a, b) not in known:
            known[(a, b)] = known[(b, a)] = projects.same(a, b)
        return known[(a, b)]

    how = link(list(squashes.values()), candidates, merges, tickets_of, same)
    holder = {sha: squash.commit.sha for squash in squashes.values() for sha in squash.linked}

    # The outcome of each squash in the period. Per project (the set of clones a squash's clones
    # are one project with), your deliveries' dates and the unlinked commits are sorted once, so
    # "the previous delivery" and "unlinked since then" are two bisections.
    dates: dict[int, list[datetime]] = defaultdict(list)
    for index, rc in enumerate(repos):
        dates[index] += [d.merge.author_date for d in rc.deliveries if d.is_yours]
    for squash in squashes.values():
        for index in squash.clones:
            dates[index].append(squash.when)
    unlinked = [c for c in candidates if c.commit.sha not in how]
    involved = sorted({*dates, *(c.clone for c in unlinked)})
    projects_of: dict[tuple[int, ...], tuple[list[datetime], list[datetime], list[_Candidate]]] = {}

    def project(squash: _Squash) -> tuple[list[datetime], list[datetime], list[_Candidate]]:
        key = tuple(sorted(squash.clones))
        if key not in projects_of:
            members = {i for i in involved if any(same(i, k) for k in key)}
            held = sorted(
                (c for c in unlinked if c.clone in members),
                key=lambda c: (c.commit.author_date, c.commit.sha),
            )
            projects_of[key] = (
                sorted(d for i in members for d in dates[i]),
                [c.commit.author_date for c in held],
                held,
            )
        return projects_of[key]

    outcome: dict[str, str] = {}
    doubtful: dict[str, _Candidate] = {}  # unlinked commits an unlinked squash may hold
    limited = 0  # squashes with no earlier delivery of yours within the lookback
    for sha, squash in squashes.items():
        if squash.linked:
            outcome[sha] = LINKED
        elif window.includes(squash.commit.author_day):
            # Your previous delivery: in every clone of the project. Its branch
            # commits are gone only when none of yours since then — pushed or not, in the period
            # or in the lookback — is left unlinked.
            delivered, written, held = project(squash)
            before = bisect_left(delivered, squash.when)
            previous = delivered[before - 1] if before else None
            low = bisect_right(written, previous) if previous is not None else 0
            doubt = held[low : bisect_right(written, squash.when)]
            outcome[sha] = UNLINKED if doubt else PROMOTED
            doubtful |= {c.commit.sha: c for c in doubt}
            limited += previous is None

    # Lines of the squashes counted as code: one read per clone that counts them.
    owner = {c.sha: index for index, rc in enumerate(repos) for c in rc.commits}
    wanted: dict[int, list[str]] = defaultdict(list)
    for sha, status in outcome.items():
        if status != LINKED and sha in owner:
            wanted[owner[sha]].append(sha)
    lines: dict[str, Lines] = {}
    for index, shas in wanted.items():
        lines |= read_lines(git, repos[index].repo.path, shas) or {}

    deliveries = {sha: _delivery(squashes[sha], outcome[sha], lines, tickets_of) for sha in outcome}
    listed: set[str] = set()
    in_other_clones = 0
    found: list[RepoCommits] = []
    for index, rc in enumerate(repos):
        mine = [
            deliveries[sha]
            for sha in outcome
            if index in squashes[sha].clones and window.includes(squashes[sha].commit.author_day)
        ]
        new = [d for d in mine if d.merge.sha not in listed]
        in_other_clones += len(mine) - len(new)
        listed |= {d.merge.sha for d in new}
        out = {sha for sha, status in outcome.items() if status == LINKED}  # never lose work
        moved = [c for c in rc.local_only if c.sha in how]
        commits = sorted(  # newest first, as collect lists them; stable on a tie
            [*(c for c in rc.commits if c.sha not in out), *moved],
            key=lambda c: c.author_date,
            reverse=True,
        )
        via = {
            c.sha: deliveries[holder[c.sha]]
            for c in (*rc.commits, *rc.local_only)
            if c.sha in holder
        }
        found.append(
            replace(
                rc,
                commits=tuple(commits),
                local_only=tuple(c for c in rc.local_only if c.sha not in how),
                deliveries=tuple(
                    sorted(
                        [*rc.deliveries, *new],
                        key=lambda d: (d.merge.author_date, d.merge.sha),
                        reverse=True,
                    )
                ),
                via=via,
                lines={
                    sha: lines.get(sha)
                    for sha, status in outcome.items()
                    if status != LINKED and owner.get(sha) == index
                },
            )
        )

    listed_outcomes = Counter(outcome[sha] for sha in listed)
    in_period = {c.commit.sha for c in candidates if c.in_period}
    ranks = Counter(rank for sha, rank in how.items() if sha in in_period)
    tally = SquashTally(
        found=len(listed),
        linked=listed_outcomes[LINKED],
        promoted=listed_outcomes[PROMOTED],
        unlinked=listed_outcomes[UNLINKED],
        unlinked_pushed=sum(c.counted for c in doubtful.values()),
        unlinked_other=sum(not c.counted for c in doubtful.values()),
        lookback_limited=limited,
        linked_earlier=sum(
            any(sha in earlier for sha in squashes[s].linked)
            for s in listed
            if outcome[s] == LINKED
        ),
        commits_linked=sum(ranks.values()),
        by_tree=ranks[TREE],
        by_subject=ranks[SUBJECT],
        by_branch=ranks[BRANCH],
        by_ticket=ranks[TICKET],
        relabelled=sum(c.sha in how for rc in repos for c in rc.local_only),
        after_period=sum(
            1
            for sha, status in outcome.items()
            if status == LINKED and squashes[sha].commit.author_day > window.end
        ),
        lines_unread=sum(status != LINKED and sha not in lines for sha, status in outcome.items()),
        off_mainline=off_mainline,
        roots_unread=len(projects.unread),
    )
    return replace(
        collection,
        repos=tuple(found),
        deliveries_in_other_clones=collection.deliveries_in_other_clones + in_other_clones,
        squashes=tally,
    )


def _delivery(
    squash: _Squash, status: str, lines: Mapping[str, Lines], tickets_of: TicketsOf
) -> Delivery:
    """The squash merge as a pull request delivered: yours (its author is the pull request's
    author); who pressed merge, git does not record — GitHub is the committer."""
    request, commit = squash.request, squash.commit
    # Linked: its branch commits are counted; else the squash itself is (the digest lists them
    # under it).
    counted = frozenset(squash.linked) if status == LINKED else frozenset({commit.sha})
    texts = (request.title, commit.body)
    return Delivery(
        forge=GITHUB,
        number=request.number,
        title=request.title,
        branch="",
        project=(),
        merge=commit,
        role=AUTHOR,
        commits=len(counted),
        yours=len(counted),
        tickets=frozenset(key for text in texts if text for key in tickets_of(text)),
        squash=status,
        lines=lines.get(commit.sha) if status != LINKED else None,
        your_commits=counted,
    )
