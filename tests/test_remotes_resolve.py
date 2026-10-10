"""sijl.remotes, classify: one remote becomes a web project on a recognised forge — through an
ssh alias when the user's ssh config renames the host — or a reason it cannot be one. ssh is
asked once per host, only for ssh remotes on hosts that are not already known, and never for a
host that fails the checks."""

import shutil

import pytest

from sijl import git as git_module
from sijl.git import SshError, SshRunner
from sijl.remotes import (
    ALIAS_LOOKUPS,
    ALIAS_SECONDS,
    ALIASES_OFF,
    BUDGET_SPENT,
    GITHUB,
    GITLAB,
    LOCAL,
    UNKNOWN_HOST,
    UNREADABLE,
    Forges,
    HostResolver,
    Remote,
    WebRepo,
    classify,
)

needs_ssh = pytest.mark.skipif(shutil.which("ssh") is None, reason="ssh is not installed")


class FakeSsh:
    """An SshRunner stand-in: answers from a table, counts the questions."""

    def __init__(self, aliases: dict[str, str] | None = None, error: str = "", clock=None) -> None:
        self.aliases, self.error, self.clock = aliases or {}, error, clock
        self.asked: list[str] = []
        self.timeouts: list[float | None] = []

    def hostname(self, host: str, *, timeout: float | None = None) -> str:
        self.asked.append(host)
        self.timeouts.append(timeout)
        if self.clock is not None:
            self.clock.now += 6.0  # every lookup takes 6 s of the run's budget
        if self.error:
            raise SshError(self.error)
        return self.aliases.get(host, host)


def remote(url: str, ssh=None, forges: Forges | None = None, name: str = "origin") -> Remote:
    return classify(name, url, forges or Forges(), HostResolver(ssh))


@needs_ssh
def test_the_github_personal_alias_resolves_to_github(tmp_path) -> None:
    config = tmp_path / "ssh_config"
    config.write_text(
        "Host github-alt\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/personal\n"
    )
    found = remote(
        "git@github-alt:alexgh/notes-app.git", SshRunner(config_file=config), name="mine"
    )
    assert found == Remote(
        "mine", web=WebRepo(GITHUB, "github.com", ("alexgh", "notes-app")),
        alias="github-alt",
    )  # fmt: skip
    assert found.web.commit_url("ab" * 20).startswith("https://github.com/alexgh/notes-app/")


@needs_ssh
def test_an_alias_in_the_default_ssh_config_resolves_too() -> None:
    # A normal run reads ssh's own config; in tests that is the hermetic, empty file.
    git_module.SSH_CONFIG.write_text("Host work-gitlab\n  HostName gitlab.com\n")
    found = remote("work-gitlab:acme/team/web-tests.git", SshRunner())
    assert found.web == WebRepo(GITLAB, "gitlab.com", ("acme", "team", "web-tests"))
    assert found.alias == "work-gitlab"


def test_known_hosts_never_ask_ssh() -> None:
    ssh = FakeSsh()
    for url in (
        "git@github.com:acme/app.git",
        "git@gitlab.com:acme/qa/app.git",
        "ssh://git@ssh.github.com:443/acme/app.git",
        "https://github.com/acme/app.git",
    ):
        assert remote(url, ssh).web is not None
    assert ssh.asked == []


def test_each_host_is_asked_once() -> None:
    ssh = FakeSsh({"github-alt": "github.com"})
    resolver = HostResolver(ssh)
    for name in ("one", "two"):
        classify(name, "git@github-alt:alexgh/app.git", Forges(), resolver)
    classify("three", "ssh://git@github-alt/alexgh/other.git", Forges(), resolver)
    assert ssh.asked == ["github-alt"]


def test_a_failure_is_remembered_too() -> None:
    ssh = FakeSsh(error="timed out after 5 s")
    resolver = HostResolver(ssh)
    first = classify("a", "git@work:acme/app.git", Forges(), resolver)
    classify("b", "git@work:acme/other.git", Forges(), resolver)
    assert ssh.asked == ["work"]
    assert first == Remote(
        "a", problem=UNKNOWN_HOST, host="work", alias_error="timed out after 5 s"
    )


