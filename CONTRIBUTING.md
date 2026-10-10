# Contributing to Sijl

Thanks for your interest. Bug reports, ideas and pull requests are all welcome.

## Reporting a problem or an idea

Open an issue with the *Bug report* or *Feature request* form. Please never paste a whole
report, digest or config file: they hold your work history, email addresses and colleagues'
names. A few lines with names and paths replaced are enough.

## Setting up

You need git 2.36 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/ibnelhithem/sijl.git
cd sijl
uv sync                    # creates .venv with Sijl and its development tools
uv run sijl --version
```

## Before you open a pull request

```bash
scripts/check.sh           # ruff (lint and format) and the whole test suite, exactly as CI runs them
```

- **Tests come with the change.** The suite builds real git repositories in temporary folders
  (`tests/gitfactory.py`), blocks the network and never reads your own git or Sijl settings.
- **When the report changes on purpose**, regenerate its golden file and review the diff like
  code: `UPDATE_GOLDEN=1 uv run pytest tests/test_report_golden.py`.
- **User-facing changes** get a line under `## [Unreleased]` in `CHANGELOG.md`, and the page in
  `docs/` that describes the feature is updated.
- **Keep Sijl read-only and offline:** it must never write into the repositories it reads, and
  nothing may reach the network except `sijl ask` / `sijl ai check` after the user's yes.
- **No personal data in the repository:** examples use made-up names (`you@example.com`,
  `~/code`, `ABC-123`). `tests/test_publication_guard.py` checks every file against a private
  word list that is never in the repository: it reads the file `PUBLICATION_GUARD_LIST` names
  (the release workflow writes it from a secret). Without that variable only its self-tests run,
  with a canary word.

Keep pull requests small and focused; a short description of what changes and why is enough.

## Releasing (maintainers)

`PUBLICATION_GUARD_LIST=/path/to/list scripts/release-check.sh vX.Y.Z` builds the wheel and
sdist, smoke-tests both and runs the publication guard on the tree and on them, without
publishing anything (without the list it warns loudly and skips that check). Pushing the tag
`vX.Y.Z` (equal to the version in `pyproject.toml`, with a `CHANGELOG.md` section of that
version) makes the release workflow build, test and publish the GitHub release; it refuses to
publish unless the repository has the `PUBLICATION_GUARD_LIST` secret (the list's content).

## Licence

By contributing you agree that your contribution is licensed under the [MIT licence](LICENSE).
