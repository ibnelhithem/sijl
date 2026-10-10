"""Where a repository's commits can be opened on the web.

A remote URL comes from a repository's own config, so it is treated as hostile: it is taken
apart into transport, host, port and project path, every part checked against a strict pattern,
and everything that could be secret — a user name, a password, a token, a query string — is
dropped on the way in. Nothing downstream ever sees the URL itself, so it cannot reach a link,
the report, a log or an error message.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from sijl.collect import RepoCommits, not_reached_from
from sijl.git import GitError, GitRunner, SshError, SshRunner
from sijl.models import GITHUB, GITLAB, HOST_NAME, PATH_SEGMENT, Delivery, RawCommit, Reach

KNOWN_FORGES = {"github.com": GITHUB, "gitlab.com": GITLAB}
# Hosts that only serve ssh for a forge whose web pages live on another name (ssh over port 443).
SSH_ONLY_HOSTS = {"ssh.github.com": "github.com", "altssh.gitlab.com": "gitlab.com"}

SSH_SCHEMES = frozenset({"ssh", "git+ssh", "ssh+git"})
WEB_SCHEMES = frozenset({"https", "http"})
# Why a remote names no project on a host — words for Caveats, never the URL.
LOCAL = "a folder on this machine"
OTHER_TRANSPORT = "another transport"
UNREADABLE = "could not be read"
UNKNOWN_HOST = "not a host Sijl knows as GitHub or GitLab"
ALIASES_OFF = "forges.ssh_aliases is false"
# How much one run may ask ssh: each distinct unknown ssh host costs one `ssh -G`, which runs the
# user's `Match exec` lines — a crafted repository with many remotes must not multiply them.
ALIAS_LOOKUPS = 8
ALIAS_SECONDS = 10.0
BUDGET_SPENT = (
    f"not asked: Sijl asks ssh about at most {ALIAS_LOOKUPS} hosts, for at most "
    f"{ALIAS_SECONDS:g} s in all, per run"
)

# `\A`: these three are only ever matched at the start of a text, and say so — unanchored, a
# `search` would restart at every position of a long run of letters, which is quadratic (the
# regex guard, tests/test_regex_budget.py). `_AUTHORITY` is matched from a position: no `\A`.
_SCHEME = re.compile(r"\A([A-Za-z][A-Za-z0-9+.-]*)://")
_AUTHORITY = re.compile(r"[^/?#\\]*")  # user:password@host:port, up to the path, query or fragment
_HELPER = re.compile(r"\A[A-Za-z][A-Za-z0-9+.-]*::")  # `ext::…`, `codecommit::…`: remote helpers
_DRIVE = re.compile(r"[A-Za-z]:[\\/]")
_SEGMENT = PATH_SEGMENT  # a group, project or owner name (and `~user` on ssh), no more
_PORT = re.compile(r"[0-9]{1,5}")
_SHA = re.compile(r"[0-9a-f]{7,64}")  # SHA-1 or SHA-256, full or abbreviated, as git prints it
_TICKET_KEY = re.compile(r"\A[A-Z][A-Z0-9_]*-[1-9][0-9]*")  # `\A`: see `_SCHEME`
# One git call lists every remote's URL and tells whether any `url.<base>.insteadOf` rule exists.
_REMOTES_AND_REWRITES = r"^(remote\..*\.url|url\..*\.insteadof)$"
_HTTPS_BASE = re.compile(r"https://([^/:?#@]+)(?::([0-9]{1,5}))?((?:/[A-Za-z0-9_.~+-]+)*)/?")


class NotAWebRemote(ValueError):
    """The URL names no project on a host. `reason` is LOCAL, OTHER_TRANSPORT or UNREADABLE, and
    is the whole message: the URL is never repeated, as it may hold a password."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RemoteURL:
    """A remote URL taken apart, with nothing secret left in it."""

    transport: str  # "ssh" (scp-like, ssh://, git+ssh://), "https", "http" or "git"
    host: str  # lower case; an ssh alias stays the alias until it is resolved
    port: int | None
    path: tuple[str, ...]  # ("group", "subgroup", "project") — ".git" removed


