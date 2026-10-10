"""The publication guard: no tracked file — and no file of a built wheel or sdist — may hold a word
from the maintainer's private list of words that must never be published.

The list is never in this repository, in any form: the guard reads it from the file that
`PUBLICATION_GUARD_LIST` names (one word per line; blank lines and `#` comments are ignored).
The release workflow writes that file from a GitHub Actions secret; a maintainer keeps their own
copy outside the repository. Without the variable the checks against the list are skipped, with
the reason; the tests below that prove the scanner itself always run, with a harmless canary.

Every text is lower-cased and cut into candidates, each looked up in the list:

- **words**: runs of letters and digits (`[a-z0-9]+`);
- **compounds**: words joined by `-`, `_`, `.`, `@` or `+` — every contiguous part of one, so
  `a-b.c` yields `a-b`, `b.c` and `a-b.c` besides the words (a hyphenated name, an address, a
  user name with a dot);
- **path pieces**: one folder between slashes, with the slashes, and with a `~` before it
  (`/x/`, `~/x/`), overlapping, so every folder of a path is one.

A failure names the file, the line and the number of the list's entry — never the word, so a
public CI log leaks nothing. To check a built distribution as well, point
`PUBLICATION_GUARD_DIST` at the folder that holds the wheel and the sdist
(`scripts/release-check.sh` and the release workflow do).
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import tarfile
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Read at import, before any fixture changes the environment.
LIST = os.environ.get("PUBLICATION_GUARD_LIST", "")
DIST = os.environ.get("PUBLICATION_GUARD_DIST", "")

_WORD = re.compile(r"[a-z0-9]+")
_COMPOUND = re.compile(r"[a-z0-9]+(?:[-_.@+][a-z0-9]+)+")
_PIECE = re.compile(r"(?=(~?/[a-z0-9._-]+/))")
_SEPARATED = re.compile(r"([-_.@+])")
_SKIP_DIRS = {".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache", ".ruff_cache"}


def read_list(path: Path) -> dict[str, int]:
    """The forbidden words of a list file, lower-cased, each with its line number in the file."""
    words: dict[str, int] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        word = line.split("#", 1)[0].strip().lower()
        if word:
            words.setdefault(word, number)
    return words


def candidates(text: str) -> set[str]:
    """Every word, compound part and path piece of `text`, lower-cased."""
    text = text.lower()
    found = set(_WORD.findall(text))
    for compound in _COMPOUND.findall(text):
        parts = _SEPARATED.split(compound)  # word, separator, word, …
        words = parts[0::2]
        for start in range(len(words)):
            for end in range(start + 2, len(words) + 1):
                found.add("".join(parts[2 * start : 2 * end - 1]))
    found.update(match.group(1) for match in _PIECE.finditer(text))
    return found


def hits(named: Iterable[tuple[str, str]], forbidden: Mapping[str, int]) -> list[str]:
    """`where: list entry N` for every forbidden word in the (where, text) pairs, line by line.
    The entry's number, not the word, is reported."""
    found: list[str] = []
    for name, text in named:
        for number, line in enumerate(text.splitlines() or [""], 1):
            for word in sorted(candidates(line) & forbidden.keys()):
                found.append(f"{name}:{number}: list entry {forbidden[word]}")
    return found


