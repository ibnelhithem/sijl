"""Links for deliveries (built by remotes.WebRepo): a GitLab merge request links into the
project its merge commit names — when a remote is that project — and a GitHub pull request into
the one GitHub project whose branches hold its merge. Anything else is shown by number only, and
counted: a wrong link is worse than none."""

from pathlib import Path

from sijl.git import GitRunner
from sijl.remotes import Forges, HostResolver, link_commits
from tests.gitfactory import COLLEAGUE, ME, RepoBuilder
from tests.test_deliveries import at, branch, run, started


def hosted(repo: RepoBuilder, tmp_path: Path, url: str, remote: str = "origin") -> None:
    """Push every branch to a stand-in remote, then point the remote at `url`."""
    repo.publish(tmp_path / "remotes" / f"{repo.path.name}-{remote}.git", "--all", remote=remote)
    repo.git("remote", "set-url", remote, url)


def links(*paths: Path, git: GitRunner | None = None, forges: Forges | None = None):
    git = git or GitRunner()
    result = run(*paths, git=git)
    return result, link_commits(result.repos, git, forges or Forges(), HostResolver(None))


def test_a_merge_request_links_into_the_project_its_merge_commit_names(make_repo, tmp_path):
    repo = started(make_repo)
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=42, title="X", at=at(6))  # acme/platform/app
    hosted(repo, tmp_path, "git@gitlab.com:ACME/platform/app.git")  # GitLab paths ignore case
    result, found = links(repo.path)
    [rc] = result.repos
    [d] = rc.deliveries
    assert found.request_url(rc.repo.path, d) == (
        "https://gitlab.com/ACME/platform/app/-/merge_requests/42"
    )
    assert found.of(rc.repo.path).unlinked_requests == 0


def test_a_pull_request_links_into_the_github_project_holding_it(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "fix/typo", (4, ME))
    repo.github_pr_merge("fix/typo", number=12, title="Fix", at=at(6))
    hosted(repo, tmp_path, "git@github.com:alexgh/app.git")
    result, found = links(repo.path)
    [rc] = result.repos
    assert (
        found.request_url(rc.repo.path, rc.deliveries[0]) == "https://github.com/alexgh/app/pull/12"
    )


def test_merged_for_others_is_linked_even_with_no_commit_of_yours(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/theirs", (4, COLLEAGUE))
    repo.gitlab_mr_merge("feature/theirs", number=7, title="Theirs", at=at(6))
    hosted(repo, tmp_path, "https://gitlab.com/acme/platform/app.git")
    result, found = links(repo.path)
    [rc] = result.repos
    assert rc.commits == ()
    assert found.request_url(rc.repo.path, rc.deliveries[0]).endswith("/-/merge_requests/7")


def test_a_merge_request_naming_another_project_gets_no_link(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=8, title="X", at=at(6), project="acme/other")
    hosted(repo, tmp_path, "git@gitlab.com:acme/platform/app.git")
    result, found = links(repo.path)
    [rc] = result.repos
    assert found.request_url(rc.repo.path, rc.deliveries[0]) == ""
    assert found.of(rc.repo.path).unlinked_requests == 1


def test_an_unknown_host_gets_no_link_until_it_is_configured(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    branch(repo, "feature/x", (4, ME))
    repo.gitlab_mr_merge("feature/x", number=9, title="X", at=at(6))
    hosted(repo, tmp_path, "git@git.acme.test:acme/platform/app.git")
    result, found = links(repo.path)
    [rc] = result.repos
    assert found.request_url(rc.repo.path, rc.deliveries[0]) == ""
    assert found.of(rc.repo.path).unlinked_requests == 1
    _, configured = links(repo.path, forges=Forges.from_hosts(gitlab=["git.acme.test"]))
    assert configured.request_url(rc.repo.path, rc.deliveries[0]) == (
        "https://git.acme.test/acme/platform/app/-/merge_requests/9"
    )


def test_a_pull_request_a_fork_and_its_upstream_both_hold_gets_no_link(make_repo, tmp_path):
    repo = started(make_repo)
    branch(repo, "fix/typo", (4, ME))
    repo.github_pr_merge("fix/typo", number=13, title="Fix", at=at(6))
    hosted(repo, tmp_path, "git@github.com:alexgh/app.git")
    hosted(repo, tmp_path, "https://github.com/acme/app.git", remote="upstream")
    result, found = links(repo.path)
    [rc] = result.repos
    assert found.request_url(rc.repo.path, rc.deliveries[0]) == ""  # #13 is one of theirs only
    assert found.of(rc.repo.path).unlinked_requests == 1


def test_a_gitlab_mirror_does_not_stop_a_pull_request_linking_to_github(make_repo, tmp_path):
    repo = started(make_repo)
    branch(repo, "fix/typo", (4, ME))
    repo.github_pr_merge("fix/typo", number=14, title="Fix", at=at(6))
    hosted(repo, tmp_path, "git@gitlab.com:alexgh/app.git")  # origin: a copy of main
    hosted(repo, tmp_path, "git@github.com:alexgh/app.git", remote="github")
    result, found = links(repo.path)
    [rc] = result.repos
    assert (
        found.request_url(rc.repo.path, rc.deliveries[0]) == "https://github.com/alexgh/app/pull/14"
    )


def test_a_pull_request_linked_in_a_repository_with_one_github_project_is_disclosed(
    make_repo, tmp_path
) -> None:
    # A clone of your fork alone, synced from upstream: #12 was merged upstream, but git cannot
    # tell the fork's history from upstream's. The link stays (one GitHub project holds it), and
    # the number is counted as one only the GitHub API can confirm.
    repo = started(make_repo)
    branch(repo, "fix", (4, ME))
    repo.github_pr_merge("fix", number=12, title="Fix", at=at(6), who=COLLEAGUE)
    hosted(repo, tmp_path, "git@github.com:alexgh/app.git")
    result, found = links(repo.path)
    [rc] = result.repos
    assert (
        found.request_url(rc.repo.path, rc.deliveries[0]) == "https://github.com/alexgh/app/pull/12"
    )
    assert found.of(rc.repo.path).pulls_unconfirmed == 1


def test_a_second_github_project_that_lacks_the_merge_settles_it(make_repo, tmp_path) -> None:
    repo = started(make_repo)
    hosted(repo, tmp_path, "https://github.com/acme/app.git", remote="upstream")  # main only
    branch(repo, "fix", (4, ME))
    repo.github_pr_merge("fix", number=13, title="Fix", at=at(6))
    hosted(repo, tmp_path, "git@github.com:alexgh/app.git")
    result, found = links(repo.path)
    [rc] = result.repos
    assert (
        found.request_url(rc.repo.path, rc.deliveries[0]) == "https://github.com/alexgh/app/pull/13"
    )
    assert found.of(rc.repo.path).pulls_unconfirmed == 0
