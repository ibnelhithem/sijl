"""Assemble the summary report, and write the sentences it is made of.

The pipeline is discover → collect → collapse → deliveries → squash merges → aggregate →
links → this. Every number comes from one model, `Insights` (aggregate.py), computed once in
`build_report()` — after the squash merges have moved commits in and out of the totals — and
carried by the `Report`; nothing below counts again. The page itself is drawn by Jinja2
templates (render/: `render_markdown()` hands the report over): the templates place the headings,
tables and lists; this module writes every sentence whose words depend on the numbers — the
summary paragraph (a fixed template filled with numbers; no AI), the opening of *Delivered*,
*Tickets* and *Not pushed yet*, and every Caveats line. The Caveats section is never empty: it
names everything that was skipped, left out or not handled yet.
Text that comes from a repository — names, subjects, prefixes, ticket ids, what the user's category
function raised — goes through `_md()`, `_code()` or `_cell()` in these sentences, and reaches a
template only as `render.safety.Text`, which prints through the same helpers; every line of the
page goes through `printable()`.

The window is stated in three places: its days and preset in the header and the summary;
how it counts — author date, each commit's own timezone — in the header and the first caveat,
which also says when a running quarter, half or year was cut at today.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from calendar import monthrange
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from sijl.aggregate import Categorise, Insights, MonthTotal, Streak, TicketTotal, aggregate
from sijl.classify import review_category
from sijl.collect import Collection, RepoCommits, collect
from sijl.deliveries import TicketsOf, find_deliveries, no_tickets
from sijl.discover import DEFAULT_MAX_DEPTH, discover
from sijl.git import GitRunner, SshRunner
from sijl.identity import IdentitySet
from sijl.models import BOTH, LINKED, PROMOTED, Delivery, Lines, RawCommit, Reach, Skipped
from sijl.normalize import collapse
from sijl.remotes import UNKNOWN_HOST, UNREADABLE, Forges, HostResolver, Links, link_commits
from sijl.squash import link_squashes
from sijl.tickets import ticket_finder
from sijl.window import Window, long_day

if TYPE_CHECKING:  # render/ imports this module: only for the annotation
    from sijl.render.markdown import CustomTemplate

FOOTER = "*These figures are evidence of activity, not a performance score.*"
PRUNED_NAMED = 10  # folders pruned by rule named in Caveats; `sijl repos` lists every one
AREAS_SHOWN = 10  # non-standard prefixes named in Caveats; the rest are counted, not named
STREAK_NEUTRAL = "weekends neither break nor extend a streak"
STREAK_RULE = f"{STREAK_NEUTRAL}; a weekday without a commit — a holiday or leave too — ends it"
MONTHS_SHOWN = 24  # past this many months, empty ones before and after the work are folded
_WHERE = {Reach.LOCAL: "local branch", Reach.WORKTREE: "worktree only"}
_TICKS = re.compile(r"`+")
# A link Sijl built itself (remotes.WebRepo): https, a host, an optional port, then only
# unreserved characters and percent escapes. Anything else is shown as text, never as a link.
_LINK = re.compile(r"https://[a-z0-9._-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%-]+)+")
LINKS_NOTE = (
    "A commit's SHA links to the GitHub or GitLab project of the first remote whose branches hold "
    "it (`origin` first); commits on no remote branch, and the SHAs of copies, are not linked."
)
# What can start a link or an image (`[`), an autolink or an HTML tag (`<`), and `\` so an escape
# can never be cancelled. `]` and `>` close nothing once these are escaped, so "->" stays as is.
_MARKUP = re.compile(r"([\\\[<])")
_MARKUP_IN_LINK = re.compile(r"([\\\[\]<])")  # inside a link's text, "]" would end it early
REQUESTS = ("merge request or pull request", "merge requests and pull requests")
DELIVERIES_NOTE = (
    "Deliveries are merge requests (GitLab: `See merge request …!N`) and pull requests (GitHub: "
    "`Merge pull request #N`) merged into a mainline — a remote's default branch — or, for a "
    "merge request, into another long-lived branch (`main`, `master`, `develop`, `release/…`) "
    "whose merge commit has since reached a mainline, dated by the merge commit's author date: "
    "yours when they merged a commit you wrote, whoever merged them; merged for others when you "
    "merged them and wrote none of their commits. Back-merges (a long-lived branch merged into "
    "another) and sync merges are never deliveries. A merge request links to the GitLab project "
    "its merge commit names, a pull request to the one GitHub project holding it. A GitHub "
    "squash merge — a one-parent commit GitHub committed, its subject ending `(#N)` — is a pull "
    "request too, dated by its own author date, and so is a merge commit GitHub committed with "
    "that subject; a subject ending `(#N)` in any other commit is just an issue reference."
)
PREFIXES_SHOWN = 10  # ticket-shaped prefixes outside jira.projects, in *Tickets* and Caveats
REFUSED_SHOWN = 10  # ids the false-positive guard refused, named in Caveats
POSITIONS_NOTE = (
    "*In a ticket position*: before the subject (`ABC-1: …`), as its scope (`feat(ABC-1): …`), "
    "in brackets at its end, in a merged branch's name, on a `Closes` or `Refs` line, or in a "
    "trailer. A prefix seen only in running text is a weaker hint."
)
CATEGORY_LABELS = {  # REVIEW_CATEGORIES, as a reviewer reads them
    "feature": "Features",
    "fix": "Fixes",
    "quality": "Quality",
    "docs": "Documentation",
    "tooling": "Tooling",
    "other": "Other",
}
FAST_FORWARD_NOTE = (
    "Merge requests and pull requests merged by fast-forward or rebase — or squashed anywhere but "
    "on GitHub's website — leave no merge commit git can recognise, so git alone cannot list them "
    "as deliveries (the GitLab and GitHub APIs can; a future version may ask them)."
)
SQUASH_MERGED_BY = "not recorded (squash)"
LINES_NOTE = (
    "Lines of a squash merge counted as code are its own change against the commit before it "
    "(`git log --numstat`: a renamed file counts what changed in it, a binary file has no lines)."
)
WHO_SQUASHED = (
    "Who merged a squash merge is not in git — GitHub is its committer, the pull request's author "
    "its author — so *Merged by* says *not recorded*."
)


@dataclass(frozen=True)
class Report:
    person: str
    emails: tuple[str, ...]
    window: Window
    roots: tuple[Path, ...]
    repos_scanned: int
    collection: Collection
    insights: Insights  # every number of the report, computed once (aggregate.py)
    not_scanned: tuple[Skipped, ...]
    generated_on: date
    not_searched: tuple[Skipped, ...] = ()  # folders pruned by rule during discovery
    links: Links = field(default_factory=Links)  # commit links, by repository

    @property
    def total(self) -> int:
        return self.insights.total

    @property
    def active_days(self) -> int:
        return self.insights.active_days

    @property
    def active_repos(self) -> list[RepoCommits]:
        """The repositories with counted commits, in the Insights' order: busiest first."""
        by_repo = {rc.repo: rc for rc in self.collection.repos}
        return [by_repo[repo] for repo, _ in self.insights.per_repo]

    def deliveries(self, *, yours: bool) -> list[tuple[RepoCommits, list[Delivery]]]:
        """Yours (`yours=True`) or the ones you merged for others, by repository — the busiest
        first — each newest first."""
        found = [
            (rc, [d for d in rc.deliveries if d.is_yours == yours]) for rc in self.collection.repos
        ]
        found = [(rc, items) for rc, items in found if items]
        return sorted(found, key=lambda item: (-len(item[1]), item[0].repo.name))


