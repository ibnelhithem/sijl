"""sijl.remotes on real repositories: which remotes a repository has (asked from git, rewrite
rules applied by git), and which project each of your commits links to — the first remote, origin
first, whose remote-tracking branches hold it."""

from pathlib import Path

from sijl.collect import collect
from sijl.discover import discover
from sijl.git import GitError, GitRunner
from sijl.identity import IdentitySet
from sijl.remotes import (
    UNKNOWN_HOST,
    Forges,
    HostResolver,
    Links,
    Remote,
    RepoLinks,
    link_commits,
    read_remotes,
)
from tests.gitfactory import ME, REVIEW_PERIOD, RepoBuilder
from tests.test_remotes_resolve import FakeSsh

MINE = IdentitySet.from_emails([ME.email])


def at(day: int) -> str:
    return f"2026-05-{day:02d}T10:00:00+02:00"


def collected(*paths: Path):
    git = GitRunner()
    return collect(discover([], list(paths), git).repos, MINE, REVIEW_PERIOD, git)


def two_remotes(make_repo, tmp_path: Path) -> RepoBuilder:
    """main on both remotes; a feature branch pushed only to `github`; one commit never pushed."""
    repo = make_repo("sijl")
    repo.commit("feat: on main", at=at(4))
    repo.publish(tmp_path / "remotes" / "gitlab.git", "main")
    repo.publish(tmp_path / "remotes" / "github.git", "main", remote="github")
    repo.branch("feat/links")
    repo.switch("feat/links")
    repo.commit("feat: only on github", at=at(5))
    repo.publish(tmp_path / "remotes" / "github.git", "feat/links", remote="github")
    repo.commit("feat: never pushed", at=at(6))
    repo.switch("main")
    return repo


def test_each_repository_keeps_its_remote_tracking_refs(make_repo, tmp_path) -> None:
    repo = two_remotes(make_repo, tmp_path)
    [rc] = collected(repo.path).repos
    names = [name for name, _ in rc.remote_refs]
    assert names == [
        "refs/remotes/github/feat/links",
        "refs/remotes/github/main",
        "refs/remotes/origin/main",
    ]
    assert (
        dict(rc.remote_refs)["refs/remotes/github/feat/links"]
        == repo.git("rev-parse", "github/feat/links").strip()
    )


class Spy(GitRunner):
    """A real GitRunner that records the git commands it runs."""

    def __init__(self, fail: str = "") -> None:
        super().__init__()
        self.commands: list[tuple[str, ...]] = []
        self.fail = fail

    def run(self, cwd, *args, stdin=None):
        self.commands.append(args)
        if self.fail and args[0] == self.fail:
            raise GitError(args, 128, "fatal: bad config line 1 in file .git/config\n")
        return super().run(cwd, *args, stdin=stdin)


def link(*repos: RepoBuilder, git: GitRunner | None = None, ssh=None, forges=None) -> Links:
    collection = collected(*(r.path for r in repos))
    return link_commits(
        collection.repos, git or GitRunner(), forges or Forges(), HostResolver(ssh or FakeSsh())
    )


def by_subject(repo: RepoBuilder, links: Links) -> dict[str, str]:
    """subject → the commit's link, for every counted commit of `repo`."""
    [rc] = collected(repo.path).repos
    return {c.subject: links.commit_url(rc.repo.path, c.sha) for c in rc.commits}


def test_remotes_are_asked_from_git_in_name_order(make_repo) -> None:
    repo = make_repo("app")
    repo.git("remote", "add", "origin", "git@gitlab.com:acme/qa/app.git")
    repo.git("remote", "add", "my.fork", "https://github.com/alexgh/app.git")
    repo.git("config", "--add", "remote.origin.url", "git@github.com:acme/second.git")
    repo.git("config", "remote.origin.pushurl", "git@github.com:acme/push-only.git")
    spy = Spy()
    assert read_remotes(spy, repo.path) == [
        ("my.fork", "https://github.com/alexgh/app.git"),
        ("origin", "git@gitlab.com:acme/qa/app.git"),  # git fetches from the first url
    ]
    assert spy.commands == [
        ("config", "--null", "--get-regexp", r"^(remote\..*\.url|url\..*\.insteadof)$")
    ]


def test_no_remotes(make_repo) -> None:
    assert read_remotes(GitRunner(), make_repo("app").path) == []


def test_insteadof_rules_are_applied_by_git(make_repo, hermetic_git) -> None:
    repo = make_repo("app")
    repo.git("remote", "add", "origin", "gh:acme/app.git")
    repo.git("remote", "add", "work", "work:platform/app.git")
    repo.git("config", "url.git@github.com:.insteadOf", "gh:")
    hermetic_git.write_text('[url "git@gitlab.com:"]\n\tinsteadOf = work:\n')  # global config
    spy = Spy()
    assert read_remotes(spy, repo.path) == [
        ("origin", "git@github.com:acme/app.git"),
        ("work", "git@gitlab.com:platform/app.git"),
    ]
    assert ("remote", "get-url", "--", "origin") in spy.commands


