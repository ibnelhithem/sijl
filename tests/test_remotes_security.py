"""Remote URLs are hostile input, proven end to end through the CLI:

- a host that would be an option to ssh (`-oProxyCommand=…`, `-F<file>`) never runs anything and
  is reported as unreadable;
- a password or token in a remote URL reaches no output — the report on stdout or in --out,
  stderr, logs, `sijl repos` (text and JSON), `config show`;
- a remote cannot inject a link: only https links Sijl builds from checked parts appear;
- the patterns that take a URL or a ticket key apart are linear on any text, whichever way they are
  called (the regex guard).
"""

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl import git as git_module
from sijl import remotes
from sijl.cli import app
from sijl.git import SshRunner
from tests.gitfactory import ME, PERIOD_ARGS, RepoBuilder
from tests.test_remotes_repo import at

runner = CliRunner()
needs_ssh = pytest.mark.skipif(shutil.which("ssh") is None, reason="ssh is not installed")
SECRET = "glpat-S3cr3tT0ken"


def pushed(make_repo, tmp_path: Path, name: str, *urls: str) -> RepoBuilder:
    """One pushed commit of its own; `origin` (then `r1`, `r2`, …) fetch from `urls`."""
    repo = make_repo(name)
    repo.commit(f"feat: work in {name}", at=at(4), files={"name.txt": name})
    repo.publish(tmp_path / "remotes" / f"{name}.git", "main")
    for index, url in enumerate(urls):
        remote = "origin" if index == 0 else f"r{index}"
        if index:
            repo.publish(tmp_path / "remotes" / f"{name}-{index}.git", "main", remote=remote)
        repo.git("remote", "set-url", remote, url)
    return repo


SSH_CALLS: list[list[str]] = []


class RecordingSsh(SshRunner):
    """The real SshRunner, with every ssh command line it starts recorded in SSH_CALLS."""

    def __init__(self, **kwargs) -> None:
        def record(argv, **options):
            SSH_CALLS.append(list(argv))
            return subprocess.run(argv, **options)

        super().__init__(runner=record, **kwargs)


@needs_ssh
def test_a_host_that_would_be_an_ssh_option_never_runs_anything(make_repo, tmp_path, monkeypatch):
    marker = tmp_path / "pwned"
    evil_config = tmp_path / "evil" / "evil_config"  # shipped inside the crafted repository
    evil = pushed(
        make_repo,
        tmp_path,
        "evil",
        "git@-oProxyCommand=touch pwned:acme/app.git",
        f"ssh://-F{evil_config}/acme/app.git",
        "git@-Fevil_config:acme/app.git",
        "ssh://git@-oLocalCommand=touch%20pwned:22/acme/app.git",
    )
    assert evil_config.parent == evil.path
    evil_config.write_text(f'Match exec "touch {marker}"\n  User x\n')
    # A legitimate alias in the same run, so ssh -G really is in use.
    git_module.SSH_CONFIG.write_text("Host github-alt\n  HostName github.com\n")
    pushed(make_repo, tmp_path, "mine", "git@github-alt:alexgh/mine.git")
    SSH_CALLS.clear()
    monkeypatch.setattr("sijl.cli.SshRunner", RecordingSsh)
    result = runner.invoke(
        app, ["report", "--root", str(tmp_path), "--author-email", ME.email, *PERIOD_ARGS]
    )
    assert result.exit_code == 0, result.output
    assert not marker.exists()
    assert [argv[-1] for argv in SSH_CALLS] == ["github-alt"]
    assert "(https://github.com/alexgh/mine/commit/" in result.stdout
    assert (
        "Remote URLs that could not be read as a project address, so no link comes from them (a "
        "URL is never shown: it can hold a password): `origin` in evil, `r1` in evil, `r2` in "
        "evil, `r3` in evil."
    ) in result.stdout
    assert "ProxyCommand" not in result.output and "evil_config" not in result.output


