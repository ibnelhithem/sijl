"""sijl.remotes, parsing: a remote URL becomes transport, host, port and project path — every
shape git accepts for a hosted project, nested GitLab groups kept whole, and nothing secret
(user names, passwords, tokens, query strings) kept at all. What is not a hosted project says
why, without ever repeating the URL."""

import pytest

from sijl.remotes import (
    LOCAL,
    OTHER_TRANSPORT,
    UNREADABLE,
    NotAWebRemote,
    RemoteURL,
    parse_remote_url,
)

SECRET = "s3cr3t-T0KEN"

# The four common shapes, ssh:// with a port, git+ssh, and a three-level
# GitLab group (group / subgroup / subgroup / project).
URL_TABLE = [
    (
        "git@github.com:acme/web-tests.git",
        RemoteURL("ssh", "github.com", None, ("acme", "web-tests")),
    ),
    (
        "git@gitlab.com:acme/team/web-tests.git",
        RemoteURL("ssh", "gitlab.com", None, ("acme", "team", "web-tests")),
    ),
    (
        "https://github.com/alexgh/sijl.git",
        RemoteURL("https", "github.com", None, ("alexgh", "sijl")),
    ),
    (
        "git@github-alt:alexgh/notes-app.git",
        RemoteURL("ssh", "github-alt", None, ("alexgh", "notes-app")),
    ),
    (
        "ssh://git@gitlab.acme.test:2222/platform/tools/sijl.git",
        RemoteURL("ssh", "gitlab.acme.test", 2222, ("platform", "tools", "sijl")),
    ),
    (
        "git+ssh://git@github.com/acme/web-tests.git",
        RemoteURL("ssh", "github.com", None, ("acme", "web-tests")),
    ),
    (
        "ssh+git://git@github.com/acme/web-tests",
        RemoteURL("ssh", "github.com", None, ("acme", "web-tests")),
    ),
    (
        "git@gitlab.com:acme/group/sub/service.git",
        RemoteURL("ssh", "gitlab.com", None, ("acme", "group", "sub", "service")),
    ),
    (
        "https://gitlab.com/acme/group/sub/service",
        RemoteURL("https", "gitlab.com", None, ("acme", "group", "sub", "service")),
    ),
    (
        "https://gitlab.acme.test:8443/acme/group/sub/service.git/",
        RemoteURL("https", "gitlab.acme.test", 8443, ("acme", "group", "sub", "service")),
    ),
    (
        "http://git.acme.test/team/app.git",
        RemoteURL("http", "git.acme.test", None, ("team", "app")),
    ),
    (
        "git://github.com/acme/web-tests.git",
        RemoteURL("git", "github.com", None, ("acme", "web-tests")),
    ),
    (
        "GitHub.COM:acme/web-tests.git",  # scp-like without a user; host names ignore case
        RemoteURL("ssh", "github.com", None, ("acme", "web-tests")),
    ),
    (
        "git@github.com:/acme/web-tests.git",  # a leading slash in an scp-like path
        RemoteURL("ssh", "github.com", None, ("acme", "web-tests")),
    ),
    (
        "ssh://git@github.com/acme/web-tests.git/",
        RemoteURL("ssh", "github.com", None, ("acme", "web-tests")),
    ),
]