def build_report(
    *,
    roots: Sequence[Path],
    paths: Sequence[Path],
    identities: IdentitySet,
    window: Window,
    git: GitRunner,
    generated_on: date,
    max_depth: int = DEFAULT_MAX_DEPTH,
    person: str = "",
    include_worktrees: bool = False,
    dedupe: str = "key",
    forges: Forges | None = None,
    ssh: SshRunner | None = None,
    tickets_of: TicketsOf | None = None,
    categorise: Categorise = review_category,
    jira_projects: Sequence[str] = (),
) -> Report:
    """`ssh` resolves ssh host aliases (`forges.ssh_aliases`); without it none is resolved.
    `tickets_of` finds ticket keys in a delivery's branch, title and message; by default the
    keys of `jira_projects` (`tickets.ticket_finder()`), none when no project is named."""
    found = discover(roots, paths, git, max_depth=max_depth)
    collection = collect(found.repos, identities, window, git, include_worktrees=include_worktrees)
    collection = collapse(collection, git, dedupe=dedupe)
    if tickets_of is None:  # a delivery's tickets: the keys of your Jira projects
        tickets_of = ticket_finder(jira_projects) if jira_projects else no_tickets
    collection = find_deliveries(collection, identities, window, git, tickets_of=tickets_of)
    collection = link_squashes(collection, window, git, tickets_of=tickets_of)
    # the one count of everything, after the squash merges moved commits in and out;
    # no month after today is listed; tickets of jira_projects
    insights = aggregate(
        collection, window, categorise=categorise, today=generated_on, projects=jira_projects
    )
    links = link_commits(collection.repos, git, forges or Forges(), HostResolver(ssh))
    names = Counter(c.author_name for c in collection.commits)
    return Report(
        person=person or (names.most_common(1)[0][0] if names else sorted(identities.emails)[0]),
        emails=tuple(sorted(identities.emails)),
        window=window,
        roots=tuple(roots),
        repos_scanned=len(collection.repos),  # read successfully; failures are in not_scanned
        collection=collection,
        insights=insights,
        not_scanned=found.skipped + collection.skipped,
        generated_on=generated_on,
        not_searched=found.pruned,
        links=links,
    )


# ---------------------------------------------------------------------------------- render


def _count(n: int, noun: str, plural: str = "") -> str:
    return f"{n:,} {noun if n == 1 else plural or noun + 's'}"


def _link(text: str, url: str) -> str:
    """A Markdown link Sijl built itself. `url` must look like one (`_LINK`) or only the text is
    shown; the text is escaped like any other, so neither part can break out of the link."""
    return f"[{_md(text, in_link=True)}]({url})" if _LINK.fullmatch(url) else _md(text)


def _lines(lines: Lines | None) -> str:
    """`+120 -30 lines` (and `, 1 binary file`), or `lines not read`."""
    if lines is None:
        return "lines not read"
    text = f"+{lines.added:,} -{lines.removed:,} lines"
    if lines.binary:
        text += f", {_count(lines.binary, 'binary file')}"
    return text


def _names(items: Sequence[str]) -> str:
    """Names as a sentence lists them (`_md()` each): PROJ; PROJ and OPS; PROJ, QC and OPS."""
    names = [_md(item) for item in items]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def _listed(items: Sequence[tuple[str, int]], limit: int, more: str, mores: str) -> str:
    """`name (n)` for the first `limit` items — each name through `_md()` — then how many more."""
    text = ", ".join(f"{_md(name)} ({n:,})" for name, n in items[:limit])
    rest = len(items) - limit
    return text + (f", plus {_count(rest, more, mores)}" if rest > 0 else "")


def _commit_cell(
    rc: RepoCommits, commit: RawCommit, url: str = "", links: Links | None = None
) -> str:
    """The short SHA — a link to the commit when it has one — then the SHAs of any copies
    collapsed into it, as plain text: a copy may live in another clone or nowhere online.
    Then what a squash merge did with it: the pull request that delivered it, or, for a
    squash merge counted as code, its lines."""
    short = f"`{commit.sha[:8]}`"
    cell = _link(short, url) if url else short
    copies = rc.copies.get(commit.sha, ())
    if copies:
        label = "copy" if len(copies) == 1 else "copies"
        cell += f" ({label} " + ", ".join(f"`{sha[:8]}`" for sha in copies) + ")"
    via = rc.via.get(commit.sha)
    if via is not None:
        request = links.request_url(rc.repo.path, via) if links is not None else ""
        cell += f" (delivered via {_link(via.ref, request) if request else via.ref})"
    if commit.sha in rc.lines:  # a squash merge counted as code: its lines, or "not read"
        cell += f" (squash merge, {_lines(rc.lines[commit.sha])})"
    return cell