def parse_remote_url(url: str) -> RemoteURL:
    """Take `url` apart the way git reads it. Raises NotAWebRemote for a local folder, a transport
    that is not a hosted project, or anything that does not pass the checks."""
    if not url:
        raise NotAWebRemote(UNREADABLE)
    scheme = _SCHEME.match(url)
    if scheme:
        name = scheme.group(1).lower()
        if name == "file":
            raise NotAWebRemote(LOCAL)
        if name not in SSH_SCHEMES | WEB_SCHEMES | {"git"}:
            raise NotAWebRemote(OTHER_TRANSPORT)
        transport = "ssh" if name in SSH_SCHEMES else name
        # The authority ends at the first "/", "?", "#" or "\\", as for urlsplit and the URL
        # parsers git hands it to: in "https://evil.example?@github.com/…" the host is
        # evil.example, never github.com.
        authority = _AUTHORITY.match(url, scheme.end()).group()
        rest = url[scheme.end() + len(authority) :]
        path = re.split(r"[?#]", rest, maxsplit=1)[0]  # a query (?private_token=) is dropped
        hostport = authority.rpartition("@")[2]  # user name and password are dropped
        host, colon, port_text = hostport.partition(":")
        port = _port(port_text) if colon and port_text else None
        segments = path.split("/")
        if transport in WEB_SCHEMES:
            segments = [unquote(segment) for segment in segments]
    elif _HELPER.match(url):
        raise NotAWebRemote(OTHER_TRANSPORT)
    elif _DRIVE.match(url):
        raise NotAWebRemote(LOCAL)
    else:
        # git's scp-like form, `[user@]host:path`, needs a colon before any slash; anything else
        # without a scheme is a path on this machine.
        colon, slash = url.find(":"), url.find("/")
        if colon <= 0 or 0 <= slash < colon:
            raise NotAWebRemote(LOCAL)
        transport, port = "ssh", None
        host = url[:colon].rpartition("@")[2]
        segments = url[colon + 1 :].split("/")
    return RemoteURL(transport, _host(host), port, _project(segments))


def _host(text: str) -> str:
    if not HOST_NAME.fullmatch(text):
        raise NotAWebRemote(UNREADABLE)
    return text.lower()


def _port(text: str) -> int:
    if not _PORT.fullmatch(text) or not 0 < int(text) < 65536:
        raise NotAWebRemote(UNREADABLE)
    return int(text)


def _project(segments: list[str]) -> tuple[str, ...]:
    parts = [segment for segment in segments if segment]  # "a//b" and a trailing "/" add nothing
    if parts:
        parts[-1] = parts[-1].removesuffix(".git")
    if not parts or not all(_SEGMENT.fullmatch(p) and p not in (".", "..") for p in parts):
        raise NotAWebRemote(UNREADABLE)
    return tuple(parts)


@dataclass(frozen=True)
class Forges:
    """Which hosts are GitHub and which are GitLab: github.com and gitlab.com always, plus the hosts
    the user names (`forges.github_hosts`, `forges.gitlab_hosts`) for Enterprise or self-hosted
    installations. Host names are compared in lower case."""

    github: frozenset[str] = frozenset()
    gitlab: frozenset[str] = frozenset()

    @classmethod
    def from_hosts(cls, github: Iterable[str] = (), gitlab: Iterable[str] = ()) -> Forges:
        return cls(frozenset(h.lower() for h in github), frozenset(h.lower() for h in gitlab))

    def web_host(self, host: str) -> str:
        """The host a forge's web pages live on: `ssh.github.com` is github.com."""
        return SSH_ONLY_HOSTS.get(host.lower(), host.lower())

    def forge_of(self, host: str) -> str:
        """GITHUB, GITLAB, or "" for a host Sijl does not know."""
        host = host.lower()
        if host in KNOWN_FORGES:
            return KNOWN_FORGES[host]
        if host in self.github:
            return GITHUB
        return GITLAB if host in self.gitlab else ""


