# 0004. A tag publishes the tested files to PyPI before the GitHub release

**Status:** accepted
**Date:** 2026-10-21
**Issue:** #29

## Context

Sijl is installed from a git URL. `uv tool install sijl`, `pipx install sijl` and `pip install sijl` need the project on PyPI. On 2026-10-19 the public JSON API for the name `sijl` answered 404. A pending publisher lets the first upload create the project, and it does not reserve the name: anyone can register it first.

PyPI keeps a file name forever, including after a delete. A GitHub release can be deleted and created again. The version in `pyproject.toml` is 1.0.0, and the tag `v1.0.0` already points at the first public release (2026-10-10). This workflow runs from the commit a tag points at.

## Decision

1. **The first upload is the existing 1.0.0.** The six later issues ship as 1.1.0. The version is not raised for this change, and today's `main` is not tagged `v1.0.0`.
2. **`uv` builds. The PyPA action uploads.** The job uses `pypa/gh-action-pypi-publish` pinned to commit `dc37677b2e1c63e2034f94d8a5b11f265b73ba33` (`v1.14.2`). It does not check out the repository. There is no long-lived PyPI token. Attestations stay on, which is the action's default.
3. **PyPI, then the check, then GitHub.** The order is build, test, upload, sha256 check, GitHub release. A failed upload must not announce a version that `uv tool install` cannot fetch.
4. **The tag rule is the gate.** No required reviewer. The `pypi` and `testpypi` environments are limited to `v*` tags. That limit is a GitHub setting, made after this workflow exists.
5. **The check compares sha256 before any install.** Linux and macOS poll every 15 seconds for at most 5 minutes. A missing version keeps polling until that deadline. A hash mismatch stops immediately. The log names the version and says either "not on PyPI yet" or "sha256 does not match". It does not print file contents. The job timeout is 10 minutes.
6. **The rehearsal is a separate manual run.** The `testpypi` checkbox defaults to off and uploads to TestPyPI only (`https://test.pypi.org/legacy/`). A pull request uploads nowhere. A tag push ignores the checkbox. The rehearsal does not install, so dependencies are not taken from TestPyPI. TestPyPI never accepts the same file name twice, so a rehearsal of a version uses that name up there only.
7. **The packaged README points at the tag. The sidebar does not.** Relative links in the README inside the wheel and sdist become `https://github.com/ibnelhithem/sijl/blob/<tag>/...`. The README in git keeps the short links. Absolute `http` and `https` links stay. Source, Release notes and the other `[project.urls]` entries stay as written in `pyproject.toml`.
8. **A failed run continues with Re-run failed jobs.** That reuses the tested `dist` artifact. The upload skips a file already on PyPI. Re-run all jobs only when nothing from that version has reached PyPI. No new tag. `uv build` was not shown to be byte-for-byte reproducible.

## Alternatives considered

- **Publish 1.1.0 as soon as this workflow is merged, and move the other milestone work to 1.2.** Rejected: 1.0.0 is already the public release, and 1.1.0 stays the version for the six later issues.
- **Wait until those six issues are done, then publish 1.1.0.** Rejected: the name stays unreserved, and an install from PyPI waits on unrelated work.
- **Move the existing `v1.0.0` tag onto today's `main`.** Rejected: that tag names the 2026-10-10 release. Moving it would publish later commits as 1.0.0.
- **`uv publish` in the upload job.** Rejected: the job that holds the token should run as little as possible. The PyPA action is pinned to a full commit SHA and creates the attestations itself. `uv publish` uploads attestations; it does not create them.
- **A long-lived PyPI token.** Rejected: a stored token can be copied. The OIDC token exists for one job.
- **Create the GitHub release before the PyPI upload.** Rejected: the release page would announce a version that cannot be installed, and the GitHub release is the announcement that can be redone.
- **A required reviewer on the environment.** Rejected: one maintainer pushes the tag. The gate would wait on that same person for every release.
- **Install from PyPI, then compare.** Rejected: a wrong file would already be on the machine. The checksum comes first.
- **Poll until the version appears, with no deadline.** Rejected: a missing version needs a named failure. The wait is bounded.
- **One run that reads the checkbox and picks the index.** Rejected: a tag push must not be able to send the files to TestPyPI.
- **Upload to TestPyPI from a pull request.** Rejected: a pull request uploads nowhere. Anyone can publish dependency names on TestPyPI, so the rehearsal does not install from there.
- **Rewrite `[project.urls]` to the tag on every build.** Rejected: those sidebar links are a different format from the README links, and tag-specific URLs would mean editing `pyproject.toml` on every build.
- **One rewriter per README link.** Rejected: every relative target is the same kind of link.
- **Retry by pushing a new tag for the same version, or by rebuilding every job.** Rejected: the tag must equal the version, a taken file name cannot be uploaded again, and a second build is not known to be the same bytes.

## Consequences

- After this change is merged, the maintainer still creates the PyPI and TestPyPI accounts with 2FA, a pending publisher on each (workflow `release.yml`, environments `pypi` and `testpypi`), and the two environments limited to `v*` tags. The jobs name those environments now, so they will not run until the environments exist.
- `v1.0.0` already points at the first public release, and that commit does not contain this workflow. A tag push runs the workflow from the tagged commit, and the real upload runs only on a tag push. Publishing the already released 1.0.0 bytes is a separate step from cutting a tag on this branch. Ask before any tag.
- The first upload was 1.0.1, not the 1.0.0 bytes. The tag `v1.0.0` points at a commit that does not contain this workflow, and it was not moved. 1.1.0 stays the version for the six later issues.
- A rehearsal consumes that version's file names on TestPyPI.
- The upload job publishes every file in the `dist` artifact. The build job is what puts the wheel and the sdist there.
- A bad release is yanked, then replaced by a new patch version. The steps are in [the developer docs](../README.md#when-a-release-is-wrong).
- The README install section, `docs/install.md` and `SECURITY.md` lead with `uv tool install sijl`. That text ships in 1.0.2. The 1.0.1 page on PyPI keeps the git install command it was built with.
- This change touches a release and an OIDC token, so it needs an independent review before it is merged. A security finding stops the merge.
