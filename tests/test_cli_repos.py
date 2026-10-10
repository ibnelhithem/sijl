"""`sijl repos [--json]` over the `discovery_tree`: every repository exactly once, with
its linked worktrees; every path not scanned, with the reason; nothing on disk changes."""

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from sijl.cli import app
from sijl.discover import (
    DEPENDENCY_FOLDER,
    HIDDEN_FOLDER,
    LINK_NOWHERE,
    LINK_TO_FOLDER,
    SUBMODULE_NOT_CHECKED_OUT,
)
from tests.discovery_tree import DiscoveryTree
from tests.gitfactory import ME, PERIOD_ARGS, reason_from_git

runner = CliRunner()


def repos_json(*roots: Path) -> dict:
    result = runner.invoke(app, ["repos", "--json", *(f"--root={r}" for r in roots)])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    return json.loads(result.stdout)


def by_name(listing: dict) -> dict[str, dict]:
    return {repo["name"]: repo for repo in listing["repositories"]}


def test_a_repository_with_many_linked_worktrees_is_listed_once_with_them(discovery_tree):
    tree: DiscoveryTree = discovery_tree
    listing = repos_json(tree.root)
    [app_entry] = [r for r in listing["repositories"] if r["path"] == str(tree.app.path)]
    assert [r["path"] for r in listing["repositories"]].count(str(tree.app.path)) == 1
    worktrees = app_entry["worktrees"]
    assert [w["path"] for w in worktrees] == [str(p) for p in tree.worktrees]
    gone, review, agent_2, agent_1, feature = worktrees
    assert (gone["prunable"], gone["branch"]) == (True, None)
    assert (review["branch"], review["locked"]) == ("review", True)
    assert feature["branch"] == "feature/a" and agent_1["branch"] is None
    assert agent_2["head"] == agent_1["head"] == tree.app.head()


def test_bare_repositories_are_found_and_symlink_loops_end(discovery_tree):
    tree: DiscoveryTree = discovery_tree
    listing = repos_json(tree.root)
    repos = by_name(listing)
    assert sorted(repos) == ["app", "lib", "mirror", "proj", "super"]
    assert (repos["mirror"]["bare"], repos["mirror"]["path"]) == (
        True,
        str(tree.root / "mirror.git"),
    )
    assert (repos["proj"]["bare"], repos["proj"]["path"]) == (True, str(tree.root / "proj"))
    assert repos["lib"]["superproject"] == str(tree.root / "super")
    skipped = {Path(s["path"]).name: s["reason"] for s in listing["skipped"]}
    assert skipped["loop"] == LINK_TO_FOLDER and skipped["self"] == LINK_NOWHERE


def test_every_path_not_scanned_is_listed_with_its_reason(discovery_tree):
    tree: DiscoveryTree = discovery_tree
    listing = repos_json(tree.root)
    skipped = [(Path(s["path"]).relative_to(tree.root).as_posix(), s["reason"])
               for s in listing["skipped"]]  # fmt: skip
    # Reasons built from git's own words: Sijl's prefix, whatever git's wording (gitfactory).
    from_git = {
        "badcfg": (),
        "garbage": ("corrupt: its .git file does not point to",),
        "hollow": ("corrupt or incomplete",),
        "orphan": ("broken link: its .git file points to ", "corrupt"),
    }
    for path, reason in [s for s in skipped if s[0] in from_git]:
        assert reason_from_git(reason, *from_git[path]), (path, reason)
    assert sorted(p for p, _ in skipped if p in from_git) == sorted(from_git)  # each listed
    skipped = [s for s in skipped if s[0] not in from_git]
    assert skipped == [
        ("dangling", LINK_NOWHERE),
        (
            "deep/1/2/3",
            "not searched deeper than 4 levels — name a repository below it with --repo",
        ),
        ("loop", LINK_TO_FOLDER),
        ("notes", "no git repository inside"),
        ("self", LINK_NOWHERE),
        ("shortcut", LINK_TO_FOLDER),
        ("super/vendor/other", SUBMODULE_NOT_CHECKED_OUT),
    ]
    assert listing["pruned"] == [
        {"path": str(tree.root / ".claude"), "reason": HIDDEN_FOLDER},
        {"path": str(tree.root / "node_modules"), "reason": DEPENDENCY_FOLDER},
    ]


def snapshot(base: Path) -> dict[str, tuple[int, int, int]]:
    """Every folder, file and link under `base` (links not followed): mtime, size, mode."""
    seen = {}
    for folder, dirs, files in os.walk(base, followlinks=False):
        for name in [".", *dirs, *files]:
            path = os.path.join(folder, name)
            info = os.lstat(path)
            seen[os.path.normpath(path)] = (info.st_mtime_ns, info.st_size, info.st_mode)
    return seen