def test_an_https_host_is_never_an_ssh_alias() -> None:
    ssh = FakeSsh({"git.acme.test": "gitlab.com"})
    assert remote("https://git.acme.test/acme/app.git", ssh) == Remote(
        "origin", problem=UNKNOWN_HOST, host="git.acme.test"
    )
    assert ssh.asked == []


def test_an_alias_for_an_unknown_host_names_both() -> None:
    found = remote("git@work-gl:platform/app.git", FakeSsh({"work-gl": "git.acme.test"}))
    assert found == Remote("origin", problem=UNKNOWN_HOST, host="git.acme.test", alias="work-gl")


def test_a_configured_host_behind_an_alias() -> None:
    forges = Forges.from_hosts(gitlab=["git.acme.test"])
    found = remote("git@work-gl:platform/tools/app.git", FakeSsh({"work-gl": "Git.Acme.test"}),
                   forges)  # fmt: skip
    assert found.web == WebRepo(GITLAB, "git.acme.test", ("platform", "tools", "app"))


def test_with_aliases_off_ssh_is_never_asked() -> None:
    assert remote("git@github-alt:alexgh/app.git", None) == Remote(
        "origin", problem=UNKNOWN_HOST, host="github-alt", alias_error=ALIASES_OFF
    )


@pytest.mark.parametrize(
    "url",
    [
        "git@-oProxyCommand=touch pwned:acme/app.git",
        "ssh://-F%2Ftmp%2Fevil/acme/app.git",
        "ssh://git@-Fevil:22/acme/app.git",
    ],
)
def test_a_hostile_host_is_unreadable_and_never_asked(url: str) -> None:
    ssh = FakeSsh()
    assert remote(url, ssh) == Remote("origin", problem=UNREADABLE)
    assert ssh.asked == []


def test_a_project_path_that_does_not_fit_the_forge_is_unreadable() -> None:
    found = remote("https://github.com/acme/team/app.git", FakeSsh())
    assert found == Remote("origin", problem=UNREADABLE, host="github.com")


def test_a_local_folder() -> None:
    assert remote("/srv/git/app.git", FakeSsh()) == Remote("origin", problem=LOCAL)


def test_no_secret_survives_classification() -> None:
    for url in (
        "https://alex:s3cret@github.com/acme/app.git",
        "https://oauth2:s3cret@git.acme.test/acme/app.git?private_token=s3cret",
        "https://s3cret@github.com/acme/team/app.git",
    ):
        assert "s3cret" not in repr(remote(url, FakeSsh()))


class Clock:
    """A clock the test moves by hand (FakeSsh adds 6 s per lookup)."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_at_most_eight_hosts_are_asked_per_run() -> None:
    # One `ssh -G` per distinct unknown ssh host, with no cap, let a
    # crafted repository with many remotes multiply a slow `Match exec` in the user's config.
    ssh = FakeSsh()
    resolver = HostResolver(ssh)
    found = [classify(f"r{n}", f"git@host{n}:acme/app.git", Forges(), resolver) for n in range(10)]
    assert ALIAS_LOOKUPS == 8 and ssh.asked == [f"host{n}" for n in range(8)]
    assert [r.alias_error for r in found[8:]] == [BUDGET_SPENT, BUDGET_SPENT]
    assert all(r.problem == UNKNOWN_HOST for r in found)


def test_at_most_ten_seconds_are_spent_asking() -> None:
    clock = Clock()
    ssh = FakeSsh(clock=clock)
    resolver = HostResolver(ssh, clock=clock)
    found = [classify(f"r{n}", f"git@host{n}:a/b.git", Forges(), resolver) for n in range(4)]
    assert ALIAS_SECONDS == 10.0
    assert ssh.asked == ["host0", "host1"]  # 6 s, then 4 s left for the second, then none
    assert ssh.timeouts == [10.0, 4.0]  # each lookup may use only what is left
    assert [r.alias_error for r in found[2:]] == [BUDGET_SPENT, BUDGET_SPENT]


def test_hosts_already_known_cost_nothing_from_the_budget() -> None:
    ssh = FakeSsh({"github-alt": "github.com"})
    resolver = HostResolver(ssh, lookups=1)
    for name in ("a", "b", "c"):
        assert classify(name, "git@github-alt:o/r.git", Forges(), resolver).web is not None
    assert ssh.asked == ["github-alt"]
