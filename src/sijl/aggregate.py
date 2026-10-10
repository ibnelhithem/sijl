"""The numbers of a review, computed once into one model that every renderer reads.

`aggregate()` walks the collapsed collection a single time and returns `Insights`: totals per
repository, per month and per conventional kind, the review categories, breaking changes, active
days and the longest streak. Only **counted** commits take part — work under *Not pushed yet* and
rebased or cherry-picked copies collapsed by normalize.py are already out of `Collection.commits`
— so every breakdown adds up to the report's headline total.

Months are calendar months of each commit's author day, in the author's own timezone,
and every month of the window is listed, an empty one too — up to today's month: nothing can have
been written in a month that has not begun, so `--until 9999-12-31` lists no future months.

The longest streak counts **weekdays**. Saturdays and Sundays are neutral: they neither break a
run nor extend it. A run is a sequence of weekdays (Monday to Friday) on each of which at least one
counted commit was authored, where each weekday follows the previous one with no weekday between —
a Friday is followed by the next Monday. Its length is the number of weekdays in it; commits on a
weekend never add to it, and an empty weekend never ends it. An empty weekday — a holiday too —
ends it. On a tie the earlier run is reported.

Which review category a commit belongs to is the user's own judgement, `classify.review_category`
— injectable as `categorise`, so tests pass their own. Whatever that function does, every counted
commit lands in exactly one category: if it raises, or returns something that is not one of
`REVIEW_CATEGORIES`, the commit is counted under "other" and the failure is counted for *Caveats*.

Tickets come from the same counted commits: `tickets.find_keys()` reads each message once
and a key counts **once per commit**, wherever and however often it is written. Only keys of the
projects the user named (`jira.projects`) are tickets; every other ticket-shaped prefix becomes a
*suggestion*, ranked by how many commits name it where tickets are written, and the ids the
false-positive guard refused (`UTF-8`, `TC-UI-07`) are counted for *Caveats*. A commit with no
key is counted like any other — tickets are a breakdown of the total, never a filter on it. Your
merge requests and pull requests add the tickets their branch, title or message names
(`Delivery.tickets`): counted per delivery, a ticket only they name joins with no commit, and a
ticket named only by work you merged for colleagues is kept apart, for *Caveats*. Which tickets
each counted commit names is kept too (`TicketInsights.by_commit`): the digest groups commits by
ticket from this one reading, never a second one.

The Highlights are the work the report names first, picked from the numbers above by a fixed
rule — no judgement (a future version may let you score work yourself): the busiest month (the most
counted commits; on a tie the earlier), your `HIGHLIGHTS_SHOWN` merge requests and pull requests
with the most commits of yours (on a tie the newer, then by repository and number; never one you
merged for a colleague), and the first `HIGHLIGHTS_SHOWN` tickets of the ticket table (named in the
most commits). They are a selection, not a filter: every one of them is also in its own section.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

from sijl.classify import (
    REVIEW_CATEGORIES,
    STANDARD_KINDS,
    Conventional,
    parse_conventional,
    review_category,
)
from sijl.collect import Collection
from sijl.models import BOTH, Delivery, Repo
from sijl.tickets import Mentions, find_keys, key_order
from sijl.window import Window

Categorise = Callable[[Conventional, Repo], str]
KINDS = (*STANDARD_KINDS, "other")  # every kind parse_conventional() can return
HIGHLIGHTS_SHOWN = 3  # deliveries and tickets the Highlights name; all are in their own sections


@dataclass(frozen=True)
class Streak:
    """The longest run of weekdays with commits; weekends neither break nor extend it."""

    weekdays: int = 0  # weekdays in the run; 0 when no weekday has a counted commit
    first: date | None = None  # the run's first weekday
    last: date | None = None  # its last weekday


@dataclass(frozen=True)
class MonthTotal:
    month: str  # "2026-04", the author day's calendar month
    commits: int
    active_days: int  # distinct author days in that month


@dataclass(frozen=True)
class TicketTotal:
    key: str  # "PROJ-1234"
    commits: int  # counted commits naming it — once each, however often it is written
    first: date  # the earliest day it is named: a commit's author day or a delivery's merge day
    last: date  # the latest
    deliveries: int = 0  # your merge requests and pull requests naming it


@dataclass(frozen=True)
class Suggestion:
    """A ticket-shaped prefix that is not in `jira.projects`: perhaps a project, perhaps not."""

    project: str  # "QC"
    commits: int  # counted commits naming one of its keys
    in_position: int  # of those, commits naming it where tickets are written (tickets.POSITIONS)
    example: str  # its key named in most commits; on a tie, the lowest number


@dataclass(frozen=True)
class TicketInsights:
    """Tickets of the allowlisted projects, suggestions for the rest, what the guard refused."""

    projects: tuple[str, ...] = ()  # `jira.projects` as used, sorted
    per_ticket: tuple[TicketTotal, ...] = ()  # most commits first, then project, then number
    with_ticket: int = 0  # counted commits naming at least one ticket
    suggestions: tuple[Suggestion, ...] = ()  # most in a ticket position, then commits, then name
    outside: int = 0  # counted commits naming a key of a project not in `projects`
    rejected: tuple[tuple[str, int], ...] = ()  # refused ids as written → commits; most first
    rejected_commits: int = 0  # counted commits with at least one refused id
    from_deliveries: tuple[str, ...] = ()  # tickets only your deliveries name, no commit; in order
    others_only: tuple[str, ...] = ()  # named only by requests you merged for colleagues: uncounted
    # Each counted commit naming a ticket (its SHA) → those tickets, in key order: the digest
    # groups commits by ticket from this one reading.
    by_commit: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Highlights:
    """The work named first, by a fixed rule (module docstring): a selection of what the
    other fields hold, never a number of its own."""

    busiest_month: MonthTotal | None = None  # the most commits; on a tie the earlier; None: none
    deliveries: tuple[tuple[Repo, Delivery], ...] = ()  # yours, the most commits of yours first
    tickets: tuple[TicketTotal, ...] = ()  # the first of `TicketInsights.per_ticket`


@dataclass(frozen=True)
class Insights:
    """Every number of the report, from counted commits only; each breakdown sums to `total`."""

    total: int
    active_days: int  # distinct author days
    per_repo: tuple[tuple[Repo, int], ...]  # repositories with counted commits, busiest first
    per_month: tuple[MonthTotal, ...]  # every month of the window up to today, empty ones too
    per_kind: tuple[tuple[str, int], ...]  # KINDS order, zeros included
    per_category: tuple[tuple[str, int], ...]  # REVIEW_CATEGORIES order, zeros included
    areas: tuple[tuple[str, int], ...]  # non-standard prefixes ("ui"), most first, then by name
    breaking: int  # `!` before the colon, or a BREAKING CHANGE footer
    non_conventional: int  # subjects without any `prefix: ` — counted as kind "other"
    category_failures: int  # commits the category function failed on — counted as "other"
    first_category_failure: str  # what went wrong the first time, "" when nothing did
    longest_streak: Streak
    not_pushed: int  # your commits on no remote branch: listed apart, in no number above
    delivered: int  # your merge requests and pull requests merged in the period
    delivered_by_you: int  # of those, the ones you merged yourself
    delivered_squashed: int  # of those, GitHub squash merges: who merged them is not recorded
    merged_for_others: int  # colleagues' requests you merged
    quiet_repos: tuple[Repo, ...]  # repositories read with no counted commit, by name
    tickets: TicketInsights
    highlights: Highlights  # picked from the fields above, by a fixed rule


def _next_weekday(day: date) -> date:
    return day + timedelta(days=3 if day.weekday() == 4 else 1)  # Friday → Monday


def longest_streak(days: Iterable[date]) -> Streak:
    """The longest run of consecutive weekdays among `days`, weekends neutral (module docstring).

    Weekend days are dropped first, so they can neither extend a run nor break one; then each
    weekday continues the run when it is the weekday right after the previous one.
    """
    best = current = Streak()
    for day in sorted({d for d in days if d.weekday() < 5}):  # Monday 0 … Friday 4
        if current.last is not None and day == _next_weekday(current.last):
            current = Streak(current.weekdays + 1, current.first, day)
        else:
            current = Streak(1, day, day)
        if current.weekdays > best.weekdays:  # strictly longer: a tie keeps the earlier run
            best = current
    return best


def months_of(window: Window, until: date | None = None) -> list[str]:
    """Every calendar month the window touches, as "YYYY-MM", in order — none after `until`'s
    month (today), so a window that runs to 9999 is not 95,000 empty months."""
    last = min(window.end, until) if until is not None else window.end
    year, month = window.start.year, window.start.month
    months = []
    while (year, month) <= (last.year, last.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _shown(value: object) -> str:
    """`repr(value)`, cut to 80 characters — or a plain description when repr itself fails."""
    try:
        return _short(repr(value))
    except Exception:  # an object of the user's: its repr is code too
        return f"a {type(value).__name__} that cannot be shown"


def _place(categorise: Categorise, commit: Conventional, repo: Repo) -> tuple[str, str]:
    """The commit's category and "" — or "other" and what went wrong. Never raises: the
    function, its answer and its exception are all the user's code, and so is describing them."""
    try:
        category = categorise(commit, repo)
    except Exception as exc:  # anything it raises is counted, never fatal
        try:
            detail = str(exc)
        except Exception:  # an exception whose message itself raises
            return "other", f"raised {type(exc).__name__} (its message cannot be shown)"
        return "other", f"raised {type(exc).__name__}" + (f": {_short(detail)}" if detail else "")
    if isinstance(category, str):
        text = str.__str__(category)  # the text itself: a str subclass's own __eq__ is never asked
        if text in REVIEW_CATEGORIES:
            return text, ""
        return "other", f"returned {_short(repr(text))}"
    return "other", f"returned {_shown(category)}"


