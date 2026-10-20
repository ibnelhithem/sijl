#!/usr/bin/env python3
"""Sijl's release checks: the version, the tag and the CHANGELOG must agree.

    python3 scripts/release.py version            print the version in pyproject.toml
    python3 scripts/release.py check-tag v1.2.3   fail unless the tag is exactly "v" + that version
    python3 scripts/release.py notes 1.2.3        print CHANGELOG.md's section for 1.2.3 (the
                                                  release notes); fail if it is missing or empty
    python3 scripts/release.py readme v1.2.3     print the README PyPI shows for that tag

pyproject.toml is the one place the version is written: the installed package reads it
back from its own metadata (`sijl --version`), the release tag must repeat it, and
CHANGELOG.md must describe it. Standard library only, so the release workflow runs it before
anything is installed. Exit codes: 0 fine, 1 a check failed, 2 usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Plain X.Y.Z, no leading zeros: the tag is "v" + this, and pip/uv print it unchanged.
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
# Keep a Changelog 1.1.0: "## [Unreleased]", "## [1.2.3] - 2026-10-20", then "### Added" etc.
HEADING = re.compile(r"## \[(?P<name>[^\]]+)\](?: - (?P<date>\S+))?")
LINK_REFERENCE = re.compile(r"\[(?P<name>[^\]]+)\]: \S+")
# A Markdown link whose target is a path in this repository, not an absolute URL.
RELATIVE_LINK = re.compile(r"\]\((?!https?://)([^)\s]+)\)")
KINDS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")


class ReleaseError(Exception):
    """A release check failed; the message says what to fix."""


@dataclass(frozen=True)
class Section:
    heading: str  # the whole "## ..." line
    name: str | None  # "Unreleased" or "1.2.3"; None when the heading is malformed
    day: str | None  # the "YYYY-MM-DD" after the name, as written
    body: str  # everything up to the next "## " heading or the link references


def read_version(pyproject: Path) -> str:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def readme_for_pypi(text: str, tag: str) -> str:
    """The copy PyPI shows: each relative Markdown link points at this tag. Absolute links stay.
    The README stored in the repository is not changed."""
    prefix = f"https://github.com/ibnelhithem/sijl/blob/{tag}/"

    def rewrite(match: re.Match[str]) -> str:
        return f"]({prefix}{match.group(1)})"

    return RELATIVE_LINK.sub(rewrite, text)


def check_tag(tag: str, version: str) -> None:
    """The tag being released must be exactly "v" + pyproject's version."""
    if not VERSION.fullmatch(version):
        raise ReleaseError(
            f"pyproject.toml's version {version!r} is not a plain X.Y.Z release number"
        )
    if tag != f"v{version}":
        raise ReleaseError(
            f"tag {tag!r} does not match pyproject.toml's version {version}: the tag must be "
            f"v{version} — or set the version first (`uv version X.Y.Z`) and tag that commit"
        )


def changelog_sections(text: str) -> list[Section]:
    """Split CHANGELOG.md at its "## " headings; the body stops at the link references that
    Keep a Changelog puts at the very end ("[1.0.0]: https://...")."""
    sections: list[Section] = []
    heading: str | None = None
    body: list[str] = []

    def close() -> None:
        if heading is not None:
            match = HEADING.fullmatch(heading)
            sections.append(
                Section(
                    heading=heading,
                    name=match["name"] if match else None,
                    day=match["date"] if match else None,
                    body="\n".join(body).strip(),
                )
            )

    for line in text.splitlines():
        if line.startswith("## "):
            close()
            heading, body = line.rstrip(), []
        elif LINK_REFERENCE.fullmatch(line.strip()):
            close()
            heading = None  # link references end the last section
        elif heading is not None:
            body.append(line)
    close()
    return sections


def release_notes(text: str, version: str) -> str:
    """The body of the version's section: what the GitHub release says."""
    for section in changelog_sections(text):
        if section.name == version:
            if not section.body:
                raise ReleaseError(f"CHANGELOG.md's section for {version} is empty")
            return section.body
    raise ReleaseError(
        f"CHANGELOG.md has no section for {version}: move the [Unreleased] entries under a new "
        f"heading '## [{version}] - YYYY-MM-DD' (the release day) and add its link reference"
    )


def _is_day(text: str) -> bool:
    """A real calendar day written YYYY-MM-DD (fromisoformat alone also takes 20261020)."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in name.split("."))


def changelog_problems(text: str) -> list[str]:
    """Everything that breaks the Keep a Changelog shape this project relies on, in plain words;
    empty when the file is well formed."""
    problems: list[str] = []
    sections = changelog_sections(text)
    references = {
        match["name"].lower()
        for line in text.splitlines()
        if (match := LINK_REFERENCE.fullmatch(line.strip()))
    }
    names = [s.name for s in sections]
    if "Unreleased" not in names:
        problems.append("no '## [Unreleased]' section: new entries go there")
    elif names[0] != "Unreleased":
        problems.append("'## [Unreleased]' must be the first section")

    released: list[str] = []
    for section in sections:
        if section.name == "Unreleased" and section.day is None:
            pass
        elif section.name is None or not VERSION.fullmatch(section.name):
            problems.append(
                f"heading {section.heading!r}: expected '## [Unreleased]' or "
                "'## [X.Y.Z] - YYYY-MM-DD'"
            )
            continue
        elif section.day is None:
            problems.append(f"heading {section.heading!r}: no release day ('- YYYY-MM-DD')")
        else:
            if not _is_day(section.day):
                problems.append(f"heading {section.heading!r}: {section.day} is not YYYY-MM-DD")
            if section.name in released:
                problems.append(f"version {section.name} appears twice")
            elif released and _version_key(section.name) > _version_key(released[-1]):
                problems.append(f"version {section.name} comes after {released[-1]}: newest first")
            released.append(section.name)
        if section.name.lower() not in references:
            problems.append(f"no link reference '[{section.name}]: <url>' at the end of the file")
        for line in section.body.splitlines():
            if line.startswith("### ") and line[4:].strip() not in KINDS:
                problems.append(
                    f"{line.strip()!r} under [{section.name}]: use one of {', '.join(KINDS)}"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="print the version in pyproject.toml")
    tag_parser = commands.add_parser("check-tag", help="the tag must be v + the version")
    tag_parser.add_argument("tag")
    notes_parser = commands.add_parser("notes", help="print the version's CHANGELOG section")
    notes_parser.add_argument("version")
    notes_parser.add_argument("--changelog", type=Path, default=ROOT / "CHANGELOG.md")
    readme_parser = commands.add_parser("readme", help="print the README PyPI shows for this tag")
    readme_parser.add_argument("tag")
    args = parser.parse_args(argv)

    try:
        if args.command == "version":
            print(read_version(ROOT / "pyproject.toml"))
        elif args.command == "check-tag":
            check_tag(args.tag, read_version(ROOT / "pyproject.toml"))
        elif args.command == "readme":
            text = (ROOT / "README.md").read_text(encoding="utf-8")
            sys.stdout.write(readme_for_pypi(text, args.tag))
        else:
            print(release_notes(args.changelog.read_text(encoding="utf-8"), args.version))
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
