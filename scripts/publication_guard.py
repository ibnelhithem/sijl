"""The publication guard: no published text may hold a word from the maintainer's private list.

One matcher for every place that checks (ADR 0003): its test (`tests/test_publication_guard.py`),
the `guard` workflow, and the `pre-push` hook (`scripts/hooks/`). It is development tooling, not
part of Sijl: it is not in the wheel, and it uses only the standard library, so a hook can run
it with any `python3` (3.9 or newer).

The list is never in this repository, in any form: it is the file `--list` or
`PUBLICATION_GUARD_LIST` names (one word per line; blank lines and `#` comments are ignored).
Every text is lower-cased and cut into candidates, each looked up in the list:

- **words**: runs of letters and digits (`[a-z0-9]+`);
- **compounds**: words joined by `-`, `_`, `.`, `@` or `+` — every contiguous part of one, so
  `a-b.c` yields `a-b`, `b.c` and `a-b.c` besides the words (a hyphenated name, an address, a
  user name with a dot);
- **path pieces**: one folder between slashes, with the slashes, and with a `~` before it
  (`/x/`, `~/x/`), overlapping, so every folder of a path is one.

A finding names the place, the line and the number of the list's entry — never the word, so a
public CI log leaks nothing:

    publication_guard.py [--list FILE] tree             the files a commit would publish
    publication_guard.py [--list FILE] commits REV...   every commit `git rev-list REV...` lists:
                                                        its author, committer and message, and
                                                        the path and text of each file it adds
                                                        or changes
    publication_guard.py [--list FILE] text WHERE       standard input, reported as WHERE
    publication_guard.py [--list FILE] pull-request EVENT
                                                        a pull request's branch name, title and
                                                        body, from the event file GitHub writes
                                                        ($GITHUB_EVENT_PATH)

Run it inside the repository to check. Exit status: 0 nothing found, 1 a listed word was found,
2 no list, or a usage or git error.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_WORD = re.compile(r"[a-z0-9]+")
_COMPOUND = re.compile(r"[a-z0-9]+(?:[-_.@+][a-z0-9]+)+")
_PIECE = re.compile(r"(?=(~?/[a-z0-9._-]+/))")
_SEPARATED = re.compile(r"([-_.@+])")
_SKIP_DIRS = {".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache", ".ruff_cache"}

USAGE = (
    "usage: publication_guard.py [--list FILE]"
    " (tree | commits REV... | text WHERE | pull-request EVENT)"
)
# git as this guard runs it: the objects as they will be pushed, never a replacement, and never
# a fetch from a promisor remote.
_GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1"}


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


def _git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        input=stdin,
        stdin=None if stdin is not None else subprocess.DEVNULL,
        capture_output=True,
        env={**os.environ, **_GIT_ENV},
        check=True,
    ).stdout


def tracked_files(root: Path = ROOT) -> list[Path]:
    """The files a commit would publish: tracked or not ignored. Outside a git checkout (an
    unpacked sdist), every file below `root` but caches and build output."""
    try:
        listed = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    except (OSError, subprocess.CalledProcessError):
        return [
            path
            for path in root.rglob("*")
            if path.is_file() and not _SKIP_DIRS.intersection(path.relative_to(root).parts)
        ]
    return [root / name for name in listed.decode("utf-8").split("\0") if name]


def tree(root: Path) -> Iterator[tuple[str, str]]:
    """The path and text of every file a commit would publish."""
    for path in tracked_files(root):
        if not path.is_file():  # deleted in the working tree
            continue
        relative = path.relative_to(root).as_posix()
        yield f"{relative} (its path)", relative  # the path itself is published too
        text = _text(path.read_bytes())
        if text is not None:
            yield relative, text


def distribution(path: Path) -> Iterator[tuple[str, str]]:
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


# Asked of git per commit, NUL-separated: no field can hold a NUL byte.
_FIELDS = ("%H", "%P", "%an", "%ae", "%cn", "%ce", "%B")
_GITLINK = "160000"  # a submodule: its "content" is a commit id in another repository


def commits(root: Path, revisions: Sequence[str]) -> Iterator[tuple[str, str]]:
    """Author, committer and message of every commit `git rev-list REVISIONS` lists, and the
    path and text of each file it adds or changes (against its first parent)."""
    output = _git(
        root,
        *("-c", "log.showSignature=false"),
        *("log", "-z", "--no-color", "--format=" + "%x00".join(_FIELDS), *revisions, "--"),
    ).decode("utf-8", "replace")
    fields = output.split("\0")[:-1]  # every record ends with a NUL
    if len(fields) % len(_FIELDS):
        raise ValueError("git log returned a record this guard cannot split")
    for start in range(0, len(fields), len(_FIELDS)):
        sha, parents, author, author_email, committer, committer_email, message = fields[
            start : start + len(_FIELDS)
        ]
        where = f"commit {sha[:12]}"
        yield f"{where}, author name", author
        yield f"{where}, author email", author_email
        yield f"{where}, committer name", committer
        yield f"{where}, committer email", committer_email
        yield f"{where}, message", message
        yield from _changed_files(root, where, sha, parents.split())


def _changed_files(
    root: Path, where: str, sha: str, parents: Sequence[str]
) -> Iterator[tuple[str, str]]:
    against = [parents[0], sha] if parents else ["--root", sha]
    raw = _git(root, "diff-tree", "-r", "-z", "--no-renames", "--no-commit-id", *against)
    entries = raw.decode("utf-8", "replace").split("\0")[:-1]  # ":modes ids status", path, …
    blobs: list[tuple[str, str]] = []
    for index in range(0, len(entries), 2):  # no zip(strict=): Python 3.9 has none
        meta, path = entries[index], entries[index + 1]
        _old_mode, new_mode, _old_id, new_id, status = meta.lstrip(":").split()
        if status == "D":  # nothing of it is published by this commit
            continue
        yield f"{where}, {path} (its path)", path
        if new_mode != _GITLINK:
            blobs.append((path, new_id))
    if blobs:
        contents = _blobs(root, [blob for _, blob in blobs])
        for path, blob in blobs:
            text = _text(contents[blob])
            if text is not None:
                yield f"{where}, {path}", text


def _blobs(root: Path, ids: Sequence[str]) -> dict[str, bytes]:
    """The content of each blob, read in one `git cat-file --batch`."""
    output = _git(root, "cat-file", "--batch", stdin="".join(f"{i}\n" for i in ids).encode())
    contents: dict[str, bytes] = {}
    position = 0
    for blob in ids:
        end = output.index(b"\n", position)
        header = output[position:end].split()  # <id> <type> <size>
        size = int(header[2])
        contents[blob] = output[end + 1 : end + 1 + size]
        position = end + 1 + size + 1  # the content, then one newline
    return contents


def pull_request(event: Path) -> list[tuple[str, str]]:
    """The branch name, title and body of the pull request in a GitHub event file. Read from the
    file, not passed as variables: a step's variables are printed in its public log."""
    request = json.loads(event.read_text(encoding="utf-8"))["pull_request"]
    return [
        ("branch name", request["head"]["ref"]),
        ("pull request title", request["title"]),
        ("pull request body", request["body"] or ""),  # null when it was left empty
    ]