def _text(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def tracked_files(root: Path = ROOT) -> list[Path]:
    """The files a commit would publish: tracked or not ignored. Outside a git checkout (an
    unpacked sdist), every file below `root` but caches and build output."""
    try:
        command = ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others"]
        listed = subprocess.run(
            [*command, "--exclude-standard"], capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return [
            path
            for path in root.rglob("*")
            if path.is_file() and not _SKIP_DIRS.intersection(path.relative_to(root).parts)
        ]
    return [root / name for name in listed.decode("utf-8").split("\0") if name]


def _tree(root: Path) -> Iterator[tuple[str, str]]:
    for path in tracked_files(root):
        if not path.is_file():  # deleted in the working tree
            continue
        relative = path.relative_to(root).as_posix()
        yield f"{relative} (its path)", relative  # the path itself is published too
        text = _text(path.read_bytes())
        if text is not None:
            yield relative, text


def _distribution(path: Path) -> Iterator[tuple[str, str]]:
    """Every member name and text member of a wheel or an sdist."""
    yield f"{path.name} (its name)", path.name
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as wheel:
            for info in wheel.infolist():
                yield f"{path.name}:{info.filename} (its path)", info.filename
                text = _text(wheel.read(info))
                if text is not None:
                    yield f"{path.name}:{info.filename}", text
    else:
        with tarfile.open(path) as sdist:
            for member in sdist.getmembers():
                yield f"{path.name}:{member.name} (its path)", member.name
                handle = sdist.extractfile(member) if member.isfile() else None
                text = _text(handle.read()) if handle is not None else None
                if text is not None:
                    yield f"{path.name}:{member.name}", text


def _private_list() -> dict[str, int]:
    """The maintainer's list, or a skip that says why there is none."""
    if not LIST:
        pytest.skip("PUBLICATION_GUARD_LIST is not set: the private word list was not checked")
    path = Path(LIST).expanduser()
    if not path.is_file():
        pytest.skip(f"PUBLICATION_GUARD_LIST names no file ({path}): the list was not checked")
    words = read_list(path)
    assert words, f"{path} holds no word"
    assert ROOT not in path.resolve().parents, "the list must live outside the repository"
    return words


def test_no_tracked_file_holds_a_forbidden_word() -> None:
    forbidden = _private_list()
    files = tracked_files()
    assert len(files) > 50 and Path(__file__).resolve() in files  # the walk saw the project
    assert hits(_tree(ROOT), forbidden) == []


def test_no_built_file_holds_a_forbidden_word() -> None:
    forbidden = _private_list()
    if not DIST:
        pytest.skip("PUBLICATION_GUARD_DIST names no folder of built files")
    built = sorted(Path(DIST).glob("*.whl")) + sorted(Path(DIST).glob("*.tar.gz"))
    assert built, f"no wheel or sdist in {DIST}"
    assert hits((pair for path in built for pair in _distribution(path)), forbidden) == []


# ------------------------------------------------------------- the scanner, with a canary word

CANARY = "zqcanary"  # a made-up word the tests forbid; nothing real
FORBIDDEN = {CANARY: 1}


def test_a_list_file_is_read_as_one_word_per_line(tmp_path: Path) -> None:
    listing = tmp_path / "forbidden.txt"
    listing.write_text(f"# words never to publish\n\n{CANARY.upper()}\n  x-y  # a compound\nx-y\n")
    assert read_list(listing) == {CANARY: 3, "x-y": 4}


@pytest.mark.parametrize(
    ("written", "forbidden"),
    [
        (f"a line with {CANARY.title()} in it", CANARY),  # a word, in any case
        (f"x = '{CANARY}'", CANARY),
        (f"see {CANARY}-repo/src", f"{CANARY}-repo"),  # a hyphenated name
        (f"git@host:{CANARY}_x.git", f"{CANARY}_x"),
        (f"mail {CANARY}.dev@{CANARY}corp.example", f"{CANARY}.dev@{CANARY}corp.example"),
        (f"mail {CANARY}.dev@{CANARY}corp.example", f"{CANARY}.dev"),  # the address's user
        (f"login {CANARY}.dev1 on the forge", f"{CANARY}.dev1"),
        (f"cd /srv/{CANARY}/repos", f"/{CANARY}/"),  # a folder of a path
        (f"roots = ['~/{CANARY}/code']", f"~/{CANARY}/"),
    ],
)
def test_a_forbidden_word_written_anywhere_is_found(
    tmp_path: Path, written: str, forbidden: str
) -> None:
    (tmp_path / "notes.md").write_text(f"before\n{written}\nafter\n")
    assert hits(_tree(tmp_path), {forbidden: 7}) == ["notes.md:2: list entry 7"]


def test_a_failure_never_repeats_the_word(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text(f"{CANARY}\n")
    (found,) = hits(_tree(tmp_path), FORBIDDEN)
    assert CANARY not in found


def test_a_forbidden_word_in_a_file_name_is_found(tmp_path: Path) -> None:
    (tmp_path / f"{CANARY}-notes.txt").write_text("nothing here\n")
    assert hits(_tree(tmp_path), FORBIDDEN) == [f"{CANARY}-notes.txt (its path):1: list entry 1"]


def test_a_word_that_only_contains_a_forbidden_one_is_not_found(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text(f"{CANARY}x x{CANARY} {CANARY}2\n")
    assert hits(_tree(tmp_path), FORBIDDEN) == []


def test_a_forbidden_word_inside_a_wheel_or_an_sdist_is_found(tmp_path: Path) -> None:
    wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("pkg-1.0.0.dist-info/METADATA", f"Name: pkg\nAuthor: {CANARY}\n")
    sdist = tmp_path / "pkg-1.0.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        data = b"ok\n"
        member = tarfile.TarInfo(f"pkg-1.0.0/{CANARY}/README.md")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    found = hits((pair for path in (wheel, sdist) for pair in _distribution(path)), FORBIDDEN)
    assert found == [
        f"{wheel.name}:pkg-1.0.0.dist-info/METADATA:2: list entry 1",
        f"{sdist.name}:pkg-1.0.0/{CANARY}/README.md (its path):1: list entry 1",
    ]
