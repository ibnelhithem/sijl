# Configuration

A small file where you write down, once, the things Sijl needs every time — your email
addresses, the folders where your repositories live, the period, your Jira projects — so that
`sijl report` on its own produces your report. `sijl config show` lists every setting, its
value, and where that value came from, then the period those settings choose today.

## Why use one

- **Shorter, repeatable runs.** `sijl report` instead of a long command line, every time you
  need it: during a review season, while drafting answers, or every month to keep a running
  record.
- **No surprises.** `sijl config show` explains why a run used a value (the file, an environment
  variable, a flag, or the built-in default).
- **Safe.** Typos are caught with a suggestion ("did you mean `scan.roots`?"), and the file never
  holds secrets — Sijl refuses anything that looks like a token or password there.

## Where the file lives

Sijl reads one TOML file:

- `~/.config/sijl/config.toml` — the usual place;
- `$XDG_CONFIG_HOME/sijl/config.toml` when `XDG_CONFIG_HOME` is set;
- any other file you name with `--config path/to/file.toml` or the `SIJL_CONFIG` environment
  variable. A file named that way must exist.

`sijl config show` prints the file it reads on its first line, and whether it was found:

```text
Config file: /Users/you/.config/sijl/config.toml (found)
```

No file is needed: without one, Sijl uses its defaults, your environment variables and your
flags.

## A complete example

```toml
[person]
name = "Your Name"                 # optional: the report's title; default is the name in your commits

[identity]
emails = ["you@example.com", "12345+you@users.noreply.github.com"]

[scan]
roots = ["~/code/work", "~/code/personal"]  # folders searched for repositories
repos = []                         # repositories to include directly
max_depth = 4                      # folder levels searched below each root
include_worktrees = false          # true: also read detached scratch worktrees
dedupe = "key"                     # "patch-id": copies must also make the same change

[jira]
projects = ["PROJ", "OPS"]         # your Jira projects: their keys are grouped as tickets

[window]
last = "6m"                        # the last 6 months, up to and including today

[categories]
test_suites = []                   # repositories whose tests are the deliverable, e.g. ["e2e-suite"]

[forges]
github_hosts = []                  # your company's GitHub Enterprise site, e.g. "github.example.com"
gitlab_hosts = []                  # your company's own GitLab site, e.g. "gitlab.example.com"
ssh_aliases = true                 # false: never ask ssh what a shortcut name like github-alt means
```

Only `identity.emails`, a folder (`scan.roots`) or a repository (`scan.repos`), and a period are
needed for a report; everything else has a default. To find the address your commits use, run
`git log -5 --format=%ae` inside one of your repositories — Sijl matches addresses exactly, so
write every one you have committed with.

The period can be written in any one of these ways — pick exactly one:

```toml
[window]
last = "6m"          # the last 6 months up to today; also "30d", "6w", "1y"
# quarter = "2026Q3" # a calendar quarter (one still running stops at today)
# half = "2026H1"    # January-June; "2026H2" is July-December
# year = 2026        # a calendar year, written as a plain number
# since = "2026-04-06"  # your own first day ...
# until = ""            # ... and last day; empty means today
```

Then:

```bash
sijl report --out ~/review.md   # everything from the file
sijl report --quarter 2026Q3    # the same, but for Q3: the flag replaces the file's period
sijl config show                # every setting, its value and its source, and the period
```

## Every setting

Each setting can come from four places; a later one wins:

| Setting | In the file | Environment variable | Flag |
|---|---|---|---|
| Your name | `person.name` | `SIJL_NAME` | — |
| Your emails | `identity.emails` | `SIJL_EMAILS` (comma-separated) | `--author-email` (repeatable) |
| Folders to search | `scan.roots` | `SIJL_ROOTS` (comma-separated) | `--root` (repeatable) |
| Repositories | `scan.repos` | `SIJL_REPOS` (comma-separated) | `--repo` (repeatable) |
| Search depth | `scan.max_depth` | `SIJL_MAX_DEPTH` | — |
| Read detached worktrees | `scan.include_worktrees` | `SIJL_INCLUDE_WORKTREES` (`true`/`false`, `yes`/`no`, `1`/`0`) | `--include-worktrees` / `--no-include-worktrees` |
| How copies are matched | `scan.dedupe` | `SIJL_DEDUPE` (`key` or `patch-id`) | `--dedupe` |
| Your Jira projects | `jira.projects` | `SIJL_JIRA_PROJECTS` (comma-separated) | `--jira-project` (repeatable) |
| First day | `window.since` | `SIJL_SINCE` | `--since` |
| Last day | `window.until` | `SIJL_UNTIL` | `--until` |
| The last N days, weeks, months or years | `window.last` (`"6m"`) | `SIJL_LAST` | `--last` |
| A calendar quarter | `window.quarter` (`"2026Q3"`) | `SIJL_QUARTER` | `--quarter` |
| A half year | `window.half` (`"2026H1"`) | `SIJL_HALF` | `--half` |
| A calendar year | `window.year` (`2026`, a number) | `SIJL_YEAR` | `--year` |
| Test-suite repositories | `categories.test_suites` | `SIJL_TEST_SUITES` (comma-separated) | — |
| GitHub Enterprise sites | `forges.github_hosts` | `SIJL_GITHUB_HOSTS` (comma-separated) | — |
| Your own GitLab sites | `forges.gitlab_hosts` | `SIJL_GITLAB_HOSTS` (comma-separated) | — |
| Look up ssh shortcut names | `forges.ssh_aliases` | `SIJL_SSH_ALIASES` (`true`/`false`, …) | — |
| AI features on | `ai.enabled` | `SIJL_AI` (`off` turns AI off) | `--no-ai` (AI commands) |
| Ask before sending to Claude | `ai.confirm` | `SIJL_AI_CONFIRM` | `--yes` (one command, no question) |
| Claude model | `ai.model` | `SIJL_AI_MODEL` | — |
| Replace other people's names and emails | `ai.redact` | `SIJL_AI_REDACT` | — |
| Longest answer `sijl ask` may get, in tokens (1,024–128,000; default 16,000) | `ai.answer_limit` | `SIJL_AI_ANSWER_LIMIT` | `--answer-limit` (`ask`) |

Order: **built-in default < config file < environment variable < flag.** A `--root` or `--repo`
on the command line replaces *both* configured lists, so the flags say exactly where to look.
A list flag (`--author-email`, `--jira-project`) replaces the file's list for that run; it is
never added to it.

### The period is one choice

