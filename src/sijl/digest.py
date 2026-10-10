"""The digest: one person's period as compact, deterministic evidence text.

Both tiers ask Claude about the same text. Tier 1 writes it to a file the user gives Claude Code
(`sijl digest --with-prompt`); Tier 2 (`sijl ask`) puts `Digest.text` in the request
and checks every id Claude cites against `Digest.ids`. It is built from the report's one
`Insights` model and its collection — every number is read from them, never counted again here —
and it is laid out for reading and citing, in a fixed order:

1. **a header**: who (the user's own name and addresses), how names are redacted, the period and
   how it counts, how ids are written, the exact totals, what the digest leaves out, and what a
   `--max-chars` budget omitted;
2. **Tickets**: every counted commit naming a ticket of `jira.projects`, under each ticket it
   names (`TicketInsights.by_commit`), headed by the ticket's numbers and the requests naming it;
3. **Requests**: every merge request and pull request merged in the period — yours (GitHub squash
   merges too) newest first, then the ones you merged for colleagues — each headed by its id,
   date, who merged it, its numbers, tickets and title, with its commits that name no ticket;
4. **Repositories**: the remaining commits — in no ticket and no request — by repository,
   busiest first;
5. **Not pushed yet**: commits on no remote branch, which no number above counts.

A commit is listed in the first of Tickets, Requests and Repositories that holds it (under every
ticket it names), each line starting with its short SHA: 8 hex characters — the report's —
or more when two listed commits would share them. A request is cited by its repository's id
and its number, `webapp!42` (GitLab) or `app#12` (GitHub) — never by the namespace or owner a
remote names, which can be a colleague's own; the header maps each repository id to its
folder's name once. A ticket is cited by its key.
Every id is a plain token of one grammar (`ids.py`, re-exported here as `ID_PATTERN`), and
`Digest.ids` is exactly the tokens the finished text holds: Sijl's own, and any a subject
or title happens to write.

Text Sijl did not write — names, subjects, titles, repository names — goes through
the redactor (only the user's own names and addresses survive) and every line through
`text.printable()`. The same report and redactor always give the same bytes: every list is
sorted by its own complete key, never by a set's or a dict's order.

**The budget** (`max_chars`). The header is always whole. What the sections may use is
split fairly: a section smaller than an equal share keeps everything, and what it leaves is
shared among the larger ones (max-min fairness); inside a section, the same split runs over its
groups — first every group's heading, then their lines from the top. A group that lost lines
says so in its heading (`(showing 3 of 12)`), and a section that lost anything ends with
`[… N more omitted …]`, N counting every line it left out. Nothing is ever cut silently.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import cached_property

from sijl import __version__
from sijl.collect import RepoCommits, read_people
from sijl.git import GitRunner
from sijl.ids import ID, ID_PATTERN  # the id grammar; ID_PATTERN re-exported for citations
from sijl.models import (
    BOTH,
    GITHUB,
    GITHUB_WEB_EMAIL,
    LINKED,
    PROMOTED,
    Delivery,
    RawCommit,
    Reach,
    Repo,
)
from sijl.redact import Redactor
from sijl.report import Report
from sijl.text import printable
from sijl.tickets import key_order

SHORT_SHA = 8  # the report's length; longer only where two listed commits would share a prefix
MARKER = "[… {n} more omitted …]"
_WHERE = {Reach.LOCAL: "local branch", Reach.WORKTREE: "worktree only"}
_NOT_IN_AN_ID = re.compile(r"[^A-Za-z0-9._-]+")  # a repository id keeps only these characters
_STREAK_RULE = "weekends neither break nor extend a streak; a weekday without a commit ends it"
# Read first, before the digest, by Claude Code (`--with-prompt`) or as the instructions of a
# request: answers from the evidence only, with the ids that back them.
PROMPT = """\
# How to answer from this digest

You are answering questions about one person's own work in a review period. The digest that
follows is Sijl's record of their git history, and it is your only source.

- Answer only from the digest. When it does not hold the answer, say "not in the data" —
  never guess, and never fill a gap from general knowledge.
