"""sijl.tickets: Jira keys found strictly, where each was written, and the ids the
false-positive guard refuses — standards, versions, parts of longer ids, leading zeros."""

import time

import pytest

from sijl.tickets import STANDARDS, Mentions, branch_names, find_keys, scan


@pytest.mark.parametrize(
    ("text", "keys", "refused"),
    [
        ("fix PROJ-1234 retry", ["PROJ-1234"], []),
        ("OPS-12,OPS-13", ["OPS-12", "OPS-13"], []),
        ("see https://jira.acme.test/browse/TASK-2.", ["TASK-2"], []),  # a sentence's full stop
        ("PROJ_SUB-7 and X2-9", ["PROJ_SUB-7", "X2-9"], []),  # `_` and digits after a letter
        ("QC-4's flaky step", ["QC-4"], []),
        ("feature/PROJ-12_fix", ["PROJ-12"], []),  # `_` after the number separates words
        # the false-positive guard
        ("UTF-8", [], [("UTF-8", "standard")]),
        # quoted as the id itself, never with a word hyphenated onto it
        ("non-UTF-8 text", [], [("UTF-8", "standard")]),
        ("UTF-8-encoded", [], [("UTF-8", "standard")]),
        ("x.UTF-8.", [], [("UTF-8", "standard")]),
        ("re-TC-UI-07-fix", [], [("TC-UI-07", "longer id")]),  # TC- belongs to the id; re- not
        ("PROJ_SUB-UI-7", [], [("PROJ_SUB-UI-7", "longer id")]),
        ("SHA-256 sums", [], [("SHA-256", "standard")]),
        ("dates in ISO-8601", [], [("ISO-8601", "standard")]),
        ("RFC-1234 says", [], [("RFC-1234", "standard")]),
        ("HTTP-2 push", [], [("HTTP-2", "standard")]),
        ("patch CVE-2024-1234", [], [("CVE-2024-1234", "standard")]),
        ("AVX-512 and X86-64", [], [("AVX-512", "standard"), ("X86-64", "standard")]),
        ("TC-UI-07", [], [("TC-UI-07", "longer id")]),  # never UI-07 out of TC-UI-07
        ("TC-UI-7", [], [("TC-UI-7", "longer id")]),  # not even without the leading zero
        ("ABC-2024-01", [], [("ABC-2024-01", "longer id")]),  # followed by -<digit>
        ("ABC-12abc", [], [("ABC-12abc", "longer id")]),
        ("RELEASE-1.2", [], [("RELEASE-1.2", "version")]),
        ("PY-3.12 only", [], [("PY-3.12", "version")]),
        ("QC-007", [], [("QC-007", "leading zero")]),
        ("ABC-0", [], [("ABC-0", "leading zero")]),
        # not ticket-shaped at all: nothing found, nothing refused
        ("proj-123 abc-1 R-1 part-2 Python-3", [], []),  # lower case, one letter
        ("xABC-1 ABC1-2x", [], [("ABC1-2x", "longer id")]),  # glued to a word before it
        # glued to a letter of any alphabet: not a key, not even a candidate
        ("GRÖSSE-2", [], []),  # never SSE-2
        ("\u0410BC-7", [], []),  # a Cyrillic capital A: never BC-7
        ("caféABC-1", [], []),
        ("ABC-12é", [], [("ABC-12é", "longer id")]),  # written in full, accent included
        ("ÜTC-UI-7", [], [("ÜTC-UI-7", "longer id")]),  # an upper-case word in any alphabet
    ],
)
def test_scan_finds_keys_and_refuses_look_alikes(text: str, keys, refused) -> None:
    assert scan(text) == (keys, refused)


def test_hyphens_between_keys_in_a_branch_name_do_not_join_them() -> None:
    """`TC-UI-07` is one id; `OPS-12-OPS-13` is two keys — the hyphen follows a number."""
    assert scan("feature/OPS-12-OPS-13-retry")[0] == ["OPS-12", "OPS-13"]
    assert scan("feature-ABC-123-x")[0] == ["ABC-123"]  # after a lower-case word: a key


