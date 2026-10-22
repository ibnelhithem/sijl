"""Release checks (scripts/release.py): one version, written in pyproject.toml, that the installed
package, the release tag and CHANGELOG.md all repeat — checked before anything is published
."""

import hashlib
import http.client
import importlib.util
import io
import subprocess
import sys
import tomllib
import urllib.error
from pathlib import Path

import pytest

from sijl import __version__

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release.py"
PYPROJECT_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def _load_script():
    spec = importlib.util.spec_from_file_location("release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["release"] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


release = _load_script()

GOOD = """\
# Changelog

Intro text.

## [Unreleased]

### Added

- Something new.

## [1.1.0] - 2026-11-02

### Added

- `sijl ask`.

### Fixed

- A crash.

## [1.0.0] - 2026-10-20

### Added

- The first release.

[Unreleased]: https://github.com/acme/sijl/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/acme/sijl/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/acme/sijl/releases/tag/v1.0.0
"""


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    # A plain interpreter, as CI runs it before anything is installed: standard library only.
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


# --- the version -----------------------------------------------------------------------------


def test_the_installed_version_is_the_one_in_pyproject() -> None:
    # `sijl --version` prints __version__, read from the installed metadata: a stale install
    # (pyproject bumped, environment not re-synced) would print the old number.
    assert __version__ == PYPROJECT_VERSION


def test_read_version_reads_the_project_table(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "sijl"\nversion = "1.2.3"\n')
    assert release.read_version(pyproject) == "1.2.3"


def test_version_command_prints_pyprojects_version() -> None:
    done = run_script("version")
    assert (done.returncode, done.stdout, done.stderr) == (0, f"{PYPROJECT_VERSION}\n", "")


# --- the tag ---------------------------------------------------------------------------------


def test_a_tag_of_v_plus_the_version_passes() -> None:
    release.check_tag("v1.0.0", "1.0.0")
    release.check_tag("v10.20.30", "10.20.30")


@pytest.mark.parametrize(
    "tag",
    ["v1.0.1", "1.0.0", "V1.0.0", "v1.0", "v1.0.0-rc1", "v1.0.0 ", "release-1.0.0", "v01.0.0"],
)
def test_any_other_tag_fails_naming_the_expected_one(tag: str) -> None:
    with pytest.raises(release.ReleaseError) as caught:
        release.check_tag(tag, "1.0.0")
    assert "v1.0.0" in str(caught.value)
    assert repr(tag) in str(caught.value)


@pytest.mark.parametrize("version", ["1.0", "1.0.0rc1", "1.0.0.post1", "01.0.0", "1.0.0-beta"])
def test_only_plain_x_y_z_versions_are_released(version: str) -> None:
    with pytest.raises(release.ReleaseError, match=r"X\.Y\.Z"):
        release.check_tag(f"v{version}", version)


def test_check_tag_command_exit_codes() -> None:
    assert run_script("check-tag", f"v{PYPROJECT_VERSION}").returncode == 0
    wrong = run_script("check-tag", "v999.0.0")
    assert wrong.returncode == 1
    assert wrong.stdout == ""
    assert f"v{PYPROJECT_VERSION}" in wrong.stderr
    assert "v999.0.0" in wrong.stderr


# --- the changelog ---------------------------------------------------------------------------


def test_notes_are_the_versions_section_without_its_heading() -> None:
    assert release.release_notes(GOOD, "1.1.0") == (
        "### Added\n\n- `sijl ask`.\n\n### Fixed\n\n- A crash."
    )


def test_notes_of_the_oldest_version_stop_before_the_link_references() -> None:
    assert release.release_notes(GOOD, "1.0.0") == "### Added\n\n- The first release."


def test_notes_for_a_version_without_a_section_fail() -> None:
    with pytest.raises(release.ReleaseError, match=r"no section for 2\.0\.0") as caught:
        release.release_notes(GOOD, "2.0.0")
    assert "## [2.0.0] - YYYY-MM-DD" in str(caught.value)  # says what to write


def test_notes_for_an_empty_section_fail() -> None:
    text = GOOD.replace("### Added\n\n- The first release.\n\n", "")
    with pytest.raises(release.ReleaseError, match="empty"):
        release.release_notes(text, "1.0.0")


def test_notes_command_prints_the_section(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(GOOD)
    done = run_script("notes", "1.0.0", "--changelog", str(changelog))
    assert (done.returncode, done.stdout) == (0, "### Added\n\n- The first release.\n")
    missing = run_script("notes", "3.0.0", "--changelog", str(changelog))
    assert (missing.returncode, missing.stdout) == (1, "")
    assert "no section for 3.0.0" in missing.stderr


def test_a_well_formed_changelog_has_no_problems() -> None:
    assert release.changelog_problems(GOOD) == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (("## [Unreleased]\n\n### Added\n\n- Something new.\n\n", ""), "[Unreleased]"),
        (("## [1.1.0] - 2026-11-02", "## 1.1.0 - 2026-11-02"), "## 1.1.0 - 2026-11-02"),
        (("## [1.1.0] - 2026-11-02", "## [1.1.0] - 2026-13-02"), "2026-13-02"),
        (("## [1.1.0] - 2026-11-02", "## [1.1.0]"), "## [1.1.0]"),
        (("## [1.1.0] - 2026-11-02", "## [0.9.0] - 2026-11-02"), "newest first"),
        (("## [1.1.0] - 2026-11-02", "## [1.0.0] - 2026-11-02"), "twice"),
        (("[1.0.0]: https://github.com/acme/sijl/releases/tag/v1.0.0\n", ""), "[1.0.0]"),
        (("### Fixed", "### Misc"), "### Misc"),
    ],
)
def test_changelog_problems_are_named(change: tuple[str, str], problem: str) -> None:
    old, new = change
    assert old in GOOD
    problems = release.changelog_problems(GOOD.replace(old, new, 1))
    assert problems, f"no problem found after replacing {old!r}"
    assert any(problem in p for p in problems), problems


def test_unreleased_must_come_first() -> None:
    moved = GOOD.replace("## [Unreleased]\n\n### Added\n\n- Something new.\n\n", "")
    moved = moved.replace("[Unreleased]: ", "## [Unreleased]\n\n[Unreleased]: ", 1)
    assert any("first" in p for p in release.changelog_problems(moved))


def test_the_projects_changelog_is_well_formed() -> None:
    assert release.changelog_problems((ROOT / "CHANGELOG.md").read_text()) == []


@pytest.mark.allow_subprocess  # the script and a fake `uv`, both local (tests/conftest.py)
def test_the_smoke_test_accepts_a_relative_dist_folder(tmp_path: Path) -> None:
    """The release workflow passes `dist`, a relative path, and the script installs from inside a
    temporary folder: every path it hands to uv must already be absolute."""
    dist = tmp_path / "project" / "dist"
    dist.mkdir(parents=True)
    for name in ("sijl-9.9.9-py3-none-any.whl", "sijl-9.9.9.tar.gz"):
        (dist / name).write_text("")
    calls = tmp_path / "uv-calls.txt"
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "uv").write_text(f'#!/bin/sh\necho "$@" >> {calls}\nexit 1\n')  # record, then fail
    (fake / "uv").chmod(0o755)
    env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path)}
    result = subprocess.run(
        [str(ROOT / "scripts" / "smoke-test.sh"), "dist", "9.9.9"],
        cwd=dist.parent, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 1  # the fake uv fails the install, as intended
    installed = calls.read_text().split()
    wheel = next(arg for arg in installed if arg.endswith(".whl"))
    assert wheel == str(dist / "sijl-9.9.9-py3-none-any.whl")


@pytest.mark.allow_subprocess  # the script and a fake `uv`, both local (tests/conftest.py)
def test_the_smoke_test_can_install_the_version_from_pypi(tmp_path: Path) -> None:
    """After an upload the release installs `sijl==VERSION` from pypi.org itself: no cache, no
    uv config file and no other index can stand in for what PyPI serves."""
    calls = tmp_path / "uv-calls.txt"
    fake = tmp_path / "bin"
    fake.mkdir()
    seen = tmp_path / "uv-env.txt"
    (fake / "uv").write_text(
        f'#!/bin/sh\necho "$@" >> {calls}\n'
        f'echo "${{UV_INDEX-}}${{UV_INDEX_URL-}}${{UV_EXTRA_INDEX_URL-}}${{UV_FIND_LINKS-}}'
        f'${{UV_CONFIG_FILE-}}"'
        f" >> {seen}\nexit 1\n"
    )  # record the arguments and any index variable, then fail
    (fake / "uv").chmod(0o755)
    elsewhere = "https://mirror.example.test/simple"
    env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path)}
    for name in (
        "UV_INDEX",
        "UV_INDEX_URL",
        "UV_EXTRA_INDEX_URL",
        "UV_FIND_LINKS",
        "UV_CONFIG_FILE",
    ):
        env[name] = elsewhere
    result = subprocess.run(
        [str(ROOT / "scripts" / "smoke-test.sh"), "--pypi", "9.9.9"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 1  # the fake uv fails the install, as intended
    assert "pypi: uv tool install failed" in result.stderr
    installed = calls.read_text().split()
    assert installed[:2] == ["tool", "install"]
    assert installed[-1] == "sijl==9.9.9"
    for option in ("--no-cache", "--no-config"):
        assert option in installed
    assert installed[installed.index("--default-index") + 1] == "https://pypi.org/simple"
    for option in ("--index", "--extra-index-url", "--find-links"):
        assert option not in installed
    assert seen.read_text() == "\n"  # no index from the environment either


def test_the_package_names_its_author_by_github_identity_and_is_mit_licensed() -> None:
    """The published metadata carries the author's GitHub identity — never a work address — and
    the MIT licence, whose text ships as LICENSE."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    emails = [author.get("email", "") for author in project["authors"]]
    assert emails and all(email.endswith("@users.noreply.github.com") for email in emails)
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    licence = (ROOT / "LICENSE").read_text()
    assert licence.startswith("MIT License\n")
    assert "Copyright (c) 2026 ibnelhithem" in licence


def test_the_pypi_readme_points_at_the_release_tag() -> None:
    """The copy PyPI shows uses full links to the tag. The README in the repository keeps
    its short links."""
    sample = "See [install](docs/install.md) and [uv](https://docs.astral.sh/uv/)."
    page = release.readme_for_pypi(sample, "v1.0.0")
    assert page == (
        "See [install](https://github.com/ibnelhithem/sijl/blob/v1.0.0/docs/install.md)"
        " and [uv](https://docs.astral.sh/uv/)."
    )

    source = (ROOT / "README.md").read_text()
    assert "](docs/install.md)" in source
    built = release.readme_for_pypi(source, "v1.0.0")
    assert "](docs/install.md)" not in built
    assert "https://github.com/ibnelhithem/sijl/blob/v1.0.0/docs/install.md" in built


WHEEL = "sijl-9.9.9-py3-none-any.whl"
SDIST = "sijl-9.9.9.tar.gz"


def _dist(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A dist folder with a wheel and an sdist, and the sha256 of each."""
    dist = tmp_path / "dist"
    dist.mkdir()
    digests = {}
    for name in (WHEEL, SDIST):
        (dist / name).write_bytes(name.encode())
        digests[name] = hashlib.sha256(name.encode()).hexdigest()
    return dist, digests


def _page(digests: dict[str, str]) -> dict:
    """The part of PyPI's /pypi/sijl/<version>/json answer that lists the files."""
    return {"urls": [{"filename": n, "digests": {"sha256": d}} for n, d in digests.items()]}


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://pypi.org/pypi/sijl/9.9.9/json", code, "", {}, None)


class FakePyPI:
    """Gives one answer per request (a page, or an exception to raise); the last answer repeats.
    Time moves only when the check sleeps, so a test never waits."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.urls: list[str] = []
        self.slept: list[float] = []
        self.lines: list[str] = []
        self.now = 0.0

    def fetch(self, url: str) -> dict:
        self.urls.append(url)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def verify(self, version: str, dist: Path) -> None:
        release.verify_pypi(
            version, dist, fetch=self.fetch, sleep=self.sleep, clock=lambda: self.now,
            log=self.lines.append,
        )  # fmt: skip


def test_verify_pypi_passes_when_pypi_serves_the_tested_files(tmp_path: Path) -> None:
    dist, digests = _dist(tmp_path)
    pypi = FakePyPI(_page(digests))
    pypi.verify("9.9.9", dist)
    assert pypi.urls == ["https://pypi.org/pypi/sijl/9.9.9/json"]
    assert pypi.slept == []
    assert pypi.lines == ["9.9.9: PyPI serves both files with the tested sha256"]


@pytest.mark.parametrize(
    ("first", "said"),
    [
        (_http(404), "not on PyPI yet"),
        (_http(429), "PyPI answered 429"),
        (_http(503), "PyPI answered 503"),
        (_http(501), "PyPI answered 501"),
        (http.client.IncompleteRead(b"{"), "PyPI could not be reached"),
        (urllib.error.URLError("timed out"), "PyPI could not be reached"),
        (TimeoutError(), "PyPI could not be reached"),
        ({"urls": []}, "not on PyPI yet"),
    ],
)
def test_verify_pypi_waits_while_the_version_is_missing_or_pypi_is_unwell(
    tmp_path: Path, first: object, said: str
) -> None:
    """A new version can take a moment to appear, and PyPI can fail for a moment: both are
    waited out."""
    dist, digests = _dist(tmp_path)
    pypi = FakePyPI(first, _page(digests))
    pypi.verify("9.9.9", dist)
    assert pypi.slept == [15]
    assert said in pypi.lines[0]
    assert pypi.lines[-1] == "9.9.9: PyPI serves both files with the tested sha256"


def test_verify_pypi_waits_until_both_files_are_listed(tmp_path: Path) -> None:
    dist, digests = _dist(tmp_path)
    pypi = FakePyPI(_page({WHEEL: digests[WHEEL]}), _page(digests))
    pypi.verify("9.9.9", dist)
    assert pypi.slept == [15]
    assert SDIST in pypi.lines[0]


def test_verify_pypi_stops_at_once_when_a_file_differs(tmp_path: Path) -> None:
    """Different bytes under a published name can never be fixed by waiting."""
    dist, digests = _dist(tmp_path)
    pypi = FakePyPI(_page({**digests, SDIST: "0" * 64}))
    with pytest.raises(release.ReleaseError, match=f"sha256 does not match: {SDIST}"):
        pypi.verify("9.9.9", dist)
    assert pypi.slept == []


def test_verify_pypi_stops_at_once_when_pypi_lists_a_file_that_was_not_tested(
    tmp_path: Path,
) -> None:
    """An installer could pick a file nobody tested, so any other file under the version stops
    the release."""
    dist, digests = _dist(tmp_path)
    other = "sijl-9.9.9-py3-none-macosx_11_0_arm64.whl"
    pypi = FakePyPI(_page({**digests, other: "0" * 64}))
    with pytest.raises(release.ReleaseError, match=f"not tested: {other}"):
        pypi.verify("9.9.9", dist)
    assert pypi.slept == []


def test_verify_pypi_stops_at_once_on_an_answer_without_a_file_list(tmp_path: Path) -> None:
    dist, _ = _dist(tmp_path)
    pypi = FakePyPI({"info": {}})
    with pytest.raises(release.ReleaseError, match="no file list"):
        pypi.verify("9.9.9", dist)
    assert pypi.slept == []


def test_verify_pypi_prints_each_line_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In CI the output is a pipe: an unflushed line would show only when the job ends."""

    class Pipe(io.StringIO):
        flushes = 0

        def flush(self) -> None:
            self.flushes += 1
            super().flush()

    dist, digests = _dist(tmp_path)
    pipe = Pipe()
    monkeypatch.setattr(sys, "stdout", pipe)
    release.verify_pypi("9.9.9", dist, fetch=lambda url: _page(digests))
    assert pipe.getvalue() == "9.9.9: PyPI serves both files with the tested sha256\n"
    assert pipe.flushes >= 1


def test_verify_pypi_stops_at_once_on_another_answer(tmp_path: Path) -> None:
    dist, _ = _dist(tmp_path)
    pypi = FakePyPI(_http(403))
    with pytest.raises(release.ReleaseError, match="PyPI answered 403"):
        pypi.verify("9.9.9", dist)
    assert pypi.slept == []


def test_verify_pypi_gives_up_after_five_minutes(tmp_path: Path) -> None:
    dist, _ = _dist(tmp_path)
    pypi = FakePyPI(_http(404))
    with pytest.raises(release.ReleaseError, match="not on PyPI after 5 minutes"):
        pypi.verify("9.9.9", dist)
    assert sum(pypi.slept) == 300
    assert len(pypi.urls) == 21  # at 0 s, every 15 s, and once more at the deadline


@pytest.mark.parametrize(
    "names",
    [(WHEEL,), (SDIST,), (WHEEL, SDIST, "sijl-9.9.9-py3-none-any.whl.sig")],
)
def test_verify_pypi_needs_exactly_the_wheel_and_the_sdist(
    tmp_path: Path, names: tuple[str, ...]
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in names:
        (dist / name).write_text("")
    pypi = FakePyPI(_http(404))
    with pytest.raises(release.ReleaseError, match="exactly"):
        pypi.verify("9.9.9", dist)
    assert pypi.urls == []  # nothing is asked before the folder is right


def test_the_verify_pypi_command_rejects_a_bad_version_before_asking_pypi(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dist, _ = _dist(tmp_path)
    assert release.main(["verify-pypi", "9.9", str(dist)]) == 1
    assert "'9.9' is not a plain X.Y.Z release number" in capsys.readouterr().err
