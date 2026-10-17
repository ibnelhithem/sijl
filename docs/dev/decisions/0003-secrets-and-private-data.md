# 0003. Leaked secrets and private data are caught in layers

**Status:** accepted
**Date:** 2026-10-16
**Issue:** #30

## Context

Two things must not become public: a credential, and a word from someone's real work. Today the
publication-guard list runs only when a release is cut, and nothing in CI reads the repository
for credentials. GitHub secret scanning and push protection are on. They know provider tokens.
They do not know a private-key block, a password in a URL, or the private word list.

A scan in CI runs after the push. A finding there is already public: revoke the credential
first, then remove it. The layers that can still stop a push are a hook on the developer
machine, and GitHub push protection.

A scan of this repository's git history, with the scanner's output redacted, found 28
credential-shaped strings. All 28 were deliberate fixtures in the tests and in one comment, in
one historical commit. After each fixture was allowed as its exact captured value, the same scan
reported 0. A private-key block written to a throwaway file outside the repository, and never
committed, was still found.

## Decision

1. **The scanner is Betterleaks 1.9.0**, pinned by version and sha256. It is the maintained
   scanner. `--validation` stays off: that flag sends a found secret to its provider. A test
   proves the script never contains it. Output is redacted, and a finding is reported by its
   place only: the report template (`scripts/secret-scan.tmpl`) prints its file, line, rule and
   commit, and a test fails if the template asks for any other field. The CI log of a public
   repository is public, so it never shows the author, email or commit message.
2. **The pin lives in one script**, `scripts/secret-scan.sh`. The pull-request job calls it, the
   release scan will call it, and a maintainer can run it by hand on Linux (it fetches the
   Linux x64 build). One test proves, on the script's code with comments ignored, that the
   checksum line comes before the line that runs the binary. The binary is ours, not a
   third-party Action.
3. **Each deliberate fake is allowed as its exact captured value**, with a reason, in
   `.betterleaks.toml`. The pattern is anchored, so a real secret that merely contains a fixture
   still fails. The short values are limited to the file that uses them: the value and the path
   must both match. A different string fails until the allow entry and its reason change
   together. The defaults stay on (`useDefault = true`).
4. **The publication-guard check for text that is not a file** (commit messages, author and
   committer name and email, branch name, pull-request title and body) lives in `scripts/`.
   The test, CI, and the hook call that module. A hook does not start the test suite. The
   module is not shipped in the wheel.
5. **The pull-request title and body check is its own workflow file.** It needs the
   `pull_request` type `edited`. The credential scan does not, so it is a job in `ci.yml` with
   no `if:`. Adding `edited` to `ci.yml` would rerun `check-macos` and, with
   `cancel-in-progress`, cancel a running check. An `if:` on `check-macos` is already forbidden
   by test.
6. **A pull request from a fork skips the word-list check** with a condition on that job. Forks
   receive no secrets. The same-repository branches still run the check. A skipped job does not
   block the merge. The backstop is the push to `main` and the release workflow, which have the
   secret. `pull_request_target` is not used: it would run untrusted code with secrets.
7. **The release scan of the unpacked wheel and sdist runs once, in `build`, on Linux**, before
   publish. The release jobs stay `build`, `test`, and `publish`.
8. **GitHub secret scanning and push protection stay on. Non-provider patterns stay off.**
   The settings page has no separate switch for them, only Disable on Secret Protection, and
   that button turns push protection off too. Validity checks stay off: they contact the
   provider.

This change adds the credential scan (1–3) and records 4–8. The word-list job, the hooks, and
the release scan are later changes under the same issue.

## Alternatives considered

- **No scanner, only GitHub push protection.** Rejected: push protection knows provider tokens.
  The 28 findings were generic credentials, and a private-key block is one of them. The private
  word list is not a credential pattern at all.
- **gitleaks.** Rejected: its README calls it feature complete, with security patches only; its
  authors now develop Betterleaks. It also does not send secrets to a provider; that was not the
  reason. `--validation` on Betterleaks stays off either way.
- **Verbose output (`-v`) to say where a finding is.** Rejected: it also prints the commit's
  author name, email and message. The template prints only what is needed to find the line.
- **A substring filter, or a line fingerprint.** Rejected: a substring would hide a real key
  that merely contains a fixture. A fingerprint breaks when a line moves, and a real key on an
  allowed line would pass. A squash merge also changes commit ids.
- **A top-level `filter` expression while extending the defaults.** Rejected for this version:
  Betterleaks 1.9.0 drops that expression when `useDefault = true`. An allowlist entry per exact
  value is what this version applies.
- **The version and sha256 copied into each workflow.** Rejected: an upgrade would be edited in
  several places, and each place would need its own "checksum before run" proof.
- **Leave the non-file word check inside the test file.** Rejected: the hook, CI, and the tests
  must share one matcher, and a hook cannot start the test suite.
- **Add `edited` to `ci.yml`.** Rejected: every title typo would rerun the macOS job (about
  6–9 minutes) and could cancel a running check. The #4 test forbids an `if:` that would skip
  `check-macos` on that event.
- **`pull_request_target` so a fork can read the word list.** Rejected: the workflow would run
  the pull request's code with the secret present.
- **A fourth release job for the scan.** Rejected: the test pins the release jobs to `build`,
  `test`, and `publish`. `test` is a matrix, so a scan there would run once per system.
- **Turn on non-provider patterns, or click Disable.** Rejected: there is no separate switch.
  Disable turns off Secret Protection, including push protection.

## Consequences

- A credential finding in CI means the value is already on the remote. Revoke it first.
- Changing a fixture fails the scan until `.betterleaks.toml` allows the new exact value and
  the reason is updated with it.
- `secret-scan` has no `if:`, so a pull request cannot skip it. Making that job a required
  status check is a repository setting, done after the merge, not in this change.
- The word-list job, the local hooks, and the release scan are still to be added. Until the
  hook exists, only push protection acts before a credential is public.
- Fork pull requests will not run the word-list check, and the skipped job does not block the
  merge. A maintainer can still merge one: the check then runs on the push to `main`, after the
  merge, and again in the release workflow.
- Do not click Disable under Secret Protection, and do not pass `--validation`.