class _TicketTally:
    """The ticket numbers, gathered one counted commit at a time; `result()` freezes them."""

    def __init__(self, projects: Iterable[str]) -> None:
        self.projects = tuple(sorted(set(projects)))
        self.commits: Counter[str] = Counter()  # allowlisted key → commits
        self.span: dict[str, tuple[date, date]] = {}  # allowlisted key → (first, last) day
        self.with_ticket = self.outside = self.rejected_commits = 0
        self.prefix_commits: Counter[str] = Counter()  # other prefix → commits
        self.prefix_placed: Counter[str] = Counter()  # other prefix → commits in a ticket position
        self.other_keys: Counter[str] = Counter()  # other key → commits, for the example
        self.rejected: Counter[str] = Counter()  # refused id as written → commits
        self.delivered: Counter[str] = Counter()  # allowlisted key → your deliveries naming it
        self.others: set[str] = set()  # allowlisted keys of requests you merged for colleagues
        self.by_commit: dict[str, tuple[str, ...]] = {}  # counted commit → its tickets

    def add(self, mentions: Mentions, day: date, sha: str) -> None:
        ticket = False  # this commit names a ticket of an allowlisted project
        placed: dict[str, bool] = {}  # other prefixes of this commit → named in a position?
        for key, where in mentions.keys.items():  # each key once: counts are per commit
            project = key.rpartition("-")[0]
            if project in self.projects:
                ticket = True
                self.commits[key] += 1
                first, last = self.span.get(key, (day, day))
                self.span[key] = (min(first, day), max(last, day))
            else:
                placed[project] = placed.get(project, False) or bool(where)
                self.other_keys[key] += 1
        self.with_ticket += ticket
        if ticket:
            keys = (k for k in mentions.keys if k.rpartition("-")[0] in self.projects)
            self.by_commit[sha] = tuple(sorted(keys, key=key_order))
        for project, in_position in placed.items():
            self.prefix_commits[project] += 1
            self.prefix_placed[project] += in_position
        self.outside += bool(placed)
        self.rejected.update(mentions.rejected)
        self.rejected_commits += bool(mentions.rejected)

    def add_delivery(self, delivery: Delivery) -> None:
        """A merge request or pull request in the period: yours count, others' are set apart."""
        keys = {k for k in delivery.tickets if k.rpartition("-")[0] in self.projects}
        if not delivery.is_yours:
            self.others |= keys
            return
        day = delivery.merged_on
        for key in keys:
            self.delivered[key] += 1
            first, last = self.span.get(key, (day, day))
            self.span[key] = (min(first, day), max(last, day))

    def result(self) -> TicketInsights:
        def example(project: str) -> str:
            keys = [k for k in self.other_keys if k.rpartition("-")[0] == project]
            return min(keys, key=lambda k: (-self.other_keys[k], key_order(k)))

        return TicketInsights(
            projects=self.projects,
            per_ticket=tuple(
                TicketTotal(key, self.commits[key], *self.span[key], self.delivered[key])
                for key in sorted(
                    self.commits.keys() | self.delivered.keys(),
                    key=lambda k: (-self.commits[k], -self.delivered[k], key_order(k)),
                )
            ),
            with_ticket=self.with_ticket,
            suggestions=tuple(
                Suggestion(p, self.prefix_commits[p], self.prefix_placed[p], example(p))
                for p in sorted(
                    self.prefix_commits,
                    key=lambda p: (-self.prefix_placed[p], -self.prefix_commits[p], p),
                )
            ),
            outside=self.outside,
            rejected=tuple(sorted(self.rejected.items(), key=lambda r: (-r[1], r[0]))),
            rejected_commits=self.rejected_commits,
            from_deliveries=tuple(
                sorted((k for k in self.delivered if not self.commits[k]), key=key_order)
            ),
            others_only=tuple(
                sorted(self.others - self.commits.keys() - self.delivered.keys(), key=key_order)
            ),
            by_commit=dict(sorted(self.by_commit.items())),
        )


