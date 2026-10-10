"""sijl.config, forges: which extra hosts are GitHub or GitLab, and whether ssh aliases are
resolved — from the file, the environment, with the usual precedence; a value that is not a host
name alone is refused without being repeated (it might be a URL with a token in it)."""

import os
from datetime import date

import pytest

from sijl.config import ConfigError, Settings, load_settings, render_sources
from tests.test_config import config_file


def load(env: dict | None = None):
    return load_settings(config=None, env={**os.environ, **(env or {})}, flags={})


def test_the_defaults_know_only_github_com_and_gitlab_com_and_resolve_aliases() -> None:
    settings = load().settings
    assert (settings.github_hosts, settings.gitlab_hosts, settings.ssh_aliases) == ((), (), True)


def test_the_file() -> None:
    config_file(
        '[forges]\ngithub_hosts = ["github.acme.test"]\n'
        'gitlab_hosts = ["git.acme.test", "gitlab.acme.test"]\nssh_aliases = false\n'
    )
    loaded = load()
    assert loaded.settings.github_hosts == ("github.acme.test",)
    assert loaded.settings.gitlab_hosts == ("git.acme.test", "gitlab.acme.test")
    assert loaded.settings.ssh_aliases is False
    assert loaded.sources["forges.gitlab_hosts"] == "file"


def test_the_environment_wins_over_the_file() -> None:
    config_file('[forges]\ngitlab_hosts = ["git.acme.test"]\n')
    loaded = load({"SIJL_GITLAB_HOSTS": "gitlab.acme.test, git2.acme.test",
                   "SIJL_SSH_ALIASES": "no"})  # fmt: skip
    assert loaded.settings.gitlab_hosts == ("gitlab.acme.test", "git2.acme.test")
    assert loaded.settings.ssh_aliases is False
    assert loaded.sources["forges.gitlab_hosts"] == "env SIJL_GITLAB_HOSTS"


@pytest.mark.parametrize(
    "value",
    [
        "https://git.acme.test",
        "git.acme.test:8443",
        "git.acme.test/group",
        "-oProxyCommand=x",
        "alex:s3cret@git.acme.test",
        "",
    ],
)
def test_only_a_host_name_alone_is_accepted(value: str) -> None:
    path = config_file(f'[forges]\ngitlab_hosts = ["{value}"]\n')
    with pytest.raises(ConfigError) as raised:
        load()
    message = str(raised.value)
    assert message.startswith(f"{path}: forges.gitlab_hosts")
    assert "like git.example.com" in message
    if value:
        assert value not in message  # never echoed: it could carry a token


def test_one_host_cannot_be_both() -> None:
    config_file('[forges]\ngithub_hosts = ["git.acme.test"]\ngitlab_hosts = ["GIT.acme.test"]\n')
    with pytest.raises(ConfigError, match=r"both list git\.acme\.test"):
        load()


def test_settings_check_themselves() -> None:
    with pytest.raises(ConfigError):
        Settings(github_hosts=("not a host",))


def test_config_show_lists_them() -> None:
    config_file('[forges]\ngitlab_hosts = ["git.acme.test"]\n')
    text = render_sources(load(), today=date(2026, 10, 8))
    assert "forges.gitlab_hosts" in text and "git.acme.test" in text
    assert "forges.github_hosts" in text and "forges.ssh_aliases" in text
