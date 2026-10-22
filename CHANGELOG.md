# Changelog

Every notable change to Sijl, newest first. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and Sijl uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html): each release is the git tag
`v<version>`, and `sijl --version` prints that same version.

New entries go under **[Unreleased]**; a release moves them under the new version's heading.

## [Unreleased]

## [1.0.1] - 2026-10-22

### Added

- A version tag publishes the tested wheel and sdist to PyPI. The GitHub release is created only after PyPI is serving those same files.

## [1.0.0] - 2026-10-10

The first public release of Sijl (Arabic سِجِلّ, "a record, a register"): your work, on the
record.

### Added

- **`sijl report`:** a Markdown summary of the commits *you* authored in a period, across every
  repository under the folders you name (`--root`) or listed directly (`--repo`), printed in the
  terminal or written to a file with `--out`. Commits are matched by your exact email addresses
  (`--author-email`), never by name. The report is laid out for a review: *Summary*,
  *Highlights* (your busiest month, your largest merge requests and pull requests and your most
  active tickets, picked by a fixed, stated rule), *Per repository*, *Tickets*, *Per month*,
  *Kinds of work*, *Delivered*, *Not pushed yet*, *Caveats* and an *Appendix* listing every
  counted commit.
- **Periods by name:** `--last 30d|6w|6m|1y`, `--quarter 2026Q3`, `--half 2026H1` and
  `--year 2026`, beside `--since` / `--until`. Days are counted by author date, in each commit's
  own timezone, both ends included; a period that is still running is labelled "to date".
- **A configuration file**, `~/.config/sijl/config.toml` (or `$XDG_CONFIG_HOME/sijl/config.toml`),
  for your name, emails, folders, period, Jira projects and company GitHub/GitLab hosts, so
  `sijl report` runs with no flags. Environment variables (`SIJL_*`) and flags override it.
  `sijl config show` lists every setting, its value and where that value came from. A mistyped
  key gets a "did you mean"; anything that looks like a password or token is refused.
- **Work counted wherever it lives:** local branches, remote branches, tags and the main
  checkout — never stash entries, notes or scratch worktrees (`--include-worktrees` adds
  detached worktrees). Commits you have not pushed are listed in their own *Not pushed yet*
  section and kept out of the totals.
- **Copies counted once:** a commit you rebased, cherry-picked or amended after pushing is one
  piece of work, shown under the copy a reviewer can open, with the other copies' SHAs beside it.
  `--dedupe patch-id` also requires the copies to make the same change.
- **`sijl repos` (and `--json`):** every repository Sijl reads — each once, with its linked
  worktrees — and every folder it skipped, with the reason.
- **Evidence links:** each commit SHA links to its project on GitHub or GitLab, including your
  company's own GitHub Enterprise or GitLab hosts and ssh shortcuts from `~/.ssh/config` (such
  as `github-alt`). Repositories on hosts Sijl does not know keep plain SHAs.
- **Merge requests and pull requests delivered:** the *Delivered* section lists the GitLab merge
  requests and GitHub pull requests of yours merged in the period — number, title, date and a
  link, whether you or a colleague pressed *Merge* — and the ones you merged for colleagues,
  read from the forges' merge commits on each project's main line. Back-merges and branch
  updates are never listed, merges never count as commits, and *Caveats* explains every merge
  commit you made and what git alone cannot show (fast-forward and rebase merges).
- **GitHub squash merges counted once:** each pull request of yours merged with "Squash and
  merge" is listed under *Delivered*, and its work counts exactly once — as its branch commits
  when they are still around (even in another copy of the project, or written up to 90 days
  before the period, so reports for adjacent periods add up), or as the squash itself with its
  lines. A commit title merely ending in "(#12)", or a commit GitHub rebased, is not a squash.
- **Kinds of work, months and your longest streak:** your commits divided by the kind their
  messages declare (`feat:`, `fix:`, … — Conventional Commits, read forgivingly) and by review
  heading (features, fixes, quality, documentation, tooling); every month of the period with its
  commits and active days; your longest run of weekdays with commits (weekends neither break nor
  extend it). Tests count as features in a test-suite repository: one whose name ends in `-tests`
  or `-test`, or one you list in `[categories] test_suites` (`SIJL_TEST_SUITES`).
- **Work grouped by Jira ticket:** name your Jira projects once — `[jira] projects = ["PROJ"]`,
  `SIJL_JIRA_PROJECTS` or `--jira-project PROJ` — and the *Tickets* section lists every ticket
  your commits and your merge requests or pull requests name (in a scope, a title, a branch
  name, a `Closes` line or a trailer), with how many commits and on which days. Without projects
  it suggests the prefixes that look like your projects, with the evidence. Standards and
  look-alikes (`UTF-8`, `SHA-256`, `ISO-8601`, `TC-UI-07`, versions) are never tickets.
- **Your own layout with `--template FILE`:** a Jinja2 template of yours gets every number and
  piece of evidence the built-in report has, and can include its sections. Text from your
  repositories is always printed safely, a template can read no other file, and a mistake stops
  the command with the file, the line and a suggestion. Sijl always adds its Caveats and footer
  after your template, and a template may write no link, picture, address or HTML of its own
  unless you pass `--allow-template-links`.
- **`sijl digest`: ask Claude Code, or any AI chat, about your work (Tier 1).** One compact text
  file holds the evidence of your period — the exact totals, then your commits grouped by
  ticket, by merge request or pull request, and by repository, each line starting with an id to
  quote (a short SHA, `webapp!42`, `app#12`, a ticket key). `--with-prompt` adds instructions
  (answer only from the file, cite the ids, say "not in the data" otherwise). Colleagues' names
  and email addresses are replaced by `[person N]` / `[email N]` unless you pass `--no-redact`.
  `--max-chars N` keeps it within N characters and says what it left out wherever it cut.
  Nothing is sent anywhere, and the same input always gives the same file.
- **`sijl ask "…"`: ask Claude about your period (Tier 2)**, with your own Anthropic API key or
  `ant auth login`. Nothing is sent before Sijl has shown you exactly what will be sent, with a
  cost estimate, and you said yes. The answer comes back as an AI-written block; after it, Sijl
  lists every id the answer cited that is exactly one of your digest's ids, each with your
  digest's line for it. An id not in your evidence — invented, mis-copied, written with
  look-alike or invisible characters — is marked where it stands, and the command exits 5. `-i`
  (or no question) asks follow-ups with one yes, re-reading the evidence from Claude's cache.
  When Claude cannot be used, `ask` prints the Tier 1 route with your question and your period
  and scope flags filled in.
- **`sijl ai status` and `sijl ai check`:** whether AI is on and why, and a one-line test that
  your credentials work (after showing what it sends, and asking).
- **`--out` never writes into a repository Sijl read** unless you give the file's full path
  (no symbolic link on the way), never into git's own files, and never through a symbolic or
  hard link.
- **Read-only and offline by design:** Sijl never writes into the repositories it reads, never
  runs commands a repository's own settings name, and makes no network calls except `sijl ask`
  and `sijl ai check` after your yes. Folder names, commit subjects and remote addresses are
  shown safely: control and invisible characters appear as visible escapes, passwords or tokens
  inside remote addresses are never printed, and no commit subject can turn into a link.
- **`sijl report --help`** says what a report needs (your emails, a folder, a period — unless
  the config file sets them) and shows an example.

[Unreleased]: https://github.com/ibnelhithem/sijl/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/ibnelhithem/sijl/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/ibnelhithem/sijl/releases/tag/v1.0.0
