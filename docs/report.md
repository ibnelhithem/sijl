# The report — `sijl report`

## What it is

One command reads the git history already on your laptop and writes a summary of the work
**you** authored in a period you choose — a paragraph with the headline numbers, the highlights
worth naming first, where the work happened, which tickets it served, how it spread over the
months and what kinds of work it was, the merge requests and pull requests you delivered (and the
ones you merged for colleagues), an honest list of everything that was left out, and — as an
appendix — every commit, grouped by repository. You can also lay the report out your own way,
with a template of your own.

## Who it's for

Anyone preparing a performance review or a self-assessment who would otherwise rebuild months
of work from memory: you point Sijl at the folders where your repositories live, give the
email addresses you commit with, and get evidence you can paste into the review form.

## Why it matters

- **Hours become seconds.** Six months across a dozen repositories typically takes a second or
  two.
- **Say the period the way you think of it.** "The last 6 months", "Q3", "the first half of the
  year" — Sijl works out the exact days, both ends included, and prints them.
- **Accurate by construction.** Commits count by the day *you wrote them*, not the day a rebase
  re-stamped them; work that only exists on a remote branch still counts; a colleague whose name
  looks like yours is never mixed in; a commit in two clones is counted once, and so is a commit
  you rebased, cherry-picked or amended into a second copy — and so is a pull request merged on
  GitHub with "squash", which naive counting would count twice.
- **Delivered work apart from experiments.** The headline numbers only include work that reached
  a remote (GitHub, GitLab); what never left your laptop is listed separately, so a reviewer
  never mistakes an experiment for a delivery — and you never lose sight of unpushed work.
- **Speaks a reviewer's language.** Commits are grouped into the headings a review asks about —
  features, fixes, quality, documentation, tooling — using *your* judgement of which is which,
  and the months and your longest run of working days show how the work was spread. Work is
  also grouped by **Jira ticket** — the unit your reviewer already knows — without test-case
  ids or standards such as UTF-8 posing as tickets.
- **What you delivered, in the reviewer's words.** Reviews talk about merge requests and pull
  requests, not commits. The report lists every one of yours merged in the period — with its
  number, title and date, whether you or a colleague pressed *Merge* — and the ones you merged
  for colleagues, which is review work too.
- **Click-through evidence.** Every counted commit, merge request and pull request links to its
  page on GitHub or GitLab, so a reviewer can open the actual change instead of taking a list of
  ids on trust.
- **Honest.** Nothing is dropped silently: every folder that was not scanned and every kind of
  commit that is not listed appears under *Caveats*, with a count.
- **Private.** It only reads your local repositories. It changes nothing in them and sends
  nothing anywhere — to turn a shortcut name such as `github-alt` into the real site, it
  asks your own ssh program, which answers from your settings without connecting anywhere.

## How to use it

```bash
sijl report \
  --root ~/code/work --root ~/code/personal \
  --author-email you@example.com --author-email 12345+you@users.noreply.github.com \
  --last 6m \
  --out ~/review.md
```

The email addresses, the folders and the period are required — on the command line, or set once
in the configuration file (see [Configuration](configuration.md)); a flag always overrides the
file. The report is either printed in the terminal or, with `--out`, written to a file — one or
the other.

- `--root` — a folder to search for repositories (sub-folders are searched too). Repeat it for
  several folders. `--repo` names one repository directly.
- `--author-email` — every email address you commit with, including GitHub's "noreply"
  address if you merge or edit on github.com. Repeat it for each.
- The period, given **one** way (see *Choosing the period* below):
  - `--last 6m` — the last 6 months up to and including today; also `30d` (days), `6w` (weeks),
    `1y` (years);
  - `--quarter 2026Q3`, `--half 2026H1`, `--year 2026` — a calendar quarter, half year or year;
  - `--since` / `--until` — the first and last day yourself, `YYYY-MM-DD`, both included;
    `--until` defaults to today.
- `--out` — write the report to a file instead of printing it. Without it, the report is
  printed, so `sijl report … > review.md` works too. Sijl never writes into a repository it
  reads unless you name the file exactly — see *Where `--out` writes* below; the simplest is to
  run it outside your repositories, or to give a path such as `~/review.md`.
- `--template` — lay the report out with a template of your own; see *Your own template* below.
- `--include-worktrees` — also read scratch worktrees that are not on any branch (off by
  default; see below).
- `--dedupe patch-id` — stricter matching of rebased and cherry-picked copies: they must also
  make the same change (off by default; see *Rebased and cherry-picked copies*).
- `--jira-project` — one of your Jira projects, such as `PROJ`; repeat it for each. Their
  ticket keys are grouped under *Tickets* (see *Tickets* below; better written once in the
  configuration file).

