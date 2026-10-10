# Ask an AI about your work — `sijl digest`

## What it is

`sijl digest` writes the evidence of your review period — every commit you made, every merge
request and pull request you delivered, every ticket you worked on — into one compact text file,
ready for an AI assistant to read. You give that file to Claude Code (or to any AI chat, as an
attachment) and ask it anything: "What did I deliver for PROJ-12?", "Summarise my Q3 in five
bullet points", "Which of my merge requests touched the billing flow?". The AI reads the file and
answers, quoting the commit, request or ticket each statement comes from, so you can check every
claim.

This is Sijl's **Tier 1** way to ask questions: it needs no API key, costs nothing beyond the AI
tool you already use, and Sijl itself sends nothing anywhere. With an Anthropic API key, **Tier 2**
— `sijl ask` — does the same from inside Sijl, reading this same digest, and checks the ids in
every answer for you ([AI assist — `sijl ask`](ai.md)).

## Who it's for

- Anyone preparing a performance review who would rather **ask** about six months of work than
  read a long report line by line.
- People who already use **Claude Code** or another AI assistant, with or without an API key.
- People who want to **see exactly** what an AI reads before it reads it: the digest is a plain
  text file you can open, search and edit first.

## Why it matters

- **Answers you can check.** Every line of the digest starts with an id — a commit's short SHA, a
  merge request such as `webapp!42`, a pull request such as `app#12` (the repository, then the
  number), a ticket key — and the instructions in the file ask the AI to cite them. A claim
  without an id is a claim to doubt.
- **Right numbers.** The totals at the top (commits, active days, requests, tickets, per month,
  per kind of work) are Sijl's own exact counts — the same as in `sijl report` — and the
  instructions tell the AI to use them instead of counting lines itself.
- **Privacy by default.** Colleagues' names and email addresses are replaced before the file is
  written; you decide what the AI reads.
- **No surprises.** Nothing leaves your computer when you run `sijl digest`. When an AI tool
  reads the file, it does so under that tool's own settings and your approvals.

## How to use it

**1. Write the digest** — with the same period and folders you use for the report (or none, when
your configuration file sets them):

```bash
sijl digest --with-prompt --out digest.txt --last 6m
```

`--with-prompt` puts short instructions at the top of the file: answer only from the digest, cite
the ids, say "not in the data" when the digest does not hold the answer. Sijl then tells you, on
screen, what it wrote — for example:

```text
Digest: 201,350 characters, about 67,200 tokens (estimated on this computer: nothing was sent), 1,340 ids to cite — 1,012 commits, 297 requests, 31 tickets.
Redaction: on — 14 names and 6 email addresses replaced (Sijl knows 85 names and user names of other people from your repositories' history); only yours are kept (--no-redact keeps them all).
Nothing was omitted.
Wrote digest.txt
Ask Claude Code about it: claude 'Read digest.txt and answer: …'
```

(Illustrative numbers for six busy months; a small example made from real output follows below.)

**2. Ask the AI.** With **Claude Code**, in the same folder:

```bash
claude 'Read digest.txt and answer: what did I deliver for the billing flow?'
```

or start `claude` and ask several questions in a row. Claude Code shows which files it reads,
under your own Claude Code permission settings.

With **any other AI chat** (a web or desktop assistant): attach `digest.txt` to the conversation
— or paste it, if it is small — and write "Follow the instructions at the top of the attached
file, then answer: …". Some chat tools limit the size of a file or a message; `--max-chars`
(below) makes the digest fit.

**3. Check the answer.** Look up the ids it cites in the digest — or in your repositories:
`git show 1a2b3c4d`, or the merge request on GitLab or GitHub.

Without `--out`, the digest is printed on screen (and nothing else is: messages go to the error
stream), so you can pipe it wherever you like — into a file, or your clipboard for a chat window.
With `--out`, the digest goes only into the file, and the screen shows the summary above.

