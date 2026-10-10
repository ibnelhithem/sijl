"""The only place Sijl starts a process: git, and `ssh -G` for host aliases.

Every call gets the same protections: inherited GIT_* variables that could redirect a command
to another repository are dropped, optional locks are off (reading never rewrites the index),
prompts are off, and repository settings that run programs (fsmonitor) are disabled from the
command line, which outranks every config file. Replace refs (`refs/replace/*`) are ignored, so
history is read as it was committed, never as a ref substitutes it. Output is parsed from git's
NUL-separated `-z` form, so commit messages may contain anything.

`SshRunner` asks `ssh -G` which host an ssh alias such as `github-alt` stands for. It
never connects anywhere; see its docstring for why the host it is given is checked so strictly.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from sijl.models import HOST_NAME, GraphNode, Lines, RawCommit

# Discovery only opens repositories (rev-parse, worktree list, config --file): an answer takes
# milliseconds, so a probe that waits longer is stuck on a pipe, a device or a hung disk.
PROBE_TIMEOUT = 10.0
MIN_VERSION = (2, 36)  # `--path-format=absolute` and `worktree list -z` exist from here

# Pass 1: one record per commit, fields split by \x1f, records ended by NUL (-z).
# The full message (%B) comes last so a stray \x1f inside it cannot shift any field.
PASS1_FORMAT = (
    "%H%x1f%P%x1f%T%x1f%an%x1f%ae%x1f%aN%x1f%aE%x1f%aI%x1f%cn%x1f%ce%x1f%cI"
    "%x1f%(trailers:only,unfold,separator=%x1d)%x1f%s%x1f%B"
)
_FIELDS = 14
_FIELD, _TRAILER = "\x1f", "\x1d"
# Everyone in a history, for redaction: author and committer as written and after .mailmap,
# then every trailer. Fields split by \x1f, trailers by \x1d, records ended by NUL (-z).
PEOPLE_FORMAT = (
    "%an%x1f%ae%x1f%aN%x1f%aE%x1f%cn%x1f%ce%x1f%cN%x1f%cE%x1f%(trailers:only,unfold,separator=%x1d)"
)
# A trailer's `Name <address>`; `\A`: one start only. The name's blanks are stripped after.
_PERSON = re.compile(r"\A(?P<name>[^<>]*)<(?P<email>[^<>]*)>\s*\Z")
# The commit graph deliveries walk: SHA, parents, raw and mailmapped author email. No
# message, no date: only who wrote each commit and what it descends from.
GRAPH_FORMAT = "%H%x1f%P%x1f%ae%x1f%aE"
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# What one commit changed against its parent, file by file (a squash merge counted as code).
# Each record is \x1e, the SHA, a blank line, then `added<TAB>removed<TAB>path` per file (`-` and
# `-` for a binary file). Without -z git quotes a path holding a control character, so neither
# \x1e nor a newline inside a name can split a record; only the two counts are read. `-M` counts a
# renamed file by what changed in it; `--no-ext-diff` and `--no-textconv` keep a repository's own
# config from running a program. It reads file contents: never in a partial clone.
NUMSTAT_FORMAT = "%x1e%H"
NUMSTAT_ARGS = (
    "log",
    "--stdin",
    "--no-walk=unsorted",
    "--numstat",
    "-M",
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    f"--format={NUMSTAT_FORMAT}",
)

# Command-line settings outrank every config file, so a repository's own config cannot undo them.
_SAFE_CONFIG = (
    "-c", "core.fsmonitor=false",
    "-c", "core.quotePath=false",
    "-c", "log.showSignature=false",
    "-c", "color.ui=false",
)  # fmt: skip

# GIT_* variables a user may legitimately set for where config lives or how ssh runs; every
# other GIT_* variable (GIT_DIR, GIT_WORK_TREE, GIT_CONFIG_PARAMETERS, ...) is dropped.
_KEEP = frozenset(
    {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_SSH", "GIT_SSH_COMMAND"}
)


class GitError(RuntimeError):
    """git exited non-zero; the message carries git's own words."""

    def __init__(self, args: tuple[str, ...], returncode: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(args)} (exit {returncode}): {stderr.strip()}")
        self.returncode = returncode
        self.stderr = stderr

    @property
    def summary(self) -> str:
        """git's first error line, for a report's Caveats."""
        lines = self.stderr.strip().splitlines()
        return lines[0].removeprefix("fatal: ").removeprefix("error: ") if lines else str(self)


