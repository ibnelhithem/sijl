# 0002. macOS CI runs on every pull request

**Status:** accepted
**Date:** 2026-10-15
**Issue:** #4

## Context

Sijl supports Linux and macOS. macOS used to run only after a merge to `main`, to save Actions
minutes while the repository was private. It is public now, and GitHub-hosted runners are free
for public repositories. On recent runs of this repository, Linux `check` took about 3 minutes
and `check-macos` took 6–9 minutes.

## Decision

`check-macos` has no `if:`. It runs on every pull request, every push to `main`, and every
manual run. The release workflow is unchanged: a pull-request dry run stays Linux-only; a tag
and a manual release run still test Linux and macOS.

## Alternatives considered

- **Keep macOS after the merge only.** Rejected: a macOS-only bug lands in `main` first.
- **Run macOS only when cutting a release.** Rejected: the bug shows up weeks later, mixed in
  with many other changes.
- **Run macOS only when a pull request has a label.** Rejected: easy to forget.
- **Skip macOS on docs-only pull requests with `paths-ignore`.** Rejected: a required check that
  never runs blocks the merge. Revisit only if the wait becomes a problem.

## Consequences

- A pull request's checks take about 6–9 minutes instead of about 3.
- A macOS failure blocks the merge.
- Making `check-macos` a required status check is a repository setting, done after the merge,
  not in this change.
- If the repository were private again, the cost of the minutes would be an open question.