@pytest.mark.parametrize(("url", "expected"), URL_TABLE, ids=[u for u, _ in URL_TABLE])
def test_the_url_table(url: str, expected: RemoteURL) -> None:
    assert parse_remote_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        f"https://alex:{SECRET}@github.com/acme/web-tests.git",
        f"https://oauth2:{SECRET}@gitlab.com/acme/team/web-tests.git",
        f"https://{SECRET}@github.com/acme/web-tests.git",  # a token as the user name
        f"https://{SECRET}:x-oauth-basic@github.com/acme/web-tests.git",
        f"https://gitlab.com/acme/team/web-tests.git?private_token={SECRET}",
        f"https://gitlab.com/acme/team/web-tests.git#{SECRET}",
        f"ssh://git:{SECRET}@github.com/acme/web-tests.git",
        f"https://alex:{SECRET}@gitlab.com:443/acme/team/web-tests.git?private_token={SECRET}",
    ],
)
def test_credentials_are_dropped_from_every_shape(url: str) -> None:
    parsed = parse_remote_url(url)
    assert SECRET not in repr(parsed)
    assert parsed.path[-1] == "web-tests"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("/srv/git/web-tests.git", LOCAL),
        ("../web-tests", LOCAL),
        ("./web-tests.git", LOCAL),
        ("web-tests", LOCAL),
        ("file:///srv/git/web-tests.git", LOCAL),
        ("C:/repos/web-tests", LOCAL),
        ("C:\\repos\\web-tests", LOCAL),
        ("ext::sh -c touch% /tmp/pwned", OTHER_TRANSPORT),  # a remote helper: never run here
        ("codecommit::eu-west-1://web-tests", OTHER_TRANSPORT),
        ("ftp://example.test/web-tests.git", OTHER_TRANSPORT),
        ("persistent-https://example.test/web-tests", OTHER_TRANSPORT),
        ("", UNREADABLE),
        ("git@-oProxyCommand=touch pwned:acme/app.git", UNREADABLE),  # `-` first: never a host
        ("ssh://-oProxyCommand=touch%20pwned/acme/app.git", UNREADABLE),
        ("git@-Fevil_config:acme/app.git", UNREADABLE),
        ("git@-F/tmp/evil_config:acme/app.git", LOCAL),  # a slash before the colon: a folder
        ("ssh://git@[::1]:22/acme/app.git", UNREADABLE),  # IPv6 literals are not supported
        ("ssh://git@github.com:99999/acme/app.git", UNREADABLE),  # no such port
        ("ssh://git@github.com:22x/acme/app.git", UNREADABLE),
        ("https://github.com/", UNREADABLE),  # no project
        ("https://github.com/.git", UNREADABLE),
        ("https://github.com/acme/../app.git", UNREADABLE),
        ("https://github.com/acme/a%2Fb.git", UNREADABLE),  # an encoded slash inside a name
        ("https://github.com/acme/app)](javascript:alert(1)", UNREADABLE),
        ("git@github.com:acme/app name.git", UNREADABLE),
        ("git@github.com:acme/app\x1b]0;x\x07.git", UNREADABLE),
        ("git@git\nhub.com:acme/app.git", UNREADABLE),
        (f"user:{SECRET}@github.com:acme/app.git", UNREADABLE),  # not scp-like at all
    ],
)
def test_what_is_not_a_hosted_project_says_why(url: str, reason: str) -> None:
    with pytest.raises(NotAWebRemote) as raised:
        parse_remote_url(url)
    assert raised.value.reason == reason
    assert str(raised.value) == reason  # the message never repeats the URL
    assert SECRET not in str(raised.value)


def test_a_percent_encoded_https_path_is_decoded_before_it_is_checked() -> None:
    parsed = parse_remote_url("https://gitlab.com/acme/my%2Bteam/app.git")
    assert parsed.path == ("acme", "my+team", "app")


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example?@github.com/acme/app.git",
        "https://evil.example#@github.com/acme/app.git",
        "https://evil.example\\@github.com/acme/app.git",
        "ssh://evil.example#@github.com/acme/app.git",
        "https://evil.example/?@github.com/acme/app.git",
    ],
)
def test_the_host_ends_where_git_and_urlsplit_end_it(url: str) -> None:
    # "@" after a "?", "#" or "\\" is not a user name's end. git (and
    # urlsplit) would fetch from evil.example, so these must never read as github.com.
    try:
        parsed = parse_remote_url(url)
    except NotAWebRemote as exc:
        assert exc.reason == UNREADABLE
    else:
        assert parsed.host == "evil.example"


def test_a_password_holding_an_at_sign_still_ends_at_the_last_one() -> None:
    parsed = parse_remote_url(f"https://alex:p@ss{SECRET}@github.com/acme/app.git")
    assert (parsed.host, parsed.path) == ("github.com", ("acme", "app"))