def every_output(tmp_path: Path, monkeypatch, caplog) -> str:
    """Everything Sijl writes for the repositories under `tmp_path`, in one string."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr("sijl.cli.SshRunner", lambda: SshRunner(runner=_no_ssh))
    base = ["--root", str(tmp_path / "repos")]
    out_file = tmp_path / "review.md"
    report = runner.invoke(
        app, ["report", *base, "--author-email", ME.email, *PERIOD_ARGS, "--out", str(out_file)]
    )
    assert report.exit_code == 0, report.output
    stdout_report = runner.invoke(app, ["report", *base, "--author-email", ME.email, *PERIOD_ARGS])
    listing = runner.invoke(app, ["repos", *base])
    as_json = runner.invoke(app, ["repos", "--json", *base])
    shown = runner.invoke(app, ["config", "show"])
    texts = [out_file.read_text(), caplog.text]
    for result in (report, stdout_report, listing, as_json, shown):
        texts += [result.stdout, result.stderr]
    return "\n".join(texts)


def _no_ssh(argv, **options):
    """ssh -G's answer for a host that is no alias: the host itself."""
    return subprocess.CompletedProcess(argv, 0, f"hostname {argv[-1]}\n".encode(), b"")


def test_credentials_in_remote_urls_reach_no_output(make_repo, tmp_path, monkeypatch, caplog):
    repos = tmp_path / "repos"
    pushed(make_repo, tmp_path, "repos/known",
           f"https://alex:{SECRET}@gitlab.com/acme/qa/known.git?private_token={SECRET}",
           f"https://oauth2:{SECRET}@github.com/acme/known.git")  # fmt: skip
    pushed(make_repo, tmp_path, "repos/token-user", f"https://{SECRET}@github.com/acme/tu.git")
    pushed(make_repo, tmp_path, "repos/unknown",
           f"https://alex:{SECRET}@git.acme.test/acme/unknown.git#{SECRET}",
           f"ssh://git:{SECRET}@work-gl:2222/acme/unknown.git")  # fmt: skip
    pushed(make_repo, tmp_path, "repos/unreadable",
           f"https://alex:{SECRET}@github.com/acme/team/too-deep.git",
           f"https://alex:{SECRET}@[bad/acme/x.git",
           f"{SECRET}:x@github.com:acme/app.git")  # fmt: skip
    assert repos.is_dir()
    text = every_output(tmp_path, monkeypatch, caplog)
    assert SECRET not in text
    assert "S3cr3t" not in text and "oauth2" not in text and "private_token" not in text
    # …while the report still says what it could: links, hosts, remote names.
    assert "(https://gitlab.com/acme/qa/known/-/commit/" in text
    assert "(https://github.com/acme/tu/commit/" in text
    assert "`git.acme.test` (unknown)" in text and "`work-gl` (unknown)" in text
    assert "`origin` in unreadable, `r1` in unreadable, `r2` in unreadable" in text


def test_a_secret_in_a_forge_setting_is_never_echoed(tmp_path) -> None:
    result = runner.invoke(
        app, ["config", "show"], env={"SIJL_GITLAB_HOSTS": f"https://oauth2:{SECRET}@git.x"}
    )
    assert result.exit_code == 2
    assert "forges.gitlab_hosts" in result.stderr and SECRET not in result.output


def test_a_remote_cannot_inject_a_link(make_repo, tmp_path) -> None:
    pushed(make_repo, tmp_path, "app",
           "https://github.com/acme/x)](javascript:alert(1))//.git",
           "git@github.com:acme/app<script>.git",
           "https://github.com/acme/app%29%5D%28data:text/html,x.git")  # fmt: skip
    result = runner.invoke(
        app, ["report", "--repo", str(tmp_path / "app"), "--author-email", ME.email, *PERIOD_ARGS]
    )
    assert result.exit_code == 0, result.output
    assert "javascript" not in result.stdout and "data:" not in result.stdout
    assert "<script>" not in result.stdout
    links = re.findall(r"\]\(([^)]*)\)", result.stdout)
    assert links == []  # no remote could be read, so no link at all


@pytest.mark.parametrize("name", ["_SCHEME", "_HELPER", "_TICKET_KEY"])
def test_patterns_used_at_the_start_of_a_text_stay_linear_even_under_search(name: str) -> None:
    # Each is only ever matched at the start of a URL or a key, but without an anchor `search`
    # would restart at every position of a long run of letters: quadratic (the regex guard).
    pattern = getattr(remotes, name)
    for text in ("a" * 200_000, "A" * 200_000, "A1" * 100_000):
        started = time.perf_counter()
        pattern.search(text)
        assert time.perf_counter() - started < 0.2


def test_the_anchored_patterns_still_read_what_they_read() -> None:
    assert remotes._SCHEME.match("git+ssh://git@host/x").group(1) == "git+ssh"
    assert remotes._SCHEME.match("x git://h") is None
    assert remotes._HELPER.match("codecommit::eu-west-1://repo")
    assert remotes._TICKET_KEY.fullmatch("PROJ_2-12") and not remotes._TICKET_KEY.fullmatch("B-0")