def test_every_standard_is_a_project_shaped_word() -> None:
    assert all(s.isupper() and s[0].isalpha() for s in STANDARDS)
    assert {"UTF", "SHA", "ISO", "RFC", "CVE", "HTTP"} <= STANDARDS


@pytest.mark.parametrize(
    ("subject", "names"),
    [
        ("Merge branch 'feature/ABC-123-x'", ("feature/ABC-123-x",)),
        ("Merge branch 'feature/ABC-123-x' into 'main'", ("feature/ABC-123-x", "main")),
        ("Merge branch 'main' into feature/PROJ-4", ("main", "feature/PROJ-4")),
        ("Merge remote-tracking branch 'origin/x' into y", ("origin/x", "y")),
        ("Merge pull request #12 from me/feature/OPS-3-x", ("feature/OPS-3-x",)),  # not `me`
        ("Merge pull request #7 from dev42/feature-login", ("feature-login",)),
        ("Merge branches 'a' and 'b'", ("a", "b")),
        ("Merged the feature/ABC-1 work", ()),  # not git's merge subject
        ("feat: merge branch handling", ()),
    ],
)
def test_branch_names_come_from_merge_subjects(subject: str, names) -> None:
    assert branch_names(subject) == names


def keys_of(subject: str, body: str = "", trailers=(), *, scope="", lead="", projects=()):
    found = find_keys(subject, body or f"{subject}\n", trailers, scope=scope, lead=lead,
                      projects=projects)  # fmt: skip
    return {key: set(positions) for key, positions in found.keys.items()}


@pytest.mark.parametrize(
    ("subject", "body", "trailers", "parsed", "expected"),
    [
        ("feat(PROJ-1): x", "", (), {"scope": "PROJ-1"}, {"PROJ-1": {"scope"}}),
        ("test(OPS-1,OPS-2): x", "", (), {"scope": "OPS-1,OPS-2"},
         {"OPS-1": {"scope"}, "OPS-2": {"scope"}}),
        ("PROJ-2: wire it", "", (), {"lead": "PROJ-2"}, {"PROJ-2": {"lead"}}),
        ("fix: retry (PROJ-3)", "", (), {}, {"PROJ-3": {"trailing"}}),
        ("test: cover it [QC-4, QC-5]", "", (), {}, {"QC-4": {"trailing"}, "QC-5": {"trailing"}}),
        ("fix: flaky TC-13 step", "", (), {}, {"TC-13": set()}),  # running text: no position
        ("Merge branch 'feature/ABC-123-x'", "", (), {}, {"ABC-123": {"branch"}}),
        ("chore: tidy", "chore: tidy\n\nCloses TASK-2\n", (), {}, {"TASK-2": {"closing"}}),
        ("chore: tidy", "chore: tidy\n\n- refs: TASK-3, TASK-4 and TASK-5\n", (), {},
         {"TASK-3": {"closing"}, "TASK-4": {"closing"}, "TASK-5": {"closing"}}),
        ("chore: tidy", "chore: tidy\n\nResolves the issue in TC-9\n", (), {}, {"TC-9": set()}),
        ("refactor: x", "refactor: x\n\nJira: PROJ-5\n", (("jira", "PROJ-5"),), {},
         {"PROJ-5": {"trailer"}}),
        ("refactor: x", "refactor: x\n\nCo-authored-by: AB-1 <a@b.test>\n",
         (("co-authored-by", "AB-1 <a@b.test>"),), {}, {"AB-1": set()}),  # a person, not a ticket
    ],
)  # fmt: skip
def test_each_key_says_where_it_was_written(subject, body, trailers, parsed, expected) -> None:
    assert keys_of(subject, body, trailers, **parsed) == expected


def test_a_key_is_found_once_per_commit_however_often_it_is_written() -> None:
    body = "feat(PROJ-1): x (PROJ-1)\n\nMore on PROJ-1.\n\nRefs PROJ-1\n"
    found = keys_of("feat(PROJ-1): x (PROJ-1)", body, scope="PROJ-1")
    assert found == {"PROJ-1": {"scope", "trailing", "closing"}}


