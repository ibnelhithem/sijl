"""Deterministic git repositories for tests.

Every commit pins its author, committer and both dates through GIT_* environment variables, so
a scenario builds to the same SHAs on any machine. The hermetic fixture in conftest.py isolates
the global git config, and the `-c` overrides below also neutralise every setting that would
block or alter a commit (signing, hooks, line endings, merge logs) if one leaked through.

The identities are fictional but copy the shapes found in real repos: one person committing under
a work email, a GitHub noreply address and a second display name, plus a different person whose
name looks alike and who must never be matched.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sijl.models import RawCommit
from sijl.window import Window


@dataclass(frozen=True)
class Who:
    name: str
    email: str


ME = Who("Alex Example", "alex.example@acme.test")
ME_GITHUB = Who("alexgh", "40400001+alexgh@users.noreply.github.com")  # GitHub web-UI merges
ME_ALT_NAME = Who("Alex-Example-ACME", "alex.example@acme.test")  # same email, other name
LOOKALIKE = Who("Alex Examplov", "alex.examplov@mail.test")  # a different person
COLLEAGUE = Who("Sam Colleague", "sam.colleague@acme.test")
GITHUB_WEB = Who("GitHub", "noreply@github.com")  # committer of every GitHub web merge

# The review period most scenarios are pinned to — defined once, so its edges stay in step.
REVIEW_PERIOD = Window(date(2026, 4, 1), date(2026, 6, 30))
PERIOD_ARGS = ["--since", REVIEW_PERIOD.start.isoformat(), "--until", REVIEW_PERIOD.end.isoformat()]


def raw_commit(*, who: Who = ME, at: str = "2026-05-04T10:00:00+02:00", **overrides) -> RawCommit:
    """A RawCommit built directly, for tests of the rules applied after git's output is parsed."""
    when = datetime.fromisoformat(at)
    subject = overrides.pop("subject", "feat: something")
    fields = {
        "sha": "a" * 40,
        "parents": ("b" * 40,),
        "tree": "c" * 40,
        "author_name_raw": who.name,
        "author_email_raw": who.email,
        "author_name": who.name,
        "author_email": who.email,
        "author_date": when,
        "committer_name": who.name,
        "committer_email": who.email,
        "committer_date": when,
        "trailers": (),
        "subject": subject,
        "body": f"{subject}\n",
    }
    return RawCommit(**(fields | overrides))


# git words some errors differently from one version to the next — git 2.43 names the missing git
# dir of a deleted worktree's repository, the CI runner's newer git prints "(null)" — and
# Sijl passes git's words through. A test of a *real* git error therefore asserts what
# Sijl guarantees: the reason starts with Sijl's own words for that case, or with
# "cannot be opened: ", which any wording it does not recognise falls back to. The exact texts
# are pinned on fixed stderr strings in tests/test_discover_skips.py (GIT_SAYS).
UNRECOGNISED = "cannot be opened: "


def reason_from_git(reason: str, *recognised: str) -> bool:
    """`reason` is Sijl's reason for a git error: one of the `recognised` prefixes, or the
    unrecognised fallback."""
    return reason.startswith((*recognised, UNRECOGNISED))


# Command-line -c beats every config file, so these hold even under a hostile global config.
NEUTRAL_CONFIG = (
    "-c", "commit.gpgsign=false",
    "-c", "tag.gpgsign=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.autocrlf=false",
    "-c", "core.fsmonitor=false",
    "-c", "merge.log=false",
    "-c", "user.useConfigOnly=false",
    "-c", "init.defaultBranch=main",
)  # fmt: skip


def run_git(
    cwd: Path, *args: str, env: Mapping[str, str] | None = None, stdin: bytes | None = None
) -> str:
    """Run git in `cwd`; fail the test with git's own error text if it exits non-zero."""
    result = subprocess.run(
        ["git", *NEUTRAL_CONFIG, *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        env={**os.environ, **(env or {})},
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} exited {result.returncode}:\n{result.stderr.decode()}"
        )
    return result.stdout.decode()


def identity_env(
    author: Who, author_date: str, committer: Who, committer_date: str
) -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": author.name,
        "GIT_AUTHOR_EMAIL": author.email,
        "GIT_AUTHOR_DATE": author_date,
        "GIT_COMMITTER_NAME": committer.name,
        "GIT_COMMITTER_EMAIL": committer.email,
        "GIT_COMMITTER_DATE": committer_date,
    }


def committer_env(committer: Who, committer_date: str) -> dict[str, str]:
    """Only the committer: a rebase, a cherry-pick or an amend keeps the original author."""
    return {
        "GIT_COMMITTER_NAME": committer.name,
        "GIT_COMMITTER_EMAIL": committer.email,
        "GIT_COMMITTER_DATE": committer_date,
    }


