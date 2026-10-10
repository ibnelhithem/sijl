# Security policy

## Reporting a vulnerability

Please report security problems **privately**, not in a public issue: on the repository's
GitHub page, open **Security → Report a vulnerability** (a private security advisory that only
the maintainer can see). Describe what you found, how to reproduce it and which version you ran
(`sijl --version`). You will get an answer as soon as possible, and a fix is released before the
problem is made public.

Please do not include real reports, digests or config files: they contain work history, email
addresses and other people's names. A minimal, made-up repository that shows the problem is
best.

## Supported versions

Security fixes go into the latest release. Upgrade with
`uv tool install git+https://github.com/ibnelhithem/sijl@vX.Y.Z` (the newest tag).

## What Sijl sends, and where

- `sijl report`, `sijl digest`, `sijl repos` and `sijl config show` make **no network
  connection**. They only read git history on your computer and never write into the
  repositories they read. To turn an ssh host alias into a web address, Sijl runs `ssh -G`, which
  answers from your ssh configuration without connecting anywhere.
- `sijl ask` and `sijl ai check` send to the **Anthropic API** (or the `ANTHROPIC_BASE_URL` you
  configured) — only after showing you exactly what will be sent and getting your yes (unless you
  pass `--yes` or set `ai.confirm = false`). They send over `https://` only (plain `http://` only
  to a program on your own computer). Colleagues' names and email addresses are redacted by
  default (`ai.redact`).
- `sijl ai status` sends nothing.
- Your Anthropic credentials come from the environment or your `ant auth login` profile; Sijl
  never reads secrets from its config file and never writes them anywhere.

Problems in any of these promises — an unexpected connection, a write into a repository, data
sent without consent, a secret printed or stored — are security bugs; please report them as
above.