- Cite the id of every piece of evidence you use, exactly as the digest writes it: a commit's
  short SHA (1a2b3c4d), a merge request (webapp!12), a pull request (app#12), a ticket
  (ABC-123).
- Take numbers from the Totals as they are: they are Sijl's exact counts. Never count the
  lines yourself. A list that ends with "[… N more omitted …]" was cut to fit a size limit, so
  an answer built from it may be incomplete: say so.
- [person N] and [email N] stand for other people whose names and addresses were removed;
  write them as they are.
- Activity is evidence of work, not a performance score.
"""


@dataclass(frozen=True)
class Counts:
    """Commits, requests and tickets: everything the digest lists, or what a budget omitted."""

    commits: int = 0
    requests: int = 0
    tickets: int = 0


@dataclass(frozen=True)
class Digest:
    """The evidence text and what the citation check needs to check answers against it."""

    text: str  # header and sections, redacted and printable; ends with a newline
    ids: frozenset[str]  # every ID_PATTERN token in the text: shas | requests | tickets
    shas: frozenset[str]  # its commit tokens ("1a2b3c4d")
    requests: frozenset[str]  # its request tokens ("webapp!42", "app#12")
    tickets: frozenset[str]  # its ticket tokens ("PROJ-12")
    listed: Counts  # what the digest holds without a budget
    omitted: Counts  # what `max_chars` left out; all zero when nothing was
    about: str  # one line for a preview: what the evidence covers; not in `text`
    redaction: bool  # whether a redactor ran over the text Sijl did not write
    redacted_names: int = 0  # distinct names the redactor has replaced, this digest included
    redacted_emails: int = 0  # distinct addresses, likewise
    projects: tuple[str, ...] = ()  # the Jira projects whose keys are tickets (`jira.projects`)

    @property
    def complete(self) -> bool:
        return self.omitted == Counts()

    @cached_property
    def _lines(self) -> dict[str, str]:
        """Each id of the text → the line of the text that is its own: the line that starts with
        it (`### <id> — …` for a ticket or a request, `- <id> …` for a commit), else the first
        line that holds it as a token."""
        found: dict[str, str] = {}
        own: set[str] = set()
        for line in self.text.splitlines():
            for match in ID_PATTERN.finditer(line):
                token = match.group()
                starts = line.startswith((f"### {token} ", f"- {token} "))
                if token not in found or (starts and token not in own):
                    found[token] = line
                    if starts:
                        own.add(token)
        return found

    def line_for(self, id_: str) -> str:
        """The digest's own line for `id_` — its date, repository and subject, or its request's
        or ticket's heading — as the text writes it; "" for an id the text does not hold. What
        `sijl ask` lists under "Evidence Sijl checked": Sijl's words from the
        user's data, never Claude's."""
        return self._lines.get(id_, "")


class BudgetTooSmall(ValueError):
    """`max_chars` is below the smallest budget that works (`smallest`): the whole digest, or the
    header, the section headings and room for every omission marker, whichever is less."""

    def __init__(self, max_chars: int, smallest: int) -> None:
        super().__init__(
            f"--max-chars {max_chars:,} is too small: the smallest digest that fits takes "
            f"{smallest:,} characters"
        )
        self.smallest = smallest


def with_prompt(text: str) -> str:
    """The digest preceded by `PROMPT`: what `sijl digest --with-prompt` writes."""
    return f"{PROMPT}\n{text}"


def redactor_for(report: Report, *, git: GitRunner | None = None, history: bool = True) -> Redactor:
    """A redactor that keeps only the user's own names and addresses and knows every other
    person Sijl can see: the authors and co-authors pass 1 read (`Collection.authors`) and
    everyone in each scanned repository's whole history — authors and committers of any date
    and every person a trailer names (`collect.read_people()`, one `git log` per repository
    with `git`, a new GitRunner by default). `history=False` skips that
    read, for a report built without repositories on disk."""
    people = set(report.collection.authors)
    if history:
        runner = git or GitRunner()
        for rc in report.collection.repos:
            people |= read_people(runner, rc.repo)
    return redactor_from_people(sorted(people), emails=report.emails, person=report.person)


def redactor_from_people(
    people: Iterable[tuple[str, str]], *, emails: Iterable[str], person: str
) -> Redactor:
    """The redactor for these (name, email) pairs. A pair is the user's when its email is one
    of `emails` — matched by exact address, never by name — and its name, the address's local
    part and a GitHub noreply login are kept; every other person's are replaced, the user
    names in their addresses too (`sam.colleague`, a login such as `samc`) unless they read as
    a role (`dev`, `ci`, `noreply`, …) or are shorter than three characters. Bots are not people:
    GitHub's web committer and `[bot]` accounts would only erase the words "GitHub" or
    "dependabot" from every subject."""
    yours = {email.casefold() for email in emails}
    own: set[str] = {person, *(user for email in yours for user in _users(email, every=True))}
    others: set[str] = set()
    for name, email in people:
        mine = email.casefold() in yours
        if not mine and _bot(name, email):
            continue
        found = [name] if name.strip() else []
        found += _users(email, every=mine) if email else []
        (own if mine else others).update(found)
    return Redactor(keep_emails=sorted(yours), keep_names=sorted(own), names=sorted(others))


# Local parts of shared and service addresses: as "names" they would erase common words.
_ROLES = frozenset({
    "admin", "bot", "build", "ci", "contact", "deploy", "dev", "devops", "git", "github", "gitlab",
    "hello", "info", "jenkins", "localhost", "mail", "no-reply", "noreply", "notifications",
    "office", "ops", "release", "root", "security", "server", "support", "team", "test", "ubuntu",
    "user",
})  # fmt: skip


def _users(email: str, *, every: bool = False) -> list[str]:
    """The user name an address carries — its local part, or a GitHub noreply address's login
    (`1234+samc@users.noreply.github.com` is `samc`) — when it can name a person: three
    characters at least, a letter among them, not a role (`every` keeps them all)."""
    local, _, domain = email.casefold().partition("@")
    if domain == "users.noreply.github.com" and "+" in local:
        local = local.partition("+")[2]
    if every or (len(local) >= 3 and any(c.isalpha() for c in local) and local not in _ROLES):
        return [local]
    return []


def _bot(name: str, email: str) -> bool:
    email = email.casefold()
    return (
        email == GITHUB_WEB_EMAIL
        or email.endswith("[bot]@users.noreply.github.com")
        or name.endswith("[bot]")
    )


# ------------------------------------------------------------------------------ the pieces


@dataclass(frozen=True)
class _Line:
    """One line of the digest, printable, without its newline."""

    text: str
    commits: frozenset[str] = frozenset()  # the full SHAs of the commits it lists (the counts)

    @property
    def size(self) -> int:
        return len(self.text) + 1  # and its newline


@dataclass(frozen=True)
class _Group:
    """A ticket, a request or a repository: its heading and its lines of evidence."""

    head: _Line
    lines: tuple[_Line, ...]
    request: str = ""  # the request it is, for the counts
    ticket: str = ""  # the ticket it is

    def size(self) -> int:
        return self.head.size + sum(line.size for line in self.lines)


@dataclass(frozen=True)
class _Section:
    head: tuple[_Line, ...]  # a blank line, its heading, and a note when it is empty
    groups: tuple[_Group, ...] = ()

    def lines(self) -> int:
        """Its lines of evidence: every group's heading and lines."""
        return sum(1 + len(group.lines) for group in self.groups)

    def body(self) -> int:
        return sum(group.size() for group in self.groups)

    def marker(self) -> int:
        """Room for its omission marker at the most it can count; 0 with nothing to omit."""
        return len(MARKER.format(n=f"{self.lines():,}")) + 1 if self.groups else 0


# A section as fitted: the groups kept (some showing fewer lines) and the lines it left out.
_Fitted = tuple[_Section, tuple[_Group, ...], int]


def _n(count: int, noun: str, plural: str = "") -> str:
    return f"{count:,} {noun if count == 1 else plural or noun + 's'}"


def _span(first: date, last: date) -> str:
    return (
        f"on {first.isoformat()}" if first == last else f"{first.isoformat()} to {last.isoformat()}"
    )


def _newest_first(
    pairs: Iterable[tuple[RepoCommits, RawCommit]],
) -> list[tuple[RepoCommits, RawCommit]]:
    """By author instant, then SHA: one order for any input order."""
    return sorted(pairs, key=lambda pair: (pair[1].author_date, pair[1].sha), reverse=True)


def _sha_width(shas: Iterable[str]) -> int:
    """The shortest prefix, from `SHORT_SHA` up, that no two of `shas` share."""
    distinct = set(shas)
    width = SHORT_SHA
    while len({sha[:width] for sha in distinct}) < len(distinct):
        width += 1
    return width


def _repo_ids(names: Mapping[Repo, str]) -> dict[Repo, str]:
    """Each repository's id in the digest, from its name as shown (`names`, already redacted):
    accents dropped, every run of characters other than `A-Z a-z 0-9 . _ -` one `-`, no `.` or
    `-` at either end — `my tool` is `my-tool`, `café` `cafe`, a name only of other scripts
    `repo`; one that would read as a SHA or a ticket key is prefixed `repo-`. Two repositories
    with one id, in (name, path) order, become `app` and `app-2`. Never a remote's namespace or
    owner, which can be a colleague's own."""
    ids: dict[Repo, str] = {}
    taken: set[str] = set()
    for repo in sorted(names, key=lambda r: (r.name, str(r.path))):
        decomposed = unicodedata.normalize("NFKD", names[repo])
        plain = "".join(char for char in decomposed if not unicodedata.combining(char))
        base = _NOT_IN_AN_ID.sub("-", plain).strip(".-") or "repo"
        if ID.match(base):
            base = f"repo-{base}"
        candidate, n = base, 1
        while candidate in taken:
            n += 1
            candidate = f"{base}-{n}"
        taken.add(candidate)
        ids[repo] = candidate
    return ids


def _squash_counted(d: Delivery) -> str:
    """What a squash merge of yours counted: its linked commits, or itself as code."""
    if d.squash == LINKED:
        return f"{_n(d.yours, 'linked commit')} counted instead of the squash"
    if d.lines is None:
        lines = "lines not read"
    else:
        lines = f"+{d.lines.added:,} -{d.lines.removed:,} lines"
        lines += f", {_n(d.lines.binary, 'binary file')}" if d.lines.binary else ""
    if d.squash == PROMOTED:
        return f"the squash counted as code, {lines} (its branch commits are gone)"
    return f"the squash counted as code, {lines} (not linked: commits of yours may repeat it)"


# ------------------------------------------------------------------------------ the budget


def shares(demands: Sequence[int], budget: int) -> list[int]:
    """`budget` split fairly over `demands` (max-min fairness): a demand at or below an equal
    share of what is left is met in full, and the rest share what remains equally. Ties go in
    the demands' order, so the split is always the same."""
    given = [0] * len(demands)
    left, waiting = max(budget, 0), len(demands)
    for index in sorted(range(len(demands)), key=lambda i: demands[i]):
        given[index] = min(demands[index], left // waiting)
        left -= given[index]
        waiting -= 1
    return given


def _shown(group: _Group, kept: int) -> _Group:
    """`group` with its first `kept` lines, its heading saying how many of how many."""
    if kept == len(group.lines):
        return group
    head = _Line(f"{group.head.text} (showing {kept:,} of {len(group.lines):,})")
    return _Group(head, group.lines[:kept], group.request, group.ticket)


def _heading_room(group: _Group) -> int:
    """A group's heading with the longest "(showing k of n)" it can get."""
    n = len(group.lines)
    return group.head.size + len(f" (showing {n:,} of {n:,})")


def _fit_groups(groups: Sequence[_Group], room: int) -> tuple[_Group, ...]:
    """As much of `groups` as `room` characters hold: every heading first, then lines from the
    top of each group by a fair split (`shares()`), then — in order — whatever lines still fit
    in what the fair split left unused. When the headings alone do not fit, the first groups
    whose headings fit are kept, showing none of their lines, and the rest are left out."""
    heads = [_heading_room(group) for group in groups]
    if sum(heads) > room:
        kept: list[_Group] = []
        for group, size in zip(groups, heads, strict=True):
            if size > room:
                break
            kept.append(_shown(group, 0))
            room -= size
        return tuple(kept)
    sizes = [[line.size for line in group.lines] for group in groups]
    spare = room - sum(heads)
    counts = []
    for lines, share in zip(sizes, shares([sum(s) for s in sizes], spare), strict=True):
        count = used = 0
        while count < len(lines) and used + lines[count] <= share:
            used += lines[count]
            count += 1
        counts.append(count)
        spare -= used
    for index, lines in enumerate(sizes):
        while counts[index] < len(lines) and lines[counts[index]] <= spare:
            spare -= lines[counts[index]]
            counts[index] += 1
    return tuple(_shown(group, count) for group, count in zip(groups, counts, strict=True))


def _fit(sections: Sequence[_Section], room: int) -> list[_Fitted]:
    """Each section with what `room` characters — after every section's marker — allow: a fair
    split over the sections' bodies; a section within its share is whole, the others are fitted
    group by group (`_fit_groups()`) and count the lines they left out."""
    room -= sum(section.marker() for section in sections)
    fitted: list[_Fitted] = []
    for section, share in zip(sections, shares([s.body() for s in sections], room), strict=True):
        if section.body() <= share:
            fitted.append((section, section.groups, 0))
            continue
        groups = _fit_groups(section.groups, share)
        kept = sum(1 + len(group.lines) for group in groups)
        fitted.append((section, groups, section.lines() - kept))
    return fitted


# ------------------------------------------------------------------------------- building


class _Builder:
    """The digest's lines for one report. Text Sijl did not write is redacted, piece by
    piece in reading order (so labels are numbered that way), and every line made printable."""

    def __init__(self, report: Report, redactor: Redactor | None) -> None:
        self.report = report
        self.insights = report.insights
        self.collection = report.collection
        self.redactor = redactor
        # each repository's name as shown (redacted) and its id: what every line cites
        self.names = {rc.repo: self.redact(rc.repo.name) for rc in self.collection.repos}
        self.ids = _repo_ids(self.names)
        self.width = _sha_width(
            c.sha for rc in self.collection.repos for c in (*rc.commits, *rc.local_only)
        )
        every = sorted(  # newest first: by merge day, then its instant, then its SHA
            ((rc, d) for rc in self.collection.repos for d in rc.deliveries),
            key=lambda found: (found[1].merged_on, found[1].merge.author_date, found[1].merge.sha),
            reverse=True,
        )
        self.mine = [(rc, d) for rc, d in every if d.is_yours]
        self.others = [(rc, d) for rc, d in every if not d.is_yours]
        self.ref = {d.merge.sha: self._request_id(rc, d) for rc, d in every}
        # The requests that delivered each commit, oldest first: it is listed under the first.
        self.delivered_by: dict[str, list[Delivery]] = defaultdict(list)
        for _, d in reversed(self.mine):
            for sha in sorted(d.your_commits):
                self.delivered_by[sha].append(d)

    def redact(self, text: str) -> str:
        return self.redactor(text) if self.redactor is not None else text

    def repo(self, repo: Repo) -> str:
        return self.ids[repo]

    def _request_id(self, rc: RepoCommits, d: Delivery) -> str:
        """`<repository id>!N` (GitLab) or `<repository id>#N` (GitHub): never the project path
        or owner a remote or merge message names — a colleague's own namespace can be one."""
        return f"{self.ids[rc.repo]}{'#' if d.forge == GITHUB else '!'}{d.number}"

    def commit_line(
        self,
        rc: RepoCommits,
        commit: RawCommit,
        *,
        repo: bool = True,
        under: str = "",
        local: bool = False,
    ) -> _Line:
        """`- <sha> <day> [<repo>] <subject>`, then `→` the requests that delivered it — but the
        one it is listed `under`. Without `repo` the heading above names the repository; a commit
        not pushed (`local`) says where it is."""
        short = commit.sha[: self.width]
        parts = [f"- {short} {commit.author_day.isoformat()}"]
        if local:
            parts.append(f"({_WHERE.get(rc.reach.get(commit.sha), 'local branch')})")
        if repo:
            parts.append(f"[{self.repo(rc.repo)}]")
        parts.append(self.redact(commit.subject))
        text = " ".join(parts)
        refs = [self.ref[d.merge.sha] for d in self.delivered_by.get(commit.sha, ())]
        refs = [ref for ref in refs if ref != under]
        if refs:
            text += " → " + ", ".join(refs)
        return _Line(printable(text), commits=frozenset({commit.sha}))

    # ------------------------------------------------------------------------- sections

    def tickets(self) -> _Section:
        tickets = self.insights.tickets
        head = (_Line(""), _Line("## Tickets — each counted commit naming a ticket, under every "
                                 "ticket it names"))  # fmt: skip
        if not tickets.projects:
            note = (
                "No Jira projects are set (--jira-project, or jira.projects in the config file), "
                "so no commit is grouped by ticket."
            )
            return _Section((*head, _Line(note)))
        if not tickets.per_ticket:
            note = f"No counted commit or request names a ticket of {', '.join(tickets.projects)}."
            return _Section((*head, _Line(printable(note))))
        under: dict[str, list[tuple[RepoCommits, RawCommit]]] = defaultdict(list)
        for rc in self.collection.repos:
            for commit in rc.commits:
                for key in tickets.by_commit.get(commit.sha, ()):
                    under[key].append((rc, commit))
        groups = []
        for total in tickets.per_ticket:
            refs = [self.ref[d.merge.sha] for _, d in self.mine if total.key in d.tickets]
            numbers = [_n(total.commits, "commit")] if total.commits else []
            numbers += [_n(total.deliveries, "request")] if total.deliveries else []
            text = f"### {total.key} — {', '.join(numbers)}; {_span(total.first, total.last)}"
            text += f"; requests: {', '.join(refs)}" if refs else ""
            heading = _Line(printable(text))
            lines = tuple(self.commit_line(rc, c) for rc, c in _newest_first(under[total.key]))
            groups.append(_Group(heading, lines, ticket=total.key))
        return _Section(head, tuple(groups))

    def requests(self) -> _Section:
        head = (
            _Line(""),
            _Line(
                "## Requests — merge requests and pull requests merged in the period: yours, "
                "newest first, then those you merged for colleagues; under each, its commits "
                "that name no ticket. Squash-merged: a GitHub squash merge, whose merger git "
                "does not record"
            ),
        )
        if not self.mine and not self.others:
            note = (
                "No merge request or pull request of yours was merged in this period, and you "
                "merged none for colleagues."
            )
            return _Section((*head, _Line(note)))
        ticketed = self.insights.tickets.by_commit
        listed: dict[str, list[tuple[RepoCommits, RawCommit]]] = defaultdict(list)
        for rc in self.collection.repos:
            for commit in rc.commits:
                by = self.delivered_by.get(commit.sha)
                if by and commit.sha not in ticketed:
                    listed[by[0].merge.sha].append((rc, commit))
        groups = []
        for rc, d in [*self.mine, *self.others]:
            ref = self.ref[d.merge.sha]
            keys = sorted(d.tickets, key=key_order)
            heading = _Line(printable(f"### {ref} — {self._request_facts(rc, d, keys)}"))
            pairs = _newest_first(listed[d.merge.sha])  # its repository's, or another clone's
            lines = tuple(
                self.commit_line(owner, c, under=ref, repo=owner.repo != rc.repo)
                for owner, c in pairs
            )
            groups.append(_Group(heading, lines, request=ref))
        return _Section(head, tuple(groups))

    def _request_facts(self, rc: RepoCommits, d: Delivery, keys: Sequence[str]) -> str:
        """`<day> [<repo>]; <who merged it>; <what of it is yours>; tickets: …; title: …`."""
        if not d.is_yours:
            facts = ["merged by you for a colleague", f"{_n(d.commits, 'commit')}, none yours"]
        elif d.squash:
            facts = ["squash-merged", _squash_counted(d)]
        else:
            by = "you" if d.role == BOTH else "a colleague"
            facts = [f"merged by {by}", f"{d.yours:,} of {_n(d.commits, 'commit')} yours"]
        facts.insert(0, f"{d.merged_on.isoformat()} [{self.repo(rc.repo)}]")
        if keys:
            facts.append(f"tickets: {', '.join(keys)}")
        if d.title:
            facts.append(f"title: {self.redact(d.title)}")
        return "; ".join(facts)

    def repositories(self) -> _Section:
        head = (
            _Line(""),
            _Line(
                "## Repositories — the other counted commits, in no ticket and no request; "
                "busiest repository first"
            ),
        )
        placed = self.insights.tickets.by_commit.keys() | self.delivered_by.keys()
        by_repo = {rc.repo: rc for rc in self.collection.repos}
        groups = []
        for repo, total in self.insights.per_repo:
            rc = by_repo[repo]
            rest = [(rc, c) for c in rc.commits if c.sha not in placed]
            if rest:
                text = f"### {self.repo(repo)} — {_n(total, 'commit')} in all; {len(rest):,} below"
                lines = tuple(self.commit_line(rc, c, repo=False) for rc, c in _newest_first(rest))
                groups.append(_Group(_Line(printable(text)), lines))
        if not groups:
            note = "None: every counted commit is listed under a ticket or a request above."
            return _Section((*head, _Line(note)))
        return _Section(head, tuple(groups))

    def not_pushed(self) -> _Section:
        head = (
            _Line(""),
            _Line(
                "## Not pushed yet — your commits on no remote branch; no number above counts them"
            ),
        )
        repos = sorted(
            (rc for rc in self.collection.repos if rc.local_only),
            key=lambda rc: (-len(rc.local_only), rc.repo.name, str(rc.repo.path)),
        )
        if not repos:
            return _Section((*head, _Line("None.")))
        groups = []
        for rc in repos:
            text = f"### {self.repo(rc.repo)} — {_n(len(rc.local_only), 'commit')}"
            pairs = _newest_first((rc, c) for c in rc.local_only)
            lines = tuple(self.commit_line(rc, c, repo=False, local=True) for rc, c in pairs)
            groups.append(_Group(_Line(printable(text)), lines))
        return _Section(head, tuple(groups))

    # --------------------------------------------------------------------------- header

    def header(self) -> list[_Line]:
        report, insights = self.report, self.insights
        window = report.window
        period = f"{window.start.isoformat()} to {window.end.isoformat()}"
        period += f" ({window.label})" if window.label else ""
        if self.redactor is not None:
            redaction = (
                "Redaction: on — every other person's name is written [person N] and every "
                "other email address [email N]; one person keeps one N throughout."
            )
        else:
            redaction = "Redaction: off — names and email addresses are as written."
        lines = [
            "# Sijl digest — evidence of one person's own work",
            f"Generated {report.generated_on.isoformat()} by Sijl {__version__} from local "
            "git repositories. Every number is Sijl's own exact count; every line of "
            "evidence starts with an id to cite.",
            f"Person: {self.redact(report.person)} "
            f"({', '.join(self.redact(email) for email in report.emails)})",
            redaction,
            f"Period: {period} — inclusive; by author date, in each commit's own timezone",
            "Ids: a commit by its short SHA (as listed), a GitLab merge request as "
            "<repository>!N, a GitHub pull request as <repository>#N, a ticket by its key.",
            self._repositories(),
            "Totals — exact; use these rather than counting the lines below:",
            f"- {self._commits_total()}",
        ]
        if insights.total:
            lines += [
                "- per repository: "
                + ", ".join(f"{self.repo(repo)} {n:,}" for repo, n in insights.per_repo),
                "- per month (the others had none): "
                + ", ".join(
                    f"{m.month} {_n(m.commits, 'commit')} on {_n(m.active_days, 'day')}"
                    for m in insights.per_month
                    if m.commits
                ),
                "- per kind: "
                + ", ".join(f"{kind} {n:,}" for kind, n in insights.per_kind if n)
                + f"; breaking changes: {insights.breaking:,}",
                "- per review category: "
                + ", ".join(f"{name} {n:,}" for name, n in insights.per_category if n),
            ]
        lines += [f"- {self._tickets_total()}", f"- {self._requests_total()}"]
        if insights.not_pushed:
            lines.append(
                f"- not pushed yet: {_n(insights.not_pushed, 'commit')}, listed last and in no "
                "number above"
            )
        lines.append(self._left_out())
        return [_Line(printable(line)) for line in lines]

    def _repositories(self) -> str:
        """Every repository the digest names, by id, with its folder's name where that differs:
        the one place a name is mapped to the id every line uses."""
        shown = {
            rc.repo for rc in self.collection.repos if rc.commits or rc.local_only or rc.deliveries
        }
        named = sorted(shown, key=lambda repo: self.ids[repo])
        listed = [
            self.ids[repo]
            + ("" if self.names[repo] == self.ids[repo] else f" = {self.names[repo]}")
            for repo in named
        ]
        return 'Repositories (each by its id; "=" gives the folder\'s name where it differs): ' + (
            ", ".join(listed) or "none"
        )

    def _commits_total(self) -> str:
        insights = self.insights
        text = (
            f"{_n(insights.total, 'counted commit')} on {_n(insights.active_days, 'active day')} "
            f"in {_n(len(insights.per_repo), 'repository', 'repositories')}"
        )
        streak = insights.longest_streak
        if streak.weekdays and streak.first is not None and streak.last is not None:
            text += (
                f"; longest streak {_n(streak.weekdays, 'weekday')}, "
                f"{_span(streak.first, streak.last)} ({_STREAK_RULE})"
            )
        return text

    def _tickets_total(self) -> str:
        tickets, total = self.insights.tickets, self.insights.total
        if not tickets.projects:
            return "tickets: no Jira projects set"
        return (
            f"tickets of {', '.join(tickets.projects)}: {len(tickets.per_ticket):,}, named by "
            f"{tickets.with_ticket:,} of the {_n(total, 'counted commit')}"
        )

    def _requests_total(self) -> str:
        i = self.insights
        by_them = i.delivered - i.delivered_by_you - i.delivered_squashed
        how = [
            (i.delivered_by_you, "merged by you"),
            (by_them, "by a colleague" if by_them == 1 else "by colleagues"),
            (i.delivered_squashed, "as GitHub squash merges (merger not recorded)"),
        ]
        parts = [f"{n:,} {what}" for n, what in how if n]
        text = f"requests of yours merged: {i.delivered:,}"
        text += f" ({', '.join(parts)})" if parts else ""
        return f"{text}; requests you merged for colleagues: {i.merged_for_others:,}"

    def _left_out(self) -> str:
        collection, report = self.collection, self.report
        parts = [
            "commit messages beyond their first line (bodies and trailers, co-authors included)",
            "merge commits (a request is listed instead; a merge is never counted as a commit)",
        ]
        if collection.copies_collapsed:
            copies = _n(collection.copies_collapsed, "rebased or cherry-picked copy",
                        "rebased or cherry-picked copies")  # fmt: skip
            parts.append(f"{copies} of commits listed once")
        if report.not_scanned:
            parts.append(f"{_n(len(report.not_scanned), 'path')} that could not be read")
        return (
            f"Not in this digest: {'; '.join(parts)}. `sijl report` explains every limit "
            "under Caveats."
        )


# ---------------------------------------------------------------------------------- build


def build(
    report: Report, *, redactor: Redactor | None = None, max_chars: int | None = None
) -> Digest:
    """The digest of `report`. With `redactor` (`redactor_for(report)`, or `ask`'s shared one),
    only the user's own names and addresses are left; with None the text is as written.
    `max_chars` bounds `Digest.text` (see the module docstring): BudgetTooSmall, naming the
    least budget that works, when it cannot hold the header and the section headings."""
    builder = _Builder(report, redactor)
    sections = [builder.tickets(), builder.requests(), builder.repositories(), builder.not_pushed()]
    header = builder.header()
    whole: list[_Fitted] = [(section, section.groups, 0) for section in sections]
    listed = _counts(whole)
    nothing = _Line("Omitted: nothing — every commit, request and ticket found is listed.")
    lines = [*header, nothing, *_body(whole)]
    if max_chars is not None and sum(line.size for line in lines) > max_chars:

        def fixed(budget: int) -> int:
            """What every digest within `budget` holds: the header — its last line as long as it
            can be (all omitted, `budget` in it) — and the sections' headings."""
            widest = _omitted(budget, listed, listed)
            heads = sum(line.size for section in sections for line in section.head)
            return sum(line.size for line in (*header, widest)) + heads

        markers = sum(section.marker() for section in sections)
        if fixed(max_chars) + markers > max_chars:
            # The least budget that works: the whole digest, or the least that holds the reserve
            # — found by iteration, as the omitted line's number grows with the budget. Both
            # rules are monotone, so every budget from it up works and every one below fails.
            least = fixed(1) + markers
            while fixed(least) + markers > least:
                least = fixed(least) + markers
            raise BudgetTooSmall(max_chars, min(least, sum(line.size for line in lines)))
        fitted = _fit(sections, max_chars - fixed(max_chars))
        omitted = _minus(listed, _counts(fitted))
        lines = [*header, _omitted(max_chars, listed, omitted), *_body(fitted)]
    else:
        omitted = Counts()
    insights = report.insights
    text = "".join(f"{line.text}\n" for line in lines)
    found: dict[str, set[str]] = {"commit": set(), "request": set(), "ticket": set()}
    for match in ID_PATTERN.finditer(text):  # the ids are exactly the tokens written
        found[match.lastgroup or ""].add(match.group())
    return Digest(
        text=text,
        ids=frozenset().union(*found.values()),
        shas=frozenset(found["commit"]),
        requests=frozenset(found["request"]),
        tickets=frozenset(found["ticket"]),
        listed=listed,
        omitted=omitted,
        about=(
            f"{_n(len(insights.per_repo), 'repository', 'repositories')}, "
            f"{_n(insights.total, 'commit')}, {_n(insights.delivered, 'request')} of yours, "
            f"{_n(len(insights.tickets.per_ticket), 'ticket')}, "
            f"{report.window.start.isoformat()} to {report.window.end.isoformat()}"
        ),
        redaction=redactor is not None,
        redacted_names=redactor.names if redactor is not None else 0,
        redacted_emails=redactor.emails if redactor is not None else 0,
        projects=tuple(insights.tickets.projects),
    )


def _counts(fitted: Sequence[_Fitted]) -> Counts:
    """The distinct commits, requests and tickets in `fitted`."""
    groups = [group for _, kept, _ in fitted for group in kept]
    commits = {sha for group in groups for line in group.lines for sha in line.commits}
    requests = {group.request for group in groups if group.request}
    tickets = {group.ticket for group in groups if group.ticket}
    return Counts(len(commits), len(requests), len(tickets))


def _minus(a: Counts, b: Counts) -> Counts:
    return Counts(a.commits - b.commits, a.requests - b.requests, a.tickets - b.tickets)


def _omitted(max_chars: int, listed: Counts, omitted: Counts) -> _Line:
    """The header's last line, under a budget: what it left out, of how many."""
    parts = [
        f"{omitted.commits:,} of {_n(listed.commits, 'commit')}",
        f"{omitted.requests:,} of {_n(listed.requests, 'request')}",
        f"{omitted.tickets:,} of {_n(listed.tickets, 'ticket')}",
    ]
    return _Line(
        f"Omitted to fit {max_chars:,} characters: {', '.join(parts)}. A ticket, request or "
        'repository that lost lines says "(showing k of n)"; a section that lost lines ends with '
        "[… N more omitted …], N counting every line it left out."
    )


def _body(fitted: Sequence[_Fitted]) -> list[_Line]:
    """The sections' lines, each section ending with its marker when it left lines out."""
    out: list[_Line] = []
    for section, groups, left_out in fitted:
        out += section.head
        for group in groups:
            out += [group.head, *group.lines]
        if left_out:
            out.append(_Line(MARKER.format(n=f"{left_out:,}")))
    return out