**Before you give it to any AI tool**, check your employer's policy on AI tools and on sharing
work information with them: the digest holds your commit subjects, merge request titles,
repository names and ticket keys as you wrote them (see [Privacy](#privacy)).

## An example

A small, made-up project — three repositories, three months — gives this summary:

```text
Digest: 5,031 characters, about 1,693 tokens (estimated on this computer: nothing was sent), 23 ids to cite — 14 commits, 6 requests, 1 ticket and 2 other key-like ids written in subjects or titles.
Redaction: on — 0 names and 0 email addresses replaced (Sijl knows 2 names and user names of other people from your repositories' history); only yours are kept (--no-redact keeps them all).
Nothing was omitted.
```

and a digest whose body reads (after its header and instructions):

```text
Totals — exact; use these rather than counting the lines below:
- 13 counted commits on 13 active days in 3 repositories; longest streak 3 weekdays, 2026-05-11 to 2026-05-13 (weekends neither break nor extend a streak; a weekday without a commit ends it)
- per repository: app 7, site 4, tools 2
- per month (the others had none): 2026-04 5 commits on 5 days, 2026-05 5 commits on 5 days, 2026-06 3 commits on 3 days
- per kind: feat 3, fix 3, docs 1, test 1, build 1, chore 1, other 3; breaking changes: 1
- per review category: feature 3, fix 3, quality 1, docs 1, tooling 2, other 3
- tickets of PROJ: 1, named by 2 of the 13 counted commits
- requests of yours merged: 5 (2 merged by you, 1 by a colleague, 2 as GitHub squash merges (merger not recorded)); requests you merged for colleagues: 1
- not pushed yet: 1 commit, listed last and in no number above
…

## Tickets — each counted commit naming a ticket, under every ticket it names
### PROJ-12 — 2 commits, 1 request; 2026-04-06 to 2026-04-08; requests: app!42
- 36be6842 2026-04-07 [app] test(PROJ-12): cover the export → app!42
- 0ae9f342 2026-04-06 [app] feat(PROJ-12): export the report → app!42

## Requests — merge requests and pull requests merged in the period: …
### site#14 — 2026-05-19 [site]; squash-merged; the squash counted as code, +1 -0 lines (its branch commits are gone); title: Fix a broken link
- fd1136bd 2026-05-19 Fix a broken link (#14)
### app!150 — 2026-05-05 [app]; merged by you; 1 of 1 commit yours; title: Retry on 503
- a57bfd52 2026-05-04 fix(api)!: retry on 503 | not on 500
…
### app!151 — 2026-05-07 [app]; merged by you for a colleague; 1 commit, none yours; title: Their feature

## Repositories — the other counted commits, in no ticket and no request; busiest repository first
### app — 7 commits in all; 4 below
- 237272ec 2026-06-01 fix: flaky QC-4 test on UTF-8 names
…

## Not pushed yet — your commits on no remote branch; no number above counts them
### app — 1 commit
- 3a8a2d6e 2026-06-02 (local branch) feat: an idea not pushed yet
```

`QC-4` and `UTF-8` in a subject are the "other key-like ids": they are written in the text, so an
answer may cite them, but they are not tickets — only the keys of your Jira projects are (here,
`PROJ`).

## What the digest contains

In this order:

1. **A header** — who you are (your name and the email addresses you commit with), the period and
   how it is counted (by the day each commit was written, in its author's timezone, both ends
   included), how ids are written, **the totals**, and what the digest leaves out.
2. **Tickets** — for every ticket of your Jira projects (`--jira-project`, or `jira.projects` in
   the configuration file): its numbers, the requests that name it, and each of your commits
   that names it. A commit that names two tickets is listed under both. With no Jira projects
   set, this section says so and lists nothing.
3. **Requests** — your merge requests and pull requests merged in the period, newest first: the
   day, the repository, who merged it, how many of its commits are yours, its tickets and its
   title, then its commits that name no ticket. GitHub squash merges are here too ("squash-
   merged"; git does not record who pressed the button), with what was counted for them. Then
   the requests you merged for colleagues.
4. **Repositories** — your remaining commits, those in no ticket and no request, per repository,
   busiest first.
5. **Not pushed yet** — commits only on your computer; no number above counts them.

Each commit line shows its short SHA, the day it was written, and its subject (the first line of
its message).

**Not in the digest:** the rest of each commit message (bodies, `Co-authored-by` lines), merge
commits themselves (the request is listed instead), links, and the long explanations of the
report's *Caveats* — `sijl report` has those ([The report](report.md)).

**The summary line on screen** counts what you can cite: commits, requests, tickets of your Jira
projects, and — when a subject or a title happens to contain one — other key-like ids such as
`UTF-8`. Without Jira projects it says "no tickets (no Jira projects are set)", exactly as the
digest's header does, and counts every key-like id as such.

## Options

| Option | What it does |
|---|---|
| `--with-prompt` | Starts the file with the instructions for the AI. |
| `--out FILE` | Writes the digest to `FILE` instead of the screen — never inside a repository Sijl reads unless you give that exact, full path, and never into git's own files (the report's rule). Run it from outside your repositories, or give a path such as `~/digest.txt`. |
| `--max-chars N` | Writes at most `N` characters, instructions included (see below). |
| `--no-redact` | Keeps colleagues' names and email addresses as written. |
| period, `--root`, `--repo`, `--author-email`, `--jira-project`, `--include-worktrees`, `--dedupe`, `--config` | The same as for `sijl report`, with the same defaults from your configuration file ([Configuration](configuration.md)). |

## Privacy

**What is replaced (redacted) by default:**

- Every email address that is not one of yours becomes `[email 1]`, `[email 2]`, …, and every
  colleague's name Sijl knows — and the user names in their addresses (`sam.colleague`, a GitHub
  login) — becomes `[person 1]`, … . The same colleague keeps the same label throughout the file,
  so the AI can still say "you and [person 1] …".
- **Which names Sijl knows:** everyone in the whole history of the repositories it read, of any
  date — everyone who wrote or committed a change, and everyone a commit message's closing lines
  name (`Co-authored-by`, `Reviewed-by`, `Signed-off-by`, `Acked-by`, `Reported-by`, …). A name is
  yours only when it was committed with one of your email addresses, so a colleague whose name
  looks like yours is still replaced. Sijl tells you how many names it knows.
- **However a name is written:** with or without its accents (`José García`, `Jose Garcia`), with
  a curly apostrophe or a typographic hyphen (`O’Brien`, `Jean‐Luc`), in capitals, split over two
  lines, or in full-width letters.
- **Addresses in any common shape** — `name@laptop`, inside links, at an IP address, typed with a
  full-width `＠` — the user name in a link (`https://sam@git.example.com/…`, and any password
  with it), and the name written beside a colleague's address: after `Reviewed-by:`, in quotes
  (`"Rita Reviewer" <rita@…>`), or simply before it in a sentence (`reported by Rita Reviewer
  <rita@…>`, `Rita Reviewer (rita@…)`).
- **Request ids never name a person:** a merge request or pull request is quoted by your
  repository and its number, never by the GitLab group or GitHub owner its project lives under,
  which can be a colleague's own account.

**What is kept as written** — and so read by whatever AI you give the file to:

- your own name and the email addresses you commit with;
- your commit subjects (first lines) and merge request and pull request titles;
- repository names (folder names), ticket keys, dates, counts and short commit SHAs. (A merged
  branch's name is never written; only a ticket key found in it is.)

**What redaction cannot catch:** a name Sijl never saw ("thanks Jane"), a nickname, GitHub
`@mentions` of people who never committed, a lower-case name before an address in a sentence, and
anything else personal or secret written in a commit subject or request title — a customer's
name, an internal host name, a password committed by mistake. Open the file and read it before
you give it to anyone, any AI tool included.

- **`--no-redact`** keeps everything as written — for your own notes, never for sharing.
- **Where it goes:** nowhere, until you give it to someone. Sijl writes the file and stops.
  Claude Code, or the chat tool you attach it to, reads it under your own account and that
  tool's terms.
- **Your employer's rules come first.** Many companies have a policy on which AI tools may see
  work information (commit messages, project and customer names). Check it before you share the
  digest with any AI tool.

## Behaviour & limits

- **Size and time.** As an illustration: six busy months — about 1,000 commits and 300 merge
  requests — make a digest of roughly 200,000 characters, about 67,000 tokens by Sijl's own
  estimate (which errs on the high side; the real count is usually lower), written in a couple of
  seconds. That fits easily in Claude's context. The estimate is worked out on your computer:
  Sijl never asks an AI provider to count, because counting would mean sending the text.
- **`--max-chars N`** keeps the file within `N` characters for a smaller context, a chat window or
  a token budget. The header and the section headings are always kept; what is left is shared
  fairly — a small section, like *Not pushed yet*, stays whole while the large ones give up the
  lines at the end of their lists first (the oldest commits and requests). **Nothing is cut
  silently**: a ticket, request or repository that lost lines says `(showing 3 of 12)`, every
  section that lost anything ends with `[… 40 more omitted …]`, the header says how many
  commits, requests and tickets were left out, and Sijl repeats it on screen. The instructions
  tell the AI that an answer built from a shortened list may be incomplete. A limit too small for
  the header itself is refused, naming the smallest limit that works (usually a few thousand
  characters; for a tiny digest, its own length).
- **The same every time.** The same repositories, period and settings always give exactly the
  same file, byte for byte — whichever order you list your folders in.
- **No network.** `sijl digest` reads your local repositories only, like `sijl report`; it
  never writes into them.
- **Quoting ids.** Commits are quoted by 8 characters of their SHA (more only if two of your
  commits would share them), requests by their repository and number (`webapp!42` for a GitLab
  merge request, `app#12` for a GitHub pull request), tickets by their key. A repository's id is
  its folder's name in plain letters, digits, `.`, `_` and `-` (`my tool` is `my-tool`; a second
  `app` is `app-2`); the digest's header lists each id with its folder's name where they differ.
  Key-shaped words in your subjects (`UTF-8`) count among the ids too: they are in the text.
- **Exit codes:** 0 written; 1 git missing or failing; 2 a usage or configuration error (a
  missing email or folder, a bad period, a `--max-chars` too small, an `--out` refused); 3 no
  repository found.

## Related

- [AI assist (Tier 2) — `sijl ask`](ai.md): the same digest, sent by Sijl after your yes, with
  every cited id checked.
- [The report — `sijl report`](report.md) · [Configuration](configuration.md) ·
  [Which repositories Sijl reads — `sijl repos`](repos.md) · [All docs](README.md)
