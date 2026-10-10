"""sijl.git, a commit's lines: `git log --numstat` records — the SHA, then one line per
file with the lines added and removed — parsed byte by byte. A binary file is counted as a file,
never as lines; a renamed file only by what changed; a file name can never be read as numbers."""

import pytest

from sijl.git import NUMSTAT_ARGS, GitRunner, parse_numstat
from sijl.models import Lines

A, B = "a" * 40, "b" * 40


def test_each_record_gives_its_lines_and_binary_files() -> None:
    out = (
        f"\x1e{A}\n\n12\t3\tsrc/app.py\n0\t7\told.txt\n-\t-\tlogo.png\n"
        f"\x1e{B}\n\n1\t1\tsrc/{{a.py => b.py}}\n"
    ).encode()
    assert parse_numstat(out) == {A: Lines(12, 10, 1), B: Lines(1, 1, 0)}


def test_a_commit_that_changes_nothing_has_no_lines() -> None:
    assert parse_numstat(f"\x1e{A}\n".encode()) == {A: Lines(0, 0, 0)}
    assert parse_numstat(b"") == {}


@pytest.mark.parametrize(
    "out",
    [
        b"\x1enot-a-sha\n\n1\t2\tf\n",
        f"\x1e{A}\n\nx\t2\tf\n".encode(),  # not a count
        f"\x1e{A}\n\n1\tf\n".encode(),  # no removed column
        f"\x1e{A}\n\n-\t2\tf\n".encode(),  # half binary
    ],
)
def test_a_record_that_is_not_one_is_an_error(out: bytes) -> None:
    with pytest.raises(ValueError, match="numstat"):
        parse_numstat(out)


def test_the_arguments_and_the_parser_agree_on_a_real_repository(make_repo) -> None:
    repo = make_repo()
    repo.commit("chore: start", at="2026-05-01T09:00:00Z", files={"keep.txt": "a\nb\nc\n"})
    odd = "we\x1eird\nname.txt"  # git quotes control characters: it cannot split a record
    text = repo.commit(
        "feat: text",
        at="2026-05-02T09:00:00Z",
        files={"keep.txt": "a\nB\nc\nd\n", odd: "1\n2\n", "é.txt": "x\n", "bin.dat": b"\x00\x01"},
    )
    repo.git("mv", "keep.txt", "kept.txt")
    moved = repo.commit("chore: rename", at="2026-05-03T09:00:00Z", files={})
    out = GitRunner().run(repo.path, *NUMSTAT_ARGS, stdin=f"{text}\n{moved}\n".encode())
    assert parse_numstat(out) == {text: Lines(5, 1, 1), moved: Lines(0, 0, 0)}
    assert {"--no-ext-diff", "--no-textconv", "-M"} <= set(NUMSTAT_ARGS)  # danger zone