def highlights(
    per_month: Iterable[MonthTotal],
    deliveries: Iterable[tuple[Repo, Delivery]],
    tickets: TicketInsights,
) -> Highlights:
    """The Highlights' fixed rule (module docstring): the month with the most commits — `max()`
    keeps the first of equals, and the months are in order, so a tie goes to the earlier — your
    deliveries with the most commits of yours, and the tickets named most."""
    busy = [m for m in per_month if m.commits]
    yours = sorted(
        ((repo, d) for repo, d in deliveries if d.is_yours),
        key=lambda item: (
            -item[1].yours,
            -item[1].merged_on.toordinal(),  # on a tie, the newer first
            item[0].name,
            str(item[0].path),
            item[1].forge,
            item[1].number,
        ),
    )
    return Highlights(
        busiest_month=max(busy, key=lambda m: m.commits) if busy else None,
        deliveries=tuple(yours[:HIGHLIGHTS_SHOWN]),
        tickets=tickets.per_ticket[:HIGHLIGHTS_SHOWN],
    )


def aggregate(
    collection: Collection,
    window: Window,
    *,
    categorise: Categorise = review_category,
    today: date | None = None,
    projects: Iterable[str] = (),
) -> Insights:
    """Every metric of the report, in one pass over the counted commits of `collection`.

    `today` ends the month list (no month after it is listed); None lists the whole window.
    `projects` is the `jira.projects` allowlist: only their keys are tickets.
    """
    per_repo: list[tuple[Repo, int]] = []
    kinds: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    areas: Counter[str] = Counter()
    months: Counter[str] = Counter()
    days: set[date] = set()
    breaking = non_conventional = failures = 0
    first_failure = ""
    tickets = _TicketTally(projects)
    for rc in collection.repos:
        if rc.commits:
            per_repo.append((rc.repo, len(rc.commits)))
        for commit in rc.commits:
            parsed = parse_conventional(commit.subject, commit.body)
            kinds[parsed.kind] += 1
            if parsed.area:
                areas[parsed.area] += 1
            breaking += parsed.breaking
            non_conventional += not parsed.conventional
            category, failure = _place(categorise, parsed, rc.repo)
            categories[category] += 1
            if failure:
                failures += 1
                first_failure = first_failure or failure
            days.add(commit.author_day)
            months[commit.author_day.strftime("%Y-%m")] += 1
            mentions = find_keys(commit.subject, commit.body, commit.trailers, scope=parsed.scope,
                                 lead=parsed.lead, projects=tickets.projects)  # fmt: skip
            tickets.add(mentions, commit.author_day, commit.sha)
        for delivery in rc.deliveries:  # what your merge requests name, after the commits
            tickets.add_delivery(delivery)

    delivered = [d for rc in collection.repos for d in rc.deliveries]
    yours = [d for d in delivered if d.is_yours]
    days_per_month = Counter(day.strftime("%Y-%m") for day in days)
    # A counted commit is always inside the window — but its author's clock may have run ahead of
    # today: the union keeps its month, so nothing is lost.
    all_months = sorted({*months_of(window, today), *months})
    per_month = tuple(MonthTotal(m, months[m], days_per_month[m]) for m in all_months)
    ticket_insights = tickets.result()
    return Insights(
        total=sum(kinds.values()),
        active_days=len(days),
        per_repo=tuple(sorted(per_repo, key=lambda p: (-p[1], p[0].name, str(p[0].path)))),
        per_month=per_month,
        per_kind=tuple((kind, kinds[kind]) for kind in KINDS),
        per_category=tuple((name, categories[name]) for name in REVIEW_CATEGORIES),
        areas=tuple(sorted(areas.items(), key=lambda a: (-a[1], a[0]))),
        breaking=breaking,
        non_conventional=non_conventional,
        category_failures=failures,
        first_category_failure=first_failure,
        longest_streak=longest_streak(days),
        # Counted here too, once, so that no renderer counts the collection again.
        not_pushed=sum(len(rc.local_only) for rc in collection.repos),
        delivered=len(yours),
        delivered_by_you=sum(d.role == BOTH for d in yours),
        delivered_squashed=sum(bool(d.squash) for d in yours),
        merged_for_others=len(delivered) - len(yours),
        quiet_repos=tuple(
            sorted(
                (rc.repo for rc in collection.repos if not rc.commits),
                key=lambda repo: (repo.name, str(repo.path)),
            )
        ),
        tickets=ticket_insights,
        highlights=highlights(
            per_month,
            ((rc.repo, d) for rc in collection.repos for d in rc.deliveries),
            ticket_insights,
        ),
    )