@dataclass(frozen=True)
class WebRepo:
    """A project on GitHub or GitLab, and the links into it. Built only from checked parts, so a
    link is always `https://`, a host name, and percent-encoded path segments."""

    forge: str  # GITHUB or GITLAB
    host: str  # the web host, lower case
    path: tuple[str, ...]  # owner/name on GitHub; every group level on GitLab
    port: int | None = None  # only an https remote's own non-default port

    @property
    def url(self) -> str:
        where = self.host if self.port is None else f"{self.host}:{self.port}"
        return f"https://{where}/" + "/".join(quote(segment, safe="") for segment in self.path)

    @property
    def label(self) -> str:
        """`host/group/project`, the project's name as a reader sees it."""
        return "/".join((self.host, *self.path))

    def commit_url(self, sha: str) -> str:
        if not _SHA.fullmatch(sha):
            raise ValueError("not a commit SHA")
        return f"{self.url}/commit/{sha}" if self.forge == GITHUB else f"{self.url}/-/commit/{sha}"

    def request_url(self, number: int) -> str:
        """A GitLab merge request or a GitHub pull request: one idea, each forge's own name."""
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError("not a merge or pull request number")
        if self.forge == GITHUB:
            return f"{self.url}/pull/{number}"
        return f"{self.url}/-/merge_requests/{number}"

    def request_ref(self, number: int) -> str:
        """How the forge itself writes the request: `#12` on GitHub, `!12` on GitLab."""
        return f"#{number}" if self.forge == GITHUB else f"!{number}"


def web_repo(forge: str, host: str, url: RemoteURL) -> WebRepo:
    """The project `url` names on `forge`, whose web pages live on `host`. A GitHub project is
    exactly owner and name; a GitLab one has at least a group. An ssh port is not the web port,
    so only an https remote keeps its own (non-default) port."""
    if forge not in (GITHUB, GITLAB) or not HOST_NAME.fullmatch(host):
        raise NotAWebRemote(UNREADABLE)
    if (forge == GITHUB and len(url.path) != 2) or len(url.path) < 2:
        raise NotAWebRemote(UNREADABLE)
    port = url.port if url.transport == "https" and url.port not in (None, 443) else None
    return WebRepo(forge, host.lower(), url.path, port)


def ticket_url(base: str, key: str) -> str:
    """`<base>/browse/<KEY>` — a Jira ticket. `base` must be a plain `https://host[:port][/path]`
    address (no user, query or fragment) and `key` a ticket key such as `PROJ-42`."""
    match = _HTTPS_BASE.fullmatch(base)
    if not match or not HOST_NAME.fullmatch(match.group(1)):
        raise ValueError("a ticket link needs a plain https:// address")
    host, port, path = match.group(1).lower(), match.group(2), match.group(3)
    if (port and not 0 < int(port) < 65536) or any(p in (".", "..") for p in path.split("/")):
        raise ValueError("a ticket link needs a plain https:// address")
    if not _TICKET_KEY.fullmatch(key):
        raise ValueError("not a ticket key")
    where = f"{host}:{port}" if port else host
    return f"https://{where}{path}/browse/{key}"


