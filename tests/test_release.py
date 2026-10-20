"""Release checks (scripts/release.py): one version, written in pyproject.toml, that the installed
package, the release tag and CHANGELOG.md all repeat — checked before anything is published
."""

import importlib.util
import subprocess
import sys
import tomllib
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
