"""Merge requests and pull requests delivered, read from merge commits.

A forge's merge button writes a merge commit whose message says what it closed: GitLab ends it
with `See merge request group/project!N` and puts the request's title on line 3; GitHub's subject
is `Merge pull request #N from owner/branch`, the title again on line 3 — or, when a repository
sets it so, just `Title (#N)`, committed by GitHub. `parse_request()` reads exactly those
shapes — nothing looser — and `is_sync_merge()` recognises the merges that only bring a mainline
(or the same branch from a remote) into a branch, which are never deliveries. `parse_squash()`
reads GitHub's squash merge, a one-parent `Title (#N)` GitHub committed; squash.py decides what
it delivered.

A request merged into a mainline — on its first-parent chain, the line a merge button extends
(`Mainline`) — is a delivery; so is a GitLab merge request merged into another long-lived branch
(`main` in git-flow, `release/*`) once its merge commit reaches a mainline by any path
(`side_merges`). A back-merge — a long-lived branch merged into another — never is. Collect found
all of them in pass 1. Whose a delivery is comes from the commits it merged,
`merge^1..merge^2`: one `git log` per distinct mainline base reads the graph below the newest
merges down to that base (`GRAPH_FORMAT`: parents and author emails only) — one read in the
common case of one default branch, or of a copy of it — and `merged_commits()` walks each chain
once, oldest first, so every merge gets exactly what its first parent did not already reach.
A delivery is yours when it merged a commit you wrote, whoever merged it; merged for others when
you merged it (the merge commit's author) and wrote none of its commits. It is dated by the merge
commit's author date, like every commit. A merge is never a commit: nothing here
changes a count.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from sijl.collect import Collection, RepoCommits
from sijl.git import GRAPH_FORMAT, GitError, GitRunner, parse_graph_z
from sijl.identity import IdentitySet
from sijl.models import (
    AUTHOR,
    BOTH,
    GITHUB,
    GITHUB_WEB_EMAIL,
    GITLAB,
    MERGER,
    PATH_SEGMENT,
    Delivery,
    GraphNode,
    RawCommit,
    Reach,
)
from sijl.window import Window

# Ticket keys in a text: build_report() passes tickets.ticket_finder() when Jira projects are named.
TicketsOf = Callable[[str], Iterable[str]]

# GitLab's last line. The path is optional: GitLab before 9.x wrote `See merge request !7`.
_SEE_MERGE_REQUEST = re.compile(
    r"^See merge request (?P<path>\S*?)!(?P<number>[1-9][0-9]{0,8})[ \t\r]*$", re.MULTILINE
)
_GITLAB_SUBJECT = re.compile(r"Merge branch '(?P<branch>[^']+)' into '(?P<target>[^']+)'")
_GITHUB_SUBJECT = re.compile(
    r"Merge pull request #(?P<number>[1-9][0-9]{0,8}) from (?P<source>\S+)"
)
# GitHub's other message: the pull request's title, then its number — a squash merge's
# subject, or a merge commit's when the repository sets its message to the title. Only the last
# number counts: a title may end in its issue's, `Fix it (#30) (#65)`. Only the number at the very
# end is matched, and the title is what comes before it: a pattern for the title too (`.*?` then
# `[ \t]*`, both matching spaces) made a long run of spaces quadratic.
_PULL_NUMBER_AT_END = re.compile(r"\(#(?P<number>[1-9][0-9]{0,8})\)\Z")
# git's own merge subjects (and the hand-written `Merge origin/main into x`): what was merged,
# from where (`of <url>`: git pull), into what (`into` is left out for the default branch).
_GIT_MERGE = re.compile(
    r"Merge (?:(?P<kind>remote-tracking branch|branch) )?'?(?P<source>[^'\s]+)'?"
    r"(?: of \S+)?(?: into '?(?P<target>[^'\s]+)'?)?"
)
MAINLINE_NAMES = ("main", "master")  # always a mainline's name, whatever the remotes say
# Branches that live as long as the project: integration and production lines, and releases.
LONG_LIVED_NAMES = ("main", "master", "develop")
RELEASE_PREFIX = "release/"


@dataclass(frozen=True)
class Request:
    """What a merge commit's message says about the merge request or pull request it closed."""

    forge: str  # GITLAB or GITHUB: the shape of the message, not the remote it came from
    number: int
    title: str  # line 3 of the message; "" when there is none
    branch: str  # the source branch the subject names; "" when it names none
    project: tuple[str, ...]  # GitLab: the project the message names; () for none (and GitHub)
    target: str = ""  # the branch it was merged into, when the message says (GitLab's default)


def _title(lines: list[str]) -> str:
    """The first line with text after the subject — the request's title — unless it is GitLab's
    closing reference."""
    for line in lines[1:]:
        if line.strip():
            return "" if _SEE_MERGE_REQUEST.fullmatch(line) else line.strip()
    return ""