The report starts with the period — its exact days, how you chose it ("Q4 2026 to date", "the
last 6 months") and the rule it counts by — and your identities, then, in this order:

1. **Summary** — e.g. "Between 1 April 2026 and 30 June 2026 you authored **13 commits** on
   **13 active days** across **3 repositories** — most in app (7), site (4), tools (2). … 1 more
   commit is not pushed yet and not in these numbers" — with your longest streak of weekdays with
   commits (weekends neither break nor extend it) and how many merge requests and pull requests
   of yours were merged. (The examples on this page come from one small sample: three
   repositories, April to June 2026 — see *A sample report* below.)
2. **Highlights** — the work worth naming first: your busiest month, your three largest merge
   requests and pull requests (by commits of yours, each a link) and your three most active
   tickets. See *Highlights* below.
3. **Per repository** — where the work happened: each repository with its number of commits, its
   share of the total and the GitHub or GitLab project it links to; then the repositories where
   you made no commits in the period.
4. **Tickets** — when you have named your Jira projects, every ticket your commits mention, with
   how many commits and on which days; otherwise the prefixes that look like Jira projects in
   your commits, so you can name yours. See *Tickets* below.
5. **Per month** — commits and active days in every month of the period, empty months too.
6. **Kinds of work** — how your commits divide into review headings (features, fixes, …) and
   into the kinds their messages declare, each with its share; see *Kinds of work* below.
7. **Delivered** — your merge requests and pull requests merged in the period, per repository,
   newest first: the day, the number (a link), the title, how many of its commits are yours and
   whether you or a colleague merged it — for a GitHub squash merge, how it was counted instead;
   then the ones you merged for colleagues. See *Merge requests and pull requests delivered* and
   *GitHub squash merges* below.
8. **Not pushed yet** — only when there is any: your commits that are on no remote branch, per
   repository, each marked *local branch* or *worktree only*.
9. **Caveats** — first how the period was counted (and, for a quarter, half or year still
   running, that it stops at today), then what was scanned, what was not and why, and what is
   not counted yet.
10. **Appendix: every counted commit** — the evidence behind every number: per repository, the
    busiest first, the folder and the project(s) it links to, then the date, short commit id and
    subject of every counted commit — the id is a link to that commit's page — with the ids of
    any copies of it beside the id, *delivered via #12* beside a commit a squash-merged pull
    request delivered, and the lines of a squash merge counted as work itself.
11. The **footer**: "These figures are evidence of activity, not a performance score."

Everything before *Caveats* is what you paste into a review form; it reads in a screen or two even
for six months of work. The appendix, after *Caveats*, is there for a reviewer who wants to check
a number — for six busy months it can easily hold over a thousand commits.

Every number in the report — the summary, the highlights, *Per repository*, both tables of
*Kinds of work*, *Per month* and the appendix's headings — is counted once, from the same commits,
so they always agree: the repositories, the kinds, the categories and the months each add up to
the total.

### A sample report

The beginning of a report for a small, made-up history — three repositories (`app` on GitLab,
`site` on GitHub, `tools` with no remote), April to June 2026:

```markdown
# Work summary — Alex Example

**Period:** 1 April 2026 to 30 June 2026 (inclusive; by author date, in each commit's own timezone)
**Identities:** 40400001+alexgh@users.noreply.github.com, alex.example@acme.test
**Generated:** 2026-10-08 by Sijl 1.0.0

## Summary

Between 1 April 2026 and 30 June 2026 you authored **13 commits** on **13 active days** across
**3 repositories** — most in app (7), site (4), tools (2). Your longest streak was **3 weekdays**
in a row with commits, from 11 May 2026 to 13 May 2026 (weekends neither break nor extend a
streak; a weekday without a commit — a holiday or leave too — ends it). 1 more commit is not
pushed yet and not in these numbers (see *Not pushed yet*). 5 merge requests and pull requests of
yours were merged, and you merged 1 for colleagues (see *Delivered*).

## Highlights

- **Busiest month:** April 2026 — 5 commits on 5 active days.
- **Largest merge requests and pull requests** (3 of 5, by commits of yours):
  - [#13](https://github.com/alexgh/site/pull/13) Search — 2 commits of yours, in site, merged 2026-04-22
  - [!42](https://gitlab.com/acme/platform/app/-/merge_requests/42) Export the report (PROJ-12) — 2 commits of yours, in app, merged 2026-04-08
  - [#14](https://github.com/alexgh/site/pull/14) Fix a broken link — 1 commit of yours, in site, merged 2026-05-19
- **Most active tickets** (1 of 1, by commits naming them):
  - PROJ-12 — named in 2 commits and 1 merge request or pull request of yours, 2026-04-06 to 2026-04-08

## Per repository

| Repository | Commits | Share | Project |
|---|---|---|---|
| app | 7 | 54% | [gitlab.com/acme/platform/app](https://gitlab.com/acme/platform/app) |
| site | 4 | 31% | [github.com/alexgh/site](https://github.com/alexgh/site) |
| tools | 2 | 15% | — |
```

(The real report writes each paragraph on one line and puts a one-line note above *Highlights*
and the tables; here it is wrapped and shortened to fit the page.)

## Highlights

The first thing a reviewer reads after the summary: the work worth naming first.

- **Your busiest month** — the month with the most commits, with its commits and active days
  (when two months tie, the **earlier** one).
- **Your largest merge requests and pull requests** — the three of yours with the most commits of
  yours, each with its link, title, repository and the day it was merged. When two have the same
  number of commits, the one merged **more recently** comes first (then by repository name and
  number). One you merged for a colleague is never your highlight. The heading says how many they
  were picked from: "(3 of 5, by commits of yours)".
- **Your most active tickets** — the first three rows of the *Tickets* table (with your Jira
  projects set): the tickets named in the most commits — on a tie, the one named by more of your
  merge requests and pull requests, then by key — with how many commits and requests name them
  and the first and last day.

The rule is **fixed and simple, not a judgement**: "largest" means most commits, which says
nothing about how hard or important the work was — the report says so above the list. A future
version may let you define what *notable* means for you. Each highlight is also listed in full in
its own section, so nothing is only here.

## Your own template (`--template`)

The built-in layout suits a review document, but your company's form may ask for something
else — a short paragraph per ticket, a table per repository, only the delivered work. With
`--template FILE` Sijl fills **your** layout with the same numbers and evidence:

```bash
sijl report --last 6m --template ~/my-review.md.j2 --out review.md
```

**A template is code.** It decides what your report says and how — so use only a template you
wrote or trust, as you would a script. Sijl holds every template to the limits below, but a
template you did not read could still, for instance, leave out work or word things oddly.

A template is a text file with placeholders, in the Jinja2 template language. A small example:

```text
# {{ person }} — {{ totals.commits|plural("commit") }} in {{ period.described }}

{% for repo in repositories %}
- {{ repo.name }}: {{ repo.commits }} commits ({{ repo.share }})
{% endfor %}
```

- **Everything the built-in report shows is available by name**: your name, the period, every
  total, the summary paragraph, the highlights, every repository with its commits, the tickets,
  the months, the kinds of work, the deliveries, the unpushed work. The built-in sections in
  [`src/sijl/render/templates/`](../src/sijl/render/templates/) use every one of them, so they
  are the best reference; `src/sijl/render/context.py` builds the values. A misspelt name is
  caught with a suggestion (see below).
- **Reuse the built-in sections**: `{% include "summary.md.j2" %}` drops in the built-in summary;
  the same works for `highlights`, `per_repository`, `tickets`, `months`, `kinds`, `delivered`,
  `not_pushed` and `appendix` — and `report.md.j2` is the whole built-in report.
- **Helpers**: `plural("commit")` writes "1 commit" / "3 commits"; `share(total)` a percentage;
  `cell` makes text safe inside a table; `code` shows it as code; `md` keeps a commit message's
  own `code` formatting in ordinary text; `shorten(60)` cuts a long subject; `join(", ")` joins a
  list.
- **Text from your repositories is always made safe.** A commit subject such as
  `[click](javascript:…)`, a title with `<img>`, a folder name with a line break in it — each
  prints as plain text on its own line, wherever your template puts it (in a table, inside your
  own `code` or link text): you cannot accidentally turn someone's commit message into a link, a
  picture or a new paragraph.
- **Every report still says what it left out.** Sijl adds its own Caveats section and the
  "evidence, not a performance score" footer **after** your template, always — a template cannot
  print, move or hide them (using `caveats` or `footer` stops the command and says so).
- **No links of its own — unless you allow them.** A template's output may contain the links
  Sijl itself makes (to your commits, merge requests, pull requests and projects) and nothing
  else of that kind: no other link, picture, web or e-mail address — even written as plain text
  or inside `code`, since GitHub and GitLab turn plain addresses into links — and no HTML or HTML
  comment. A template that writes one is stopped (exit code 2) with its line, before anything is
  written: a picture's address could otherwise send your names and subjects to whoever runs that
  address, the moment someone opens the report. If you wrote the template and want your own links
  (your intranet, a dashboard), add `--allow-template-links` — and know that the template can then
  send your report's contents anywhere.
- **Mistakes are caught at once and pointed at.** A misspelt name — `{{ totals.comits }}` —
  stops the command (exit code 2) with the file, the line and a suggestion: "template
  my-review.md.j2, line 3: 'comits' is not a field here — did you mean 'commits'?". A template
  with a syntax error is refused before any repository is read.
- **What templates cannot do**, on purpose:
  - read any file but your one template and the built-in sections, or run Python;
  - change text from your repositories (no find-and-replace, no cutting except `shorten`), or
    compare it with anything (a name is never equal to text you write — `{% if repo.name ==
    "app" %}` is always false), so no template can work out what a message says letter by
    letter;
  - capture what they printed into a variable (`{% set %}` blocks, macros, `{% filter %}`,
    `{% call %}`, `{% import %}`) — use `{% include %}`, `{% for %}`, `{% if %}` and the helpers;
  - print, move or hide the Caveats and the footer;
  - write a link, picture, address or HTML Sijl did not make (unless
    `--allow-template-links`);
  - run on and on, or grow without end: a template is at most 1,000,000 characters, its output
    at most 50,000,000, and it may run for 60 seconds; `**` (powers) is not available and repeating
    text is limited to 10,000 characters.
  Each refusal names its line.

## Where `--out` writes

Sijl never changes the repositories it reads, and the report file is the one thing it
writes. So before writing, it checks where `--out` would put the file:

- **Outside every repository it read** — written, as always.
- **Inside a repository it read** — written only when you **name the file exactly**: the full
  path (`/Users/you/code/work/app/reports/review.md`, or from `~`), with no shortcut (symbolic link)
  anywhere on the way — the file itself included. A short name such as `--out review.md` typed
  while you stand inside a project is refused, because the folder you happened to be in decided
  where a personal report would land — one `git add` away from being shared. So is a path through
  a shortcut a repository contains: a project could ship `reports/review.md` as a shortcut to
  another project's hidden git hooks. The message gives the full path to use if you meant it.
- **Into git's own files** — a repository's `.git` folder (however it is capitalised), or the
  `.git` file of one of its extra worktrees — never.
- **Safely, always**: the report is written to a new file and then put in place, so an existing
  shortcut or "hard link" at that name is replaced rather than written through, and nothing that
  changes on disk while the report is being made can redirect it.

A refused `--out` exits with code 2 and writes nothing.

## Choosing the period

Give the period in whichever way matches how you think about it. On 8 October 2026:

| You type | The report covers |
|---|---|
| `--last 6m` | 9 April 2026 to 8 October 2026 — *the last 6 months* |
| `--last 30d` | 9 September 2026 to 8 October 2026 — *the last 30 days* |
| `--last 2w` | 25 September 2026 to 8 October 2026 — *the last 2 weeks* |
| `--last 1y` | 9 October 2025 to 8 October 2026 — *the last 1 year* |
| `--quarter 2026Q3` | 1 July 2026 to 30 September 2026 — *Q3 2026* |
| `--quarter 2026Q4` | 1 October 2026 to 8 October 2026 — *Q4 2026 to date* |
| `--half 2026H1` | 1 January 2026 to 30 June 2026 — *H1 2026* |
| `--half 2026H2` | 1 July 2026 to 8 October 2026 — *H2 2026 to date* |
| `--year 2025` | 1 January 2025 to 31 December 2025 — *2025* |
| `--year 2026` | 1 January 2026 to 8 October 2026 — *2026 to date* |
| `--since 2026-04-06 --until 2026-06-30` | exactly those days |
| `--since 2026-04-06` | 6 April 2026 to today |

**Both ends are always included**, and every report prints the exact first and last day.

**"The last …" always ends today, and today counts.** `--last 6m` is the six whole months that
end today, so it starts where the *next* six months would end: the day after the same date six
months earlier — on 8 October, the period starts on 9 April. That way `--last 1d` is just today,
`--last 7d` is seven days, and the period is exactly six months long.

**On the last day of a month, "the last … months" start on the 1st.** `--last 1m` on
28 February covers all of February; `--last 3m` on 30 June is exactly the second quarter, the
same days as `--quarter 2026Q2`; `--last 6m` on 31 August is March to August. So if you run
`--last 1m` at the end of every month — or `--last 3m` at the end of every quarter — each day
lands in exactly one report: no overlap, no gap.

**Months that are shorter.** Counting back can land on a day the month does not have, such as
30 February; Sijl then uses the last day of that month. One consequence: run on any day from
27 to 30 March 2026, `--last 1m` starts on 28 February; run on 31 March, it starts on 1 March.
The report always prints the exact first and last day.

**Quarters, halves and years.** Q1 is January–March, Q2 April–June, Q3 July–September, Q4
October–December; H1 is January–June, H2 July–December. Write them as `2026Q3`, `2026H1`
(a small `q` or `h` works too) and `--year 2026`.

**A period that is not over yet stops at today and says so.** `--quarter 2026Q4` on 8 October
covers 1 to 8 October and is labelled **"Q4 2026 to date"** in the header and the summary;
*Caveats* adds "Q4 2026 is not over yet, so the period stops at today, 8 October 2026." Run it
again after the quarter ends for the whole quarter. A period that **has not started yet**
(`--quarter 2027Q1` in 2026) is refused with a message saying so, instead of an empty report.

**Years before 1970 cannot be named.** git cannot read dates before 1 January 1970 reliably,
so `--year 1969`, `--quarter 1969Q4` or `--since 1969-12-31` stop with a message naming the limit
("years 1970 to 9999"). `--last` can still reach that far back — `--last 100y` reads your whole
history — and still counts only the commits written inside the period.

**One way at a time.** `--last 6m --quarter 2026Q3` asks for two different periods, so Sijl
stops before reading anything and names both: "--last and --quarter both set the period —
choose one". `--since` with `--until` counts as one way. (For scripts: this is exit code 2,
like every other usage error.)

