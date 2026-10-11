# 0001. Sijl starts as a fresh public repository, and stays free of anyone's real work data

**Status:** accepted
**Date:** 2026-10-10

## Context

Sijl began life as a private tool, built and tested against its author's own repositories. Its
history, issue tracker and design notes were full of things that do not belong in public: real
measurements from those repositories, internal project and ticket names, local paths and a work
identity. Sijl reads exactly this kind of data for its users, so it should be especially careful
with it in its own repository.

## Decision

1. Sijl is published as a **new repository with a fresh history**: version 1.0.0 is its first
   commit. The earlier private history, tracker and design records stay private.
2. **Nothing from anyone's real work enters the repository** — not in code, tests, docs, commit
   messages, issues, pull requests or release notes. Examples are made up; measurements come
   from synthetic repositories built by the test suite.
3. A **publication guard** test checks every tracked file, and the release workflow checks the
   built packages too, against a private word list that is never stored in the repository in any
   form (a hash of a short word can be reversed by guessing). Locally it reads the list from the
   file `PUBLICATION_GUARD_LIST` names; in CI from an encrypted secret. A release is never
   published unguarded.
4. Design decisions from now on are recorded here, in `docs/dev/decisions/`.

## Alternatives considered

- **Make the private repository public.** Rejected: its history and tracker would publish the
  data above, and pull-request references keep old commits reachable even after a rewrite.
- **Rewrite the private history and publish it.** Rejected for the same reason, at higher cost.
- **Publish the old design records with numbers removed.** Rejected: too much of their reasoning
  rested on real measurements to anonymise faithfully.
- **No developer docs at all.** Rejected: contributors need the *why*, not only the code.

## Consequences

- Earlier design reasoning is not public; the code, tests and user docs carry what matters, and
  new decisions are written here.
- Every contribution follows the privacy rule; the guard catches the known words, review catches
  the rest.
- Releases need the guard secret configured on the repository.