def _project(path: str) -> tuple[str, ...] | None:
    """`group/sub/project` as its segments, () for no path, None when it is not a project path."""
    if not path:
        return ()
    parts = tuple(path.split("/"))
    if len(parts) < 2 or not all(PATH_SEGMENT.fullmatch(p) and p not in (".", "..") for p in parts):
        return None
    return parts


def _github_titled(commit: RawCommit) -> Request | None:
    """`Title (#N)` in a commit GitHub itself committed at merge time: pull request N. The
    committer is what makes it one — anyone can end a subject with `(#12)`, and in their own
    commits that is an issue reference — and the one instant: GitHub stamps a squash (and a merge)
    with the same author and committer instant (every one checked did), while "Rebase and merge"
    commits your commit anew but keeps its author date, subject and all. GitHub names no branch
    and no project."""
    if commit.committer_email.casefold() != GITHUB_WEB_EMAIL:
        return None
    if commit.author_date != commit.committer_date:  # aware datetimes: compared as instants
        return None
    subject = commit.subject.strip()
    found = _PULL_NUMBER_AT_END.search(subject)
    if found is None or "\n" in subject:  # git's %s never holds one; a crafted record might
        return None
    return Request(GITHUB, int(found["number"]), subject[: found.start()].strip(), "", ())


def parse_squash(commit: RawCommit) -> Request | None:
    """The pull request a GitHub squash merge closed, or None. GitHub's "Squash and merge"
    writes one commit with one parent, committed by `noreply@github.com` at the instant it is
    authored, whose subject is the pull request's title and ` (#N)`. A trailing `(#N)` alone is
    just an issue reference."""
    if len(commit.parents) != 1:
        return None
    return _github_titled(commit)


def parse_request(commit: RawCommit) -> Request | None:
    """The merge request or pull request `commit` merged, or None. Only a merge commit can be
    one: a single-parent commit ending in `(#12)` is a squash merge at most (`parse_squash`)."""
    if not commit.is_merge:
        return None
    lines = [line.rstrip("\r") for line in commit.body.split("\n")]
    subject = lines[0].strip()
    github = _GITHUB_SUBJECT.fullmatch(subject)
    if github:
        branch = github["source"].partition("/")[2] or github["source"]
        return Request(GITHUB, int(github["number"]), _title(lines), branch, ())
    titled = _github_titled(commit)  # the merge message set to the pull request's title
    if titled is not None:
        return titled
    for found in reversed(list(_SEE_MERGE_REQUEST.finditer(commit.body))):
        project = _project(found["path"])
        if project is None:
            continue  # not a line GitLab writes; an earlier one may be
        default = _GITLAB_SUBJECT.fullmatch(subject)
        title = _title(lines) if default else subject  # a custom template: the subject is the title
        branch = default["branch"] if default else ""
        target = default["target"] if default else ""
        return Request(GITLAB, int(found["number"]), title, branch, project, target)
    return None


def is_sync_merge(commit: RawCommit, mainline_names: Iterable[str]) -> bool:
    """True for a merge that brings a mainline into a branch (`Merge branch 'main' into
    feature/x`, `Merge remote-tracking branch 'origin/main'`, `git pull` on main) or a branch's
    own remote copy into it (`Merge remote-tracking branch 'origin/x' into x`). Read from the
    subject alone — git's words, or the same words written by hand — so a GitLab merge request
    from `main` into a feature branch is a sync merge too."""
    if not commit.is_merge:
        return False
    found = _GIT_MERGE.fullmatch(commit.subject.strip())
    if found is None:
        return False
    source, target = found["source"], found["target"] or ""
    if found["kind"] != "branch" and "/" in source:
        source = source.split("/", 1)[1]  # `origin/main`: a remote-tracking branch, its remote off
    if source in {*MAINLINE_NAMES, *mainline_names}:
        return True
    return source == target


def is_long_lived(branch: str, mainline_names: Iterable[str]) -> bool:
    """`main`, `master`, `develop`, a remote's default branch, or a `release/…` branch."""
    return branch in {*LONG_LIVED_NAMES, *mainline_names} or (
        branch.startswith(RELEASE_PREFIX) and len(branch) > len(RELEASE_PREFIX)
    )


def is_back_merge(request: Request, mainline_names: Iterable[str]) -> bool:
    """A request that brings a long-lived branch of this project into another branch: a back-merge
    (`main` into `develop`, a release into `main`) or a sync through a merge request (`main` into
    `feature/x`). Never a delivery: what it brings was delivered where it was first merged. A
    source named like its target (`main` into `main`) cannot be this project's own branch — it
    is another project's (a fork's) and stays a request. Only GitLab's default subject names the
    target; without it (GitHub, custom templates) a request is never a back-merge."""
    if not request.branch or not request.target or request.branch == request.target:
        return False
    return is_long_lived(request.branch, mainline_names)


