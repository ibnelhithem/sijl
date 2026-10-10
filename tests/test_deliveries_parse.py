"""sijl.deliveries, the message rules: which merge commits are GitLab merge requests or
GitHub pull requests — number, title, branch, project — and which are sync merges that bring a
mainline into a branch. Pure functions over a commit; no git."""

import pytest

from sijl.deliveries import Request, is_back_merge, is_long_lived, is_sync_merge, parse_request
from sijl.models import GITHUB, GITLAB
from tests.gitfactory import raw_commit

TWO_PARENTS = ("1" * 40, "2" * 40)


def merge(text: str):
    """A merge commit whose full message is `text`."""
    return raw_commit(parents=TWO_PARENTS, subject=text.split("\n", 1)[0], body=text)


def test_a_gitlab_merge_request_gives_number_title_branch_and_project() -> None:
    text = (
        "Merge branch 'feature/PROJ-12-report' into 'main'\n\nAdd the x report\n\n"
        "Closes #4\n\nSee merge request acme/platform/app!42\n"
    )
    assert parse_request(merge(text)) == Request(
        forge=GITLAB,
        number=42,
        title="Add the x report",
        branch="feature/PROJ-12-report",
        project=("acme", "platform", "app"),
        target="main",
    )


def test_a_github_pull_request_gives_number_title_and_branch() -> None:
    text = "Merge pull request #12 from alexgh/fix/typo\n\nFix a typo\n"
    assert parse_request(merge(text)) == Request(GITHUB, 12, "Fix a typo", "fix/typo", ())


def test_titles_may_be_missing_and_lose_stray_carriage_returns() -> None:
    assert parse_request(merge("Merge pull request #3 from alexgh/x\n")).title == ""
    gitlab = "Merge branch 'x' into 'main'\r\n\r\nTitle\r\n\r\nSee merge request acme/app!3\r\n"
    assert parse_request(merge(gitlab)).title == "Title"
    assert (
        parse_request(merge("Merge branch 'x' into 'main'\n\nSee merge request a/b!3\n")).title
        == ""
    )


def test_a_custom_gitlab_template_keeps_the_subject_as_the_title() -> None:
    request = parse_request(merge("Add the x report\n\nSee merge request acme/app!7\n"))
    assert (request.title, request.branch, request.number) == ("Add the x report", "", 7)


def test_an_old_gitlab_reference_names_no_project() -> None:
    request = parse_request(
        merge("Merge branch 'x' into 'main'\n\nTitle\n\nSee merge request !7\n")
    )
    assert (request.forge, request.number, request.project) == (GITLAB, 7, ())


def test_the_last_merge_request_line_wins_and_github_subjects_come_first() -> None:
    quoted = (
        "Merge branch 'x' into 'main'\n\nTitle\n\nReverts what See merge request a/b!1 did\n"
        "See merge request a/old!1\n\nSee merge request a/b!2\n"
    )
    assert (parse_request(merge(quoted)).project, parse_request(merge(quoted)).number) == (
        ("a", "b"),
        2,
    )
    mirrored = "Merge pull request #5 from alexgh/x\n\nTitle\n\nSee merge request a/b!9\n"
    assert parse_request(merge(mirrored)).forge == GITHUB


@pytest.mark.parametrize(
    "text",
    [
        "Merge branch 'feature/x' into 'main'\n",  # a merge, but no request
        "Merge pull request #0 from alexgh/x\n",  # no request is number 0
        "Merge pull request #1234567890 from alexgh/x\n",  # more digits than any forge has
        "Merge pull request #12 from alexgh/x and more\n",
        "Merge branch 'x' into 'main'\n\nSee merge request ../evil!1\n",
        "Merge branch 'x' into 'main'\n\nSee merge request a/./b!1\n",
        "Merge branch 'x' into 'main'\n\nSee merge request a/b!1 trailing\n",
        "Merge branch 'x' into 'main'\n\nSee merge request a b!1\n",
        "Merge branch 'x' into 'main'\n\nsee merge request a/b!1\n",
    ],
)
def test_anything_else_is_not_a_request(text: str) -> None:
    assert parse_request(merge(text)) is None