class HostResolver:
    """ssh host aliases resolved through `ssh -G` (`git.SshRunner`), each host asked once per run
    — a failure too — and within a budget: at most `lookups` hosts and `seconds` in all; each
    call may only use the time that is left. A host beyond the budget is not asked
    (`BUDGET_SPENT`, named in Caveats). Without an SshRunner (`forges.ssh_aliases = false`)
    nothing is resolved."""

    def __init__(
        self,
        ssh: SshRunner | None,
        *,
        lookups: int = ALIAS_LOOKUPS,
        seconds: float = ALIAS_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ssh = ssh
        self._known: dict[str, tuple[str, str]] = {}
        self._lookups, self._seconds, self._clock = lookups, seconds, clock
        self._spent = 0.0

    def resolve(self, host: str) -> tuple[str, str]:
        """(the host ssh would connect to, "") — or ("", why it is not known)."""
        if self._ssh is None:
            return "", ALIASES_OFF
        if host not in self._known:
            left = self._seconds - self._spent
            asked = sum(1 for _, why in self._known.values() if why != BUDGET_SPENT)
            if asked >= self._lookups or left <= 0:
                self._known[host] = ("", BUDGET_SPENT)
                return self._known[host]
            started = self._clock()
            try:
                self._known[host] = (self._ssh.hostname(host, timeout=left), "")
            except SshError as exc:
                self._known[host] = ("", str(exc))
            finally:
                self._spent += self._clock() - started
        return self._known[host]


@dataclass(frozen=True)
class Remote:
    """One remote of a repository, as links see it: a web project, or why there is none."""

    name: str
    web: WebRepo | None = None  # the project on a recognised forge
    problem: str = ""  # otherwise: LOCAL, OTHER_TRANSPORT, UNREADABLE or UNKNOWN_HOST
    host: str = ""  # the host (after alias resolution) when it is known but gives no project
    alias: str = ""  # the ssh alias the URL named, when ssh resolved it to another host
    alias_error: str = ""  # why ssh could not say what the host stands for


def classify(name: str, url: str, forges: Forges, resolver: HostResolver) -> Remote:
    """The web project remote `name` (fetching from `url`) stands for. ssh is asked only for an
    ssh remote whose host is not a known forge, and only with a host that passed the checks."""
    try:
        parsed = parse_remote_url(url)
    except NotAWebRemote as exc:
        return Remote(name, problem=exc.reason)
    host, alias, alias_error = forges.web_host(parsed.host), "", ""
    if not forges.forge_of(host) and parsed.transport == "ssh":
        resolved, alias_error = resolver.resolve(parsed.host)
        if resolved and resolved != parsed.host:
            host, alias = forges.web_host(resolved), parsed.host
    forge = forges.forge_of(host)
    if not forge:
        return Remote(name, problem=UNKNOWN_HOST, host=host, alias=alias, alias_error=alias_error)
    try:
        return Remote(name, web=web_repo(forge, host, parsed), alias=alias)
    except NotAWebRemote as exc:
        return Remote(name, problem=exc.reason, host=host, alias=alias)


def read_remotes(git: GitRunner, repo: Path) -> list[tuple[str, str]]:
    """Each remote's name and the URL git fetches from (its first `url`), in name order — asked
    from git with `--null`, never read from `.git/config` here. When any `url.<base>.insteadOf`
    rule exists, in any config file git reads, git applies it itself (`git remote get-url`, one
    call per remote). Push URLs (`pushurl`, `pushInsteadOf`) are not read: links show where a
    project lives, which is where it is fetched from."""
    try:
        out = git.run(repo, "config", "--null", "--get-regexp", _REMOTES_AND_REWRITES)
    except GitError as exc:
        if exc.returncode == 1:
            return []  # nothing matched: no remotes
        raise
    urls: dict[str, str] = {}
    rewrites = False
    for entry in out.decode("utf-8", "replace").split("\0"):
        key, _, value = entry.partition("\n")  # "key\nvalue" with --null; the value may be anything
        if key.startswith("remote.") and key.endswith(".url"):
            urls.setdefault(key.removeprefix("remote.").removesuffix(".url"), value)
        elif key.startswith("url."):
            rewrites = True
    if rewrites:
        for name in urls:
            try:
                out = git.run(repo, "remote", "get-url", "--", name)
                urls[name] = out.decode("utf-8", "replace").removesuffix("\n")
            except GitError:
                urls[name] = ""  # unreadable, like any URL that does not parse
    return sorted(urls.items())


@dataclass(frozen=True)
class RepoLinks:
    """The links one repository's counted commits and deliveries get."""

    remotes: tuple[Remote, ...] = ()  # every remote: origin first, then by name
    commits: Mapping[str, WebRepo] = field(default_factory=dict)  # sha → the project holding it
    unlinked: int = 0  # counted commits on remote branches, but on no remote a link exists for
    error: str = ""  # git could not list the remotes or say which holds what: git's words
    # merge sha → the project its merge request or pull request lives in
    requests: Mapping[str, WebRepo] = field(default_factory=dict)
    unlinked_requests: int = 0  # deliveries shown by their number alone
    # Pull requests linked in a repository with one GitHub project: in a fork synced from its
    # upstream the number may be upstream's; only the GitHub API can tell.
    pulls_unconfirmed: int = 0

    @property
    def projects(self) -> list[WebRepo]:
        """The projects its commits link to, in remote order, each once."""
        used = set(self.commits.values())
        found = [r.web for r in self.remotes if r.web is not None and r.web in used]
        return list(dict.fromkeys(found))


@dataclass(frozen=True)
class Links:
    """Commit links for the report, by repository (`Repo.path`); only repositories with counted
    commits are here."""

    repos: Mapping[Path, RepoLinks] = field(default_factory=dict)

    def of(self, path: Path) -> RepoLinks:
        return self.repos.get(path, RepoLinks())

    def commit_url(self, path: Path, sha: str) -> str:
        """The commit's link, or "" when it gets none."""
        web = self.of(path).commits.get(sha)
        return web.commit_url(sha) if web is not None else ""

    def request_url(self, path: Path, delivery: Delivery) -> str:
        """The merge request's or pull request's link, or "" when it gets none."""
        web = self.of(path).requests.get(delivery.merge.sha)
        return web.request_url(delivery.number) if web is not None else ""


def link_commits(
    repos: Sequence[RepoCommits], git: GitRunner, forges: Forges, resolver: HostResolver
) -> Links:
    """Which project each counted commit links to: the first remote — origin, then the others by
    name — that is a project on a recognised forge and whose remote-tracking branches reach the
    commit. Commits on no remote branch, and the SHAs of copies, get no link. Deliveries
    link to the project their request lives in (`_request_projects`)."""
    found: dict[Path, RepoLinks] = {}
    for rc in repos:
        if not rc.commits and not rc.deliveries:
            continue  # nothing of yours is listed for it
        try:
            classified = [
                classify(n, u, forges, resolver) for n, u in read_remotes(git, rc.repo.path)
            ]
            remotes = tuple(sorted(classified, key=lambda r: (r.name != "origin", r.name)))
            tips = _remote_tips(rc, remotes)
            held = _holders(git, rc, remotes, tips) if rc.commits else {}
            requests, unconfirmed = _request_projects(git, rc, remotes, tips)
        except GitError as exc:
            found[rc.repo.path] = RepoLinks(error=exc.summary)
            continue
        unlinked = sum(c.sha not in held for c in _pushed(rc)) if rc.has_remote else 0
        unlinked_requests = len(rc.deliveries) - len(requests)
        found[rc.repo.path] = RepoLinks(
            remotes, held, unlinked, "", requests, unlinked_requests, unconfirmed
        )
    return Links(found)


def _pushed(rc: RepoCommits) -> list[RawCommit]:
    """The counted commits a remote branch holds. A commit only on your laptop is counted when a
    squash merge delivered it (`RepoCommits.via`) — but no web page shows it, so it is never
    linked, nor counted as a pushed commit left without a link."""
    return [c for c in rc.commits if rc.reach[c.sha] >= Reach.PUSHED]


def _remote_tips(rc: RepoCommits, remotes: Sequence[Remote]) -> dict[str, list[str]]:
    """Each remote's remote-tracking tips, by remote name. A ref belongs to the remote with the
    longest name it starts with (`team/fork` before `team`); one left by a removed remote belongs
    to none ("")."""
    names = sorted((r.name for r in remotes), key=len, reverse=True)
    tips: dict[str, list[str]] = {}
    for ref, sha in rc.remote_refs:
        owner = next((n for n in names if ref.startswith(f"refs/remotes/{n}/")), "")
        tips.setdefault(owner, []).append(sha)
    return tips


def _holders(
    git: GitRunner, rc: RepoCommits, remotes: Sequence[Remote], tips: Mapping[str, list[str]]
) -> dict[str, WebRepo]:
    """sha → the project of the first linkable remote whose branches reach it."""
    linkable = [r for r in remotes if r.web is not None and tips.get(r.name)]
    pending = [c.sha for c in _pushed(rc)]
    if not linkable:
        return {}
    first = linkable[0]
    if set(tips) == {first.name}:  # every remote branch is this remote's: it holds every commit
        return dict.fromkeys(pending, first.web)
    held: dict[str, WebRepo] = {}
    for remote in linkable:
        missing = not_reached_from(git, rc.repo, pending, tips[remote.name])
        held.update((sha, remote.web) for sha in pending if sha not in missing)
        pending = [sha for sha in pending if sha in missing]
        if not pending:
            break
    return held


def _request_projects(
    git: GitRunner, rc: RepoCommits, remotes: Sequence[Remote], tips: Mapping[str, list[str]]
) -> tuple[dict[str, WebRepo], int]:
    """merge sha → the project a delivery's merge request or pull request lives in.

    A GitLab merge commit names its project (`See merge request group/project!N`): the link goes
    to the first GitLab remote that *is* that project (paths compared ignoring case, as GitLab
    does) — never to another project, where the same number is someone else's request. A GitHub
    merge commit names none: the link goes to the GitHub project whose remote-tracking branches
    hold the merge, only when exactly one does — a fork and its upstream both hold it, and the
    number belongs to one of them only. One `rev-list` per remote of that forge, and none when a
    single remote owns every remote-tracking branch.

    Also returns how many pull requests were linked where the repository knows only one GitHub
    project: a clone of a fork alone, synced from its upstream, holds upstream's merges, and git
    cannot tell them from the fork's own — the link stays, and the report says so."""
    by_forge = {
        forge: [r for r in remotes if r.web is not None and r.web.forge == forge]
        for forge in (GITHUB, GITLAB)
    }
    found: dict[str, WebRepo] = {}
    unnamed: dict[str, list[str]] = defaultdict(list)  # forge → merge shas naming no project
    for d in rc.deliveries:
        if not d.project:
            unnamed[d.forge].append(d.merge.sha)
            continue
        wanted = tuple(part.lower() for part in d.project)
        for remote in by_forge[d.forge]:
            if remote.web is not None and tuple(p.lower() for p in remote.web.path) == wanted:
                found[d.merge.sha] = remote.web
                break
    for forge, shas in unnamed.items():
        holders: dict[str, set[WebRepo]] = defaultdict(set)
        for remote in by_forge[forge]:
            if remote.web is None or not tips.get(remote.name):
                continue
            if set(tips) == {remote.name}:
                missing: set[str] = set()  # it owns every remote branch: it holds them all
            else:
                missing = not_reached_from(git, rc.repo, shas, tips[remote.name])
            for sha in shas:
                if sha not in missing:
                    holders[sha].add(remote.web)
        found.update((sha, webs.pop()) for sha, webs in holders.items() if len(webs) == 1)
    github_projects = {r.web for r in by_forge[GITHUB] if r.web is not None}
    unconfirmed = (
        sum(sha in found for sha in unnamed.get(GITHUB, ())) if len(github_projects) == 1 else 0
    )
    return found, unconfirmed