def no_tickets(text: str) -> frozenset[str]:
    """The default `tickets_of`: no ticket keys. `build_report()` passes
    `tickets.ticket_finder()` instead when Jira projects are named."""
    return frozenset()


def read_graph(git: GitRunner, repo: Path, heads: Sequence[str], base: str) -> dict[str, GraphNode]:
    """Every commit reachable from `heads` but not from `base` — what the merges from the period
    on brought in, whenever it was written, plus the mainline between them. One `git log`, with
    no date filter at all: a branch begun months before the period still says whose it is."""
    stdin = "".join(f"{line}\n" for line in [*heads, f"^{base}"])
    out = git.run(
        repo,
        "log",
        "--stdin",
        "-z",
        "--no-color",
        "--no-decorate",
        f"--format={GRAPH_FORMAT}",
        stdin=stdin.encode(),
    )
    return {node.sha: node for node in parse_graph_z(out)}


def merged_commits(
    graph: Mapping[str, GraphNode], heads: Iterable[str]
) -> dict[str, frozenset[str]]:
    """For each merge on the first-parent chains from `heads`: the commits it brought in that its
    first parent did not reach (`merge^1..merge^2`), within `graph`. Each chain is walked once,
    oldest first, keeping everything reached so far, so a merge's walk stops wherever an earlier
    point of the mainline already reached: a sync merge's mainline commits and a stacked
    branch's base are never counted twice."""
    brought: dict[str, frozenset[str]] = {}
    for head in heads:
        if head in brought:
            continue  # on a chain already walked from a later head
        chain, sha = [], head
        while sha in graph:
            chain.append(sha)
            sha = graph[sha].parents[0] if graph[sha].parents else ""
        reached: set[str] = set()
        for sha in reversed(chain):
            new: set[str] = set()
            stack = list(graph[sha].parents[1:])
            while stack:
                commit = stack.pop()
                if commit in reached or commit in new or commit not in graph:
                    continue
                new.add(commit)
                stack.extend(graph[commit].parents)
            if len(graph[sha].parents) > 1:
                brought[sha] = frozenset(new)
            reached |= new
            reached.add(sha)
    return brought


def traced(
    git: GitRunner, rc: RepoCommits, side: Sequence[str] = ()
) -> tuple[dict[str, GraphNode], dict[str, frozenset[str]]]:
    """The graph and, for every merge on the repository's mainlines, what it brought in —
    exactly `merge^1..merge^2`. Mainlines are read **per distinct base**: a base is an ancestor of
    every first parent on its own chain, so leaving out what it reaches never hides a commit one
    of that chain's merges brought in — but another chain's base may (a fork that merged your
    branch in March, its upstream that merged it as your pull request in May). Mainlines that
    share a base — one default branch, or a copy of it that lags behind — share one read.

    `side` are merges off the mainlines (newest first): each one the graph holds reaches a
    mainline, and gets what it brought in too, walked along its own branch's first parents. What
    it merged that the mainline already had before the period (below the base) is not counted
    again."""
    by_base: dict[str, set[str]] = defaultdict(set)
    for mainline in rc.mainlines:
        by_base[mainline.base].add(mainline.merges[0].sha)
    nodes: dict[str, GraphNode] = {}
    brought: dict[str, frozenset[str]] = {}
    for base, heads in sorted(by_base.items()):
        graph = read_graph(git, rc.repo.path, sorted(heads), base)
        walks = [*sorted(heads), *(sha for sha in side if sha in graph)]
        for sha, commits in merged_commits(graph, walks).items():
            brought.setdefault(sha, commits)
        nodes.update(graph)
    return nodes, brought


def _role(authored: bool, merged: bool) -> str:
    return BOTH if authored and merged else AUTHOR if authored else MERGER if merged else ""


@dataclass
class _Traced:
    """One repository's request merges and what each brought in — or why git could not say."""

    rc: RepoCommits  # with `delivery_error` set when the graph could not be read
    requests: dict[str, tuple[RawCommit, Request]]
    graph: dict[str, GraphNode]
    brought: dict[str, frozenset[str]]