def safe_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not k.startswith("GIT_") or k in _KEEP}
    env.update(
        GIT_OPTIONAL_LOCKS="0",  # reading must never refresh (rewrite) a repository's index
        GIT_TERMINAL_PROMPT="0",  # never wait for a password
        # No transport at all — not even file://, whose upload-pack program a repository's own
        # config can name — so a partial clone's missing object fails instead of being fetched.
        # "none" is a name no transport has. A future opt-in --fetch must set its own list.
        GIT_ALLOW_PROTOCOL="none",
        GIT_NO_LAZY_FETCH="1",  # and no lazy fetch into a partial clone (git 2.44 + backports)
        LC_ALL="C",  # git's messages in English, so errors can be recognised
    )
    return env


class GitRunner:
    """Runs `git -C <repo> ...` with the protections above and returns raw stdout bytes."""

    def __init__(
        self,
        *,
        timeout: float = 120.0,
        runner: Callable = subprocess.run,
        probe_timeout: float = PROBE_TIMEOUT,
    ) -> None:
        self._timeout = timeout
        self._probe_timeout = min(probe_timeout, timeout)
        self._runner = runner
        self._env = safe_env()

    def _spawn(
        self,
        args: tuple[str, ...],
        argv: list[str],
        stdin: bytes | None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ):
        """The single process call: same environment, same timeout, for every git command."""
        limit = self._timeout if timeout is None else timeout
        try:
            return self._runner(
                argv,
                input=stdin,
                capture_output=True,
                env=self._env if env is None else env,
                timeout=limit,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise GitError(args, -1, f"timed out after {limit:g} s") from None

    def run(self, cwd: Path, *args: str, stdin: bytes | None = None) -> bytes:
        return self._run(cwd, args, stdin, None)

    def run_quick(self, cwd: Path, *args: str) -> bytes:
        """Like `run()`, with discovery's short timeout (`PROBE_TIMEOUT`, 10 s): opening a
        repository takes milliseconds, so waiting longer means git is stuck."""
        return self._run(cwd, args, None, None, self._probe_timeout)

    def run_here(self, cwd: Path, *args: str) -> bytes:
        """Like `run()`, but git may not search above `cwd` for a repository: the parent of `cwd`
        is a ceiling (GIT_CEILING_DIRECTORIES). Probing a folder whose own `.git` is broken then
        fails, instead of silently opening the repository around it. Only this call gets it,
        and it has discovery's short timeout, as `run_quick()`.

        The parent is taken from the *resolved* path, as git compares real paths: through a
        symbolic link or a trailing `..` the lexical parent is not the folder above. A parent
        containing ":" still splits into useless entries — callers verify what git opened."""
        try:
            parent = cwd.resolve().parent
        except (OSError, RuntimeError):  # a symbolic link loop: git will fail on it anyway
            parent = cwd.absolute().parent
        env = {**self._env, "GIT_CEILING_DIRECTORIES": str(parent)}
        return self._run(cwd, args, None, env, self._probe_timeout)

    def _run(
        self,
        cwd: Path,
        args: tuple[str, ...],
        stdin: bytes | None,
        env: Mapping[str, str] | None,
        timeout: float | None = None,
    ) -> bytes:
        argv = ["git", "-C", str(cwd), "--no-pager", "--no-replace-objects", *_SAFE_CONFIG, *args]
        proc = self._spawn(args, argv, stdin, env, timeout)
        if proc.returncode != 0:
            raise GitError(args, proc.returncode, proc.stderr.decode("utf-8", "replace"))
        return proc.stdout

    def version(self) -> tuple[int, ...]:
        proc = self._spawn(("--version",), ["git", *_SAFE_CONFIG, "--version"], None)
        match = re.search(rb"(\d+)\.(\d+)\.(\d+)", proc.stdout)
        if match is None:
            raise GitError(("--version",), proc.returncode, "cannot read the git version")
        return tuple(int(part) for part in match.groups())

    def require_version(self) -> None:
        found = self.version()
        if found < MIN_VERSION:
            needed = ".".join(map(str, MIN_VERSION))
            have = ".".join(map(str, found))
            raise GitError(("--version",), 1, f"Sijl needs git {needed} or newer; found {have}")


# The ssh config `ssh -G` reads: None means ssh's own files (~/.ssh/config, /etc/ssh/ssh_config).
# ssh finds ~/.ssh/config through the password database, not $HOME, so the test suite's hermetic
# fixture points this at an empty file — no test ever reads the developer's own ssh config.
SSH_CONFIG: Path | None = None


class SshError(RuntimeError):
    """`ssh -G` could not say which host an alias stands for; the message says why."""


def ssh_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """What ssh needs to find programs and the user's files — nothing else: no agent socket, no
    GIT_*, no LD_PRELOAD, no tokens. English messages, like git's."""
    source = os.environ if base is None else base
    env = {"PATH": source.get("PATH", os.defpath), "LC_ALL": "C"}
    if source.get("HOME"):
        env["HOME"] = source["HOME"]
    return env


def run_in_own_group(
    argv: list[str],
    *,
    stdin: int | None = None,
    capture_output: bool = False,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """`subprocess.run()` for ssh, with one difference: the command starts a session — a process
    group — of its own, and a timeout kills the whole group. `subprocess.run()` kills only ssh, so
    the `sh -c` a `Match exec` line started, and whatever that started, kept running. A program
    that leaves the group on purpose (setsid, a daemon) still escapes; that is the user's own ssh
    config."""
    pipe = subprocess.PIPE if capture_output else None
    with subprocess.Popen(
        argv, stdin=stdin, stdout=pipe, stderr=pipe, env=env, cwd=cwd, start_new_session=True
    ) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # a new session: its group id is ssh's pid
            except (AttributeError, ProcessLookupError, PermissionError):  # no killpg on Windows
                proc.kill()
            raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


class SshRunner:
    """Asks `ssh -G` which host an ssh alias stands for.

    `ssh -G` prints the configuration ssh would use for a host and exits: it opens no connection,
    and `-o CanonicalizeHostname=no` (the command line outranks every config file) keeps it from
    looking names up in DNS. It does evaluate `Match exec` lines, which run commands — from the
    user's own ssh config, as every `git fetch` does — but a config file named on the command line
    would run *its* commands, and the host comes from a repository's remote URL, which anyone can
    write. So the host must be a plain host name (`HOST_NAME`: never a leading "-", no spaces, no
    "=") and comes after `--`; ssh runs with stdin closed, `ssh_env()`, from the root folder (no
    repository), in a process group of its own that a timeout kills whole (`run_in_own_group()`).
    """

    def __init__(
        self,
        *,
        timeout: float = 5.0,
        config_file: Path | None = None,
        runner: Callable = run_in_own_group,
    ) -> None:
        self._timeout = timeout
        self._config_file = config_file
        self._runner = runner

    def hostname(self, host: str, *, timeout: float | None = None) -> str:
        """The host name ssh would connect to for `host`, in lower case; `host` itself when no
        `Host` block renames it. `timeout` may shorten this call's limit (a run's budget running
        out), never lengthen it. Raises SshError — before running anything for a bad host."""
        limit = self._timeout if timeout is None else min(timeout, self._timeout)
        if not HOST_NAME.fullmatch(host):
            raise SshError("not a host name")
        config = self._config_file if self._config_file is not None else SSH_CONFIG
        argv = ["ssh", "-G", "-T", "-o", "CanonicalizeHostname=no"]
        if config is not None:
            argv += ["-F", str(config)]
        argv += ["--", host]
        try:
            proc = self._runner(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=ssh_env(),
                cwd=os.path.abspath(os.sep),
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            raise SshError(f"timed out after {round(limit, 1):g} s") from None
        except FileNotFoundError:
            raise SshError("ssh is not installed") from None
        except OSError as exc:
            raise SshError(f"ssh could not start: {exc.strerror}") from None
        if proc.returncode != 0:
            lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise SshError(lines[0] if lines else f"ssh exited {proc.returncode}")
        resolved = parse_ssh_config(proc.stdout).get("hostname", "")
        if not HOST_NAME.fullmatch(resolved):
            raise SshError("ssh gave no usable host name")
        return resolved.lower()


def parse_ssh_config(out: bytes) -> dict[str, str]:
    """`ssh -G` output — one `keyword value` per line, keywords in lower case — as a dict; a
    keyword listed more than once (identityfile) keeps its first value."""
    config: dict[str, str] = {}
    for line in out.decode("utf-8", "replace").splitlines():
        key, _, value = line.partition(" ")
        if key:
            config.setdefault(key, value.strip())
    return config


def parse_log_z(out: bytes) -> list[RawCommit]:
    """Records from `git log -z --format=PASS1_FORMAT`, in git's order."""
    commits = []
    for chunk in out.split(b"\x00"):
        if not chunk.strip(b"\n"):
            continue  # the empty tail after the last NUL
        fields = chunk.decode("utf-8", "replace").lstrip("\n").split(_FIELD, _FIELDS - 1)
        if len(fields) != _FIELDS:
            raise ValueError(f"a git log record has {len(fields)} fields, expected {_FIELDS}")
        sha, parents, tree, an, ae, an_m, ae_m, a_iso, cn, ce, c_iso, trailers, subject, body = (
            fields
        )
        commits.append(
            RawCommit(
                sha=sha,
                parents=tuple(parents.split()),
                tree=tree,
                author_name_raw=an,
                author_email_raw=ae,
                author_name=an_m,
                author_email=ae_m,
                author_date=datetime.fromisoformat(a_iso),  # accepts both "Z" and "+00:00"
                committer_name=cn,
                committer_email=ce,
                committer_date=datetime.fromisoformat(c_iso),
                trailers=_parse_trailers(trailers),
                subject=subject,
                body=body,
            )
        )
    return commits


def parse_people_z(out: bytes) -> set[tuple[str, str]]:
    """(name, email) of everyone in the records of `git log -z --format=PEOPLE_FORMAT`: the four
    identities of each commit, every trailer whose value is `Name <address>`, and the name a
    `…-by:` trailer gives without an address (email ""). Blank names are left out."""
    people: set[tuple[str, str]] = set()
    for chunk in out.split(b"\x00"):
        fields = chunk.decode("utf-8", "replace").lstrip("\n").split(_FIELD, 8)
        if len(fields) != 9:
            continue  # the empty tail after the last NUL
        people.update(zip(fields[0:8:2], fields[1:8:2], strict=True))
        for key, value in _parse_trailers(fields[8]):
            person = _PERSON.match(value)
            if person is not None:
                people.add((person["name"].strip().strip('"'), person["email"].strip()))
            elif key.endswith("-by"):
                people.add((value, ""))
    return {(name, email) for name, email in people if name.strip()}


def parse_graph_z(out: bytes) -> list[GraphNode]:
    """Records from `git log -z --format=GRAPH_FORMAT`, in git's order. A record without exactly
    four fields, or whose SHA is not one, raises ValueError: a graph read wrong would give work
    to the wrong person."""
    nodes = []
    for chunk in out.split(b"\x00"):
        if not chunk.strip(b"\n"):
            continue  # the empty tail after the last NUL
        fields = chunk.decode("utf-8", "replace").lstrip("\n").split(_FIELD)
        if len(fields) != 4 or not _SHA.fullmatch(fields[0]):
            raise ValueError("a git graph record is not a SHA, parents and two emails")
        sha, parents, email_raw, email = fields
        nodes.append(GraphNode(sha, tuple(parents.split()), email_raw, email))
    return nodes


def parse_numstat(out: bytes) -> dict[str, Lines]:
    """Records from `git log NUMSTAT_ARGS`, by SHA. A record whose head is not a SHA, or a file
    line that is not two counts (or `-` twice), raises ValueError: lines read wrong would be
    counted as someone's work."""
    found: dict[str, Lines] = {}
    for chunk in out.split(b"\x1e"):
        if not chunk.strip():
            continue  # what comes before the first record
        head, _, rest = chunk.partition(b"\n")
        sha = head.decode("ascii", "replace").strip()
        if not _SHA.fullmatch(sha):
            raise ValueError("a git numstat record does not start with a SHA")
        added = removed = binary = 0
        for line in rest.split(b"\n"):
            if not line:
                continue
            plus, _, tail = line.partition(b"\t")
            minus, tab, _path = tail.partition(b"\t")
            if (plus, minus) == (b"-", b"-") and tab:
                binary += 1
            elif plus.isdigit() and minus.isdigit() and tab:
                added, removed = added + int(plus), removed + int(minus)
            else:
                raise ValueError("a git numstat line is not two counts and a path")
        found[sha] = Lines(added, removed, binary)
    return found


def parse_worktrees_z(out: bytes) -> list[dict[str, str]]:
    """Records from `git worktree list --porcelain -z`, in git's order: the main worktree (or the
    bare repository itself) first, then each linked worktree. A field is `key value`, or a bare
    `key` (`bare`, `detached`, `locked`, `prunable`) stored with the value ""; an empty field ends
    a record. Decoded as file names are (os.fsdecode): a path that is not UTF-8 keeps its bytes as
    surrogates, so it still names the folder; never fatal."""
    records: list[dict[str, str]] = [{}]
    for item in os.fsdecode(out).split("\0"):
        if not item:  # an empty field ends a record
            records.append({})
            continue
        key, _, value = item.partition(" ")
        records[-1][key] = value
    return [record for record in records if record]


def _parse_trailers(text: str) -> tuple[tuple[str, str], ...]:
    pairs = []
    for item in text.split(_TRAILER):
        key, sep, value = item.partition(":")
        if sep and key.strip():
            pairs.append((key.strip().lower(), value.strip()))
    return tuple(pairs)
