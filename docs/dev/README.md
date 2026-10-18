# Sijl developer docs

For people changing Sijl's code. User-facing docs live one level up in [`docs/`](../README.md).

- **[`decisions/`](decisions/)** — architecture decision records (ADRs): one short file per notable
  choice, with its context, the evidence, the alternatives rejected and why, and the consequences.
  Start from the [template](decisions/_TEMPLATE.md). Numbers are four digits, in order.
- **`code/`** — notes per module, added as modules change: what the module does, why it is built
  that way, the gotchas. (None yet; the first change to a module adds its page.)

## How Sijl fits together

A report is one pass through a pipeline of plain functions passing frozen dataclasses
(`models.py`):

```
discover → collect → normalize (collapse copies) → deliveries → squash → aggregate → render
 repos      commits    one commit per change        MRs / PRs    GitHub     Insights   Markdown
```

| Module | What it does |
|---|---|
| `discover.py` | Finds every repository under the folders given, once each; names every folder skipped and why |
| `git.py` | The **only** place that starts processes (git, and `ssh -G`): safe environment, no transport, no lazy fetch |
| `collect.py` | Your commits in the period, by exact email and author date; how far each travelled (pushed, local, …) |
| `normalize.py` | Collapses rebased and cherry-picked copies into one commit |
| `deliveries.py`, `squash.py` | Merge requests and pull requests delivered, including GitHub squash merges |
| `classify.py`, `tickets.py` | Conventional-commit kinds, review categories, Jira keys of your own projects |
| `aggregate.py` | The one `Insights` model every renderer reads — numbers are computed once |
| `report.py`, `render/` | The Markdown report from sandboxed Jinja2 templates; every piece of repository text escaped |
| `remotes.py` | Remote URLs parsed strictly (credentials dropped) into GitHub / GitLab links |
| `digest.py`, `ids.py`, `redact.py` | The compact evidence text for AI, its id grammar, and the redaction of other people |
| `ai.py`, `ask.py`, `citations.py` | The optional Claude tier: preview, consent, the client, and positive citation checks |
| `outfile.py`, `text.py`, `config.py`, `window.py`, `cli.py` | Safe file writing, terminal-safe text, settings, periods, the commands |

## Rules that keep Sijl safe — read before changing how repositories are read

- **Read-only.** Sijl never writes into a repository it reads: no `git status`, `gc` or fetch; no
  network transport at all; partial clones are never asked for file contents.
- **A repository is untrusted input.** Its config can run programs, its remote URLs can hold
  secrets, its names and messages can carry terminal escapes or Markdown. Only `git.py` starts
  processes; ask git for config values instead of parsing them; print through `text.printable()`;
  build Markdown from `report._md()` / `_code()` / `_cell()`.
- **Nothing reaches an AI before the preview and the user's yes**, and only over https.
- **No silent loss.** Every filter, cap or skip is counted and named in the report's *Caveats*.
- **People are matched by exact email, never by name.**

## Checks and CI

`scripts/check.sh` runs the linter, the formatter and the whole test suite.
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs it on Linux and macOS for every
pull request and every push to `main`.
[`.github/workflows/release.yml`](../../.github/workflows/release.yml) tests both systems before
publishing; a pull-request dry run of that workflow stays Linux-only. The choice to run macOS on
every pull request is recorded in
[0002](decisions/0002-macos-ci-on-every-pull-request.md).

## Secrets and private data

Two different things must not become public: a credential, and a word from someone's real work.
A finding in CI is already on the remote. **Revoke the credential first**, then remove it; a
listed word is rewritten out of the text it is in.

| Layer | What it catches | When |
|---|---|---|
| `pre-commit` hook (opt-in) | Credentials in the staged changes (Betterleaks) | Before a commit, on your machine |
| `pre-push` hook (opt-in) | Listed words in each new commit (author, committer, message, the files it adds or changes) and in the pushed ref's name | Before a push, on your machine |
| GitHub push protection | Provider tokens GitHub already knows | Before the push is accepted |
| `secret-scan` job in `ci.yml` | Private-key blocks, passwords in URLs, other generic credentials, in the whole history | Every pull request and every push to `main` |
| `guard.yml` (the word list) | Listed words in the tree, each new commit, the branch name, and the pull request's title and body | Every pull request from this repository, again when its title or body is edited, and every push to `main`; forks and Dependabot skip it |
| The release | Credentials in the unpacked wheel and sdist; listed words in the tree and in both files | Before anything is published |

Only the first three act before anything is public. Neither CI check is required to merge: by
the time it runs the text is public, so read a red one before you merge.

A credential finding names only its file, line, rule and commit (`scripts/secret-scan.tmpl`); the
scan checks a pinned checksum before it runs, redacts what it prints, does not send a found
secret to its provider, and stops after 10 minutes. Deliberate fixtures are allowed one exact
value at a time, each with a reason, in `.betterleaks.toml`. A word-list finding names the place
and the number of the list's entry, never the word (`scripts/publication_guard.py`).

### The hooks

```bash
git config core.hooksPath scripts/hooks                 # turn both hooks on, in this clone
git config publicationGuard.list /path/to/your/list     # pre-push's list, unless PUBLICATION_GUARD_LIST is set
git config --unset core.hooksPath                       # turn them off again
```

`pre-commit` needs `betterleaks` on `PATH` (the version `scripts/secret-scan.sh` pins), and
`pre-push` needs `python3` and the list. Without one of them, the hook warns loudly and lets the
commit or push go. `--no-verify` skips the hooks once. The hooks only read.

The choices for every layer are recorded in [0003](decisions/0003-secrets-and-private-data.md).

## Writing these docs

Use made-up examples only (`alex@example.test`, `acme/app`, `PROJ-123`, `~/code/work`). When a
decision rests on measurements, measure on synthetic repositories (the test suite's
`tests/gitfactory.py` builds them deterministically) and say how — never quote numbers from
anyone's real work. The publication guard (`tests/test_publication_guard.py`) checks every file.
