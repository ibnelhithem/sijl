# Sijl

**Sijl** (سِجِلّ, Arabic for "a record, a register") — *your work, on the record.*

Sijl summarises **your own** git work for a review period — a quarter, a half year, the last six
months — straight from the repositories already on your computer. It reads git history only:
no accounts to connect, no tracker to fill in, nothing uploaded.

## What it does

- **Finds your repositories** under the folders you name, and **your commits** in them — matched
  by your exact email addresses, pushed or not, copies (rebases, cherry-picks) counted once.
- **Writes a review-ready Markdown report**: a summary in sentences, highlights, per repository,
  per month, kinds of work, the merge requests and pull requests you delivered, Jira tickets,
  work not pushed yet, and *Caveats* saying what was left out and why. Every commit is linked to
  GitHub or GitLab.
- **Lets you ask an AI about your period** with evidence: `sijl digest` writes a compact file
  with ids to cite for Claude Code or any AI chat; `sijl ask` asks Claude directly and checks
  every id the answer cites against your history.
- **Stays honest about its numbers**: a fixed, stated rule for everything it picks, and the
  numbers are evidence of work, not a performance score.

A report starts like this (a made-up example):

```markdown
# Work summary — Alex Example

**Period:** 1 April 2026 to 30 June 2026 (inclusive; by author date, in each commit's own timezone)

## Summary

Between 1 April 2026 and 30 June 2026 you authored **13 commits** on **13 active days** across
**3 repositories** — most in app (7), site (4), tools (2). Your longest streak was **3 weekdays**
in a row with commits, from 11 May 2026 to 13 May 2026 (…). 1 more commit is not pushed yet and
not in these numbers (see *Not pushed yet*). 5 merge requests and pull requests of yours were
merged, and you merged 1 for colleagues (see *Delivered*).

## Highlights

- **Busiest month:** April 2026 — 5 commits on 5 active days.
- **Largest merge requests and pull requests** (3 of 5, by commits of yours):
  - #13 Search — 2 commits of yours, in site, merged 2026-04-22
  - !42 Export the report (PROJ-12) — 2 commits of yours, in app, merged 2026-04-08
  …
```

## Requirements

- **macOS or Linux.** Windows is untested.
- **git 2.36 or newer** (`git --version`).
- **[uv](https://docs.astral.sh/uv/)**, which installs Sijl and fetches Python 3.12 for it if
  your computer does not have it: `brew install uv`, or
  `curl -LsSf https://astral.sh/uv/install.sh | sh`.

## Install

```bash
uv tool install git+https://github.com/ibnelhithem/sijl@v1.0.0
sijl --version                    # sijl 1.0.0
```

If your shell says `sijl: command not found`, run `uv tool update-shell` once and open a new
terminal.

- **Upgrade:** run the install command again with the newer version (`…@v1.1.0`).
- **Uninstall:** `uv tool uninstall sijl` (your config file and saved reports stay; delete them
  yourself if you want them gone).

More, including installing from a downloaded wheel: [docs/install.md](docs/install.md).

## First run

1. **See which config file Sijl reads:**

   ```bash
   sijl config show
   ```

   The first line names it: `~/.config/sijl/config.toml`, or `$XDG_CONFIG_HOME/sijl/config.toml`
   when that variable is set. It does not exist yet — create it:

   ```toml
   [identity]
   emails = ["you@example.com"]    # every address you commit with

   [scan]
   roots = ["~/code"]              # folders that hold your repositories

   [window]
   last = "6m"                     # or quarter = "2026Q3", half = "2026H1", year = 2026
   ```

   Not sure which addresses you commit with? Inside one of your repositories,
   `git log -5 --format=%ae` shows them. Use exactly those, and list them all (a work address,
   a personal one, GitHub's `…@users.noreply.github.com`). Every setting:
   [docs/configuration.md](docs/configuration.md).

2. **Check what it will read:**

   ```bash
   sijl repos
   ```

   Every repository it found, once each, and every folder it skipped, with the reason
   ([docs/repos.md](docs/repos.md)).

3. **Write the report:**

   ```bash
   sijl report --out ~/review.md
   ```

   Run it from outside your repositories (your home folder is fine): Sijl refuses to write a
   relative `--out` path into a repository it reads. Without `--out` the report is printed
   instead. Any flag overrides the file for one run, for example `--quarter 2026Q3` or
   `--root ~/code/work`; `sijl report --help` lists them all.

4. **Read it**, starting with *Summary*, *Highlights* and *Caveats* — the Caveats say what was
   not counted and why, before anyone else asks. What every section means:
   [docs/report.md](docs/report.md).

## Asking AI — two tiers

Both tiers ask about the same evidence: your period's commits, merge requests, pull requests
and tickets, each with an id the answer can cite.

**Tier 1 — free, nothing sent by Sijl.** Write the digest and give it to Claude Code (or attach
it in any AI chat you use):

```bash
sijl digest --with-prompt --out digest.txt
claude 'Read digest.txt and answer: what did I deliver for ABC-123?'
```

Colleagues' names and email addresses are replaced by `[person N]` / `[email N]`; your own name
and addresses, commit subjects, merge request and pull request titles, repository names and
ticket keys are kept as written. Check your employer's policy on AI tools before you give the
file to one. More: [docs/digest.md](docs/digest.md).

**Tier 2 — `sijl ask`, billed to you.** With an Anthropic API key (create one at
[console.anthropic.com](https://console.anthropic.com/); usage is billed to your account):

```bash
export ANTHROPIC_API_KEY=…
sijl ai status                            # is AI on, and why
sijl ask "What did I deliver in Q3?"      # one question
sijl ask -i                               # follow-up questions, one yes for all
```

Nothing is sent before Sijl shows you exactly what will be sent, with a cost estimate, and you
say yes. As a rough guide for six months of work: about $0.40–0.80 for the first question and
about $0.02 for each follow-up, which re-reads the evidence from Claude's cache. The answer is
shown as an AI-written block; the **Evidence** section after it is what Sijl verified — each id
the answer cited, with your history's own line for it. Ids that are not in your evidence are
marked. More: [docs/ai.md](docs/ai.md).

## Troubleshooting

| You see | What it means |
|---|---|
| `no git repository found` (exit 3) | Nothing under your roots is a repository Sijl can read. Run `sijl repos` to see what it searched and why each folder was skipped; point `roots` at the folder that *contains* your repositories. |
| The report says "No commits by … were found" | Your emails do not match your commits exactly. Run `git log -5 --format=%ae` in a repository and copy those addresses into `emails` — all of them. |
| `--out … would write into the scanned repository …` (exit 2) | You ran it inside a repository with a relative path. Run it from your home folder, or give the full path the message suggests. |
| `sijl: command not found` | Run `uv tool update-shell` and open a new terminal. |

Exit codes: **0** done · **2** a usage or configuration error (the message names the flag or
the setting) · **3** no repository found · **4** AI is off, failed or gave no complete answer ·
**5** an answer cites an id that is not in your evidence · **1** git is missing or failed.

## Privacy

- Everything runs **on your computer**. Sijl only reads git history: it never writes into your
  repositories and never runs commands a repository's own settings name.
- **No network** — except `sijl ask` and `sijl ai check`, which send to Claude only after showing
  you what will be sent and getting your yes.
- Reports and digests are files you choose to write; they contain your work history, so treat
  them like any other personal document.

## Documentation

[docs/](docs/README.md) — install, configuration, the report, `sijl repos`, `sijl digest`,
`sijl ask`. Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Changes: [CHANGELOG.md](CHANGELOG.md).

## Licence

[MIT](LICENSE) © 2026 ibnelhithem.