def _md(text: str, *, in_link: bool = False) -> str:
    """`text` shown as itself in Markdown, as a **self-contained piece**: whatever text sits beside
    it on the same line, nothing in it can become a link, an image or an HTML tag.

    Outside code spans, `\\`, `[` and `<` are escaped — `[` starts a link or an image, `<` an
    autolink or a tag, and `\\` is escaped so an escape can never be cancelled. A code span
    (`like this`, closed by a run of as many backticks) is kept as written: nothing inside one is
    markup. Every backtick that closes nothing is escaped too: left raw, it would pair with a
    backtick in the *next* piece on the line (another repository's name, a remote's) and turn
    the escaped text between them into code and the code after them into live Markdown.
    `in_link` also escapes `]`, which would end a link's text
    early.

    Every backtick run is found once, and each run's closer — the next run of the same length — is
    looked up by bisection: scanning the rest of the text for each run that closes nothing was
    O(n^1.5) on a crafted subject (about a minute for 1 MB)."""
    markup = _MARKUP_IN_LINK if in_link else _MARKUP
    runs = [(m.start(), m.end()) for m in _TICKS.finditer(text)]
    by_length: dict[int, list[int]] = defaultdict(list)  # run length → its runs' indexes, in order
    for index, (start, end) in enumerate(runs):
        by_length[end - start].append(index)
    out, pos, index = [], 0, 0
    while index < len(runs):
        start, end = runs[index]
        out.append(markup.sub(r"\\\1", text[pos:start]))
        same = by_length[end - start]
        later = bisect_right(same, index)
        if later == len(same):  # closes nothing in this piece: escaped, so it pairs with nothing
            out.append("\\`" * (end - start))
            pos, index = end, index + 1
        else:
            closing = same[later]
            pos, index = runs[closing][1], closing + 1
            out.append(text[start:pos])
    out.append(markup.sub(r"\\\1", text[pos:]))
    return "".join(out)