def _load_list(given: str) -> dict[str, int]:
    """The list `--list` or PUBLICATION_GUARD_LIST names; SystemExit(2) saying why there is none."""
    if not given:
        raise SystemExit(
            "publication guard: no word list: pass --list FILE or set PUBLICATION_GUARD_LIST"
        )
    path = Path(given).expanduser()
    if not path.is_file():
        raise SystemExit(f"publication guard: the word list names no file: {path}")
    if ROOT in path.resolve().parents:
        raise SystemExit("publication guard: the word list must live outside the repository")
    words = read_list(path)
    if not words:
        raise SystemExit(f"publication guard: the word list holds no word: {path}")
    return words


def _revision(argument: str) -> str:
    # Revisions and the two options a pre-push hook needs; never an option that writes a file.
    if argument.startswith("-") and argument != "--not" and not argument.startswith("--remotes="):
        raise SystemExit(f"publication guard: not a revision: {argument}\n{USAGE}")
    return argument


def main(argv: Sequence[str]) -> int:
    arguments = list(argv)
    given = os.environ.get("PUBLICATION_GUARD_LIST", "")
    if arguments[:1] == ["--list"] and len(arguments) > 1:
        given, arguments = arguments[1], arguments[2:]
    mode, rest = (arguments[0], arguments[1:]) if arguments else ("", [])
    if not (
        (mode == "tree" and not rest)
        or (mode == "commits" and rest)
        or (mode in ("text", "pull-request") and len(rest) == 1)
    ):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        forbidden = _load_list(given)
        root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        if mode == "tree":
            named = list(tree(root))
        elif mode == "commits":
            named = list(commits(root, [_revision(argument) for argument in rest]))
        elif mode == "pull-request":
            named = pull_request(Path(rest[0]))
        else:
            named = [(rest[0], sys.stdin.buffer.read().decode("utf-8", "replace"))]
    except SystemExit as stop:
        print(stop.code, file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as failed:
        error = failed.stderr.decode("utf-8", "replace").strip()
        print(f"publication guard: git failed: {error}", file=sys.stderr)
        return 2
    except Exception as failed:  # only its name: the message could quote the list or a text
        print(f"publication guard: failed ({type(failed).__name__})", file=sys.stderr)
        return 2
    found = hits(named, forbidden)
    for line in found:
        print(line)
    if found:
        print(
            f"publication guard: {len(found)} place(s) hold a word from the list. The entry"
            " numbers say which; the words are never printed.",
            file=sys.stderr,
        )
        return 1
    print(f"publication guard: no listed word in {len(named)} text(s) ({mode})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
