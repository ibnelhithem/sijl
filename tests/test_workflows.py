"""The GitHub Actions workflows: every action pinned to a full commit SHA, the publish job the only
one that can write, a release only from a tag push after its tests, and macOS kept off pull
requests.

The files are read as text — PyYAML is not a dependency — so the job blocks are found by their
two-space indent under `jobs:`, the layout both workflows use.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
CI = (WORKFLOWS / "ci.yml").read_text()
RELEASE = (WORKFLOWS / "release.yml").read_text()

USES = re.compile(r"\s*(?:- )?uses: (?P<action>\S+)(?P<rest>.*)")
PINNED = re.compile(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}")
VERSION_COMMENT = re.compile(r" # v\d+\.\d+\.\d+")


def _code(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def top_level(text: str) -> str:
    return text.split("\njobs:\n", 1)[0]


def jobs(text: str) -> dict[str, str]:
    """Each job's lines (comments dropped), by job id."""
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in _code(text.split("\njobs:\n", 1)[1]):
        if match := re.fullmatch(r"  ([\w-]+):", line):
            current = match[1]
            blocks[current] = []
        elif line and not line.startswith(" "):
            current = None  # a top-level key after the jobs
        elif current:
            blocks[current].append(line)
    return {name: "\n".join(lines) for name, lines in blocks.items()}


def test_every_action_is_pinned_to_a_commit_sha_with_its_version() -> None:
    # A tag can be moved to other code; a full commit SHA cannot. The comment says which release.
    seen = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if match := USES.fullmatch(line):
                seen += 1
                where = f"{path.name}:{number}: {match['action']}"
                assert PINNED.fullmatch(match["action"]), f"{where} is not a full commit SHA"
                assert VERSION_COMMENT.fullmatch(match["rest"]), f"{where} needs '# vX.Y.Z'"
    assert seen >= 10


def test_release_jobs_get_only_the_token_permissions_they_need() -> None:
    assert "\npermissions: {}\n" in top_level(RELEASE)  # nothing unless a job asks
    blocks = jobs(RELEASE)
    assert set(blocks) == {"build", "test", "publish"}
    for name, block in blocks.items():
        expected = ["contents: write"] if name == "publish" else ["contents: read"]
        assert re.findall(r"\b\w+: (?:read|write)\b", block) == expected, name


def test_ci_only_reads() -> None:
    assert "\npermissions:\n  contents: read\n" in top_level(CI)
    assert not any(re.search(r": write\b", line) for line in _code(CI))


def test_only_a_tag_push_publishes_and_only_after_build_and_tests() -> None:
    blocks = jobs(RELEASE)
    publish = blocks["publish"]
    assert "    if: github.event_name == 'push' && github.ref_type == 'tag'" in publish
    assert "    needs: [build, test]" in publish
    assert "gh release create" in publish
    assert "--verify-tag" in publish  # never lets gh create a tag of its own
    assert not any("gh release" in block for name, block in blocks.items() if name != "publish")


def test_the_tag_and_changelog_are_checked_before_anything_is_built() -> None:
    build = jobs(RELEASE)["build"]
    assert build.index("release.py check-tag") < build.index("uv build")
    assert build.index("release.py notes") < build.index("uv build")
    assert "git merge-base --is-ancestor HEAD origin/main" in build  # a release is cut on main


def test_release_runs_never_restore_a_cache() -> None:
    # A cache written by another run (a pull request's, say) must not feed what gets published.
    setups = RELEASE.count("uses: astral-sh/setup-uv@")
    assert setups == 2
    assert RELEASE.count("enable-cache: false") == setups


def test_the_smoke_test_runs_on_the_files_that_are_published() -> None:
    blocks = jobs(RELEASE)
    assert "uses: actions/upload-artifact@" in blocks["build"]
    for name in ("test", "publish"):
        assert "uses: actions/download-artifact@" in blocks[name]
    assert "scripts/smoke-test.sh dist" in blocks["test"]


def test_macos_runs_after_merges_and_for_releases_but_not_on_pull_requests() -> None:
    ci = jobs(CI)
    assert "runs-on: ubuntu-latest" in ci["check"]
    assert "if:" not in ci["check"]  # the Linux job runs on every pull request, as before
    assert "runs-on: macos-latest" in ci["check-macos"]
    assert "    if: github.event_name != 'pull_request'" in ci["check-macos"]
    matrix = jobs(RELEASE)["test"]
    assert (
        "fromJSON(github.event_name == 'pull_request' && '[\"ubuntu-latest\"]' || "
        '\'["ubuntu-latest", "macos-latest"]\')'
    ) in matrix
