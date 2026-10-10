"""The values a report template reads, built once from a `Report`.

`context(report)` turns the report — every number from its one `Insights` model, the
commits, deliveries and links it carries — into plain, safe values (`render/safety.py`): every
piece of text Sijl did not write becomes a `Text`, every sentence Sijl wrote (with any such
text already escaped by `report.py`'s helpers) a `Markdown`, every count an int, every group a
`View`. Nothing here counts anything: totals, shares, ticket and month rows are read from the model
or computed by the same `report.py` functions the sentences use, so a template only places and
prints — it cannot disagree with the numbers.

Sentences whose grammar depends on numbers ("1 commit is", "3 commits are"; the dashes of
*Delivered*'s first paragraph) stay in `report.py`, tested function by function; templates draw
the headings, tables and lists around them. `context()` names every field.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

from sijl import __version__
from sijl.collect import RepoCommits
from sijl.models import Delivery, RawCommit
from sijl.render.safety import FieldUndefined, Markdown, Text, View
from sijl.report import (
    APPENDIX_NOTE,
    CATEGORY_LABELS,
    FOOTER,
    HIGHLIGHTS_NOTE,
    POSITIONS_NOTE,
    PREFIXES_SHOWN,
    SHARES_NOTE,
    Report,
    _code,
    _commit_cell,
    _link,
    breaking_sentence,
    caveats,
    delivered_paragraph,
    merged_by_cell,
    month_label,
    month_rows,
    named_in,
    not_pushed_sentence,
    share,
    summary_paragraph,
    tickets_sentence,
    where,
    your_commits_cell,
)
from sijl.tickets import key_order
from sijl.window import COUNTING_RULE, long_day


def _texts(items: Iterable[str]) -> tuple[Text, ...]:
    return tuple(Text(item) for item in items)


def _day(day: date | None) -> Markdown | None:
    return Markdown(day.isoformat()) if day is not None else None


def _period(report: Report) -> View:
    window = report.window
    return View(
        start=Markdown(window.start.isoformat()),
        end=Markdown(window.end.isoformat()),
        first_day=Markdown(long_day(window.start)),
        last_day=Markdown(long_day(window.end)),
        label=Markdown(window.label),
        described=Markdown(window.describe()),
        rule=Markdown(COUNTING_RULE),
        to_date=window.to_date,
    )


def _totals(report: Report) -> View:
    """Every headline number of the model, for a template that writes its own sentences."""
    insights = report.insights
    return View(
        commits=insights.total,
        active_days=insights.active_days,
        repositories=len(insights.per_repo),
        repositories_scanned=report.repos_scanned,
        quiet_repositories=len(insights.quiet_repos),
        not_pushed=insights.not_pushed,
        delivered=insights.delivered,
        delivered_by_you=insights.delivered_by_you,
        delivered_squashed=insights.delivered_squashed,
        merged_for_others=insights.merged_for_others,
        breaking=insights.breaking,
        non_conventional=insights.non_conventional,
        tickets=len(insights.tickets.per_ticket),
        with_ticket=insights.tickets.with_ticket,
    )


def _request(report: Report, path: Path, d: Delivery) -> Markdown:
    """A merge request's or pull request's number: a link when it has one."""
    url = report.links.request_url(path, d)
    return Markdown(_link(d.ref, url) if url else d.ref)


def _highlights(report: Report) -> View:
    """The Highlights the model picked (aggregate.Highlights): the busiest month, your largest
    deliveries, the most active tickets — with how many they were picked from."""
    insights = report.insights
    picked = insights.highlights
    month = picked.busiest_month
    return View(
        shown=bool(month or picked.deliveries or picked.tickets),
        note=Markdown(HIGHLIGHTS_NOTE),
        month=View(
            label=Markdown(month_label(month.month, report.window, report.generated_on)),
            commits=month.commits,
            active_days=month.active_days,
        )
        if month
        else None,
        deliveries=tuple(
            View(
                request=_request(report, repo.path, d),
                title=Text(d.title),
                repo=Text(repo.name),
                yours=d.yours,
                commits=d.commits,
                merged=Markdown(d.merged_on.isoformat()),
                squash=bool(d.squash),
            )
            for repo, d in picked.deliveries
        ),
        deliveries_of=insights.delivered,
        tickets=tuple(
            View(
                key=Text(t.key),
                commits=t.commits,
                deliveries=t.deliveries,
                named=Markdown(named_in(t)),
                first=Markdown(t.first.isoformat()),
                last=Markdown(t.last.isoformat()),
            )
            for t in picked.tickets
        ),
        tickets_of=len(insights.tickets.per_ticket),
    )


def _kinds(report: Report) -> View:
    insights = report.insights
    total = insights.total
    return View(
        note=Markdown(SHARES_NOTE),
        categories=tuple(
            View(
                name=Markdown(name),
                label=Markdown(CATEGORY_LABELS[name]),
                commits=n,
                share=Markdown(share(n, total)) if n else None,
            )
            for name, n in insights.per_category
        ),
        kinds=tuple(
            View(
                kind=Markdown(kind if kind == "other" else _code(kind)),
                commits=n,
                share=Markdown(share(n, total)) if n else None,
            )
            for kind, n in insights.per_kind
        ),
        breaking=Markdown(breaking_sentence(insights)) if insights.breaking else None,
    )


def _tickets(report: Report) -> View:
    insights = report.insights
    tickets = insights.tickets
    shown = bool(insights.total and (tickets.projects or tickets.suggestions))
    suggestions = tickets.suggestions[:PREFIXES_SHOWN]
    return View(
        shown=shown,
        projects=_texts(tickets.projects),
        sentence=Markdown(tickets_sentence(report)) if shown else None,
        requests_column=any(t.deliveries for t in tickets.per_ticket),
        rows=tuple(
            View(
                key=Text(t.key),
                commits=t.commits,
                deliveries=t.deliveries,
                first=Markdown(t.first.isoformat()),
                last=Markdown(t.last.isoformat()),
            )
            for t in tickets.per_ticket
        ),
        suggestions=tuple(
            View(
                prefix=Text(s.project),
                commits=s.commits,
                in_position=s.in_position,
                example=Text(s.example),
            )
            for s in suggestions
        ),
        suggestions_more=len(tickets.suggestions) - len(suggestions),
        positions_note=Markdown(POSITIONS_NOTE),
    )


def _delivered(report: Report) -> View:
    groups = []
    for yours, heading in ((True, "Yours"), (False, "Merged for others")):
        repos = []
        for rc, items in report.deliveries(yours=yours):
            rows = []
            for d in items:
                rows.append(
                    View(
                        merged=Markdown(d.merged_on.isoformat()),
                        request=_request(report, rc.repo.path, d),
                        title=Text(d.title),
                        commits=d.commits,
                        your_commits=Markdown(your_commits_cell(d)) if yours else None,
                        merged_by=Markdown(merged_by_cell(d)) if yours else None,
                        squash=bool(d.squash),
                        tickets=_texts(sorted(d.tickets, key=key_order)),
                    )
                )
            repos.append(View(name=Text(rc.repo.name), count=len(items), rows=tuple(rows)))
        if repos:
            groups.append(View(heading=Markdown(heading), yours=yours, repos=tuple(repos)))
    return View(
        paragraph=Markdown(delivered_paragraph(report)),
        tickets_column=any(d.tickets for rc in report.collection.repos for d in rc.deliveries),
        groups=tuple(groups),
    )


def _commit_row(report: Report, rc: RepoCommits, commit: RawCommit) -> View:
    """A counted commit: its day, its SHA linked, copies and squash notes beside it."""
    url = report.links.commit_url(rc.repo.path, commit.sha)
    return View(
        date=Markdown(commit.author_day.isoformat()),
        sha=Markdown(commit.sha),
        commit=Markdown(_commit_cell(rc, commit, url, report.links)),
        subject=Text(commit.subject),
    )


def _repositories(report: Report) -> tuple[View, ...]:
    """The repositories with counted commits, busiest first (`insights.per_repo`)."""
    insights = report.insights
    out = []
    for (repo, n), rc in zip(insights.per_repo, report.active_repos, strict=True):
        projects = report.links.of(repo.path).projects
        out.append(
            View(
                name=Text(repo.name),
                path=Text(str(repo.path)),
                commits=n,
                share=Markdown(share(n, insights.total)),
                projects=tuple(Markdown(_link(p.label, p.url)) for p in projects),
                rows=tuple(_commit_row(report, rc, c) for c in rc.commits),
            )
        )
    return tuple(out)


def _not_pushed(report: Report) -> View:
    repos = sorted(
        (rc for rc in report.collection.repos if rc.local_only),
        key=lambda rc: (-len(rc.local_only), rc.repo.name),
    )
    return View(
        count=report.insights.not_pushed,
        sentence=Markdown(not_pushed_sentence(report)) if repos else None,
        repos=tuple(
            View(
                name=Text(rc.repo.name),
                count=len(rc.local_only),
                rows=tuple(
                    View(
                        date=Markdown(c.author_day.isoformat()),
                        commit=Markdown(_commit_cell(rc, c)),
                        where=Markdown(where(rc, c)),
                        subject=Text(c.subject),
                    )
                    for c in rc.local_only
                ),
            )
            for rc in repos
        ),
    )


def _months(report: Report) -> tuple[View, ...]:
    if not report.insights.total:
        return ()
    return tuple(
        View(label=Markdown(label), commits=commits, active_days=days)
        for label, commits, days in month_rows(report)
    )


def _streak(report: Report) -> View:
    streak = report.insights.longest_streak
    return View(weekdays=streak.weekdays, first=_day(streak.first), last=_day(streak.last))


def built_urls(report: Report) -> frozenset[str]:
    """Every address Sijl itself links to in this report: each repository's projects, each
    counted commit, each merge request and pull request — delivered here, or delivering a commit
    here (*delivered via #N*). A custom template's output may link to these and nothing else
    (`render.markdown`)."""
    links, urls = report.links, set()
    for rc in report.collection.repos:
        path = rc.repo.path
        urls.update(project.url for project in links.of(path).projects)
        urls.update(links.commit_url(path, commit.sha) for commit in rc.commits)
        urls.update(links.request_url(path, d) for d in (*rc.deliveries, *rc.via.values()))
    urls.discard("")
    return frozenset(urls)


ADDED_AFTER = (
    "Sijl adds the Caveats and the footer after every custom template itself; a template "
    "cannot print, move or hide them"
)


def context(report: Report, *, custom: bool = False) -> dict[str, object]:
    """Every value a report template can read, by name. For a `custom`
    template, `caveats` and `footer` are not values but an explanation: Sijl appends both
    after its output (a template could otherwise hide them in an HTML comment)."""
    values: dict[str, object] = {
        "version": Markdown(__version__),
        "person": Text(report.person),
        "emails": _texts(report.emails),
        "generated": Markdown(report.generated_on.isoformat()),
        "period": _period(report),
        "totals": _totals(report),
        "streak": _streak(report),
        "summary": Markdown(summary_paragraph(report)),
        "highlights": _highlights(report),
        "shares_note": Markdown(SHARES_NOTE),
        "kinds": _kinds(report),
        "months": _months(report),
        "tickets": _tickets(report),
        "delivered": _delivered(report),
        "repositories": _repositories(report),
        "quiet": _texts(repo.name for repo in report.insights.quiet_repos),
        "not_pushed": _not_pushed(report),
        "caveats": tuple(Markdown(line) for line in caveats(report)),
        "appendix_note": Markdown(APPENDIX_NOTE),
        "footer": Markdown(FOOTER),
    }
    if custom:
        for name in ("caveats", "footer"):
            values[name] = FieldUndefined(hint=ADDED_AFTER, name=name)
    return values
