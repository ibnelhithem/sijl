"""The GitHub Actions workflows: every action pinned to a full commit SHA, the publish job the only
one that can write, a release only from a tag push after its tests, macOS runs on pull requests
as well, credentials and private words are looked for before anything is published, and no
expression is pasted into a script.

The files are read as text — PyYAML is not a dependency — so the job blocks are found by their
two-space indent under `jobs:`, the layout both workflows use.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
CI = (WORKFLOWS / "ci.yml").read_text()
RELEASE = (WORKFLOWS / "release.yml").read_text()
GUARD = (WORKFLOWS / "guard.yml").read_text() if (WORKFLOWS / "guard.yml").exists() else ""
SCRIPTS = WORKFLOWS.parents[1] / "scripts"
SECRET_SCAN = (SCRIPTS / "secret-scan.sh").read_text()
SECRET_SCAN_REPORT = (
    (SCRIPTS / "secret-scan.tmpl").read_text() if (SCRIPTS / "secret-scan.tmpl").exists() else ""
)

USES = re.compile(r"\s*(?:- )?uses: (?P<action>\S+)(?P<rest>.*)")
PINNED = re.compile(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}")
VERSION_COMMENT = re.compile(r" # v\d+\.\d+\.\d+")
# A token permission however it is written: `contents: write`, `contents:  write`, `"write"`.
PERMISSION = re.compile(r"(?<![\w-])([\w-]+):\s+[\"']?(read|write)\b")


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
    assert set(blocks) == {
        "build",
        "test",
        "publish",
        "publish-pypi",
        "publish-testpypi",
        "verify-pypi",
    }
    for name, block in blocks.items():
        if name in {"publish-pypi", "publish-testpypi"}:
            expected = [("id-token", "write")]  # the upload token and nothing else
        elif name == "publish":
            expected = [("contents", "write")]
        else:
            expected = [("contents", "read")]
        assert PERMISSION.findall(block) == expected, name


def test_the_permission_pattern_sees_every_spelling() -> None:
    for line in ("contents: write", "contents:  write", 'contents: "write"', "contents: 'write'"):
        assert PERMISSION.findall(f"    {line}") == [("contents", "write")], line


def test_ci_only_reads() -> None:
    assert "\npermissions:\n  contents: read\n" in top_level(CI)
    assert not any(re.search(r": write\b", line) for line in _code(CI))


def test_only_a_tag_push_publishes_and_only_after_build_and_tests() -> None:
    blocks = jobs(RELEASE)
    publish = blocks["publish"]
    assert "    if: github.event_name == 'push' && github.ref_type == 'tag'" in publish
    assert "    needs: [build, test, verify-pypi]" in publish
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
    assert setups == 3  # build, test, verify-pypi
    assert RELEASE.count("enable-cache: false") == setups


def test_the_smoke_test_runs_on_the_files_that_are_published() -> None:
    blocks = jobs(RELEASE)
    assert "uses: actions/upload-artifact@" in blocks["build"]
    for name in ("test", "publish"):
        assert "uses: actions/download-artifact@" in blocks[name]
    assert "scripts/smoke-test.sh dist" in blocks["test"]


def test_macos_runs_on_pull_requests_merges_and_releases() -> None:
    ci = jobs(CI)
    assert "runs-on: ubuntu-latest" in ci["check"]
    assert "if:" not in ci["check"]  # the Linux job runs on every pull request, as before
    assert "runs-on: macos-latest" in ci["check-macos"]
    assert "timeout-minutes: 20" in ci["check-macos"]
    assert "persist-credentials: false" in ci["check-macos"]
    assert "if:" not in ci["check-macos"]  # no condition: the job runs on every trigger
    matrix = jobs(RELEASE)["test"]
    assert (
        "fromJSON(github.event_name == 'pull_request' && '[\"ubuntu-latest\"]' || "
        '\'["ubuntu-latest", "macos-latest"]\')'
    ) in matrix


def test_secret_scan_runs_on_every_trigger_within_a_time_limit() -> None:
    job = jobs(CI)["secret-scan"]
    assert "if:" not in job  # no condition: a pull request cannot skip the scan
    assert "timeout-minutes: 10" in job  # a hung download cannot hold a runner for hours
    assert "persist-credentials: false" in job
    assert "contents: write" not in job
    assert "run: sh scripts/secret-scan.sh" in job
    assert "uses: betterleaks" not in job  # our binary, not a third-party action


def test_secret_scan_verifies_the_checksum_before_the_binary_runs() -> None:
    # Comment lines are dropped: a word in a comment proves nothing about the order of the code.
    code = [line.strip() for line in _code(SECRET_SCAN)]
    assert "set -eu" in code  # a failed check stops the script
    verify = [n for n, line in enumerate(code) if "sha256sum -c" in line]
    run = [n for n, line in enumerate(code) if line.startswith('"$work/betterleaks"')]
    assert len(verify) == 1 and len(run) == 1
    assert verify[0] < run[0]


def test_a_finding_is_reported_by_its_place_never_by_its_content() -> None:
    # The CI log of a public repository is public: the file, line, rule and commit are enough.
    code = "\n".join(_code(SECRET_SCAN))
    assert "--redact" in code  # a template mistake would still print REDACTED, not the secret
    assert "--report-format template --report-template scripts/secret-scan.tmpl" in code
    assert "--validation" not in code  # it would send a found secret to its provider
    assert set(re.findall(r"\.([A-Z]\w*)", SECRET_SCAN_REPORT)) == {
        "File",
        "StartLine",
        "RuleID",
        "Commit",
    }


def _scripts(text: str) -> list[str]:
    """The text of every `run:` step: a one-line value, or the indented block under `run: |`."""
    found: list[str] = []
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if not (match := re.fullmatch(r"(\s*)(?:- )?run: ?(.*)", line)):
            continue
        if match[2] not in ("|", ">", "|-", ">-"):
            found.append(match[2])
            continue
        indent = len(match[1])
        block = []
        for following in lines[number + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            block.append(following)
        found.append("\n".join(block))
    return found


def test_no_expression_is_pasted_into_a_script() -> None:
    # `${{ }}` inside `run:` is replaced before the shell starts: a pull request title such as
    # `"; curl … $SECRET` would run as code. Values reach a script through `env:` only.
    seen = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for script in _scripts(path.read_text()):
            seen += 1
            assert "${{" not in script, f"{path.name}: {script.strip().splitlines()[0]}"
    assert seen >= 10


def test_the_word_list_checks_pull_requests_their_edits_and_main() -> None:
    top = top_level(GUARD)
    assert "    types: [opened, synchronize, reopened, edited]" in top  # a title edit reruns it
    assert "    branches: [main]" in top  # the backstop after a merge
    assert "\npermissions:\n  contents: read\n" in top
    assert "pull_request_target" not in GUARD  # it would run a pull request's code with secrets
    assert "edited" not in CI  # every title edit would rerun the macOS job, and cancel it
    job = jobs(GUARD)["word-list"]
    assert not any(re.search(r": write\b", line) for line in job.splitlines())
    assert "persist-credentials: false" in job
    assert "timeout-minutes: 10" in job
    for checked in ("tree", 'commits "$BASE..$HEAD"', 'commits "$BEFORE..$GITHUB_SHA"'):
        assert f"guard {checked}" in job
    assert 'guard pull-request "$GITHUB_EVENT_PATH"' in job
    # A step's variables are printed in its public log: a listed word in the body would get a
    # second copy there. The branch name, title and body are read from the event file instead.
    for text in ("pull_request.title", "pull_request.body", "head_ref"):
        assert text not in GUARD


def test_a_push_to_main_is_never_cancelled_by_the_next_one() -> None:
    # Each push checks only its own commits: a cancelled run would leave a merge unchecked.
    assert "  group: guard-${{ github.event.pull_request.number || github.sha }}\n" in GUARD


def test_forks_and_dependabot_skip_the_word_list_never_pass_it() -> None:
    # Neither gets the Actions secret. A job-level condition shows the job as skipped; a step
    # that passed without the list would look like a check that ran.
    job = jobs(GUARD)["word-list"]
    condition = job.split("    runs-on:", 1)[0]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in condition
    assert "github.actor != 'dependabot[bot]'" in condition
    assert "GUARD_LIST: ${{ secrets.PUBLICATION_GUARD_LIST }}" in job
    assert 'if [ -z "$GUARD_LIST" ]; then' in job  # a run that should have it fails without it


def test_the_release_scans_the_unpacked_files_before_they_are_uploaded() -> None:
    build = jobs(RELEASE)["build"]
    scan = build.index("run: sh scripts/secret-scan.sh dist dist")
    assert build.index("uv build") < scan < build.index("uses: actions/upload-artifact@")
    paths = top_level(RELEASE).split("  pull_request:", 1)[1]
    for changed in ("scripts/secret-scan.sh", "scripts/secret-scan.tmpl", ".betterleaks.toml"):
        assert f'      - "{changed}"' in paths  # a change to the scan reruns the dry run


def test_the_history_and_the_built_files_are_scanned_with_this_repository_allowlist() -> None:
    # A folder scan looks for its config inside the unpacked files, where there is none: the
    # test fixtures' allowed values would fail it. One run line serves both scans.
    code = "\n".join(_code(SECRET_SCAN))
    assert "set -- git ." in code
    assert 'set -- dir "$work/unpacked"' in code
    assert '"$work/betterleaks" "$@" --config .betterleaks.toml' in code


def test_the_pypi_readme_is_rewritten_before_the_build() -> None:
    """PyPI shows the README inside the built files. Those links are rewritten to the tag
    before uv build reads README.md."""
    build = jobs(RELEASE)["build"]
    assert build.index("release.py readme") < build.index("uv build")


def test_a_local_tag_build_rewrites_the_readme_before_uv_build() -> None:
    """release-check.sh packages the same README as the workflow when a tag is given."""
    script = (SCRIPTS / "release-check.sh").read_text()
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert code.index('release readme "$1"') < code.index("uv build")


def test_the_built_files_are_checked_with_twine_before_upload() -> None:
    """The wheel and sdist must render before they can be uploaded."""
    build = jobs(RELEASE)["build"]
    check = build.index("twine==7.0.0 twine check --strict dist/*")
    assert build.index("uv build") < check < build.index("uses: actions/upload-artifact@")


def test_a_local_build_checks_the_files_with_twine() -> None:
    """The build on your machine checks the same files the workflow checks."""
    script = (SCRIPTS / "release-check.sh").read_text()
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    check = code.index('twine==7.0.0 twine check --strict "$work"/dist/*')
    assert code.index("uv build") < check


def test_only_the_pypi_job_may_ask_for_a_token() -> None:
    """The job that uploads holds a short-lived PyPI token and runs no repository code."""
    blocks = jobs(RELEASE)
    assert "publish-pypi" in blocks
    for name, block in blocks.items():
        if name in {"publish-pypi", "publish-testpypi"}:
            assert "id-token: write" in block
            assert "actions/checkout@" not in block
        else:
            assert "id-token: write" not in block


def test_the_github_release_waits_until_pypi_succeeded() -> None:
    """A failed PyPI upload must not announce a version people cannot install."""
    publish = jobs(RELEASE)["publish"]
    assert "needs: [build, test, verify-pypi]" in publish


def test_the_bytes_on_pypi_are_checked_before_the_github_release() -> None:
    """The GitHub release waits until PyPI is serving the tested files."""
    blocks = jobs(RELEASE)
    assert "verify-pypi" in blocks
    verify = blocks["verify-pypi"]
    assert "needs: [build, publish-pypi]" in verify
    assert "id-token: write" not in verify
    publish = blocks["publish"]
    assert "needs: [build, test, verify-pypi]" in publish


def test_the_testpypi_rehearsal_is_a_manual_choice() -> None:
    """TestPyPI runs only when a person turns the checkbox on. A tag push does not read it."""
    header = top_level(RELEASE)
    assert "testpypi:" in header
    assert "type: boolean" in header
    assert "default: false" in header
    blocks = jobs(RELEASE)
    assert "publish-testpypi" in blocks
    job = blocks["publish-testpypi"]
    assert "environment: testpypi" in job
    assert "id-token: write" in job
    assert "actions/checkout@" not in job
    assert "inputs.testpypi" in job
    assert "inputs.testpypi" not in blocks["publish-pypi"]


def test_pypi_is_checked_by_sha256_before_its_version_is_installed() -> None:
    """The install from PyPI runs only after PyPI served the tested bytes, and the job that
    installs holds no upload token and keeps no git credentials."""
    verify = jobs(RELEASE)["verify-pypi"]
    check = verify.index('release.py verify-pypi "$VERSION" dist')
    install = verify.index('scripts/smoke-test.sh --pypi "$VERSION"')
    assert verify.index("uses: actions/download-artifact@") < check < install
    assert "persist-credentials: false" in verify
    assert "id-token" not in verify
    assert "os: [ubuntu-latest, macos-latest]" in verify


def test_no_release_step_may_fail_quietly() -> None:
    """`continue-on-error`, `always()` or `cancelled()` would let a failed check go on to the
    upload or the GitHub release."""
    for escape in ("continue-on-error", "always()", "cancelled()"):
        assert escape not in RELEASE, escape