def _code(text: str) -> str:
    """`text` as one code span — inert in Markdown — fenced by more backticks than any run in it
    (CommonMark strips the padding spaces again). Empty text is "(empty)": a bare "``" is not a
    code span but an open backtick run that would pair with the next piece's."""
    if not text:
        return "(empty)"
    fence = "`" * (max((len(m.group()) for m in _TICKS.finditer(text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _cell(text: str) -> str:
    """A table cell: Markdown escaped, and `|` escaped everywhere (GFM splits cells on it, even in
    code spans)."""
    return _md(text).replace("|", "\\|")


def _between(window: Window) -> str:
    """'1 October 2026 and 8 October 2026 (Q4 2026 to date)'; the days alone for --since."""
    days = f"{long_day(window.start)} and {long_day(window.end)}"
    return f"{days} ({window.label})" if window.label else days


def _delivered_counts(report: Report) -> tuple[int, int, int, int]:
    """(yours, yours you merged yourself, yours squash-merged — merger unknown — merged for
    others), read from the model."""
    insights = report.insights
    return (
        insights.delivered,
        insights.delivered_by_you,
        insights.delivered_squashed,
        insights.merged_for_others,
    )


def delivered_sentence(report: Report) -> str:
    """The summary's last sentence: how many requests of yours were merged and how many you
    merged for colleagues; "" when there are none."""
    yours, _, _, others = _delivered_counts(report)
    if yours:
        text = f" {_count(yours, *REQUESTS)} of yours {'was' if yours == 1 else 'were'} merged"
        text += f", and you merged {others:,} for colleagues" if others else ""
        return text + " (see *Delivered*)."
    if others:
        return f" You merged {_count(others, *REQUESTS)} for colleagues (see *Delivered*)."
    return ""


def share(n: int, total: int) -> str:
    """`n` as a whole percentage of `total`, half rounded up — but never "0%" for some work nor
    "100%" for less than all of it."""
    if n * 100 < total:
        return "<1%"
    percent = (n * 200 + total) // (total * 2)
    return ">99%" if percent == 100 and n < total else f"{percent}%"


def month_label(month: str, window: Window, today: date | None = None) -> str:
    """A "YYYY-MM" month as "April 2026", plus the days covered when the period starts or ends
    inside it — or today does — so a short first or last month does not read as a quiet one."""
    name = date.fromisoformat(f"{month}-01").strftime("%B")
    start = window.start
    end = min(window.end, today) if today is not None else window.end
    cut_start = start.strftime("%Y-%m") == month and start.day > 1
    # not the month's last day — asked of the calendar, never `end + 1 day` (9999-12-31 overflows)
    cut_end = end.strftime("%Y-%m") == month and end.day < monthrange(end.year, end.month)[1]
    label = f"{name} {month[:4]}"
    if cut_start and cut_end:
        return f"{label} ({start.day} to {end.day} {name})"
    if cut_start:
        return f"{label} (from {start.day} {name})"
    if cut_end:
        return f"{label} (to {end.day} {name})"
    return label


def _streak_sentence(streak: Streak, total: int) -> str:
    """The summary's sentence about the longest streak — always with the rule beside the number
    — or, when every commit was on a weekend, that there is none; "" with no commits."""
    if not streak.weekdays or streak.first is None or streak.last is None:
        if not total:
            return ""
        return (
            " Every commit was made on a Saturday or a Sunday, so there is no streak of weekdays "
            f"({STREAK_NEUTRAL})."
        )
    if streak.weekdays == 1:
        return (
            f" Your longest streak was **1 weekday** with commits, on {long_day(streak.first)} "
            f"({STREAK_RULE})."
        )
    return (
        f" Your longest streak was **{_count(streak.weekdays, 'weekday')}** in a row with "
        f"commits, from {long_day(streak.first)} to {long_day(streak.last)} ({STREAK_RULE})."
    )


def summary_paragraph(report: Report) -> str:
    period = _between(report.window)
    if not report.total:
        return (
            f"No commits by {_md(', '.join(report.emails))} were found between {period} in the "
            f"{_count(report.repos_scanned, 'repository', 'repositories')} scanned."
        ) + delivered_sentence(report)
    insights = report.insights
    top = ", ".join(f"{_md(repo.name)} ({n:,})" for repo, n in insights.per_repo[:3])
    text = (
        f"Between {period} you authored **{_count(insights.total, 'commit')}** on "
        f"**{_count(insights.active_days, 'active day')}** across "
        f"**{_count(len(insights.per_repo), 'repository', 'repositories')}** — most in {top}."
    )
    text += _streak_sentence(insights.longest_streak, insights.total)
    unpushed = report.insights.not_pushed
    if unpushed:
        verb = "is" if unpushed == 1 else "are"
        text += f" {unpushed:,} more {'commit' if unpushed == 1 else 'commits'} {verb} not "
        text += "pushed yet and not in these numbers (see *Not pushed yet*)."
    return text + delivered_sentence(report)


def delivered_paragraph(report: Report) -> str:
    """The paragraph that opens *Delivered*: how many requests of yours were merged and how
    — by you, by colleagues, as GitHub squash merges — and how many you merged for colleagues; or
    that there were none. Its tables are the template's (render/templates/delivered.md.j2)."""
    yours, by_you, squashed, others = _delivered_counts(report)
    if not yours and not others:
        return (
            "No merge request or pull request of yours was merged into a mainline in this period, "
            "and you merged none for colleagues."
        )
    if yours:
        by_them = yours - by_you - squashed
        text = f"**{_count(yours, *REQUESTS)}** of yours {'was' if yours == 1 else 'were'} merged "
        colleagues = "a colleague" if by_them == 1 else "colleagues"
        how = [
            (by_you, "by you", "by you"),
            (by_them, f"by {colleagues}", f"by {colleagues}"),
            (
                squashed,
                f"as {'a GitHub squash merge' if squashed == 1 else 'GitHub squash merges'} "
                "(git does not record by whom)",
                "as a GitHub squash merge" if squashed == 1 else "as GitHub squash merges",
            ),
        ]
        present = [(n, alone, among) for n, alone, among in how if n]
        if len(present) > 1:  # one dash opens the list; a second only before what follows it
            text += "into a mainline — "
            text += ", ".join(f"{n:,} {among}" for n, _, among in present)
            text += " —" if others else ""
        else:
            text += f"into a mainline, {'' if yours == 1 else 'all '}{present[0][1]}"
            text += "," if others else ""
        text += f" and you merged {others:,} for colleagues." if others else "."
    else:
        text = f"You merged **{_count(others, *REQUESTS)}** for colleagues."
    text += (
        " Each is dated by its merge commit; the commits it merged are counted under *Per "
        "repository*, never again here."
    )
    if squashed:
        text += (
            " A squash merge is counted once too — as the commits of yours linked to it, or as "
            "itself when its branch commits are gone — and *Caveats* says how each was decided."
        )
    return text


def your_commits_cell(d: Delivery) -> str:
    """A delivery of yours' *Your commits* cell: "2 of 3", or how a squash merge was counted."""
    return _squashed(d) if d.squash else f"{d.yours:,} of {d.commits:,}"


def merged_by_cell(d: Delivery) -> str:
    """A delivery of yours' *Merged by* cell — never a colleague's name: "you", "a colleague",
    or, for a squash merge, "not recorded (squash)"."""
    if d.squash:
        return SQUASH_MERGED_BY
    return "you" if d.role == BOTH else "a colleague"


def _squashed(d: Delivery) -> str:
    """A squash merge's *Your commits* cell: the commits linked to it, or the squash itself
    counted with its lines — "not linked" when commits of yours may be its own."""
    if d.squash == LINKED:
        return f"{d.yours:,} linked"
    label = "the squash" if d.squash == PROMOTED else "the squash, not linked"
    return f"{label}: {_lines(d.lines)}" if d.lines else f"{label} (lines not read)"


def delivery_caveats(report: Report) -> list[str]:
    """Your merge commits by what they were, what git cannot show (fast-forward, rebase and squash
    merges), what could not be read or placed, and how deliveries are chosen."""
    c = report.collection
    lines = []
    if c.merges_left_out:
        n = c.merges_left_out
        by_hand = n - c.merges_delivered - c.merges_sync - c.merges_elsewhere - c.merges_unread
        parts = [
            (c.merges_delivered, "listed under *Delivered*"),
            (
                c.merges_sync,
                "brought a long-lived branch into another branch ("
                + ("a sync merge or back-merge" if c.merges_sync == 1 else "sync and back-merges")
                + ", ignored)",
            ),
            (
                c.merges_elsewhere,
                "merged a merge request or pull request into a branch other than a mainline "
                "(not listed)",
            ),
            (c.merges_unread, "could not be traced on a mainline (not listed)"),
            (by_hand, "merged a branch with no merge request or pull request (not listed)"),
        ]
        lines.append(
            f"{_count(n, 'merge commit')} you made {'is' if n == 1 else 'are'} not counted as "
            f"{'a commit' if n == 1 else 'commits'}: "
            + ", ".join(f"{count:,} {what}" for count, what in parts if count)
            + "."
        )
    if c.landed_without_request:
        n = c.landed_without_request
        one = n == 1
        lines.append(
            f"{_count(n, 'commit')} of yours reached a mainline with no merge request or pull "
            "request merge commit — a fast-forward or rebase merge, a squash not made on GitHub's "
            "website, a merge made by hand, or a push straight to it. "
            f"{'It is' if one else 'They are'} counted as {'a commit' if one else 'commits'}; "
            f"which request delivered {'it' if one else 'them'} cannot be seen in git alone (the "
            "GitLab and GitHub APIs can; a future version may ask them)."
        )
    else:
        lines.append(FAST_FORWARD_NOTE)
    for rc in sorted(c.repos, key=lambda rc: rc.repo.name):
        if rc.delivery_error:
            lines.append(
                f"Merged commits could not be read in {_md(rc.repo.name)} "
                f"({_md(rc.delivery_error)}): its merge requests and pull requests are not listed."
            )
    if c.deliveries_in_other_clones:
        n = c.deliveries_in_other_clones
        lines.append(
            f"{_count(n, 'delivery', 'deliveries')} found in more than one clone "
            f"{'was' if n == 1 else 'were'} listed once."
        )
    return lines + squash_caveats(report)


def squash_caveats(report: Report) -> list[str]:
    """How every squash merge of yours was counted, and every commit it moved."""
    t = report.collection.squashes
    start = report.collection.lookback_start
    days = (report.window.start - start).days if start else 0
    lines = []
    if t.found:
        parts = []
        if t.linked:
            one = t.linked == 1
            parts.append(
                f"{t.linked:,} {'was' if one else 'were'} linked to the commits of yours on "
                f"{'its branch' if one else 'their branches'}, which are counted instead of the "
                f"{'squash' if one else 'squashes'}"
            )
        if t.promoted:
            one = t.promoted == 1
            parts.append(
                f"{t.promoted:,} whose branch commits are gone — none of your commits written "
                f"since your previous delivery is left unlinked — {'is' if one else 'are'} "
                f"counted as code {'itself' if one else 'themselves'}, with "
                f"{'its' if one else 'their'} lines"
            )
        if t.unlinked:
            one = t.unlinked == 1
            doubt = [
                (t.unlinked_pushed, "pushed and counted in this period, where that work may be "
                 "counted twice"),
                (t.unlinked_other, "not counted in this period (only on your laptop, or written "
                 "before it)"),
            ]  # fmt: skip
            parts.append(
                f"{t.unlinked:,} could not be linked to branch commits and {'is' if one else 'are'}"
                f" counted as code {'itself' if one else 'themselves'}, with "
                f"{'its' if one else 'their'} lines — of your commits written since your previous "
                "delivery that are linked to no squash and may be "
                f"{'its branch' if one else 'their branches'}, "
                + " and ".join(f"{n:,} {what}" for n, what in doubt if n)
            )
        n = t.found
        lines.append(
            f"{_count(n, 'GitHub squash merge')} of yours (one-parent commits GitHub committed, "
            f"the subject ending `(#N)`) {'is' if n == 1 else 'are'} listed under *Delivered* and "
            "counted once: " + "; ".join(parts) + "."
        )
    if t.commits_linked:
        n, one = t.commits_linked, t.commits_linked == 1
        how = [
            (t.by_tree, "by tree (a branch tip with the squash's tree)"),
            (t.by_subject, "by subject (the squash's title, or an item GitHub listed in it)"),
            (t.by_branch, "below such a tip on its branch"),
            (t.by_ticket, "by a ticket key both name"),
        ]
        text = (
            f"{n:,} of your commits {'was' if one else 'were'} linked to a squash merge — "
            + ", ".join(f"{count:,} {what}" for count, what in how if count)
            + f" — and {'is' if one else 'are'} marked *delivered via #N*"
        )
        if t.relabelled:
            r = t.relabelled
            text += (
                f"; {r:,} of them {'is' if r == 1 else 'are'} on no remote branch and "
                f"{'is' if r == 1 else 'are'} counted, not listed under *Not pushed yet*"
            )
        if t.after_period:
            a = t.after_period
            text += (
                f"; {_count(a, 'squash merge')} after the period delivered commits written in it "
                "(not listed)"
            )
        lines.append(text + ".")
    if t.lookback_limited:
        n = t.lookback_limited
        lines.append(
            f"For {_count(n, 'squash merge')} of yours counted as code, no earlier delivery of "
            f"yours was found within the {days} days read before the period: branch commits "
            f"written before that are not read, so {'it' if n == 1 else 'they'} may hold work "
            "already counted in an earlier period."
        )
    if t.linked_earlier:
        n = t.linked_earlier
        here = "the squash is a delivery" if n == 1 else "the squash merges are deliveries"
        lines.append(
            f"{_count(n, 'squash merge')} in this period {'holds' if n == 1 else 'hold'} commits "
            f"written before it (read in the {days} days before the period): that work counts in "
            f"the period it was written; here {here}, not code."
        )
    if t.promoted or t.unlinked:
        text = LINES_NOTE
        if t.lines_unread:
            text += (
                f" Lines could not be read for {t.lines_unread:,} of them (a partial clone, where "
                "reading a change would fetch file contents into it, or a git error)."
            )
        lines.append(text)
    if t.found:
        lines.append(WHO_SQUASHED)
    if t.roots_unread:
        n = t.roots_unread
        lines.append(
            f"Root commits could not be read in {_count(n, 'repository', 'repositories')} while "
            "linking squash merges: a squash merge there is linked only to commits in the same "
            "clone, never in another clone of the same project."
        )
    if t.off_mainline:
        n, one = t.off_mainline, t.off_mainline == 1
        lines.append(
            f"{_count(n, 'squash merge')} of yours {'is' if one else 'are'} on no mainline's own "
            f"line ({'a pull request' if one else 'pull requests'} squashed into "
            f"{'another branch' if one else 'other branches'}): counted as "
            f"{'an ordinary commit' if one else 'ordinary commits'}, not linked."
        )
    return lines


def period_caveat(window: Window) -> str:
    """How the window counts — always the first caveat — and whether it was cut at today."""
    line = (
        "Commits are counted by author date — the day each was written, in its author's own "
        f"timezone — from {long_day(window.start)} to {long_day(window.end)}, both days "
        "included; rebasing or pushing a commit later does not move it."
    )
    if window.to_date:
        line += f" {window.name} is not over yet, so the period stops at today, "
        line += f"{long_day(window.end)}."
    return line


def caveats(report: Report) -> list[str]:
    c = report.collection
    lines = [
        period_caveat(report.window),
        f"Scanned {_count(report.repos_scanned, 'repository', 'repositories')}"
        + (f" under {', '.join(_code(str(r)) for r in report.roots)}" if report.roots else "")
        + ".",
    ]
    lines += [f"Not scanned: {_code(str(s.path))} — {_md(s.reason)}." for s in report.not_scanned]
    if report.not_searched:
        n = len(report.not_searched)
        named = ", ".join(_code(str(s.path)) for s in report.not_searched[:PRUNED_NAMED])
        more = (
            f" and {n - PRUNED_NAMED} more (`sijl repos` lists them all)"
            if n > PRUNED_NAMED
            else ""
        )
        lines.append(
            f"{_count(n, 'folder')} {'was' if n == 1 else 'were'} not searched, by rule — hidden, "
            f"dependency and build folders hold tools, not projects: {named}{more}."
        )
    lines += delivery_caveats(report)
    if c.seen_in_other_clones:
        verb = "was" if c.seen_in_other_clones == 1 else "were"
        lines.append(
            f"{_count(c.seen_in_other_clones, 'commit')} found in more than one clone {verb} "
            "counted once."
        )
    unpushed = report.insights.not_pushed
    if unpushed:
        lines.append(
            f"{_count(unpushed, 'commit')} on no remote branch "
            f"{'is' if unpushed == 1 else 'are'} listed under *Not pushed yet* and left out of "
            "the totals."
        )
    unchecked = sorted(_md(rc.repo.name) for rc in c.repos_without_remote if rc.commits)
    if unchecked:
        lines.append(
            f"No remote-tracking branches in {', '.join(unchecked)}: commits there are counted, "
            "but whether they were pushed cannot be checked."
        )
    if c.tags_ignored:
        lines.append(
            f"{_count(c.tags_ignored, 'tag')} that point at no commit (a tree, a file or "
            "another tag) were not read."
        )
    match = "author, author time and subject" + (", and patch" if c.dedupe == "patch-id" else "")
    if c.copies_collapsed:
        n = c.copies_collapsed
        lines.append(
            f"{_count(n, 'rebased or cherry-picked copy', 'rebased or cherry-picked copies')} of "
            f"a commit (same {match}) {'is' if n == 1 else 'are'} not counted again; the copies' "
            "SHAs are listed beside the one kept, the best-pushed."
        )
    else:
        lines.append(f"No rebased or cherry-picked copies of a commit were found (same {match}).")
    if c.patch_splits:
        n = c.patch_splits
        lines.append(
            f"{_count(n, 'commit')} with the same author time and subject as another "
            f"{'is' if n == 1 else 'are'} kept as separate work: the changes differ "
            "(`--dedupe patch-id`)."
        )
    if c.patch_unread:
        n = c.patch_unread
        lines.append(
            f"{_count(n, 'commit')} could not have {'its' if n == 1 else 'their'} patch read (a "
            f"partial clone, or a git error) and {'was' if n == 1 else 'were'} matched by author, "
            "author time and subject alone."
        )
    if c.roots_unread:
        lines.append(
            f"Root commits could not be read in "
            f"{_count(c.roots_unread, 'repository', 'repositories')}: copies there are not "
            "matched with other clones of the same project."
        )
    lines += link_caveats(report)
    lines += _classification_caveats(report.insights)
    lines += _ticket_caveats(report)
    lines += [
        "History is read from local branches, remote-tracking branches, tags and the main "
        "checkout's HEAD; stash entries, notes and other refs are never read.",
        "Commits that exist only in a detached linked worktree are included and marked "
        "*worktree only*."
        if c.include_worktrees
        else "Commits that exist only in a detached linked worktree are left out "
        "(`--include-worktrees` adds them).",
        LINKS_NOTE,
        DELIVERIES_NOTE,
    ]
    return lines


def link_caveats(report: Report) -> list[str]:
    """What got no link and why: commits pushed only where no link can be built, hosts that
    are not known forges, remotes that are folders or cannot be read — never a remote's URL, which
    can hold a password — and the ssh aliases that were resolved."""
    names = {rc.repo.path: rc.repo.name for rc in report.collection.repos}
    found = sorted(report.links.repos.items(), key=lambda item: (names[item[0]], str(item[0])))
    lines = []
    unlinked = [(_md(names[path]), rl.unlinked) for path, rl in found if rl.unlinked]
    if unlinked:
        n = sum(count for _, count in unlinked)
        one = n == 1
        where = ", ".join(f"{name} ({count:,})" for name, count in unlinked)
        lines.append(
            f"{_count(n, 'commit')} on remote branches {'has' if one else 'have'} no link, only "
            f"{'its SHA' if one else 'their SHAs'}: no remote holding {'it' if one else 'them'} "
            f"is a GitHub or GitLab project Sijl recognises — {where}."
        )
    requests = [
        (_md(names[path]), rl.unlinked_requests) for path, rl in found if rl.unlinked_requests
    ]
    if requests:
        n = sum(count for _, count in requests)
        one = n == 1
        where = ", ".join(f"{name} ({count:,})" for name, count in requests)
        lines.append(
            f"{_count(n, *REQUESTS)} {'has' if one else 'have'} no link, only "
            f"{'its' if one else 'their'} number: no remote is the GitLab project "
            f"{'its' if one else 'their'} merge commit names, or not exactly one GitHub project "
            f"holds {'it' if one else 'them'} — {where}."
        )
    unconfirmed = [
        (_md(names[path]), rl.pulls_unconfirmed) for path, rl in found if rl.pulls_unconfirmed
    ]
    if unconfirmed:
        n = sum(count for _, count in unconfirmed)
        one = n == 1
        where = ", ".join(f"{name} ({count:,})" for name, count in unconfirmed)
        lines.append(
            f"{_count(n, 'pull request')} {'is' if one else 'are'} linked to the only GitHub "
            f"project whose branches hold {'its' if one else 'their'} merge — {where}. In a fork "
            "synced from its upstream, a pull request's number may belong to the upstream project "
            "instead; only the GitHub API can tell."
        )
    unknown: dict[str, set[str]] = {}
    aliases: set[str] = set()
    unreadable, elsewhere, errors = [], [], []
    for path, rl in found:
        name = _md(names[path])
        if rl.error:
            errors.append(f"{name} ({_md(rl.error)})")
        for r in rl.remotes:
            if r.web is not None and r.alias:
                aliases.add(f"{_code(r.alias)} → {_code(r.web.host)}")
            elif r.problem == UNKNOWN_HOST:
                label = f"{_code(r.alias)} → {_code(r.host)}" if r.alias else _code(r.host)
                if r.alias_error:
                    label += f" (not resolved: {_md(r.alias_error)})"
                unknown.setdefault(label, set()).add(name)
            elif r.problem == UNREADABLE:
                unreadable.append(f"{_code(r.name)} in {name}")
            elif r.problem:
                elsewhere.append(f"{_code(r.name)} in {name} ({r.problem})")
    if aliases:
        lines.append(f"ssh host aliases resolved with `ssh -G`: {', '.join(sorted(aliases))}.")
    if unknown:
        hosts = ", ".join(
            f"{label} ({', '.join(sorted(repos))})" for label, repos in unknown.items()
        )
        lines.append(
            f"Hosts Sijl does not know as GitHub or GitLab, so commits there have no link: "
            f"{hosts}. A GitHub Enterprise or self-hosted GitLab host can be added with "
            "`forges.github_hosts` or `forges.gitlab_hosts`."
        )
    if unreadable:
        lines.append(
            "Remote URLs that could not be read as a project address, so no link comes from them "
            f"(a URL is never shown: it can hold a password): {', '.join(unreadable)}."
        )
    if elsewhere:
        lines.append(
            "Remotes that are not projects on a web host, so no link comes from them: "
            f"{', '.join(elsewhere)}."
        )
    if errors:
        lines.append(
            f"Remotes could not be read in {', '.join(errors)}; commits there have no link."
        )
    return lines


def _classification_caveats(insights: Insights) -> list[str]:
    """What kind *other* holds, and the commits review_category() failed on."""
    lines = []
    held = []
    if insights.non_conventional:
        held.append(
            f"{_count(insights.non_conventional, 'commit')} without a conventional prefix "
            "(`type: …`)"
        )
    if insights.areas:
        n = sum(count for _, count in insights.areas)
        shown, rest = insights.areas[:AREAS_SHOWN], insights.areas[AREAS_SHOWN:]
        areas = ", ".join(f"{_md(area)} ({count:,})" for area, count in shown)
        if rest:
            more = sum(count for _, count in rest)
            areas += f", plus {_count(len(rest), 'more prefix', 'more prefixes')} "
            areas += f"({_count(more, 'commit')})"
        held.append(f"{_count(n, 'commit')} whose prefix is not a standard kind: {areas}")
    if held:
        lines.append(f"Kind *other* holds {' and '.join(held)}.")
    if insights.category_failures:
        n = insights.category_failures
        lines.append(
            f"`review_category()` failed on {_count(n, 'commit')} (the first time it "
            f"{_md(insights.first_category_failure)}); {'it is' if n == 1 else 'they are'} "
            "counted under *other*."
        )
    return lines


def _ticket_caveats(report: Report) -> list[str]:
    """Keys outside `jira.projects`, ids the guard refused, merge branches not read."""
    tickets = report.insights.tickets
    lines = []
    if tickets.outside:
        n = tickets.outside
        if tickets.projects:
            by_commits = sorted(tickets.suggestions, key=lambda s: (-s.commits, s.project))
            prefixes = [(s.project, s.commits) for s in by_commits]  # most first, as left out
            named = _listed(prefixes, PREFIXES_SHOWN, "more prefix", "more prefixes")
            lines.append(
                f"{_count(n, 'commit')} {'names' if n == 1 else 'name'} keys of projects not in "
                f"`jira.projects` — {named} — which are not counted as tickets."
            )
        else:
            lines.append(
                f"No Jira projects are set (`jira.projects`), so the ticket-shaped keys in "
                f"{_count(n, 'commit')} are not counted as tickets; *Tickets* suggests the likely "
                "projects."
            )
    if tickets.rejected:
        n = tickets.rejected_commits
        lines.append(
            f"{_count(n, 'commit')} {'mentions' if n == 1 else 'mention'} ids that only look like "
            "ticket keys — standards, versions or parts of longer ids — and are never counted as "
            f"tickets: {_listed(tickets.rejected, REFUSED_SHOWN, 'more id', 'more ids')}."
        )
    if tickets.from_deliveries:
        n = len(tickets.from_deliveries)
        lines.append(
            f"{_count(n, 'ticket')} {'is' if n == 1 else 'are'} named only by your merge requests "
            "and pull requests — in a branch name, title or message — and by no counted commit: "
            f"{_keys_listed(tickets.from_deliveries)}."
        )
    if tickets.others_only:
        n = len(tickets.others_only)
        lines.append(
            f"{_count(n, 'ticket')} named only by merge requests and pull requests you merged for "
            f"colleagues {'is' if n == 1 else 'are'} not counted as yours: "
            f"{_keys_listed(tickets.others_only)}."
        )
    c = report.collection
    unread = c.merges_left_out - c.merges_delivered  # sync and back-merges, merges by hand
    if tickets.projects and unread:
        lines.append(
            "Ticket keys are read from counted commits and from merge requests and pull requests "
            f"(their branch, title and message); the other {_count(unread, 'merge commit')} you "
            f"made {'is' if unread == 1 else 'are'} not read for tickets."
        )
    elif not tickets.projects and tickets.suggestions and c.merges_left_out:
        lines.append(
            "Suggested prefixes come from counted commits only; merge requests and pull requests "
            "are read for tickets once Jira projects are set."
        )
    return lines


def _keys_listed(keys: Sequence[str]) -> str:
    """Ticket keys, each through `_md()`: the first `PREFIXES_SHOWN`, then how many more."""
    text = ", ".join(_md(key) for key in keys[:PREFIXES_SHOWN])
    rest = len(keys) - PREFIXES_SHOWN
    return text + (f", plus {_count(rest, 'more ticket')}" if rest > 0 else "")


SHARES_NOTE = (
    "Shares are rounded to whole percents, so a table's shares can add up to a few points more or "
    "less than 100%."
)


HIGHLIGHTS_NOTE = (
    "Picked by a fixed rule, not by judgement: the month with the most commits, your merge "
    "requests and pull requests with the most commits of yours, and the tickets named in the most "
    "commits. Each is also in its own section below."
)
APPENDIX_NOTE = (
    "Every counted commit, by repository — the busiest first, the newest commit first in each. "
    "Commits under *Not pushed yet* are not here."
)


def named_in(ticket: TicketTotal) -> str:
    """A highlighted ticket's evidence: "named in 3 commits", "… and 1 merge request or pull
    request of yours", or the requests alone when no commit names it."""
    parts = []
    if ticket.commits:
        parts.append(_count(ticket.commits, "commit"))
    if ticket.deliveries:
        parts.append(f"{_count(ticket.deliveries, *REQUESTS)} of yours")
    return "named in " + " and ".join(parts)


def breaking_sentence(insights: Insights) -> str:
    """*Kinds of work*'s last sentence: how many commits are breaking changes; "" when none."""
    n = insights.breaking
    if not n:
        return ""
    return (
        f"{_count(n, 'commit')} {'is' if n == 1 else 'are'} marked as "
        f"{'a breaking change' if n == 1 else 'breaking changes'} (`!` or a "
        "`BREAKING CHANGE` footer)."
    )


def month_rows(report: Report) -> list[tuple[str, int, int]]:
    """*Per month*'s rows: (label, commits, active days) for every month of the period.

    Past `MONTHS_SHOWN` rows, the empty months before the first month with commits and after the
    last are folded into one row each ("No commits from January 1970 to April 2026"), so
    `--since 1970-01-01` is not 680 rows of zeros. Every month with commits, and every empty month
    between two that have some, is still its own row."""
    insights = report.insights

    def label(m: MonthTotal) -> str:
        return month_label(m.month, report.window, report.generated_on)

    rows = list(insights.per_month)
    before: list[MonthTotal] = []
    after: list[MonthTotal] = []
    if len(rows) > MONTHS_SHOWN and insights.total:
        busy = [i for i, m in enumerate(rows) if m.commits]
        before, after = rows[: busy[0]], rows[busy[-1] + 1 :]
        rows = rows[busy[0] : busy[-1] + 1]

    def folded(months: list[MonthTotal]) -> list[tuple[str, int, int]]:
        if not months:
            return []
        span = label(months[0]) + (f" to {label(months[-1])}" if len(months) > 1 else "")
        return [(f"No commits {'from' if len(months) > 1 else 'in'} {span}", 0, 0)]

    return [
        *folded(before),
        *((label(m), m.commits, m.active_days) for m in rows),
        *folded(after),
    ]


def tickets_sentence(report: Report) -> str:
    """*Tickets*' opening sentence: the tickets of your Jira projects and how many commits
    name one; that none does; or, with no project set, what the suggestions below are for."""
    insights = report.insights
    tickets = insights.tickets
    total, named = insights.total, tickets.with_ticket
    if tickets.per_ticket:
        found = f"**{_count(len(tickets.per_ticket), 'ticket')}** in {_names(tickets.projects)}"
        only = len(tickets.from_deliveries)
        if named:
            unnamed = total - named
            sentence = (
                f"{found}, named in {named:,} of your {_count(total, 'commit')} "
                f"({share(named, total)}); the other {unnamed:,} "
                f"{'names' if unnamed == 1 else 'name'} none."
            )
            if only:
                sentence += (
                    f" {only:,} of them {'is' if only == 1 else 'are'} named only by your merge "
                    "requests and pull requests."
                )
        else:
            sentence = (
                f"{found}, named only by your merge requests and pull requests; none of your "
                f"{_count(total, 'commit')} names one."
            )
        return (
            sentence + " A commit counts once under each ticket it names, wherever its message "
            "names it."
        )
    if tickets.projects:
        return (
            f"None of your {_count(total, 'commit')} names a ticket in "
            f"{_names(tickets.projects)} (`jira.projects`)."
        )
    return (
        "No Jira projects are set, so no commit is grouped by ticket. The prefixes below look "
        "like Jira project keys in your commits; add yours to `jira.projects` in the config "
        "file, or pass `--jira-project`, to count their keys as tickets."
    )


def not_pushed_sentence(report: Report) -> str:
    """*Not pushed yet*'s opening sentence."""
    total = report.insights.not_pushed
    return (
        f"{_count(total, 'commit')} of yours {'is' if total == 1 else 'are'} on no remote "
        "branch, so not in the numbers above."
    )


def where(rc: RepoCommits, commit: RawCommit) -> str:
    """*Not pushed yet*'s *Where*: "local branch" or "worktree only"."""
    return _WHERE[rc.reach[commit.sha]]


def render_markdown(report: Report, template: CustomTemplate | None = None) -> str:
    """The report as Markdown: the built-in templates, or the user's own `template`. Here
    so that callers need not know where templates live; render/ reads this module, so it is
    imported when called."""
    from sijl.render.markdown import render

    return render(report, template)
