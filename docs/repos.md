# Which repositories Sijl reads — `sijl repos`

A command that shows exactly which repositories Sijl finds in your folders — each one once,
with its extra working copies — and every folder it did not look at, with the reason. It is the
same search `sijl report` does, without reading any commits, so you can check it before you
trust a report.

## Who it's for

- Anyone about to run a review report who wants to be sure **all** their projects are in it.
- Anyone who keeps many working copies of a project (git *worktrees*, for example those Claude
  Code creates for its agents) and wonders whether they are counted several times — they are
  not, and this command shows them grouped under their project.
- Tools and scripts: `--json` gives the same list in a machine-readable form that is identical
  from one run to the next.

## Why it matters

- **Trust.** A report is only as complete as the list of repositories behind it. This command
  makes that list visible: what was found, and what was skipped and why.
- **Nothing lost silently.** Every folder that could have held a project but was not read is
  named with a reason you can act on — a broken copy, a folder too deep, a link Sijl does not
  follow, a repository owned by another user.
- **Safe.** It only reads. It changes nothing in your repositories (a test compares every
  file's modification time before and after), and it contacts nothing over the network.

## How to use it

```bash
sijl repos --root ~/code/work --root ~/code/personal   # readable list
sijl repos --root ~/code/work --json                   # the same, as JSON
sijl repos                                             # folders from your configuration
```

- `--root` — a folder to search (repeatable); `--repo` — one repository to include directly
  (repeatable). As with `sijl report`, giving either on the command line replaces the folders
  and repositories in your configuration file.
- `--json` — print JSON instead of text: an object with `repositories`, `skipped` (not scanned)
  and `pruned` (not searched, by rule), in a fixed order, with exact folder names.
- `--config` — read settings from another file.

The readable list has three parts, always in this order and always present (an empty part says
"none"). For example, for two folders, `~/code/work` and `~/code/personal`:

```text
Repositories (3)
- api — /Users/you/code/work/api
- notes — /Users/you/code/personal/notes
  1 linked worktree:
  - /Users/you/code/personal/notes-draft — branch draft
- web — /Users/you/code/work/web

Not scanned (2)
- /Users/you/code/personal/scratch — no git repository inside
- /Users/you/code/personal/site — no git repository inside

Not searched, by rule (1)
- /Users/you/code/personal/site/node_modules — dependency or build folder — not searched
```

1. **Repositories** — each project once, by name and folder; *bare* for a repository without a
   working copy (a mirror), *submodule of …* for a project inside another one. Under it, its
   **linked worktrees** — the extra working copies — each with the branch it is on (or the commit
   it is parked at, *detached*), marked *locked* when git was told to keep it, and *its folder is
   gone* when the folder was deleted but git still remembers it.
2. **Not scanned** — every place that could have held a repository but was not read, with the
   reason.
3. **Not searched, by rule** — folders Sijl deliberately does not search into (see below).

The command exits with 0 when it found at least one repository, 3 when it found none (the list
is still printed — it says why), 2 when no folder was given, and 1 when git is missing or too old.

## Configuration

`sijl repos` uses the same settings as `sijl report` — see
[Configuration](configuration.md): `scan.roots`, `scan.repos` and `scan.max_depth` (how many
folder levels below each root are searched, 4 by default). It needs no email addresses and no
period.

## Behaviour & limits

**Each project once.** A project is recognised by git's own internal folder, which all its
working copies share. So a project with 20 worktrees is one entry with 20 worktrees listed —
wherever those worktrees live, even outside the folders you named. Two separate clones of a
project are two entries (the report counts their shared commits once). When several folders lead
to one project, the entry always shows the same one — the project itself (a mirror, or the main
checkout) rather than one of its worktrees — so the list does not change with the order in which
you name your folders.

**What is searched.** Each folder you name, and the folders inside it down to 4 levels. Inside a
project, nothing more is searched — its sub-folders are its content. Two kinds of folders are
**not searched into, by rule**, and are listed as such:

- **hidden folders** (names starting with a dot: `.venv`, `.cache`, `.claude` — where Claude Code
  keeps its worktrees and settings);
- **dependency and build folders**: `node_modules`, `venv`, `__pycache__`, `site-packages`,
  `dist`.

The rule only stops Sijl going *into* such a folder: a folder that is itself a repository is
always found, even if it is called `dist` or `.dotfiles`.

**Submodules.** A project that includes another project as a *submodule* (a library kept inside
it) has that submodule found and listed as its own repository — your commits in it count. A
submodule that is not checked out on this machine is named under *Not scanned*. Sijl checks
that a submodule really belongs to its project: one whose history is kept somewhere else — for
example a different project on your disk — is not read through it, and never relabelled.

**Shortcuts (symbolic links) are never followed** — a shortcut can point back to a folder that
contains it, or anywhere on the disk. Each one is named: one to a folder says to name its target
with `--root` or `--repo`; a broken one, or one that points at itself, says it leads nowhere. A
folder you name with `--root` is followed even if it is a shortcut.

**Reasons you may see under *Not scanned*:**

| Reason | What it means | What you can do |
|---|---|---|
| no git repository inside | a folder with no project in it — often a copy without its `.git` | nothing, or delete the copy |
| not searched deeper than 4 levels | projects may sit deeper | name one with `--repo`, or raise `scan.max_depth` |
| symbolic link — not followed | a shortcut to a folder | name its target with `--root` or `--repo` |
| symbolic link that leads nowhere | a broken shortcut, or one that loops | remove it |
| cannot be read | no permission to look inside | fix the permissions, or ignore |
| owned by another user — git does not trust it | git refuses projects owned by another account, because their settings could run programs as you | Sijl never overrides this; if the project really is yours, fix its ownership |
| a bare repository your git configuration only opens when named explicitly | your own git setting `safe.bareRepository` refuses it | Sijl does not override your setting |
| corrupt / corrupt or incomplete | its `.git` is damaged or an interrupted clone | re-clone it |
| broken link: its .git file points to … | a worktree whose project was moved or deleted | remove the folder, or move the project back |
| cannot be opened: … | anything else git refused, in git's own words | read git's words |
| submodule not checked out | a submodule with no copy on this machine | nothing to read |
| opens the repository around it | the folder's own `.git` is broken, so git would have read the project it sits in | re-clone it |
| its work tree is elsewhere | the folder's `.git` points to a history whose files are checked out in another folder | name that other folder with `--repo` if it is yours |
| submodule whose git dir is not its superproject's | a submodule whose history lives in another project | check the project — this is unusual |
| submodule path names a .git folder | the project's list of submodules points into git's own data | check the project — this is unusual |
| a git directory whose work tree is elsewhere | git's data for a project whose files are in a folder Sijl did not reach | name that folder with `--repo` |
| its HEAD is not a regular file | something that is not a file sits where git keeps its data; git would wait forever | look at the folder — this is unusual |
| git gave no answer (timed out after 10 s) | git did not answer within 10 seconds — a slow or hung disk, or a trap | try again, or name the project with `--repo` |

**In the report.** `sijl report` runs exactly this search: its *Caveats* list every path
under *Not scanned* and name the folders *not searched, by rule* in one line (the first 10, and
how many more).

**Safe output.** Folder names, commit subjects and git's messages can contain invisible
control characters that would change your terminal (clear it, rename its window) or reverse how
text reads. Sijl shows each such character as a visible code instead (for example `\x1b`),
in both commands; the JSON keeps the exact name. A folder name that is not valid UTF-8 text
(possible on Linux) is still found and read; the bytes that are not text show as codes such as
`\xff`.

**Limits.**

- A project inside another project's folder that is *not* registered as a submodule is not
  found — name it with `--repo`.
- Sijl opens each repository but does not check every stored object; a damaged history is
  reported later, by `sijl report` ("history could not be read").
- It is quick: on a typical laptop, two folders holding a dozen or so repositories and a few
  dozen worktrees are listed in well under a second.

## See also

- [Configuration](configuration.md) — `scan.roots`, `scan.repos`, `scan.max_depth`
- [The report — `sijl report`](report.md)
- [Install, upgrade and uninstall](install.md)
- [All user docs](README.md) · [Project README](../README.md)
