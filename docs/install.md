# Install, upgrade and uninstall

Sijl is installed with one command, from PyPI, as a small stand-alone program on your computer.
Every release has a version number (such as `1.0.2`), and `sijl --version` always tells you
which one you are running — it is the same number as the release's name on GitHub (`v1.0.2`).

## Why it is installed this way

- **One step to start.** No cloning, no setting up Python by hand: one command, and the
  `sijl` command works in any terminal.
- **You know what you run.** A version number that matches a published release, with a list of
  what changed in each one ([`CHANGELOG.md`](../CHANGELOG.md)), so two people comparing reports
  can tell whether they used the same Sijl.
- **Nothing else is disturbed.** Sijl gets its own set of libraries, separate from any
  other Python tool or project on your machine, and removing it removes all of it.
- **Every release is tried before it is published.** Each one is tested on Linux and macOS and
  installed the same way you install it, then made to produce a small report, before it is
  published.

## Requirements

- **macOS or Linux.** Windows is not tested.
- **git 2.36 or newer** (`git --version`). macOS's own git is new enough; so is any current
  Linux distribution.
- **uv**, the tool that installs Sijl and gives it the Python it needs — it downloads
  Python 3.12 for Sijl if your computer does not have 3.12 or newer:
  - macOS: `brew install uv`, or the official installer below;
  - macOS or Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`.

No account or sign-in is needed: the package on PyPI and the repository are public.

## Install

```bash
uv tool install sijl
```

This installs the newest release.

**One exact version.** Name it. Keep the quotes: a range such as `"sijl>=1.0.2"` needs them,
or the shell reads `>` as a redirect. To move on from a pinned version, install the next one
you choose the same way.

```bash
uv tool install "sijl==1.0.2"
```

**From a git tag.** The same releases can be installed from the repository. Pick the tag on
the [*Releases*](https://github.com/ibnelhithem/sijl/releases) page (or the top of
[`CHANGELOG.md`](../CHANGELOG.md)):

```bash
uv tool install git+https://github.com/ibnelhithem/sijl@v1.0.2
```

The part after `@` is the version to install. If you prefer git over SSH:

```bash
uv tool install git+ssh://git@github.com/ibnelhithem/sijl@v1.0.2
```

If you use an SSH shortcut for a second GitHub account (a `Host github-alt` entry in
`~/.ssh/config`), put the shortcut where `github.com` was:

```bash
uv tool install git+ssh://git@github-alt/ibnelhithem/sijl@v1.0.2
```

**With pip.** `pip install sijl` works too, but only inside a virtual environment.
`uv tool install` gives Sijl its own libraries, apart from your other Python tools.
`pipx install sijl` also gives Sijl its own libraries; upgrade that one with
`pipx upgrade sijl`.

The `sijl` package is the `sijl` command. The `sijl.*` modules are internal and can change in
any release.

## Check it works

```bash
sijl --version          # sijl 1.0.2 — the version you are running
uv tool list            # sijl v1.0.2, and the command it installed
```

**`sijl: command not found`?** The folder uv puts commands in is not on your `PATH` yet. Run
`uv tool update-shell`, then open a new terminal, and try again.

Then set up your configuration file and run your first report:
[Configuration](configuration.md) · [The report](report.md) · the
[project README](../README.md) has a five-minute first run.

## Upgrade to a newer version

```bash
uv tool upgrade sijl
```

uv replaces the old version with the newest release; your configuration file is not touched.
Read the new version's section in [`CHANGELOG.md`](../CHANGELOG.md) (or its release page) to
see what changed.

If you pinned a version (`"sijl==1.0.2"`) or installed from a git tag, `uv tool upgrade sijl`
answers "Nothing to upgrade". That install stays where you put it. Install the version you
want next with the same command you used before.

## Go back to an older version

For a version on PyPI, install that version:

```bash
uv tool install "sijl==1.0.1"
```

`1.0.0` was published on GitHub before Sijl was on PyPI, so that release is the git tag:

```bash
uv tool install git+https://github.com/ibnelhithem/sijl@v1.0.0
```

Every git tag stays installable. PyPI keeps each version it has published.

## Uninstall

```bash
uv tool uninstall sijl
```

This removes the program and its own libraries. Your settings in
`~/.config/sijl/config.toml` and any reports you saved are yours: delete them yourself if
you want them gone.

## Install from a downloaded file

Each release page on GitHub also carries two files: a **wheel**
(`sijl-1.0.2-py3-none-any.whl`) and the source archive (`sijl-1.0.2.tar.gz`). These are
the exact files that were published to PyPI. Download one (in a browser, or with
`gh release download v1.0.2 --repo ibnelhithem/sijl`) and install it directly:

```bash
uv tool install ./sijl-1.0.2-py3-none-any.whl
# or
uv tool install ./sijl-1.0.2.tar.gz
```

This needs no git access to GitHub at install time; uv still downloads Sijl's libraries from
PyPI.

## Where things go

Nothing to configure for the install itself. uv decides where things go:

| What | Where (macOS and Linux) | Ask uv |
|---|---|---|
| The `sijl` command | `~/.local/bin/sijl` | `uv tool dir --bin` |
| Sijl's own libraries | `~/.local/share/uv/tools/sijl/` | `uv tool dir` |
| Your Sijl settings | `~/.config/sijl/config.toml` (not created by the install) | `sijl config show` |

## Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| `sijl: command not found` | uv's command folder is not on your `PATH` | `uv tool update-shell`, then open a new terminal |
| `uv: command not found` | uv is not installed, or not on your `PATH` | install uv (see *Requirements*), then open a new terminal |
| "Repository not found" with the `git+ssh://` form | git signed in over SSH with a key that GitHub does not know | use the `https://` form, or your SSH shortcut for the right account |
| "needs git 2.36 or newer; found …" when Sijl runs (exit code 1) | your git is older than 2.36 | update git (Homebrew, or your Linux distribution's packages) |
| `uv tool upgrade sijl` says "Nothing to upgrade" | the install is pinned, or it came from a git tag | install the newer release (`uv tool install sijl`), or name the version |
| A tag that does not exist (`@v9.9.9`) | uv cannot find that version | check the *Releases* page for the versions that exist |
| No matching distribution for `sijl==1.0.0` | `1.0.0` is not on PyPI | install the git tag `v1.0.0`, or pick a version PyPI has (`1.0.1` and later) |

## Behaviour & limits

- **The newest release, until you pin one.** `uv tool install sijl` follows PyPI. Nothing
  updates by itself, and nothing checks for updates in the background: you run
  `uv tool upgrade sijl` when you want the newer release. A pin (`"sijl==1.0.2"`) or a git
  tag stays on that version until you install another.
- **Libraries are chosen when you install.** Sijl's own code is exactly the release's, but
  the libraries it uses (Typer, Rich, Jinja2) are the newest versions its rules allow on the day
  you install — the same way the release was smoke-tested. Two people installing the same
  version months apart can get newer library versions; the release process tests against
  what was current on release day.
- **On PyPI, as a command.** `uv tool install sijl` installs it. `pip install sijl` belongs in
  a virtual environment. The `sijl` package is the `sijl` command. The `sijl.*` modules are
  internal and can change in any release.
- **Systems.** Tested on Linux and macOS. Windows is not tested.
- **Releases are plain version numbers** (`1.0.0`, `1.1.0`, `2.0.0`): no beta or "rc" releases.
- **Privacy.** Installing from PyPI downloads Sijl and its libraries from PyPI. Installing from
  a git tag downloads Sijl from GitHub and its libraries from PyPI. Sijl itself never contacts
  the network unless you ask it to (`sijl ask`, `sijl ai check`).

## Licence

Sijl is released under the [MIT licence](../LICENSE): you may use it, fork it, change it and
share it, for any purpose, as long as the copyright notice and the licence text stay with the
code. The installed package carries the licence too (`License-Expression: MIT`), and names its
author by GitHub identity only.

## See also

- [Configuration](configuration.md) · [Which repositories Sijl reads](repos.md) ·
  [The report](report.md) · [Asking Claude Code with the digest](digest.md) ·
  [Asking Claude from Sijl](ai.md)
- What changed in each version: [`CHANGELOG.md`](../CHANGELOG.md)
- [All user docs](README.md) · [Project README](../README.md)