def test_each_commit_links_to_the_first_remote_holding_it_origin_first(make_repo, tmp_path):
    repo = two_remotes(make_repo, tmp_path)
    repo.git("remote", "set-url", "origin", "git@gitlab.com:acme/mirror/sijl.git")
    repo.git("remote", "set-url", "github", "git@github-alt:alexgh/sijl.git")
    links = link(repo, ssh=FakeSsh({"github-alt": "github.com"}))
    on_main = repo.git("rev-parse", "main").strip()
    on_github = repo.git("rev-parse", "github/feat/links").strip()
    assert by_subject(repo, links) == {
        "feat: on main": f"https://gitlab.com/acme/mirror/sijl/-/commit/{on_main}",
        "feat: only on github": f"https://github.com/alexgh/sijl/commit/{on_github}",
    }
    [repo_links] = links.repos.values()
    assert [p.label for p in repo_links.projects] == [
        "gitlab.com/acme/mirror/sijl",
        "github.com/alexgh/sijl",
    ]
    assert repo_links.unlinked == 0
    assert [r.name for r in repo_links.remotes] == ["origin", "github"]
    assert repo_links.remotes[1].alias == "github-alt"


def test_one_remote_holding_every_branch_costs_no_extra_git_call(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: pushed", at=at(4))
    repo.publish(tmp_path / "remote.git", "main")
    repo.git("remote", "set-url", "origin", "https://github.com/acme/app.git")
    spy = Spy()
    links = link(repo, git=spy)
    assert [c[0] for c in spy.commands] == ["config"]
    assert set(by_subject(repo, links).values()) == {
        f"https://github.com/acme/app/commit/{repo.head()}"
    }


def test_commits_on_a_remote_of_an_unknown_host_stay_plain(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: pushed", at=at(4))
    repo.publish(tmp_path / "remote.git", "main")
    repo.git("remote", "set-url", "origin", "git@git.acme.test:platform/app.git")
    links = link(repo)
    assert by_subject(repo, links) == {"feat: pushed": ""}
    [repo_links] = links.repos.values()
    assert repo_links.unlinked == 1
    assert repo_links.remotes == (Remote("origin", problem=UNKNOWN_HOST, host="git.acme.test"),)


def test_a_configured_self_hosted_gitlab_links(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: pushed", at=at(4))
    repo.publish(tmp_path / "remote.git", "main")
    repo.git("remote", "set-url", "origin", "ssh://git@git.acme.test:2222/platform/tools/app.git")
    links = link(repo, forges=Forges.from_hosts(gitlab=["git.acme.test"]))
    assert by_subject(repo, links) == {
        "feat: pushed": f"https://git.acme.test/platform/tools/app/-/commit/{repo.head()}"
    }


def test_refs_of_a_removed_remote_hold_commits_no_link_is_built_for(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: on both", at=at(4))
    repo.publish(tmp_path / "remote.git", "main")
    repo.commit("feat: only on the old remote", at=at(5))
    repo.publish(tmp_path / "old.git", "main", remote="old")
    repo.git("remote", "set-url", "origin", "https://github.com/acme/app.git")
    repo.git("config", "--remove-section", "remote.old")  # its tracking refs stay behind
    assert repo.git("for-each-ref", "refs/remotes/old")  # still there
    links = link(repo)
    linked = by_subject(repo, links)
    assert linked["feat: on both"].startswith("https://github.com/acme/app/commit/")
    assert linked["feat: only on the old remote"] == ""
    assert next(iter(links.repos.values())).unlinked == 1


def test_a_remote_name_with_a_slash_owns_only_its_own_refs(make_repo, tmp_path) -> None:
    # refs/remotes/team/fork/main belongs to the remote "team/fork", not to "team": otherwise the
    # second commit would be "held" by team and linked to the wrong project.
    repo = make_repo("app")
    repo.commit("feat: on team", at=at(4))
    repo.publish(tmp_path / "team.git", "main", remote="team")
    repo.commit("feat: on team/fork", at=at(5))
    # git 2.5x refuses `git remote add team/fork`, but repositories made by older git still have
    # such remotes: set one up the way it looks on disk — its URL and its remote-tracking ref.
    repo.git("config", "remote.team/fork.url", "https://github.com/alexgh/app.git")
    repo.git("update-ref", "refs/remotes/team/fork/main", repo.head())
    repo.git("remote", "set-url", "team", "https://gitlab.com/acme/app.git")
    linked = by_subject(repo, link(repo))
    assert linked["feat: on team"].startswith("https://gitlab.com/acme/app/-/commit/")
    assert linked["feat: on team/fork"].startswith("https://github.com/alexgh/app/commit/")


def test_a_repository_without_remote_branches_has_no_links(make_repo) -> None:
    repo = make_repo("app")
    repo.commit("feat: local project", at=at(4))
    repo.git("remote", "add", "origin", "https://github.com/acme/app.git")  # never fetched
    links = link(repo)
    assert by_subject(repo, links) == {"feat: local project": ""}
    assert next(iter(links.repos.values())).unlinked == 0  # Caveats already name such repos


def test_a_repository_whose_remotes_cannot_be_read_says_so(make_repo, tmp_path) -> None:
    repo = make_repo("app")
    repo.commit("feat: pushed", at=at(4))
    repo.publish(tmp_path / "remote.git", "main")
    links = link(repo, git=Spy(fail="config"))
    assert next(iter(links.repos.values())) == RepoLinks(
        error="bad config line 1 in file .git/config"
    )


def test_repositories_without_counted_commits_are_not_asked(make_repo) -> None:
    quiet = make_repo("quiet")
    quiet.commit("feat: before the period", at="2026-01-04T10:00:00+02:00")
    spy = Spy()
    assert link(quiet, git=spy).repos == {}
    assert spy.commands == []