def test_guard_refusals_are_listed_as_written_and_never_as_keys() -> None:
    found = find_keys("docs: UTF-8 and TC-UI-07", "docs: UTF-8 and TC-UI-07\n\nSHA-256\n")
    assert found == Mentions(keys={}, rejected=frozenset({"UTF-8", "TC-UI-07", "SHA-256"}))


@pytest.mark.parametrize(
    ("subject", "projects", "expected"),
    [
        ("Merge branch 'Feature/proj 1234' into 'main'", ("PROJ",), {"PROJ-1234"}),
        ("Merge branch 'feature/proj_1234_retry'", ("PROJ",), {"PROJ-1234"}),
        ("Merge branch 'proj-12-retry'", ("PROJ",), {"PROJ-12"}),  # at the start of the name
        ("Merge branch 'feature/proj_12_fix'", ("PROJ",), {"PROJ-12"}),  # `_` separates words
        ("Merge pull request #7 from me/feature/proj-5", ("PROJ",), {"PROJ-5"}),
        # the project and the number are whole words of the name
        ("Merge branch 'feature/proj1234'", ("PROJ",), set()),  # no separator: one word
        ("Merge branch 'feature/PROJ2-5-retry'", ("PROJ",), {"PROJ2-5"}),  # project PROJ2, not PROJ
        ("Merge branch 'feature/proj-12abc'", ("PROJ",), set()),  # digits glued to letters
        ("Merge pull request #7 from dev42/feature-login", ("DEV",), set()),  # the owner
        ("Merge branch 'feature/tc-proj-5'", ("PROJ",), set()),  # tc-proj-5: a longer id
        ("Merge branch 'feature-proj-5'", ("PROJ",), set()),  # the same shape: not read
        ("Merge branch 'feature/proj 1234'", (), set()),  # lenient only for your projects
        ("Merge branch 'feature/proj_misc'", ("PROJ",), set()),  # no number
        ("Merge branch 'feature/proj-0123'", ("PROJ",), set()),  # a leading zero
        ("Merge branch 'feature/proj-1.2'", ("PROJ",), set()),  # a version
        ("Merge branch 'feature/xproj-12'", ("PROJ",), set()),  # glued to a word
        ("Merge branch 'feature/TC-PROJ-12'", ("PROJ",), set()),  # part of a longer id
        ("fix: proj 1234 retry", ("PROJ",), set()),  # lenient in branch names only
    ],
)
def test_branch_names_match_your_projects_leniently(subject: str, projects, expected) -> None:
    assert set(keys_of(subject, projects=projects)) == expected


SIZE = 100_000  # a semantic-release or squash body full of keys reaches this size


@pytest.mark.parametrize(
    "body",
    [
        ("see PROJ-1 " * SIZE)[:SIZE],  # one key, repeated
        " ".join(f"PROJ-{i}" for i in range(1, SIZE // 10))[:SIZE],  # distinct keys
        "Refs " + " ".join(f"PROJ-{i}" for i in range(1, SIZE // 10))[:SIZE],  # on one Refs line
        ".".join("AB-0" for _ in range(SIZE // 5))[:SIZE],  # one id run of refused candidates
        " ".join(f"QC-0{i}" for i in range(1, SIZE // 9))[:SIZE],  # distinct refused ids
    ],
    ids=["repeated", "distinct", "refs-line", "one-run", "refused"],
)
def test_a_100kb_body_is_read_in_linear_time(body: str) -> None:
    started = time.perf_counter()
    find_keys("fix: x", f"fix: x\n\n{body}\n", projects=("PROJ",))
    assert time.perf_counter() - started < 0.5


def test_a_refused_id_inside_a_long_run_is_written_as_the_match_itself() -> None:
    """A run of id characters longer than the 40 shown is not quoted whole: the id alone."""
    found = find_keys("x", "x\n\n" + ".".join("AB-0" for _ in range(50)) + "\n")
    assert found.rejected == frozenset({"AB-0"})