def test_nothing_under_the_scanned_tree_changes(discovery_tree):
    tree: DiscoveryTree = discovery_tree
    before = snapshot(tree.base)
    assert any("/.git/" in path for path in before) and len(before) > 300  # the .git dirs too
    roots = [f"--root={tree.root}", f"--root={tree.outside}"]
    for args in (
        ["repos", *roots],
        ["repos", "--json", *roots],
        ["report", *roots, "--author-email", ME.email, *PERIOD_ARGS,
         "--include-worktrees", "--dedupe", "patch-id"],
    ):  # fmt: skip
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    assert snapshot(tree.base) == before


def test_the_json_is_stable_sorted_whatever_the_order_of_roots(discovery_tree):
    tree: DiscoveryTree = discovery_tree
    roots = [f"--root={tree.root}", f"--root={tree.outside}", f"--root={tree.root / 'super'}"]
    first = runner.invoke(app, ["repos", "--json", *roots]).stdout
    second = runner.invoke(app, ["repos", "--json", *roots[::-1]]).stdout
    assert first == second
    listing = json.loads(first)
    assert first == json.dumps(listing, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    repos = listing["repositories"]
    assert repos == sorted(repos, key=lambda r: (r["name"], r["path"]))
    for repo in repos:
        assert repo["worktrees"] == sorted(repo["worktrees"], key=lambda w: Path(w["path"]).parts)
    for kind in ("skipped", "pruned"):
        assert listing[kind] == sorted(
            listing[kind], key=lambda s: (Path(s["path"]).parts, s["reason"])
        )


def test_the_default_listing_is_for_reading(discovery_tree):
    tree: DiscoveryTree = discovery_tree
    result = runner.invoke(app, ["repos", "--root", str(tree.root)])
    assert result.exit_code == 0, result.output
    text = result.stdout
    assert text.startswith("Repositories (5)\n")
    assert f"- app — {tree.app.path}\n  5 linked worktrees:\n" in text
    assert f"  - {tree.outside / 'review'} — branch review — locked\n" in text
    assert (
        f"  - {tree.outside / 'gone'} — detached at {tree.app.head()[:8]} — its folder is gone\n"
        in text
    )
    assert f"- mirror — {tree.root / 'mirror.git'} — bare\n" in text
    assert (
        f"- lib — {tree.root / 'super' / 'vendor' / 'lib'} — submodule of {tree.root / 'super'}\n"
        in text
    )
    assert "\nNot scanned (11)\n" in text
    assert f"- {tree.root / 'loop'} — {LINK_TO_FOLDER}\n" in text
    assert "\nNot searched, by rule (2)\n" in text
    assert f"- {tree.root / 'node_modules'} — {DEPENDENCY_FOLDER}\n" in text


def test_the_report_caveats_still_name_what_discovery_skipped(discovery_tree):
    tree: DiscoveryTree = discovery_tree
    result = runner.invoke(
        app, ["report", "--root", str(tree.root), "--author-email", ME.email, *PERIOD_ARGS]
    )
    assert result.exit_code == 0, result.output
    caveats = result.stdout.split("## Caveats")[1]
    listing = repos_json(tree.root)
    for skipped in listing["skipped"]:
        assert f"Not scanned: `{skipped['path']}` — {skipped['reason']}." in caveats
    assert (
        f"2 folders were not searched, by rule — hidden, dependency and build folders hold tools, "
        f"not projects: `{tree.root / '.claude'}`, `{tree.root / 'node_modules'}`."
    ) in caveats


def test_folders_come_from_the_configuration_like_report(make_repo, tmp_path, monkeypatch):
    make_repo("app").commit("feat: a", at="2026-05-04T10:00:00+02:00")
    monkeypatch.setenv("SIJL_ROOTS", str(tmp_path))
    result = runner.invoke(app, ["repos"])
    assert result.exit_code == 0 and result.stdout.startswith("Repositories (1)\n- app — ")


def test_no_folder_is_a_usage_error_and_no_repository_exits_3(tmp_path):
    usage = runner.invoke(app, ["repos"])
    assert usage.exit_code == 2 and "--root" in usage.output
    (tmp_path / "empty").mkdir()
    none = runner.invoke(app, ["repos", "--json", "--root", str(tmp_path / "empty")])
    assert none.exit_code == 3
    assert json.loads(none.stdout)["skipped"] == [
        {"path": str(tmp_path / "empty"), "reason": "no git repository inside"}
    ]  # the listing is still printed: it says why nothing was found
    assert "no git repository found" in none.stderr