def message(subject: str, body: str = "", trailers: Sequence[str] = ()) -> str:
    """Subject, blank line, body, blank line, trailers: the layout git and GitHub produce."""
    parts = [subject]
    if body:
        parts.append(body.strip("\n"))
    if trailers:
        parts.append("\n".join(trailers))
    return "\n\n".join(parts) + "\n"


class RepoBuilder:
    """A git repository built step by step with pinned identities and dates.

    Dates are ISO 8601 with an offset, e.g. "2026-05-04T10:00:00+02:00".
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "--object-format=sha1", "-b", "main")

    @classmethod
    def existing(cls, path: Path) -> RepoBuilder:
        """Wrap a repository that already exists (a clone, a worktree) without re-initialising."""
        builder = cls.__new__(cls)
        builder.path = path
        return builder

    def clone(self, dest: Path) -> RepoBuilder:
        """A second clone: its own common git dir, the same commit SHAs, `origin` = this repo."""
        run_git(dest.parent, "clone", "-q", str(self.path), str(dest))
        return RepoBuilder.existing(dest)

    def add_worktree(self, dest: Path, *, branch: str | None = None) -> RepoBuilder:
        """A linked worktree: a `.git` file pointing back into this repo's common dir."""
        where = ["-b", branch] if branch else ["--detach"]
        self.git("worktree", "add", "-q", *where, str(dest))
        return RepoBuilder.existing(dest)

    def stash(self, message_text: str, *, files: Mapping[str, str], at: str, who: Who = ME) -> None:
        """A stash entry (refs/stash) holding changes to tracked files."""
        for rel, content in files.items():
            (self.path / rel).write_text(content)
        self.git("stash", "push", "-q", "-m", message_text, env=identity_env(who, at, who, at))

    def git(
        self, *args: str, env: Mapping[str, str] | None = None, stdin: bytes | None = None
    ) -> str:
        return run_git(self.path, *args, env=env, stdin=stdin)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def commit(
        self,
        subject: str,
        *,
        at: str,
        who: Who = ME,
        files: Mapping[str, str | bytes | None] | None = None,
        body: str = "",
        trailers: Sequence[str] = (),
        committer: Who | None = None,
        committed_at: str | None = None,
    ) -> str:
        """Write `files` (path -> new content; None deletes the file), commit, return the SHA.

        Without `files`, a line is appended to history.txt so every commit changes something.
        The committer and the committer date default to the author and `at`.
        """
        if files is None:
            history = self.path / "history.txt"
            previous = history.read_text() if history.exists() else ""
            files = {"history.txt": f"{previous}{at} {subject}\n"}
        for rel, content in files.items():
            if content is None:
                self.git("rm", "-q", "--", rel)
                continue
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode() if isinstance(content, str) else content)
        self.git("add", "-A")
        env = identity_env(who, at, committer or who, committed_at or at)
        text = message(subject, body, trailers)
        self.git(
            "commit",
            "-q",
            "--no-verify",
            "--allow-empty",
            "--cleanup=verbatim",
            "-F",
            "-",
            env=env,
            stdin=text.encode(),
        )
        return self.head()

    def branch(self, name: str, start: str = "HEAD") -> None:
        self.git("branch", name, start)

    def switch(self, name: str) -> None:
        self.git("switch", "-q", name)

    def merge(
        self, ref: str, *, message_text: str, at: str, who: Who = ME, committer: Who | None = None
    ) -> str:
        """A true merge commit (--no-ff) of `ref` into the current branch; returns its SHA. The
        committer defaults to the author, both at `at`."""
        # Unlike `commit -F -`, `merge -F` cannot read stdin, so the message goes via a file.
        message_file = self.path / ".git" / "SIJL_TEST_MERGE_MSG"
        message_file.write_text(message_text)
        env = identity_env(who, at, committer or who, at)
        self.git(
            "merge",
            "-q",
            "--no-ff",
            "--no-verify",
            "--cleanup=verbatim",
            "-F",
            str(message_file),
            ref,
            env=env,
        )
        return self.head()

    def gitlab_mr_merge(
        self,
        branch: str,
        *,
        number: int,
        title: str,
        at: str,
        who: Who = ME,
        project: str = "acme/platform/app",
        into: str = "main",
        description: str = "",
    ) -> str:
        """Merge `branch` into the current branch as GitLab's merge button does: a merge
        commit written by the person who clicked merge — author and committer, one instant — with
        GitLab's default message: the subject, the merge request's title on line 3, an optional
        description, then `See merge request <project>!<number>`. Returns its SHA."""
        parts = [f"Merge branch '{branch}' into '{into}'", title]
        if description:
            parts.append(description.strip("\n"))
        parts.append(f"See merge request {project}!{number}")
        return self.merge(branch, message_text="\n\n".join(parts) + "\n", at=at, who=who)

    def github_pr_merge(
        self,
        branch: str,
        *,
        number: int,
        title: str,
        at: str,
        who: Who = ME_GITHUB,
        owner: str = "alexgh",
    ) -> str:
        """Merge `branch` into the current branch as GitHub's web merge does: authored by
        the merger's GitHub identity, committed by GitHub (`noreply@github.com`), the message
        `Merge pull request #<number> from <owner>/<branch>` with the pull request's title on
        line 3. Returns its SHA."""
        text = f"Merge pull request #{number} from {owner}/{branch}\n\n{title}\n"
        return self.merge(branch, message_text=text, at=at, who=who, committer=GITHUB_WEB)

    def github_squash(
        self,
        branch: str,
        *,
        number: int,
        title: str,
        at: str,
        who: Who = ME_GITHUB,
        body: str | None = None,
    ) -> str:
        """Squash-merge `branch` into the current branch as GitHub's "Squash and merge" does:
        one commit with one parent holding the branch's whole change — authored by the pull
        request's author (`who`, their GitHub identity), committed by GitHub, both at `at` — whose
        subject is `<title> (#<number>)`. `body=None` writes GitHub's default description: the
        commit's own body for a one-commit branch, otherwise every commit's message as a `* ` item;
        any other text replaces it, as when the author edits it before merging. Returns its SHA."""
        shas = self.git("rev-list", "--reverse", f"HEAD..{branch}").split()
        if body is None:
            texts = [self.git("log", "-1", "--format=%B", sha).strip("\n") for sha in shas]
            if len(texts) == 1:
                body = texts[0].partition("\n")[2].strip("\n")
            else:
                body = "\n\n".join(f"* {text}" for text in texts)
        env = identity_env(who, at, GITHUB_WEB, at)
        self.git("merge", "--squash", "-q", branch, env=env)  # stages the change; no commit yet
        self.git(
            "commit",
            "-q",
            "--no-verify",
            "--allow-empty",
            "--cleanup=verbatim",
            "-F",
            "-",
            env=env,
            stdin=message(f"{title} (#{number})", body).encode(),
        )
        return self.head()

    def github_title_merge(
        self, branch: str, *, number: int, title: str, at: str, who: Who = ME_GITHUB
    ) -> str:
        """Merge `branch` as GitHub's web merge does when a repository sets its merge message to the
        pull request's title: a merge commit — two parents — committed by GitHub, authored
        by the merger, its whole message `<title> (#<number>)`. Returns its SHA."""
        text = f"{title} (#{number})\n"
        return self.merge(branch, message_text=text, at=at, who=who, committer=GITHUB_WEB)

    def cherry_pick(self, sha: str, *, at: str, committer: Who = ME) -> str:
        """Copy `sha` onto the current branch: a new SHA and committer date, the same author,
        author date and message. Returns the copy's SHA."""
        self.git("cherry-pick", "--allow-empty", sha, env=committer_env(committer, at))
        return self.head()

    def rebase(self, onto: str, *, at: str, committer: Who = ME) -> str:
        """Replay the current branch onto `onto`; every replayed commit is a new copy."""
        self.git("rebase", "-q", onto, env=committer_env(committer, at))
        return self.head()

    def amend(self, *, at: str, committer: Who = ME) -> str:
        """Rewrite HEAD without changing it: only the committer date differs, so the SHA does."""
        args = ("commit", "--amend", "-q", "--no-edit", "--no-verify", "--allow-empty")
        self.git(*args, env=committer_env(committer, at))
        return self.head()

    def publish(self, remote_dir: Path, *refspecs: str, remote: str = "origin") -> None:
        """Push to a bare repo standing in for GitLab/GitHub, then fetch it back, so the
        refs/remotes/<remote>/* tracking refs look exactly as they do after a real push."""
        if not (remote_dir / "HEAD").exists():
            remote_dir.mkdir(parents=True, exist_ok=True)
            run_git(remote_dir, "init", "-q", "--bare", "--object-format=sha1", "-b", "main")
        if remote not in self.git("remote").split():
            self.git("remote", "add", remote, str(remote_dir))
        self.git("push", "-q", remote, *(refspecs or ("HEAD",)))
        self.git("fetch", "-q", remote)