**What every report says about its period.** The header gives the days, how you chose them and
the counting rule, for example:

> **Period:** 1 October 2026 to 8 October 2026 — Q4 2026 to date (inclusive; by author date, in
> each commit's own timezone)

The summary repeats the days and the name ("Between 1 October 2026 and 8 October 2026 (Q4 2026
to date) you authored …"), and the first line of *Caveats* says how commits were counted:
"Commits are counted by author date — the day each was written, in its author's own timezone —
from 1 October 2026 to 8 October 2026, both days included; rebasing or pushing a commit later
does not move it."

**Which "today", which timezone.** "Today" is the date on your computer when you run the report.
Each commit belongs to the day it was written *in the timezone it was written in* — 23:30 in
New York on 31 March is a March commit, even though it was already 1 April in London.

**Neighbouring periods add up.** If, on 8 October 2026, `--quarter 2026Q3` counts 120 commits
and `--quarter 2026Q4` (to date) 30, then `--half 2026H2` (to date) counts exactly 150 — the two
quarters together, with no overlap and no gap. A named period and the same days written with
`--since` and `--until` always give the same numbers.

## Pushed or not pushed

Every commit is placed by how far it travelled:

- **On the main branch of a remote** or **on any other remote branch** — counted in the
  headline numbers. This includes work whose local branch you already deleted.
- **Only on your laptop** — on a local branch, a tag, or a checkout not on any branch. Listed
  under *Not pushed yet*, outside the numbers.
- **Only in a scratch worktree** that is not on a branch — left out entirely unless you ask
  with `--include-worktrees` (or `include_worktrees = true` in the config); then it is listed
  under *Not pushed yet* as *worktree only*.

A repository with **no remote at all** (a purely local project) is different: there is nothing
to push to, so its commits are counted, and *Caveats* names the repository so a reader knows
its work could not be checked as pushed. Sijl judges "pushed" from what your laptop last
heard from the remote — it does not contact GitHub or GitLab.

## Rebased and cherry-picked copies

When you **rebase** a branch, **cherry-pick** a fix onto another branch, or **amend** a commit you
had already pushed, git does not move your commit: it writes a new one, with a new id, and the
old one often stays around — on the remote branch you pushed before rebasing, on the branch
you picked the fix from, or in a second copy of the repository on your disk. Counted naively,
one piece of work would appear twice, typically once as delivered and once under *Not pushed
yet*, which makes finished work look unfinished. Sijl recognises these copies and counts the
work once.

**What counts as a copy.** Two of your commits are one piece of work when all of these match —
the things git keeps when it copies a commit:

- the **same author email address**, exactly as written in the commit (so an address-mapping
  file that exists in only one copy of a repository cannot stop two copies matching);
- the **same moment of writing**, to the second — the date the commit was first written, which
  a rebase or a cherry-pick does not change (the same moment written in another timezone still
  matches);
- the **same title** (the commit's first line), ignoring extra spaces — but capital letters and
  wording still count;
- and the **same project**: one repository, or several copies of the same project on your disk
  (recognised because they start from the very same first commit).

Two different projects are never merged, even when you made a commit with the same title in the
same second in both — a script that updates the same setting across many repositories, for
example. Each of those is real work in its own project.

**What you see.** One row per piece of work. Which id is shown first follows three rules, in
order:

1. **Work already in your numbers stays in your numbers.** If one copy is counted, that copy is
   kept — collapsing copies only removes duplicates, it never moves counted work out of the
   totals and into *Not pushed yet*.
2. **The copy that travelled furthest** — on a remote's main branch beats another remote
   branch, which beats only on your laptop. This is the one a reviewer can open on GitHub or
   GitLab.
3. **The original** — if two copies are still equal, the one made first.

The other ids follow in brackets, for example *1a2b3c4d (copy 5e6f7a8b)*, or *(copies …, …)*
when there are several, so a reviewer can still find every copy. A copy is not counted again and
no longer appears under *Not pushed yet* once another copy of it is pushed. If every copy is
still only on your laptop, the work stays under *Not pushed yet* — as one row, with its copies
beside it.

**In Caveats.** There is always exactly one line about copies — how many were found and by which
rule, or that none were:

- "3 rebased or cherry-picked copies of a commit (same author, author time and subject) are not
  counted again; the copies' SHAs are listed beside the one kept, the best-pushed."
- "No rebased or cherry-picked copies of a commit were found (same author, author time and
  subject)."

(A *SHA* is git's name for a commit id.)

One more line appears only when Sijl could not tell whether two copies of a repository on
your disk belong to the same project — because git could not read where their history starts:
"Root commits could not be read in 2 repositories: copies there are not matched with other
clones of the same project." In that case a copy in such a repository may be counted twice;
Sijl prefers that to merging work from two different projects, and tells you.

**In the sample report.** `docs: explain the export` was pushed to `app`'s main branch, then
cherry-picked onto a local branch. The report counts it once, under the pushed copy, with the
local copy's id beside it — ``[`cf1b55ff`](…) (copy `7aae3ccd`)`` in the *Appendix* — and the
local copy does not appear under *Not pushed yet*. *Caveats* says: "1 rebased or cherry-picked
copy of a commit (same author, author time and subject) is not counted again; the copies' SHAs
are listed beside the one kept, the best-pushed."

### Stricter matching: `--dedupe patch-id`

By default, a copy is recognised by who wrote it, when, and its title. With `--dedupe patch-id`
(or `dedupe = "patch-id"` in the configuration file), Sijl also compares **the change
itself** — the lines added and removed — and keeps two commits apart when their changes differ,
even though everything else matches. It can only keep more commits apart; it never merges more.

**When to use it:**

- you often make several commits with the same title in the same second — scripts, bulk edits,
  automated commits — and want to be certain they are never counted as one;
- you are preparing a review where an exact count matters and want the extra check.

**Why it is not the default.** When you resolve a conflict during a rebase, the change itself is
different afterwards, so with this option such a copy is counted twice — and those are exactly
the rebases that matter most. It is also slower: about 10 milliseconds per commit compared,
although only commits that already matched on the default rule are compared, so a report takes
at most a moment longer.

When it keeps commits apart, *Caveats* say how many, for example: "2 commits with the same
author time and subject as another are kept as separate work: the changes differ
(`--dedupe patch-id`)." The copies line then ends "(same author, author time and subject, and
patch)".

**When the change cannot be compared.** Some copies of a repository are *partial clones*: made
to download file contents only when they are needed. Comparing changes there would make git
download into your repository, and Sijl never changes a repository it reads — so it does
not compare changes in a partial clone at all. Those copies, and any whose change git fails to
read, are matched by the default rule instead, and *Caveats* say how many commits that
concerns: "2 commits could not have their patch read (a partial clone, or a git error) and were
matched by author, author time and subject alone." They are never reported as "the changes
differ" — not knowing is not the same as different.

**Limits of copy detection:**

- A rebase that resets the date or the author (options most people never use), or a commit
  whose title you reworded, is **not** recognised as a copy: it is counted again.
- Two genuinely different commits with the same author, second and title are treated as one by
  default; `--dedupe patch-id` separates them.
- A branch merged on GitHub with "squash" is not a copy — the squashed commit has a new date,
  and a title ending in the pull request number. It is linked to the work it contains instead:
  see *GitHub squash merges* below.
- Merge commits are not collapsed; they are read as merge requests and pull requests delivered
  (see the next section).
- A shallow copy of a repository (one cloned with only its recent history) does not know where
  its history really starts, so its copies are matched only within itself: an original in a full
  copy and its rebased copy in the shallow one are both counted. This is not named in *Caveats*.
- A copy of a repository whose history start git could not read is never matched with other
  copies of the same project; *Caveats* name how many such repositories there were.
- The id of a copy shown in brackets may belong to another copy of the repository on your disk,
  not the one whose section it appears in.

## Kinds of work

A reviewer does not ask "how many commits" so much as "what did you work on": new capabilities,
fixes, keeping the code healthy, documentation, the tooling around it. The *Kinds of work*
section answers that from your commit messages.

### How a commit's kind is read

Many teams start commit titles with a short word saying what kind of change it is — the
*Conventional Commits* habit: `feat: add login`, `fix(api): handle 404`, `docs: update the
readme`. Sijl reads that word, and is forgiving where the meaning is certain:

- **Capitals and extra spaces** do not matter: `Feat:  Add login` is a feature.
- **Common spellings** count as the standard word: `feature`, `bugfix`, `hotfix`, `tests`,
  `documentation` and a few others.
- **A reverted commit** (git's own "Revert …" title, also with GitHub's pull request number
  after it: `Revert "feat: x" (#123)`) is a revert; a re-applied one (`Reapply "feat: x"`) is
  the kind of the work it brings back — here a feature.
- **A breaking change** — an exclamation mark before the colon (`feat!: …`) or a line starting
  `BREAKING CHANGE:` in the message, its text on the same line or the indented next one — is
  counted, and the report says how many there were.
- **A ticket key or a backlog id in front** is looked past: in "ABC-123 feat: add login",
  "[ABC-123] fix: …" or "S12: feat: scaffold" the kind is read from what follows. A title that
  starts with a ticket key and has no kind after it ("ABC-123: fix login") is kind *other*,
  without a prefix. A backlog id is one capital letter and a number followed by a colon
  ("S12:", "B3:"); it names a piece of work, not a part of the code, so it is never listed as a
  team prefix.

The standard kinds are features, fixes, documentation, style, refactoring, performance, tests,
build, continuous integration, chores and reverts. Everything else is kind **other**, and never
dropped:

- a title **without** such a prefix ("Add login page", "WIP");
- a prefix your team made up that is not a standard kind ("ui: new header") — Sijl does not
  guess what it means, but keeps the word, and *Caveats* name the ten most used such prefixes
  with their counts (and count the rest);
- a title where the colon is not followed by a space ("feat:login") — too many ordinary titles
  look like that to trust it.

*Caveats* always say how many commits are kind *other* for each of those reasons, so a large
*other* row is explained, not mysterious.

### Review categories — your own judgement

Kinds are what the messages *say*; a review wants **headings**. Sijl uses six:

| Category | Meant for |
|---|---|
| **Features** | a new capability someone can use |
| **Fixes** | something broken now works |
| **Quality** | tests, refactoring, performance, style: better code, same behaviour |
| **Documentation** | documentation |
| **Tooling** | build, CI, dependencies, chores: the machinery around the code |
| **Other** | anything that cannot be placed |

**Which kind of work goes under which heading is your call, not Sijl's.** Is writing tests
in a test-automation project quality work, or the very thing you deliver? Is a performance
improvement a customer asked for a feature? Is a team prefix such as "ui" feature work? The
answers differ by person and role, and a reviewer will read the table as your own account of
your work. Sijl comes with a **default rule**:

| Kind of commit | Heading |
|---|---|
| `feat` | Features |
| `fix` | Fixes |
| `test` | Quality — or Features in a test-suite repository, where the tests are the deliverable |
| `refactor`, `perf`, `style` | Quality |
| `docs` | Documentation |
| `build`, `ci`, `chore` | Tooling |
| anything else — reverts, team prefixes such as "ui", titles without a prefix | Other |

**A test-suite repository** is one whose folder name ends in `-tests` or `-test` (`web-tests`,
`api-test`), or one you list in the configuration file — for a test project with another kind of
name:

```toml
[categories]
test_suites = ["e2e-suite"]   # repository (folder) names, exactly as written; default: none
```

(or `SIJL_TEST_SUITES="e2e-suite"` in the environment, several names separated by commas; see
[Configuration](configuration.md)).

For anything else, the rule is one short function, `review_category()` in
`src/sijl/classify.py`, which you can change in your own copy of Sijl. It receives the commit's
kind, scope, team prefix, whether it breaks something and the repository it is in, and returns
one of the six headings.

In the sample report (13 commits) the default rule gives features 23%, fixes 23%, quality 8%,
documentation 8%, tooling 15% and other 23%.

Sijl guarantees the table adds up whatever the rule does: if your rule fails on a commit —
it stops with an error, or answers something that is not one of the six headings — that commit
is counted under **Other** and *Caveats* say how many commits it happened to and what went
wrong the first time, so you can fix the rule without losing any work from the numbers.

### What you see

Two tables, each with the number of commits and their share of the total, rounded to a whole
percent (a small but real share shows as "<1%", never "0%"; most-but-not-all as ">99%"):

- **By review category**, always in the order above, leaving out headings with no commits;
- **By conventional-commit kind**, in the standard order with *other* last, leaving out kinds
  with no commits.

Because every share is rounded, a table's shares can add up to a few points more or less than
100% — the section says so above the tables.

Then, if there were any, how many commits were marked as breaking changes.

In the sample report:

```markdown
By conventional-commit kind — the `type:` that starts a subject:

| Kind | Commits | Share |
|---|---|---|
| `feat` | 3 | 23% |
| `fix` | 3 | 23% |
| `docs` | 1 | 8% |
| `test` | 1 | 8% |
| `build` | 1 | 8% |
| `chore` | 1 | 8% |
| other | 3 | 23% |

1 commit is marked as a breaking change (`!` or a `BREAKING CHANGE` footer).
```

and *Caveats* explains the *other* row: "Kind *other* holds 2 commits without a conventional
prefix (`type: …`) and 1 commit whose prefix is not a standard kind: ui (1)."

## Months and your longest streak

**Per month** lists every calendar month the period touches — commits and active days (days with
at least one commit). An empty month is shown with zeros rather than left out, because in a
review a quiet month is information too. When the period starts or ends partway through a
month, the label says so — "April 2026 (from 6 April)", "October 2026 (to 8 October)" — so a
short month does not look like a slow one. A commit belongs to the month of the day *you* wrote
it, in your own timezone, like everything else in the report.

Months after today are never listed — nothing can be in them — so a period that runs into the
future (`--until 9999-12-31`) stops at this month. And when the period is long, say
`--since 1970-01-01`, the empty months before your first commit and after your last are folded
into one row each — "No commits from January 1970 to April 2026" — as soon as the table would
have more than 24 rows; every month with commits, and every empty month between two that have
some, keeps its own row.

**Your longest streak** — in the summary — is the longest run of **weekdays** in a row on which
you made at least one counted commit. **Weekends are neutral**: a Saturday or Sunday neither
breaks a run nor extends it. So working Thursday, Friday, Monday and Tuesday is a streak of 4,
whether or not you touched anything at the weekend; committing on Saturday and Sunday as well
still makes it 4. A weekday without a commit ends the run. If two runs are equally long, the
earlier one is shown. The rule is printed in the same sentence as the number, every time — "Your
longest streak was **1 weekday** with commits, on 6 May 2026 (weekends neither break nor extend a
streak; a weekday without a commit — a holiday or leave too — ends it)." If every one of your
commits fell on a Saturday or a Sunday, the summary says there is no streak of weekdays.

Why this rule: counting calendar days would make every normal weekend a break, and counting
weekend commits would reward working weekends. Take three weeks in which you commit every
weekday, and also on the first Saturday: counted in calendar days, the longest run is 6 (Monday
to that Saturday); with weekends neutral it is 15 — every weekday of the three weeks, which is
what actually happened.

## Merge requests and pull requests delivered

**What counts.** When you press *Merge* on GitLab or GitHub, the site records the merge in git
with a message that names what it closed — GitLab ends it with "See merge request
group/project!42" and puts the merge request's title on the third line, GitHub starts it with
"Merge pull request #12 from …" and puts the title on the third line too. Sijl reads exactly
those messages. A merge into the **main line** of a project (the branch your remote treats as
its default, usually `main`) is a delivery. So is a GitLab merge request into another
**long-lived branch** — `main` when your team works on `develop`, or a `release/…` branch — as
soon as that work reaches the main line: a hotfix merged into `main` counts once it is carried
back into `develop`. A merge request into a feature branch counts through the merge request that
later delivers that whole branch. Each delivery shows its **number** (`!42` on GitLab, `#12` on
GitHub), its **title**, and the **day it was merged**, by the clock of whoever merged it.

**Whose it is.**

- **Yours** — the merge request contains at least one commit you wrote. It stays yours when a
  colleague pressed *Merge*: the table says "merged by a colleague" (never a colleague's name).
  A merge request you and a colleague both worked on is yours too, and shows how many of its
  commits are yours ("2 of 3").
- **Merged for others** — you pressed *Merge* and none of its commits are yours. Reviewing and
  merging colleagues' work is part of your contribution, so it gets its own list.
- A colleague merging their own work is not listed. As everywhere in Sijl, "you" means your
  exact email addresses — never a name.

**Not deliveries.**

- **Keeping a branch up to date** — merging `main` into your feature branch, or `git pull`
  creating a merge — only brings existing work in. These "sync merges" are never listed, and the
  commits they bring in never count as the merge request's.
- **Back-merges** — a merge request that brings one long-lived branch into another, such as
  `main` back into `develop` or a release into `main` — carry work that was delivered when it was
  first merged, so they are never listed either. A merge request from another project's `main`
  (a fork) into yours is a real contribution and is listed.
- **A branch merged by hand** (merged on your laptop and pushed, with no merge request) is not a
  merge request or pull request; its commits are counted as usual.
- **Merges are never commits.** Delivering a merge request adds nothing to the commit totals:
  its commits are already counted under *Per repository*.

**What git cannot show.** A merge request merged by **fast-forward** or by **rebase** leaves no
merge record behind, so git alone cannot list it as a delivery — only GitLab's and GitHub's own
records can (a future version may ask them). A pull request **squash-merged on GitHub** is the
exception: GitHub's own commit says which pull request it was, so it is listed (see the next
section); a squash made anywhere else still is not. *Caveats* says so on every report and, when
some of your work reached the main line that way, how many of your commits that concerns — for
example: "4 commits of yours reached a mainline with no merge request or pull request merge
commit — … They are counted as commits; which request delivered them cannot be seen in git alone
(the GitLab and GitHub APIs can; a future version may ask them)." They are still counted as
commits.

**Your merge commits, explained.** *Caveats* says what every merge commit you made was: listed
as a delivery, a sync merge or back-merge (ignored), a merge request merged into a branch that
never reached the main line, or a branch merged by hand — the parts always add up to the total.
Work of yours that reached the main line counts as delivered when *any* of your local copies
(clones) of the project shows the merge request that brought it in.

**Links.** A merge request links to the GitLab project its merge message names, when one of the
repository's remotes is that project; a pull request links to the GitHub project that holds it,
when exactly one does. A fork and the project it was forked from both hold the same pull
request, and the number belongs to only one of them, so neither gets a link — a wrong link would
open somebody else's request. Anything without a link shows its number alone and is counted in
*Caveats*. One thing git cannot settle: in a copy of **only** your fork, kept in sync with the
project it was forked from, a pull request merged in that original project looks exactly like
one merged in your fork. The link then goes to your fork, and *Caveats* says how many pull
requests are linked this way and that only GitHub itself can confirm whose number it is (a future
version may ask GitHub).

**Merged with the pull request's title.** A GitHub project can be set to write the pull request's
title as the merge message — "Fix a typo (#12)" instead of "Merge pull request #12 from …". That
merge is a pull request too, read the same way.

**Tickets.** When you have named your Jira projects, each delivery shows the tickets of those
projects that its branch name, title or description names — `feature/PROJ-42-export` is
PROJ-42 — in a *Tickets* column, and they count in the *Tickets* section too (see *Tickets*).
GitHub squash merges included: their title and description are read the same way, and a commit
of yours that names the same ticket as a squash merge is recognised as that pull request's work
(*delivered via #N*) even when nothing else ties them.

**In the sample report**, *Delivered* opens with: "**5 merge requests and pull requests** of
yours were merged into a mainline — 2 by you, 1 by a colleague, 2 as GitHub squash merges — and
you merged 1 for colleagues." Then one table per repository:

```markdown
#### app — 2 deliveries

| Merged | Request | Title | Your commits | Merged by | Tickets |
|---|---|---|---|---|---|
| 2026-05-05 | [!150](https://gitlab.com/acme/platform/app/-/merge_requests/150) | Retry on 503 | 1 of 1 | you |  |
| 2026-04-08 | [!42](https://gitlab.com/acme/platform/app/-/merge_requests/42) | Export the report (PROJ-12) | 2 of 2 | a colleague | PROJ-12 |
```

and, under *Merged for others*, the colleague's merge request `!151` you merged. *Caveats*
accounts for every merge commit you made: "3 merge commits you made are not counted as commits:
3 listed under *Delivered*." All three of the site's pull requests come from a project with a
single GitHub address, so *Caveats* also notes that their numbers can only be confirmed by
GitHub.

## GitHub squash merges

**What it is.** On GitHub a pull request can be merged with **"Squash and merge"**: instead of
bringing the branch's commits into the main line, GitHub writes **one new commit** holding all of
the branch's changes, titled like the pull request with its number at the end — "Fix a typo
(#12)". GitHub itself is recorded as the one who made that commit; you, as the pull request's
author, are recorded as its author.

**Why it needs care.** Counted naively, there are two unrelated things — a commit of yours on
the main line (the squash) and the commits on your branch — and both would be counted:

- when the branch is still on GitHub, the same work would be counted **twice**: once as each
  branch commit and once more as the squash;
- when GitHub has deleted the branch but your laptop still has it, the branch commits would sit
  under *Not pushed yet*, so **delivered work would look unfinished**;
- when the branch is gone everywhere, only the squash is left — one commit, however big the pull
  request was.

**What Sijl does.** Every squash merge of yours on a main line is listed under *Delivered*, like
any pull request — its day, its number (a link to the pull request), its title — and its work is
counted **exactly once**:

- **Its branch commits are still here** → they are the work. Sijl recognises them — by an
  identical snapshot of the files (the branch was up to date when it was merged), by their titles
  (GitHub's description lists every commit of the branch, and a one-commit pull request is titled
  after its commit), by being further down that same branch, or by a ticket key both mention
  (with your Jira projects named) — and counts them, **not** the squash.
  When two of your commits share a title, the one that reached GitHub wins over one that never
  left your laptop, then the one written last before the merge. They are found in any copy of the
  project on your disk, and also when they were written up to 90 days before the period. Those
  only on your laptop count too: they were delivered, so they leave *Not pushed yet* and are
  marked *delivered via #12*. The *Delivered* row says "2 linked".
- **Its branch commits are gone** → the squash itself is counted as one commit **with its lines**
  — how many lines the pull request added and removed in total — so a reader can see how big
  that one commit's work was. The row says "the squash: +120 -30 lines". Sijl says "gone"
  only when it checked: none of your commits since your previous delivery is left unlinked.
- **Neither can be told for sure** → nothing links to the squash, but commits of yours since
  your previous delivery are unaccounted for and may be its branch — or another branch's. The
  squash is still counted as code, with its lines ("the squash, not linked: …"): taking it out
  could lose real work. *Caveats* says how many squashes this concerns and how many of those
  commits were pushed and counted in the same period, where the same work may be counted twice.

A squash merge leaves the totals **only** when its own branch commits are counted instead.

A commit is never linked to a pull request merged before the commit was written, and each
commit belongs to one pull request at most. A pull request squash-merged after the period is not
listed, but the commits written in the period that it delivered still count as delivered.

**Periods side by side add up.** Run the report for Q2 and then for Q3, and the two together count
exactly what one report over both quarters counts. A branch written at the end of June and
squash-merged on 2 July is counted in Q2, where it was written — its commits, pushed or delivered
from your laptop — and in Q3 the pull request is listed as a delivery, not counted again: Sijl
reads 90 days before each period to find such branches. A branch written more than 90 days before
its merge is the exception, and *Caveats* says for how many squash merges the 90 days were the
limit.

**A merge GitHub rebased is your own commit.** GitHub's "Rebase and merge" also records GitHub as
the one who committed, and your commit title may end in an issue number — "fix: handle empty input
(#30)". Sijl tells it from a squash by its date: a squash is stamped at the moment of merging,
a rebased commit keeps the moment you wrote it. It stays your commit, counted as usual.

**Who merged it** is not recorded by git for a squash merge (GitHub is the one recorded), so the
*Merged by* column says "not recorded (squash)", and the headline counts these pull requests
apart: "— 2 by you, 1 by a colleague, 2 as GitHub squash merges".

**A number in brackets alone is not a squash.** Many people end their own commit titles with an
issue number — "fix: the typo (#12)". That is an issue reference, not a pull request: only a
commit GitHub itself made, with one parent, at the moment of merging, is read as a squash merge.
Every report says so in *Caveats*.

**In Caveats.** How many squash merges were listed and how each was counted; how many of your
commits were linked and how (by snapshot, by title, further down a branch, by ticket); how many
left *Not pushed yet*; how many squash merges in the period hold commits written before it, and
for how many the 90 days read before the period were the limit; how the lines are counted ("its
own change against the commit before it": a renamed file counts only what changed in it, a
picture or other binary file has no lines) and for how many they could not be read; and why
*Merged by* says "not recorded".

**Lines are never read where it would change your repository.** Some repositories are *partial
clones*, which download file contents only when needed; counting lines there would download into
your repository, so Sijl does not, and says for how many squash merges the lines were not
read.

**Limits:**

- A pull request whose branch was not up to date and whose description you rewrote cannot be
  linked; it is counted as work, and if pushed commits of yours since your previous delivery were
  in fact its branch, that work is counted twice. *Caveats* gives both numbers.
- A branch written more than 90 days before its squash merge is not found from the merge's period:
  the squash is counted there, the branch in its own period. *Caveats* says how often.
- A squash counted as work may sit beside commits under *Not pushed yet* that were in fact its
  branch, when nothing linked them (they stay outside the totals, so nothing is counted twice).
- When a branch was built on top of another branch that was squash-merged before it, and nothing
  links the first branch's commits to their own pull request, the later pull request takes them
  and the first one is counted as work too.
- A colleague's squash-merged pull request that contains commits of yours is not linked (it names
  you as co-author, which a future version may read).
- Copies of the same project on your disk are recognised by their shared first commit; a copy
  whose history start git cannot read is linked only within itself, and *Caveats* says so.
- A web edit on GitHub whose title ends in "(#12)" looks exactly like a squash; it links nothing,
  is listed under that number and stays counted as your work.
- A pull request squashed into another branch rather than the main line stays an ordinary commit,
  counted in *Caveats*.

**In the sample report**, the project `site` has two squash-merged pull requests. Search (#13)
was merged while its two branch commits were still on the laptop: they are linked to it, counted
as your work, listed in the *Appendix* as delivered rather than under *Not pushed yet*, and its
*Delivered* row says "2 linked":

```markdown
| 2026-04-21 | `c8026221` (delivered via [#13](https://github.com/alexgh/site/pull/13)) | feat: search results |
| 2026-04-20 | `632d94ff` (delivered via [#13](https://github.com/alexgh/site/pull/13)) | feat: a search box |
```

The branch of "Fix a broken link" (#14) is gone, so the squash itself is counted, with its lines
— "the squash: +1 -0 lines" under *Delivered*, and in the *Appendix*:

```markdown
| 2026-05-19 | [`fd1136bd`](https://github.com/alexgh/site/commit/fd1136bd…) (squash merge, +1 -0 lines) | Fix a broken link (#14) |
```

*Caveats* explains both: "2 GitHub squash merges of yours … are listed under *Delivered* and
counted once: 1 was linked to the commits of yours on its branch, which are counted instead of
the squash; 1 whose branch commits are gone — none of your commits written since your previous
delivery is left unlinked — is counted as code itself, with its lines."

## Links to the evidence

Each counted commit's short id in the *Appendix* is a link to that commit on GitHub or GitLab,
each repository there names the project(s) it links to, and so does *Per repository*. A reviewer reading the report
in a Markdown viewer (GitHub, GitLab, an editor's preview, Confluence) clicks straight through to
the change.

**Where a link points.** Sijl reads where each of your repositories was cloned from or
pushed to (its *remotes*) — the same information `git remote -v` shows — and builds the web
address from it:

- **Every common way of writing an address works**: `git@gitlab.com:group/project.git`,
  `https://github.com/owner/project.git`, `ssh://git@host:2222/…` and the older `git+ssh://`
  form.
- **GitLab groups inside groups are kept whole**: `group/team/sub/project` links to that exact
  project, not to its top group.
- **Shortcut names from your ssh settings work**: if your remotes say `git@github-alt:…`
  and your `~/.ssh/config` says `github-alt` means github.com, the links go to github.com.
  Sijl asks your ssh program — it never connects anywhere — and *Caveats* list each shortcut
  it resolved, for example "`github-alt` → `github.com`". It asks about at most 8 names, and
  for at most 10 seconds in all, per report, so a repository with dozens of made-up site names
  cannot make the report slow; any name beyond that is listed in *Caveats* as not asked.
- **Several remotes:** a commit links to the first remote that actually has it — `origin` first,
  then the others by name. In a repository whose `origin` only mirrors the main branch while the
  feature branches live on another remote, each commit still links to a page that exists.

**What has no link** — shown as a plain id, never a broken or guessed link:

- commits that are not pushed yet (they exist only on your laptop) and the ids of copies shown in
  brackets (a copy may live in another clone, or nowhere online);
- commits in a repository with no remote branches at all (already named in *Caveats*);
- commits pushed only to a site Sijl does not know as GitHub or GitLab. github.com and
  gitlab.com are always known; a company's own GitHub Enterprise or GitLab installation must be
  named once in the configuration (see [configuration](configuration.md#links-to-github-and-gitlab)).
  *Caveats* say how many commits this concerns, in which repositories, and which site names were
  not recognised.

**Safe by design.** The address of a remote can contain a password or an access token
(`https://name:token@…`, `?private_token=…`). Sijl removes it before doing anything else: it
never appears in a link, in the report, in an error message or anywhere else. A remote whose
address cannot be read safely is named in *Caveats* by its name only, never by its address. The
only links in the report are the ones Sijl builds itself, always `https://` to GitHub or
GitLab; a commit title, folder name or remote name that looks like a link or a web-page tag is
shown as plain text — even when several such names sit side by side on one line — so the report
cannot carry a disguised link to somewhere else. An address written so that it seems to point at
GitHub but really points elsewhere (`https://other.site?@github.com/…`) is read as the other
site, never linked as GitHub.

**In the sample report**, the 7 commits of `app` link to gitlab.com/acme/platform/app and the
pushed commits of `site` to github.com/alexgh/site. `tools` has no remote, so its commits keep
plain ids and *Caveats* says "No remote-tracking branches in tools: commits there are counted,
but whether they were pushed cannot be checked." The commits delivered by #13 are only on the
laptop, so they link to the pull request instead of a commit page.

## Tickets

A reviewer thinks in tickets: "what did you do for the new billing flow?". When your commit
messages mention Jira tickets — `PROJ-1234` — Sijl groups your work by them.

### Name your Jira projects once

Ticket-shaped ids are everywhere in commit messages, and only some are tickets: `TC-13` may be a
test case, `R2-1` a requirement, `QC-22` a data check — and nothing in the id itself says which.
So Sijl counts tickets **only for the Jira projects you name** — in the configuration file
(`jira.projects`), or with `--jira-project` on the command line. Until you name any, nothing is
counted as a ticket; instead *Tickets* shows which prefixes look like projects in your commits,
so you can pick yours:

| Prefix | Commits | In a ticket position | Example |
|---|---|---|---|
| PROJ | 48 | 40 | PROJ-2048 |
| OPS | 21 | 18 | OPS-567 |
| QC | 12 | 5 | QC-28 |
| TC | 9 | 0 | TC-16 |

*Commits* is how many of your counted commits mention the prefix; *In a ticket position* is how
many mention it where people write tickets — before the title ("PROJ-12: …"), in brackets after
the kind ("feat(PROJ-12): …"), in brackets at the end of the title, in a merged branch's name,
on a "Closes PROJ-12" or "Refs PROJ-12" line, or as a "Jira: PROJ-12" line at the end of the
message. A prefix that only ever appears in running text ("see TC-16 for details") is a weaker
hint, so the table puts prefixes that appear in ticket positions first. In the example, PROJ and
OPS are the projects; QC and TC are test-case ids. Example ids are real keys from your commits,
so you can recognise them. The table shows the ten most likely prefixes and says how many more
there are.

### What you see

With your projects named, *Tickets* starts with one sentence and a table — in the sample report:

"**1 ticket** in PROJ, named in 2 of your 13 commits (15%); the other 11 name none. A commit
counts once under each ticket it names, wherever its message names it."

| Ticket | Commits | Requests | First | Last |
|---|---|---|---|---|
| PROJ-12 | 2 | 1 | 2026-04-06 | 2026-04-08 |

- **Commits** — how many of your counted commits mention the ticket. A commit that mentions it
  three times counts once; a commit that mentions two tickets counts under both, which is why
  the column can add up to more than the number of commits with a ticket.
- **First** and **Last** — the days of the first and the last of those commits, in your own
  timezone like every date in the report.
- Most commits first; tickets with the same number of commits in key order.
- **Requests** — shown when one of your merge requests or pull requests names a ticket: how many
  of them name it. A ticket that only a request names (its branch was `feature/PROJ-77-export`,
  but no commit message says PROJ-77) is listed with 0 commits, and *First* and *Last* then are
  the day it was merged; for the others they cover the commits and the merges.
- **Every ticket is listed** — none is left out of the table.
- Any other prefixes that still look like projects follow in the table above, under "Other
  prefixes that look like Jira project keys — not in `jira.projects`, so not counted as
  tickets", so a project you forgot to name stays visible.

**Tickets never change your other numbers.** A commit without a ticket is counted in the
summary, the kinds of work, the months and its repository exactly like one with a ticket;
*Tickets* is a way of looking at the same commits, not a filter on them. Commits under *Not
pushed yet* and rebased copies are left out of *Tickets* just as they are left out of the
totals.

### What is never a ticket

Many things are written like a ticket key — capital letters, a hyphen, a number — and Sijl
recognises and skips them:

- **standards and well-known names**: UTF-8, SHA-256, ISO-8601, RFC-1234, HTTP-2,
  CVE-2024-1234, X86-64, AVX-512, PEP-8 and a few dozen more;
- **versions**: anything followed by a dot and a number, such as PY-3.12 or RELEASE-1.2;
- **parts of longer ids**: TC-UI-07 is one id, never the ticket "UI-07"; ABC-2024-01 is not
  ABC-2024;
- **numbers with a leading zero**: QC-007 or CH-03 — Jira never pads its numbers;
- **ids glued to a word**: "GRÖSSE-2" or "caféABC-1" hold no ticket, whatever the alphabet;
- **lower-case ids** such as proj-123 in a title — Jira writes keys in capitals. The one
  exception is a branch name: for the projects you named, "feature/proj 1234",
  "feature/proj_1234_retry" or "proj-1234-retry" in a merged branch's name is PROJ-1234. The
  project and the number must be separate words there: "proj1234" is not read, nor is
  "feature/PROJ2-5" (that is project PROJ2), nor "feature-proj-5", which looks exactly like the
  tail of a longer id such as "tc-proj-5". In a GitHub merge ("from owner/branch") only the
  branch is read, never the owner's user name.

An underscore after the number ends a key: "feature/PROJ-12_fix" names PROJ-12. A title that
starts with several keys ("PROJ-1 PROJ-2 feat: …") names all of them, and its kind is still read.

A standard can never be one of your projects: naming UTF as a Jira project is refused with an
explanation.

### In Caveats

Whatever is not counted as a ticket is said, with numbers. From the sample report:

- "1 commit names keys of projects not in `jira.projects` — QC (1) — which are not counted as
  tickets." With more prefixes, the ten most frequent are named and the rest counted ("…, plus
  3 more prefixes"). Without named projects: "No Jira projects are set (`jira.projects`), so the
  ticket-shaped keys in 12 commits are not counted as tickets; *Tickets* suggests the likely
  projects."
- "1 commit mentions ids that only look like ticket keys — standards, versions or parts of
  longer ids — and are never counted as tickets: UTF-8 (1)." — so you can check nothing real
  was skipped.

And when they apply, for example:

- "1 ticket is named only by your merge requests and pull requests — in a branch name, title
  or message — and by no counted commit: PROJ-77." — a ticket your commits never mention but
  your merge request's branch does is still yours; this line says where it came from.
- "1 ticket named only by merge requests and pull requests you merged for colleagues is not
  counted as yours: …" — merging a colleague's work is review work, not their ticket.
- "Ticket keys are read from counted commits and from merge requests and pull requests (their
  branch, title and message); the other 2 merge commits you made are not read for tickets." —
  the merges that are not deliveries (keeping a branch up to date, merges by hand).

Naming your projects changes only *Tickets* and these lines: every other number in the report
stays exactly the same.

### Not yet

- Ticket **titles** and types are not shown: they live in Jira, which Sijl does not ask; a
  future version may read them. Today the table shows keys only.
- Merge commits that are not merge requests or pull requests (keeping a branch up to date,
  merges by hand) are not read for tickets.

## Configuration

Write your emails, folders and period once in `~/.config/sijl/config.toml`; then
`sijl report` needs no flags, and any flag still overrides the file — see
[configuration](configuration.md). The period can be written there the same ways —
`last = "6m"`, `quarter = "2026Q3"`, `half = "2026H1"`, `year = 2026`, or `since`/`until` — and
any period given on the command line replaces it entirely. `sijl config show` ends with the
period your settings choose today. How copies are matched is the `scan.dedupe` setting:
`key` (the default) or `patch-id`. Which sites get links is the `[forges]` section: your
company's GitHub Enterprise or GitLab site names, and whether ssh shortcut names are looked up.
Your Jira projects are `jira.projects`, for example `["PROJ", "OPS"]`, and the repositories whose
tests count as features are `categories.test_suites`.

## Behaviour & limits

- **Your commits only**, matched by exact email address — never by name.
- **By author date**, in each commit's own timezone: a commit written at 23:30 on 31 March in
  New York belongs to March.
- **The period** is whole days, both ends included, chosen one way at a time; "the last …"
  ends today, and on a month's last day "the last … months" start on the 1st; a quarter, half or
  year still running stops at today and is marked "to date"; one that has not started is
  refused; no year before 1970 can be named. Every report states its days and the counting rule
  in the header and in *Caveats*.
- **Searches** up to 4 folder levels below each `--root`, never inside a repository and never
  through a symbolic link. A submodule (a project kept inside another) is found and read as a
  repository of its own. Hidden, dependency and build folders (`.claude`, `.venv`,
  `node_modules`, …) are not searched into and are named in one Caveats line; every other path
  not scanned — too deep, unreadable, a link, a damaged repository, one owned by another user —
  is listed under Caveats with its reason. Use `--repo` to include one directly, and
  [`sijl repos`](repos.md) to see the whole list before you run a report.
- **Safe to print:** invisible control characters in folder names, commit subjects or git's
  messages — which could clear or rename your terminal, or reverse how text reads — are shown
  as visible codes such as `\x1b`, in the terminal and in the file `--out` writes; a folder name
  that is not valid text shows its odd bytes as `\xff`-style codes instead of breaking the run.
- **Never misled by a folder's `.git`:** a broken one is never read as the project around it, a
  `.git` that leads elsewhere (another checkout, another project's history) is not read through
  it, and a folder that would make git wait forever is skipped — each named under Caveats.
- **Read from** local branches, remote branches, tags and your main checkout. Stash entries,
  notes and git's internal backups are never read; scratch worktrees only when asked.
- **Links** go to the GitHub or GitLab page of each counted commit, from the first remote that
  has it, and of each delivered merge request or pull request, in its own project; unpushed
  commits, copies and sites Sijl does not know stay plain ids, explained in *Caveats*.
  Passwords or tokens written into a remote's address never appear anywhere. Ticket keys are not
  links yet: Sijl has no setting for your Jira site's address.
- **Merge requests and pull requests** merged into a project's main line in the period — or,
  for GitLab, into another long-lived branch whose merge has reached the main line — are listed
  under *Delivered*: yours, and the ones you merged for colleagues. Back-merges and sync merges
  never are. Pull requests squash-merged on GitHub are listed too, their work counted once (see
  *GitHub squash merges*). Those merged by fast-forward or rebase cannot be seen in git and are
  disclosed in *Caveats*; a merge request merged into a branch that has not reached the main line
  is not listed until it does. GitHub's merge message does not name the target branch, so a pull
  request counts only once its merge sits on the main line itself.
- **Merge commits** are never counted as commits; *Caveats* says what each one you made was.
- **Kinds of work, months and the streak** count exactly the commits in the headline total:
  never work under *Not pushed yet*, never a collapsed copy, never a merge commit.
- **Review categories are your judgement**: the default rule above is a starting point — list
  your test-suite repositories in `categories.test_suites`, or change the rule itself to fit your
  role. A rule that fails on a commit puts it under *Other*, and *Caveats* say so.
- **Tickets** are counted only for the Jira projects you name, from the same commits as every
  other number, each commit once per ticket; standards, versions, parts of longer ids and
  padded numbers are never tickets, and *Caveats* say how many ids were skipped and why.
- **Holidays and days off end a streak**: Sijl knows nothing about your calendar, only
  weekends. A run that began before the period is counted from the period's first day.
- **Rebased, cherry-picked and amended copies** of a commit are counted once — under a copy
  already in your numbers if there is one, otherwise the copy that travelled furthest — with the
  others' ids beside it; *Caveats* say how many, and anything that could not be checked (see
  *Rebased and cherry-picked copies* above for exactly what counts as a copy).
- **GitHub squash merges** are deliveries whose work counts once — as the branch commits linked
  to them (those left only on your laptop marked *delivered via #N*), or as the squash itself
  with its lines when they are gone, or not at all while pushed work may be its own; a title
  ending in "(#12)" in your own commit is just an issue reference.
- **Highlights follow a fixed rule** — the most commits; on a tie the earlier month, the more
  recently merged request, the ticket named by more requests — not a judgement of importance;
  each highlight is also listed in its own section.
- **Your own template** sees exactly the numbers and evidence of the built-in report, cannot read
  other files or run code, prints text from your repositories only in its safe form, writes no
  link or address of its own unless you allow it, and is always followed by Sijl's Caveats
  and footer. It is still code: use templates you trust.
- **`--out` never writes into a repository Sijl read** unless you give the file's full path
  with no symbolic link on the way, never into git's own files, and never through a link.
- The figures are **evidence of activity, not a performance score** — the report says so.

## Related

- [Install, upgrade and uninstall](install.md)
- [Configuration](configuration.md) — every setting, in the file, the environment or a flag
- [Which repositories Sijl reads — `sijl repos`](repos.md)
- [Ask Claude Code about your work — `sijl digest`](digest.md)
- [Ask Claude from Sijl — `sijl ask`](ai.md)
- [All user documentation](README.md) · [Project README](../README.md)
