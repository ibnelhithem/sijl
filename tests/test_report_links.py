"""Links in the Markdown report: each counted commit's SHA links to the project that holds
it, each repository names the projects it links to, and anything that gets no link — an unknown
host, a remote that is a folder or cannot be read — is a plain SHA with a Caveats line."""

import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl import git as git_module
from sijl.cli import app
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.remotes import BUDGET_SPENT, Forges
from sijl.report import Report, build_report, caveats, render_markdown
from tests.gitfactory import ME, PERIOD_ARGS, REVIEW_PERIOD, RepoBuilder
from tests.test_config import config_file
from tests.test_remotes_repo import Spy, at
from tests.test_remotes_resolve import FakeSsh

runner = CliRunner()
needs_ssh = pytest.mark.skipif(shutil.which("ssh") is None, reason="ssh is not installed")
STANDING = (
    "A commit's SHA links to the GitHub or GitLab project of the first remote whose branches hold "
    "it (`origin` first); commits on no remote branch, and the SHAs of copies, are not linked."
)


def pushed(make_repo, tmp_path: Path, url: str, name: str = "app") -> RepoBuilder:
    """A repository with one pushed commit and one never pushed, `origin` pointing at `url`."""
    repo = make_repo(name)
    repo.commit("feat: pushed", at=at(4), files={"name.txt": name})  # its own SHA per repository
    repo.publish(tmp_path / "remotes" / f"{name}.git", "main")
    repo.git("remote", "set-url", "origin", url)
    repo.branch("wip")
    repo.switch("wip")
    repo.commit("feat: not pushed", at=at(5))
    repo.switch("main")
    return repo


def build(*repos: RepoBuilder, git=None, ssh=None, forges=None) -> Report:
    return build_report(
        roots=[],
        paths=[r.path for r in repos],
        identities=IdentitySet.from_emails([ME.email]),
        window=REVIEW_PERIOD,
        git=git or GitRunner(),
        generated_on=date(2026, 10, 8),
        forges=forges,
        ssh=ssh,
    )


def test_links_appear_in_the_markdown_report(make_repo, tmp_path) -> None:
    repo = pushed(make_repo, tmp_path, "git@gitlab.com:acme/group/sub/app.git")
    sha = repo.git("rev-parse", "main").strip()
    wip = repo.git("rev-parse", "wip").strip()
    text = render_markdown(build(repo))
    project = "https://gitlab.com/acme/group/sub/app"
    assert f"| [`{sha[:8]}`]({project}/-/commit/{sha}) | feat: pushed |" in text
    assert f"`{repo.path}` · [gitlab.com/acme/group/sub/app]({project})" in text
    assert f"| `{wip[:8]}` | local branch | feat: not pushed |" in text  # never pushed: no link
    assert STANDING in caveats(build(repo))


def test_an_unknown_host_falls_back_to_plain_shas_with_a_caveat(make_repo, tmp_path) -> None:
    repo = pushed(make_repo, tmp_path, "git@git.acme.test:platform/app.git")
    sha = repo.git("rev-parse", "main").strip()
    report = build(repo, ssh=FakeSsh())  # ssh says: no alias, the host is itself
    text = render_markdown(report)
    assert f"| `{sha[:8]}` | feat: pushed |" in text
    assert "](https://" not in text
    lines = caveats(report)
    assert (
        "1 commit on remote branches has no link, only its SHA: no remote holding it is a GitHub "
        "or GitLab project Sijl recognises — app (1)."
    ) in lines
    assert (
        "Hosts Sijl does not know as GitHub or GitLab, so commits there have no link: "
        "`git.acme.test` (app). A GitHub Enterprise or self-hosted GitLab host can be added with "
        "`forges.github_hosts` or `forges.gitlab_hosts`."
    ) in lines


def test_a_configured_host_gets_links(make_repo, tmp_path) -> None:
    repo = pushed(make_repo, tmp_path, "git@git.acme.test:platform/app.git")
    text = render_markdown(build(repo, forges=Forges.from_hosts(gitlab=["git.acme.test"])))
    assert "(https://git.acme.test/platform/app/-/commit/" in text