The six `window` settings are six ways of saying the same thing, so they follow two rules of
their own (what each way means is explained in
[the report guide](report.md#choosing-the-period)):

- **One way per place.** The file, your environment variables and the command line may each
  give the period only one way. `--since` with `--until` counts as one way. Two ways in one place
  stop the run with a message naming both — "--last and --quarter both set the period — choose
  one", or "…/config.toml: window.since and window.last both set the period — choose one" for the
  file — even if the command line would have replaced them.
- **The command line replaces the whole period.** A period given on the command line replaces
  the period from the environment and from the file, whichever way those gave it: with
  `last = "6m"` in the file, `sijl report --since 2026-01-01 --until 2026-03-31` reports on
  the first quarter. Likewise an environment variable replaces the file's period, and what it
  replaced stays replaced: with `since` and `until` in the file and `SIJL_LAST` set, a
  `--until` on the command line has no first day to go with, so Sijl asks for one. The one
  exception: when nothing in between replaced them, `since` and `until` still combine across
  places, so `since` in the file plus `--until` on the command line is one period.

`sijl config show` lists every `window` setting with where it came from, marks a value that
was replaced — for example `window.since  (not set)  file, overridden by env SIJL_HALF` — and
ends with the period your settings choose today:

```text
Period: 1 July 2026 to 8 October 2026 — H2 2026 to date (inclusive; by author date, in each commit's own timezone)
```

If no period is set it says "Period: not set" and lists the ways to give one; if the period
cannot be used today (a quarter that has not started yet) it says why. What `config show` says
is exactly the period a report would use. An empty value such as `since = ""` sets nothing, so
it is shown as not set, from the file — never as "overridden".

### How copies are matched (`scan.dedupe`)

A commit you rebased, cherry-picked or amended exists twice in git, under two ids; Sijl counts
it once. This setting decides how strict that recognition is:

- `key` — the default. Two of your commits in the same project are one piece of work when they
  have the same author email, the same moment of writing (to the second) and the same title.
- `patch-id` — the same, and the change itself (the lines added and removed) must also be the
  same. Use it if you often make commits with the same title in the same second, for example
  from a script; it is a little slower, and a rebase where you resolved a conflict is then
  counted twice.

Whichever is used, the report's *Caveats* name the rule and how many copies were found — see
[the report guide](report.md#rebased-and-cherry-picked-copies) for what a copy is and how it is
shown.

### Your Jira projects (`jira.projects`)

The project keys of the Jira projects you work in — the letters before the hyphen of a ticket,
`PROJ` in `PROJ-1234`. Only their tickets are grouped in the report's *Tickets* section; ids of
anything else that looks like a ticket (test-case ids such as `TC-13`, requirement ids) are not
counted as tickets, and *Caveats* say how many there were.

- **Not sure which to write?** Run the report once without it: *Tickets* then lists the
  prefixes that look like Jira projects in your commits, with how often each appears where
  people write tickets and an example id. Copy the ones that are your Jira projects.
- Write them as Jira does — capital letters, digits or `_`, starting with a letter, at least two
  characters: `PROJ`, `OPS`, `PROJ_SUB`. `proj` (lower case), `PROJ-1` (that is a ticket, not
  a project) or `B` stop the run with a message that suggests the right spelling ("like PROJ").
- Standards and other well-known names that are written like tickets — `UTF` (UTF-8), `SHA`
  (SHA-256), `ISO`, `RFC`, `HTTP`, `CVE` and a few dozen more — are refused: Sijl never
  counts their ids as tickets, so naming them could only hide a mistake.
- On the command line, `--jira-project PROJ --jira-project OPS` **replaces** the list in the
  file for that run, like every other list flag.
- Empty (the default) means no tickets are counted; the report suggests projects instead.

### Test-suite repositories (`categories.test_suites`)

The report sorts your commits under review headings — features, fixes, quality, documentation,
tooling (see [the report guide](report.md#kinds-of-work)). A `test:` commit is normally
*quality* work: better code, same behaviour. In a repository whose whole purpose is tests — an
end-to-end suite, a QA monorepo — a new test *is* the deliverable, so there `test:` commits count
under *features*.

- A repository whose folder name ends in `-tests` or `-test` (`api-tests`, `web-test`) is always
  treated as a test suite.
- `test_suites` names any others, by folder name, exactly as `sijl repos` shows them:

  ```toml
  [categories]
  test_suites = ["e2e-suite"]
  ```

- The default is an empty list: only the name rule above applies.
- From the environment: `SIJL_TEST_SUITES=e2e-suite,smoke-checks` (comma-separated, like every list
  variable). There is no flag.
- `sijl config show` lists it as `categories.test_suites`, with where its value came from.

Only the review headings change: a commit's kind (`test`) and every other number stay the same.

## Links to GitHub and GitLab

The report links every counted commit to its page on GitHub or GitLab (see
[the report guide](report.md#links-to-the-evidence)). github.com and gitlab.com are always
recognised. Two settings cover the rest:

- **`forges.github_hosts` / `forges.gitlab_hosts`** — if your company runs its own GitHub
  Enterprise or GitLab, write the site's name, exactly as it appears in your remotes' addresses
  (`git@gitlab.example.com:team/project.git` → `gitlab.example.com`). Write the name alone — no
  `https://`, no user name, no port, no path. Without it, commits there are plain ids and
  *Caveats* name the site and these settings. Sijl does not guess from a name: a site called
  `gitlab.something` might not be GitLab, and a wrong link is worse than none.
- **`forges.ssh_aliases`** (on by default) — many people give GitHub or GitLab a shortcut name in
  their ssh settings (`~/.ssh/config`), for example `github-alt` for a second GitHub
  account. Sijl asks the ssh program installed on your machine what such a name stands for;
  ssh answers from your settings and **does not connect anywhere**. Turn it off with
  `ssh_aliases = false` if you would rather Sijl never ran ssh: commits behind a shortcut name
  are then plain ids, and *Caveats* say why. (One thing to know: if your ssh settings contain
  "Match exec" rules — commands ssh runs to decide which settings apply — ssh runs them while
  answering, just as it does every time you use git with that site.) Sijl asks about at most
  8 names, for at most 10 seconds in all, per report, and stops anything ssh started if it takes
  too long; names beyond that are not looked up, and *Caveats* say so.

## AI settings

These matter only for asking Claude from inside Sijl (`sijl ask` — see
[Asking Claude from Sijl](ai.md)). The defaults are the safe ones; you need none of them to use
Sijl.

```toml
[ai]
enabled = true                 # false turns every AI feature off
confirm = true                 # show what will be sent and ask before sending
model = "claude-opus-5-5"      # the Claude model AI commands use
redact = true                  # replace other people's names and email addresses before sending
answer_limit = 16000           # the longest answer `sijl ask` may get, in tokens
```

- **`enabled`** — `true` by default: AI is on as soon as Claude credentials are found, and off
  without them. `false` (or `SIJL_AI=off` in your shell, or `--no-ai` on an AI command) turns
  it off even when credentials exist; `sijl ai status` then says where it was turned off
  ("turned off by SIJL_AI=off").
- **`confirm`** — `true` by default: before anything is sent, Sijl shows all of it with a cost
  estimate and asks `Send this to Claude? [y/N]`, once per command. `false` (or
  `SIJL_AI_CONFIRM=false` in your shell) sends without asking; `--yes` does the same for one
  command. The preview is printed either way.
- **`model`** — the Claude model to use, `claude-opus-5-5` by default. It cannot be empty; a
  name Claude does not know is reported when you use it ("check ai.model"). Cost estimates are
  shown for the models Sijl has prices for.
- **`redact`** — `true` by default: every email address that is not one of your `identity.emails`,
  and every other person's name Sijl knows from your history, is replaced by a label such as
  `[person 1]` before sending. `false` sends them as they are; the preview says so.
- **`answer_limit`** — 16,000 by default, between 1,024 and 128,000: the longest answer
  `sijl ask` may get from Claude, in tokens, its thinking included. The preview's cost
  ceiling is priced at it, so a lower limit means a lower "at most"; an answer that needs more is
  cut off — billed, and not shown — and Sijl says to raise it. `--answer-limit` sets it for
  one question; `SIJL_AI_ANSWER_LIMIT` for a shell. (It is not called "max tokens" because
  Sijl refuses any setting whose name contains "token": those look like secrets.)
- **Your Claude key never goes in this file.** Sijl refuses `api_key` (or anything that looks
  like a key or token) here. Claude credentials come from the `ANTHROPIC_API_KEY` or
  `ANTHROPIC_AUTH_TOKEN` environment variables, or from logging in with `ant auth login`.

## Behaviour & limits

- An unknown key, a wrong type (`emails = "x"` instead of a list), or a day not written as
  `YYYY-MM-DD` stops the run with a message naming the key and where the value came from. The
  same goes for a period in the wrong shape (`last = "6 months"`, `quarter = "Q3"`), for
  `year = "2026"` in quotes — the year is a plain number — and for any year before 1970, which
  git cannot read reliably ("years 1970 to 9999").
- Numbers in environment variables (`SIJL_YEAR`, `SIJL_MAX_DEPTH`) must be written with
  the digits 0–9; anything else is refused with the variable's name.
- Keys that look like secrets (`token`, `password`, `api_key`, …) are refused: secrets belong
  in environment variables.
- `scan.dedupe` accepts exactly `key` or `patch-id`, in lower case; anything else (`sha`,
  `Patch-ID`) stops the run with "scan.dedupe must be one of key, patch-id" and where the value
  came from.
- Two ways of giving the period in one place stop the run (exit code 2); a period on the command
  line replaces the configured one, including `--until` on its own — which then has no first day,
  so Sijl asks for one. Give `--since` too, or a ready-made period.
- "Today" — the end of "the last …", and where a running quarter, half or year stops — is your
  computer's date when the command runs.
- `forges.github_hosts` and `forges.gitlab_hosts` accept host names only; anything else — a full
  address, a name with a space, one starting with `-` — stops the run with "each entry must be a
  host name alone, like git.example.com". The wrong value is **not** repeated in that message,
  in case it was an address with a password or token in it. A site listed in both stops the run
  too.
- A company GitLab whose ssh address differs from its web address (`gitlab-ssh.example.com` for
  ssh, `gitlab.example.com` in the browser) cannot be linked yet; a future version may.
- `jira.projects` accepts project keys in capitals only, and never a standard such as `UTF`;
  anything else stops the run, naming the value, where it came from and the likely spelling.

## See also

- [Install, upgrade and uninstall](install.md)
- [Which repositories Sijl reads — `sijl repos`](repos.md)
- [The report — `sijl report`](report.md)
- [Asking Claude Code with the digest — `sijl digest`](digest.md)
- [Asking Claude from Sijl — `sijl ask`](ai.md)
- [All user docs](README.md) · [Project README](../README.md)