def test_only_a_merge_commit_can_be_a_request() -> None:
    # A squash commit "Title (#12)" or a commit quoting GitLab is one parent: squash.py's job.
    text = "Merge pull request #12 from alexgh/x\n\nSee merge request a/b!3\n"
    assert parse_request(raw_commit(subject=text.split("\n")[0], body=text)) is None


@pytest.mark.parametrize(
    "subject",
    [
        "Merge branch 'main' into feature/x",
        "Merge branch 'master' into 'feature/x'",
        "Merge remote-tracking branch 'origin/main' into feature/x",
        "Merge remote-tracking branch 'origin/feature/x' into feature/x",  # your own branch, pulled
        "Merge branch 'feature/x' of github.com:acme/app into feature/x",
        "Merge branch 'main' of github.com:acme/app",  # git pull on main
        "Merge remote-tracking branch 'origin/main'",
        "Merge origin/main into feature/x",  # written by hand
    ],
)
def test_sync_merges_bring_a_mainline_or_the_same_branch_in(subject: str) -> None:
    assert is_sync_merge(raw_commit(parents=TWO_PARENTS, subject=subject), ())


@pytest.mark.parametrize(
    "subject",
    [
        "Merge branch 'feature/x' into main",  # a branch merged into the mainline by hand
        "Merge branch 'feature/x'",
        "Merge remote-tracking branch 'origin/feature/x'",
        "Merge branch 'feature/x' into 'release/1.2'",
        "Merge tag 'v1.2'",
        "Merge commit '0123abc' into feature/x",
        "Merge pull request #12 from alexgh/main",  # a fork's main: a pull request, not a sync
        "Merge branch 'hotfix/main' into 'release/1.2'",  # a branch whose name ends in "main"
        "Merge branch 'hotfix/main' into feature/x",
        "Revert \"Merge branch 'main' into feature/x\"",
    ],
)
def test_other_merges_are_not_sync_merges(subject: str) -> None:
    assert not is_sync_merge(raw_commit(parents=TWO_PARENTS, subject=subject), ())


def test_the_repositorys_own_default_branch_counts_as_a_mainline() -> None:
    commit = raw_commit(parents=TWO_PARENTS, subject="Merge branch 'develop' into feature/x")
    assert not is_sync_merge(commit, ())
    assert is_sync_merge(commit, ("develop",))
    assert not is_sync_merge(raw_commit(subject="Merge branch 'main' into feature/x"), ())


def test_only_gitlabs_default_subject_names_the_target() -> None:
    assert (
        parse_request(
            merge("Merge branch 'x' into 'release/1.2'\n\nT\n\nSee merge request a/b!1\n")
        ).target
        == "release/1.2"
    )
    assert parse_request(merge("Merge pull request #3 from alexgh/x\n\nT\n")).target == ""
    assert parse_request(merge("Custom\n\nSee merge request a/b!1\n")).target == ""


@pytest.mark.parametrize(
    ("branch", "long_lived"),
    [("main", True), ("master", True), ("develop", True), ("trunk", True), ("release/1.2", True),
     ("feature/x", False), ("hotfix/main", False), ("releases", False), ("release", False)],
)  # fmt: skip
def test_long_lived_branches(branch: str, long_lived: bool) -> None:
    assert is_long_lived(branch, ("trunk",)) is long_lived


@pytest.mark.parametrize(
    ("text", "back"),
    [
        ("Merge branch 'main' into 'develop'", True),  # git-flow back-merge
        ("Merge branch 'release/1.2' into 'main'", True),  # a release merged into production
        ("Merge branch 'main' into 'feature/x'", True),  # a sync, through a merge request
        ("Merge branch 'main' into 'main'", False),  # source = target: another project's main
        ("Merge branch 'hotfix/x' into 'main'", False),
        ("Merge branch 'feature/x' into 'release/1.2'", False),
    ],
)
def test_back_merges_bring_a_long_lived_branch_into_another(text: str, back: bool) -> None:
    request = parse_request(merge(f"{text}\n\nTitle\n\nSee merge request acme/app!3\n"))
    assert is_back_merge(request, ()) is back


def test_a_pull_requests_target_is_unknown_so_it_is_never_a_back_merge() -> None:
    request = parse_request(merge("Merge pull request #5 from alexgh/main\n\nTitle\n"))
    assert not is_back_merge(request, ())
