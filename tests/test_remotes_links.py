"""sijl.remotes, links: a recognised forge turns a parsed remote into a web project, and the
project builds commit, merge request, pull request and ticket links — always https, always from
checked parts, every path segment percent-encoded, nothing a Markdown viewer could act on."""

import re

import pytest

from sijl.remotes import (
    GITHUB,
    GITLAB,
    UNREADABLE,
    Forges,
    NotAWebRemote,
    WebRepo,
    parse_remote_url,
    ticket_url,
    web_repo,
)

SHA = "0123456789abcdef0123456789abcdef01234567"
# What every link Sijl builds looks like: https, a host, then only unreserved characters,
# "/", "-" and percent escapes — no ")", "]", "|", "<", spaces or quotes.
SAFE_LINK = re.compile(r"https://[a-z0-9._-]+(:[0-9]+)?(/[A-Za-z0-9._~%!-]+)+")


NO_EXTRA_HOSTS = Forges()


def project(url: str, forges: Forges = NO_EXTRA_HOSTS) -> WebRepo:
    parsed = parse_remote_url(url)
    return web_repo(forges.forge_of(parsed.host), parsed.host, parsed)


def test_github_links() -> None:
    repo = project("git@github.com:acme/web-tests.git")
    assert repo == WebRepo(GITHUB, "github.com", ("acme", "web-tests"))
    assert repo.url == "https://github.com/acme/web-tests"
    assert repo.label == "github.com/acme/web-tests"
    assert repo.commit_url(SHA) == f"https://github.com/acme/web-tests/commit/{SHA}"
    assert repo.request_url(12) == "https://github.com/acme/web-tests/pull/12"
    assert repo.request_ref(12) == "#12"


def test_gitlab_links_keep_every_group_level() -> None:
    repo = project("git@gitlab.com:acme/group/sub/service.git")
    assert repo.url == "https://gitlab.com/acme/group/sub/service"
    assert repo.commit_url(SHA) == f"https://gitlab.com/acme/group/sub/service/-/commit/{SHA}"
    assert repo.request_url(42) == "https://gitlab.com/acme/group/sub/service/-/merge_requests/42"
    assert repo.request_ref(42) == "!42"


def test_an_https_port_is_kept_but_an_ssh_port_is_not_the_web_port() -> None:
    forges = Forges.from_hosts(gitlab=["gitlab.acme.test"])
    https = project("https://gitlab.acme.test:8443/platform/tools/app.git", forges)
    assert https.url == "https://gitlab.acme.test:8443/platform/tools/app"
    assert project("https://gitlab.acme.test:443/platform/app", forges).url == (
        "https://gitlab.acme.test/platform/app"
    )
    ssh = project("ssh://git@gitlab.acme.test:2222/platform/tools/app.git", forges)
    assert ssh.url == "https://gitlab.acme.test/platform/tools/app"


def test_an_http_remote_still_gets_an_https_link() -> None:
    assert project("http://github.com/acme/app.git").url == "https://github.com/acme/app"


def test_ssh_only_host_names_lead_to_the_forge_s_web_host() -> None:
    parsed = parse_remote_url("ssh://git@ssh.github.com:443/acme/app.git")
    forges = Forges()
    host = forges.web_host(parsed.host)
    assert (host, forges.forge_of(host)) == ("github.com", GITHUB)
    assert forges.web_host("altssh.gitlab.com") == "gitlab.com"


def test_forges_known_and_configured() -> None:
    forges = Forges.from_hosts(github=["GitHub.Acme.test"], gitlab=["git.acme.test"])
    assert forges.forge_of("github.com") == GITHUB
    assert forges.forge_of("gitlab.com") == GITLAB
    assert forges.forge_of("github.acme.test") == GITHUB
    assert forges.forge_of("GIT.acme.test") == GITLAB
    assert forges.forge_of("bitbucket.org") == ""
    assert Forges().forge_of("git.acme.test") == ""


def test_a_github_project_is_exactly_owner_and_name() -> None:
    parsed = parse_remote_url("https://github.com/acme/team/app.git")
    with pytest.raises(NotAWebRemote) as raised:
        web_repo(GITHUB, "github.com", parsed)
    assert raised.value.reason == UNREADABLE


def test_a_gitlab_project_has_a_group() -> None:
    with pytest.raises(NotAWebRemote):
        web_repo(GITLAB, "gitlab.com", parse_remote_url("https://gitlab.com/app.git"))


def test_path_segments_are_percent_encoded() -> None:
    repo = WebRepo(GITLAB, "gitlab.com", ("acme", "my+team", "app~1"))
    assert repo.url == "https://gitlab.com/acme/my%2Bteam/app~1"


@pytest.mark.parametrize("sha", ["", "xyz", SHA.upper(), "123456", SHA + "0" * 25, "a)b"])
def test_a_commit_link_needs_a_real_sha(sha: str) -> None:
    with pytest.raises(ValueError):
        project("git@github.com:acme/app.git").commit_url(sha)


@pytest.mark.parametrize("number", [0, -1, True])
def test_a_request_link_needs_a_positive_number(number: int) -> None:
    with pytest.raises(ValueError):
        project("git@gitlab.com:acme/app.git").request_url(number)


def test_ticket_links() -> None:
    assert ticket_url("https://acme.atlassian.net", "PROJ-42") == (
        "https://acme.atlassian.net/browse/PROJ-42"
    )
    assert ticket_url("https://jira.acme.test:8443/jira/", "QA2-7") == (
        "https://jira.acme.test:8443/jira/browse/QA2-7"
    )


@pytest.mark.parametrize(
    "base",
    [
        "http://acme.atlassian.net",
        "javascript:alert(1)//",
        "data:text/html,x",
        "//acme.atlassian.net",
        "/browse",
        "acme.atlassian.net",
        "https://user:pw@acme.atlassian.net",
        "https://acme.atlassian.net/?q=1",
        "https://acme.atlassian.net/../x",
        "https://-acme.atlassian.net",
        "https://acme.atlassian.net/a b",
    ],
)
def test_a_ticket_base_must_be_a_plain_https_address(base: str) -> None:
    with pytest.raises(ValueError):
        ticket_url(base, "PROJ-1")


@pytest.mark.parametrize("key", ["proj-1", "PROJ-0", "PROJ-", "PROJ-1)", "-1", "PROJ-1/../x"])
def test_a_ticket_key_must_be_a_key(key: str) -> None:
    with pytest.raises(ValueError):
        ticket_url("https://acme.atlassian.net", key)


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:acme/web-tests.git",
        "git@gitlab.com:acme/group/sub/service.git",
        "https://gitlab.com/acme/my%2Bteam/a~b.git",
        "https://alex:s3cret@github.com/acme/app.git?private_token=s3cret",
    ],
)
def test_every_link_is_https_and_inert_in_markdown(url: str) -> None:
    repo = project(url)
    for link in (repo.url, repo.commit_url(SHA), repo.request_url(3)):
        assert SAFE_LINK.fullmatch(link), link
        assert "s3cret" not in link