def _trace(git: GitRunner, rc: RepoCommits) -> _Traced:
    """The repository's candidate requests: every request merge on a mainline but back-merges,
    and every GitLab merge request into another long-lived branch that reached a mainline."""
    names = rc.mainline_names
    requests = {
        m.sha: (m, request)
        for mainline in rc.mainlines
        for m in mainline.merges
        if (request := parse_request(m)) is not None and not is_back_merge(request, names)
    }
    side = {
        m.sha: (m, request)
        for m in rc.side_merges
        if (request := parse_request(m)) is not None
        and is_long_lived(request.target, names)
        and not is_back_merge(request, names)
    }
    if not rc.mainlines or not (requests or side):
        return _Traced(rc, {}, {}, {})
    try:
        graph, brought = traced(git, rc, list(side))
        if not requests.keys() <= brought.keys():  # never: each chain is read whole
            raise ValueError("a merge on a default branch is missing from its history")
        requests |= {sha: found for sha, found in side.items() if sha in brought}
    except GitError as exc:
        return _Traced(replace(rc, delivery_error=exc.summary), {}, {}, {})
    except ValueError as exc:  # a record the parser cannot trust
        return _Traced(replace(rc, delivery_error=str(exc)), {}, {}, {})
    return _Traced(rc, requests, graph, brought)


def find_deliveries(
    collection: Collection,
    identities: IdentitySet,
    window: Window,
    git: GitRunner,
    *,
    tickets_of: TicketsOf = no_tickets,
) -> Collection:
    """The collection with each repository's deliveries — the merge requests and pull requests
    merged into a mainline in the period that are yours or that you merged for others — and the
    counts Caveats need. A delivery several clones hold is listed once, in the first."""
    read: list[_Traced] = [_trace(git, rc) for rc in collection.repos]
    # Every commit a request merge brought in, in any clone: a commit listed in one clone may
    # have been delivered by a request that only another clone's default branch holds.
    delivered = {
        commit for t in read for merge in t.requests for commit in t.brought.get(merge, ())
    }
    # A GitHub squash merge of yours on a mainline is its pull request's own commit: it
    # came with a request, so it never counts as work that landed without one.
    delivered |= {c.sha for t in read for c in t.rc.github_commits if parse_squash(c) is not None}
    listed: set[str] = set()
    tally: Counter[str] = Counter()
    repos: list[RepoCommits] = []
    for t in read:
        rc, requests, graph, brought = t.rc, t.requests, t.graph, t.brought
        if rc.delivery_error:
            repos.append(rc)
            continue
        tally["landed"] += sum(
            rc.reach[c.sha] == Reach.MAINLINE and c.sha not in delivered for c in rc.commits
        )
        found: list[Delivery] = []
        for sha, (merge, request) in requests.items():
            if not window.includes(merge.author_day):
                continue  # merged after the period: it only told which commits it brought in
            nodes = [graph[c] for c in brought[sha] if len(graph[c].parents) < 2]
            mine = frozenset(
                n.sha for n in nodes if identities.matches_email(n.author_email_raw, n.author_email)
            )
            role = _role(bool(mine), identities.matches(merge))
            if not role:
                continue  # colleagues' work merged by a colleague
            if sha in listed:
                tally["in_other_clones"] += 1
                continue
            listed.add(sha)
            texts = (request.branch, request.title, merge.body)
            tickets = frozenset(key for text in texts if text for key in tickets_of(text))
            found.append(
                Delivery(
                    forge=request.forge,
                    number=request.number,
                    title=request.title,
                    branch=request.branch,
                    project=request.project,
                    merge=merge,
                    role=role,
                    commits=len(nodes),
                    yours=len(mine),
                    tickets=tickets,
                    your_commits=mine,
                )
            )
        found.sort(key=lambda d: (d.merge.author_date, d.merge.sha), reverse=True)
        repos.append(replace(rc, deliveries=tuple(found)))
    return replace(
        collection,
        repos=tuple(repos),
        landed_without_request=tally["landed"],
        deliveries_in_other_clones=tally["in_other_clones"],
        **_your_merges(repos, listed),
    )


def _your_merges(repos: list[RepoCommits], listed: set[str]) -> dict[str, int]:
    """What each of your merge commits in the period was, each merge once however many clones
    hold it. A request first: listed as a delivery; a back-merge (counted with the sync merges);
    in a repository whose graph git could not read (it may have been a delivery); otherwise
    merged where it never reached a mainline, or into a short-lived branch. Then a sync merge.
    The rest were merged with no request — by hand."""
    yours = {m.sha: (m, rc) for rc in repos for m in rc.merges}
    kinds: Counter[str] = Counter()
    for sha, (merge, rc) in yours.items():
        names = rc.mainline_names
        if sha in listed:
            kinds["merges_delivered"] += 1
        elif (request := parse_request(merge)) is not None:
            if is_back_merge(request, names):
                kinds["merges_sync"] += 1
            elif rc.delivery_error:
                kinds["merges_unread"] += 1
            else:
                kinds["merges_elsewhere"] += 1
        elif is_sync_merge(merge, names):
            kinds["merges_sync"] += 1
    return {
        key: kinds[key]
        for key in ("merges_delivered", "merges_sync", "merges_elsewhere", "merges_unread")
    }