def test_aliases_are_named_where_they_were_resolved_or_not(make_repo, tmp_path) -> None:
    mine = pushed(make_repo, tmp_path, "git@github-alt:alexgh/app.git", name="mine")
    work = pushed(make_repo, tmp_path, "git@work-gl:platform/app.git", name="work")
    ssh = FakeSsh({"github-alt": "github.com", "work-gl": "git.acme.test"})
    lines = caveats(build(mine, work, ssh=ssh))
    assert "ssh host aliases resolved with `ssh -G`: `github-alt` → `github.com`." in lines
    assert any("`work-gl` → `git.acme.test` (work)" in line for line in lines), lines
    lines = caveats(build(mine, ssh=FakeSsh(error="timed out after 5 s")))
    assert any("`github-alt` (not resolved: timed out after 5 s) (mine)" in x for x in lines)


def test_remotes_that_are_folders_or_cannot_be_read_are_named(make_repo, tmp_path) -> None:
    folder = pushed(make_repo, tmp_path, str(tmp_path / "remotes" / "folder.git"), name="folder")
    odd = pushed(make_repo, tmp_path, "https://github.com/acme/team/odd.git", name="odd")
    lines = caveats(build(folder, odd))
    assert (
        "Remotes that are not projects on a web host, so no link comes from them: `origin` in "
        "folder (a folder on this machine)."
    ) in lines
    assert (
        "Remote URLs that could not be read as a project address, so no link comes from them (a "
        "URL is never shown: it can hold a password): `origin` in odd."
    ) in lines


def test_remotes_that_git_cannot_list_are_a_caveat(make_repo, tmp_path) -> None:
    repo = pushed(make_repo, tmp_path, "git@github.com:acme/app.git")
    lines = caveats(build(repo, git=Spy(fail="config")))
    assert (
        "Remotes could not be read in app (bad config line 1 in file .git/config); commits there "
        "have no link."
    ) in lines


def test_the_cli_uses_the_forge_settings(make_repo, tmp_path) -> None:
    pushed(make_repo, tmp_path, "git@git.acme.test:platform/app.git")
    config_file('[forges]\ngitlab_hosts = ["git.acme.test"]\n')
    result = runner.invoke(
        app, ["report", "--repo", str(tmp_path / "app"), "--author-email", ME.email, *PERIOD_ARGS]
    )
    assert result.exit_code == 0, result.output
    assert "(https://git.acme.test/platform/app/-/commit/" in result.stdout


@needs_ssh
def test_the_cli_resolves_aliases_through_ssh(make_repo, tmp_path) -> None:
    git_module.SSH_CONFIG.write_text("Host github-alt\n  HostName github.com\n")
    pushed(make_repo, tmp_path, "git@github-alt:alexgh/app.git")
    args = ["report", "--repo", str(tmp_path / "app"), "--author-email", ME.email, *PERIOD_ARGS]
    result = runner.invoke(app, args)
    assert "(https://github.com/alexgh/app/commit/" in result.stdout
    result = runner.invoke(app, args, env={"SIJL_SSH_ALIASES": "false"})
    assert "](https://" not in result.stdout
    assert "`github-alt` (not resolved: forges.ssh_aliases is false)" in result.stdout


def test_hosts_beyond_the_ssh_budget_are_named_as_not_asked(make_repo, tmp_path) -> None:
    repo = pushed(make_repo, tmp_path, "git@host0:acme/app.git", name="many")
    for n in range(1, 9):
        repo.git("remote", "add", f"r{n}", f"git@host{n}:acme/app.git")
    ssh = FakeSsh()
    lines = caveats(build(repo, ssh=ssh))
    assert len(ssh.asked) == 8
    [hosts] = [line for line in lines if line.startswith("Hosts Sijl does not know")]
    assert f"`host8` (not resolved: {BUDGET_SPENT}) (many)" in hosts
