"""sijl.digest: the evidence of one person's period as compact, deterministic text —
grouped by ticket, then request, then repository, each line starting with an id to cite;
colleagues' names and addresses redacted; the same input always the same bytes."""

import os
import re
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from sijl import __version__, digest
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.report import Report, build_report
from tests.filesystem import require_raw_byte_names
from tests.gitfactory import COLLEAGUE, LOOKALIKE, ME, ME_GITHUB, REVIEW_PERIOD, RepoBuilder
from tests.test_deliveries import at, started
from tests.test_report_squash import three_squashes

MINE = IdentitySet.from_emails([ME.email, ME_GITHUB.email])
SAM_TRAILER = f"Co-authored-by: {COLLEAGUE.name} <{COLLEAGUE.email}>"
JANE_TRAILER = "Co-authored-by: Jane Pair <jane.pair@acme.test>"  # a co-author, never an author


def webapp(make_repo, tmp_path: Path) -> tuple[RepoBuilder, dict[str, str]]:
    """A GitLab project: !42 (yours, PROJ-12, merged by a colleague), !147 (yours, no ticket,
    merged by you), !148 (a colleague's, merged by you); two commits straight on main naming
    colleagues in their subjects; one not pushed."""
    repo = started(make_repo, "webapp")
    shas = {}
    repo.branch("feature/PROJ-12", "main")
    repo.switch("feature/PROJ-12")
    shas["billing"] = repo.commit("feat(PROJ-12): billing flow", at=at(4), files={"b.txt": "x"})
    shas["retry"] = repo.commit("fix: retry for PROJ-12 and PROJ-13", at=at(5),
                                files={"r.txt": "x"},
                                trailers=[SAM_TRAILER, JANE_TRAILER])  # fmt: skip
    repo.switch("main")
    repo.gitlab_mr_merge("feature/PROJ-12", number=42, title="Billing (PROJ-12)", at=at(6),
                         who=COLLEAGUE, project="acme/platform/webapp")  # fmt: skip
    repo.branch("feature/tidy", "main")
    repo.switch("feature/tidy")
    shas["tidy"] = repo.commit("refactor: tidy the parser", at=at(7), files={"t.txt": "x"})
    repo.switch("main")
    repo.gitlab_mr_merge("feature/tidy", number=147, title="Tidy", at=at(8),
                         project="acme/platform/webapp")  # fmt: skip
    repo.commit("fix: their typo", at=at(9), who=LOOKALIKE, files={"l.txt": "x"})
    shas["credit"] = repo.commit(f"docs: credit sam@sam-laptop, {COLLEAGUE.name} and Jane Pair",
                                 at=at(11), files={"c.txt": "x"})  # fmt: skip
    shas["wide"] = repo.commit("chore: ask sam.colleague\uff20acme.test", at=at(12),
                               files={"w.txt": "x"})  # fmt: skip
    repo.branch("feature/theirs", "main")
    repo.switch("feature/theirs")
    repo.commit("feat: their part", at=at(13), who=COLLEAGUE, files={"p.txt": "x"})
    repo.switch("main")
    repo.gitlab_mr_merge("feature/theirs", number=148, title="Their feature", at=at(14),
                         project="acme/platform/webapp")  # fmt: skip
    repo.publish(tmp_path / "remotes" / "webapp.git", "--all")
    repo.git("remote", "set-url", "origin", "git@gitlab.com:acme/platform/webapp.git")
    repo.branch("wip", "main")
    repo.switch("wip")
    shas["wip"] = repo.commit(f"feat: draft PROJ-14 with {LOOKALIKE.name}", at=at(15),
                              files={"d.txt": "x"})  # fmt: skip
    repo.switch("main")
    return repo, shas


def build(*paths: Path, roots=()) -> Report:
    return build_report(
        roots=list(roots),
        paths=list(paths),
        identities=MINE,
        window=REVIEW_PERIOD,
        git=GitRunner(),
        generated_on=date(2026, 10, 8),
        person=ME.name,
        jira_projects=["PROJ"],
    )


def scenario(make_repo, tmp_path: Path) -> tuple[Report, dict[str, str]]:
    repo, shas = webapp(make_repo, tmp_path)
    app, squashes = three_squashes(make_repo, tmp_path)
    return build(repo.path, app.path), shas | squashes


def text_of(report: Report, **options) -> str:
    return digest.build(report, redactor=digest.redactor_for(report), **options).text


def expected(s: dict[str, str]) -> str:
    """The whole digest of `scenario()`, as the user and Claude read it."""
    short = {name: sha[:8] for name, sha in s.items()}
    webapp, app = "webapp", "app"
    return f"""\
# Sijl digest — evidence of one person's own work
Generated 2026-10-08 by Sijl {__version__} from local git repositories. Every number is Sijl's own exact count; every line of evidence starts with an id to cite.
Person: Alex Example (40400001+alexgh@users.noreply.github.com, alex.example@acme.test)
Redaction: on — every other person's name is written [person N] and every other email address [email N]; one person keeps one N throughout.
Period: 2026-04-01 to 2026-06-30 — inclusive; by author date, in each commit's own timezone
Ids: a commit by its short SHA (as listed), a GitLab merge request as <repository>!N, a GitHub pull request as <repository>#N, a ticket by its key.
Repositories (each by its id; "=" gives the folder's name where it differs): app, webapp
Totals — exact; use these rather than counting the lines below:
- 10 counted commits on 7 active days in 2 repositories; longest streak 2 weekdays, 2026-05-04 to 2026-05-05 (weekends neither break nor extend a streak; a weekday without a commit ends it)
- per repository: app 5, webapp 5
- per month (the others had none): 2026-05 10 commits on 7 days
- per kind: feat 4, fix 1, docs 1, refactor 1, chore 1, other 2; breaking changes: 0
- per review category: feature 4, fix 1, quality 1, docs 1, tooling 1, other 2
- tickets of PROJ: 2, named by 2 of the 10 counted commits
- requests of yours merged: 5 (1 merged by you, 1 by a colleague, 3 as GitHub squash merges (merger not recorded)); requests you merged for colleagues: 1
- not pushed yet: 1 commit, listed last and in no number above
Not in this digest: commit messages beyond their first line (bodies and trailers, co-authors included); merge commits (a request is listed instead; a merge is never counted as a commit). `sijl report` explains every limit under Caveats.
Omitted: nothing — every commit, request and ticket found is listed.

## Tickets — each counted commit naming a ticket, under every ticket it names
### PROJ-12 — 2 commits, 1 request; 2026-05-04 to 2026-05-06; requests: {webapp}!42
- {short["retry"]} 2026-05-05 [webapp] fix: retry for PROJ-12 and PROJ-13 → {webapp}!42
- {short["billing"]} 2026-05-04 [webapp] feat(PROJ-12): billing flow → {webapp}!42
### PROJ-13 — 1 commit; on 2026-05-05
- {short["retry"]} 2026-05-05 [webapp] fix: retry for PROJ-12 and PROJ-13 → {webapp}!42

## Requests — merge requests and pull requests merged in the period: yours, newest first, then those you merged for colleagues; under each, its commits that name no ticket. Squash-merged: a GitHub squash merge, whose merger git does not record
### {app}#14 — 2026-05-12 [app]; squash-merged; the squash counted as code, +1 -0 lines (not linked: commits of yours may repeat it); title: X
- {short["unlinked"]} 2026-05-12 X (#14)
### {app}#13 — 2026-05-09 [app]; squash-merged; the squash counted as code, +1 -0 lines (its branch commits are gone); title: Gone
- {short["promoted"]} 2026-05-09 Gone (#13)
### {webapp}!147 — 2026-05-08 [webapp]; merged by you; 1 of 1 commit yours; title: Tidy
- {short["tidy"]} 2026-05-07 refactor: tidy the parser
### {app}#12 — 2026-05-07 [app]; squash-merged; 2 linked commits counted instead of the squash; title: Fix a typo
- {short["two"]} 2026-05-05 feat: fix/typo 5
- {short["one"]} 2026-05-04 feat: fix/typo 4
### {webapp}!42 — 2026-05-06 [webapp]; merged by a colleague; 2 of 2 commits yours; tickets: PROJ-12; title: Billing (PROJ-12)
### {webapp}!148 — 2026-05-14 [webapp]; merged by you for a colleague; 1 commit, none yours; title: Their feature

## Repositories — the other counted commits, in no ticket and no request; busiest repository first
### app — 5 commits in all; 1 below
- {short["kept"]} 2026-05-10 feat: feature/x 10
### webapp — 5 commits in all; 2 below
- {short["wide"]} 2026-05-12 chore: ask [email 1]
- {short["credit"]} 2026-05-11 docs: credit [email 2], [person 1] and [person 2]

## Not pushed yet — your commits on no remote branch; no number above counts them
### webapp — 1 commit
- {short["wip"]} 2026-05-15 (local branch) feat: draft PROJ-14 with [person 3]
"""  # noqa: E501


def test_the_digest_groups_evidence_by_ticket_then_request_then_repository(
    make_repo, tmp_path
) -> None:
    report, shas = scenario(make_repo, tmp_path)
    assert text_of(report) == expected(shas)


def test_every_number_is_read_from_the_insights_never_counted_again(make_repo, tmp_path) -> None:
    """The header's numbers are the Insights': change them and the digest follows."""
    report, _ = scenario(make_repo, tmp_path)
    tickets = report.insights.tickets
    altered = replace(
        report.insights,
        total=999,
        delivered=77,
        tickets=replace(tickets, per_ticket=(replace(tickets.per_ticket[0], commits=42),)),
    )
    text = text_of(replace(report, insights=altered))
    assert "- 999 counted commits on 7 active days" in text
    assert "- requests of yours merged: 77 (" in text
    assert "### PROJ-12 — 42 commits, 1 request;" in text


# ---------------------------------------------------------------------------------- redaction


def test_colleagues_names_and_addresses_are_redacted_yours_are_kept(make_repo, tmp_path) -> None:
    """Every shape the fixture holds: git's `user@host`, a full-width at sign, a colleague's and
    a look-alike's name in a subject, and a co-author known only from a trailer (the digest
    shows no trailer, but the redactor learns their names from them)."""
    report, _ = scenario(make_repo, tmp_path)
    redactor = digest.redactor_for(report)
    result = digest.build(report, redactor=redactor)
    for gone in (COLLEAGUE.name, COLLEAGUE.email, "sam@sam-laptop", "sam.colleague\uff20acme.test",
                 LOOKALIKE.name, "Sam", "Examplov", "Jane", "jane.pair"):  # fmt: skip
        assert gone not in result.text
    assert ME.name in result.text and ME.email in result.text and ME_GITHUB.email in result.text
    assert (result.redaction, result.redacted_names, result.redacted_emails) == (True, 3, 2)


def test_without_a_redactor_the_text_is_as_written(make_repo, tmp_path) -> None:
    """`sijl digest --no-redact`: names and addresses as written, and the header says so."""
    report, _ = scenario(make_repo, tmp_path)
    result = digest.build(report)
    assert f"credit sam@sam-laptop, {COLLEAGUE.name} and Jane Pair" in result.text
    assert f"draft PROJ-14 with {LOOKALIKE.name}" in result.text
    assert "Redaction: off — names and email addresses are as written." in result.text
    assert (result.redaction, result.redacted_names, result.redacted_emails) == (False, 0, 0)


def test_the_redactor_knows_every_author_and_keeps_only_yours(make_repo, tmp_path) -> None:
    report, _ = scenario(make_repo, tmp_path)
    redact = digest.redactor_for(report)
    text = f"{ME.name}, {ME_GITHUB.name}, {COLLEAGUE.name}, {LOOKALIKE.name}, GitHub"
    assert redact(text) == f"{ME.name}, {ME_GITHUB.name}, [person 1], [person 2], GitHub"


# ---------------------------------------------------------------------------------------- ids

COMMIT_LINE = re.compile(r"^- ([0-9a-f]{8,}) ", re.MULTILINE)
GRAMMAR = {  # the id grammar `sijl ask` relies on, written out here independently of the module
    "commit": re.compile(r"[0-9a-f]{8,40}"),
    "request": re.compile(r"[A-Za-z0-9._-]+[!#][0-9]+"),
    "ticket": re.compile(r"[A-Z][A-Z0-9_]+-[0-9]+"),
}


def written_ids(text: str) -> tuple[set[str], set[str], set[str]]:
    """Every id token in the text, read with the one tokenizer `ask` uses (`digest.ID_PATTERN`),
    by kind: commits, requests, tickets."""
    found: dict[str, set[str]] = {"commit": set(), "request": set(), "ticket": set()}
    for match in digest.ID_PATTERN.finditer(text):
        found[match.lastgroup].add(match.group())
    return found["commit"], found["request"], found["ticket"]


def plain(ids: set[str]) -> bool:
    """Every id is one token of the grammar: ASCII, no space, no redaction label."""
    return all(
        i.isascii() and any(g.fullmatch(i) for g in GRAMMAR.values()) and "[" not in i for i in ids
    )


def test_ids_are_exactly_the_tokens_written(make_repo, tmp_path) -> None:
    """`sijl ask` checks every id Claude cites against `Digest.ids`: it holds exactly the
    tokens of the id grammar the text holds — every SHA, request and ticket Sijl writes, and
    any a subject happens to write (here PROJ-14, named by a commit not pushed) — read back
    with ONE regex."""
    report, shas = scenario(make_repo, tmp_path)
    result = digest.build(report, redactor=digest.redactor_for(report))
    assert (result.shas, result.requests, result.tickets) == written_ids(result.text)
    assert result.ids == result.shas | result.requests | result.tickets
    assert plain(result.ids)
    counted = {c.sha[:8] for c in report.collection.commits} | {shas["wip"][:8]}
    assert result.shas == counted  # every commit, the one not pushed too; never a merge's
    assert result.requests == {
        "webapp!42", "webapp!147", "webapp!148", "app#12", "app#13", "app#14",
    }  # fmt: skip
    assert result.tickets == {"PROJ-12", "PROJ-13", "PROJ-14"}
    assert (result.listed, result.omitted) == (digest.Counts(11, 6, 2), digest.Counts())
    assert result.about == (
        "2 repositories, 10 commits, 5 requests of yours, 2 tickets, 2026-04-01 to 2026-06-30"
    )


def folders_with_requests(tmp_path: Path, *names: str) -> Report:
    """One repository per folder name, each with one pull request of yours, #7."""
    repos = []
    for n, name in enumerate(names):
        repo = started(lambda folder: RepoBuilder(tmp_path / folder), name)
        repo.branch("fix", "main")
        repo.switch("fix")
        repo.commit(f"fix: typo {n}", at=at(4 + n), files={"x.txt": "x"})
        repo.switch("main")
        repo.github_pr_merge("fix", number=7, title="Fix typo", at=at(8 + n))
        repo.publish(tmp_path / "remotes" / f"{n}.git", "--all")
        repos.append(repo.path)
    return build(*repos)


def test_ids_stay_plain_whatever_the_folders_are_called(tmp_path) -> None:
    """A folder with a soft hyphen (which `printable()` escapes) and one
    named after a colleague (a redaction label): every id is still a plain token, written
    exactly as `Digest.ids` holds it."""
    report = folders_with_requests(tmp_path, "my\u00adtool", f"{COLLEAGUE.name} notes")
    result = digest.build(report, redactor=digest.redactor_for(report))
    assert (result.shas, result.requests, result.tickets) == written_ids(result.text)
    assert plain(result.ids) and all(i in result.text for i in result.ids)
    assert result.requests == {"my-tool#7", "person-1-notes#7"}
    assert COLLEAGUE.name not in result.text
    assert "my-tool = my\\xadtool, person-1-notes = [person 1] notes" in result.text


def test_a_folder_name_that_is_not_utf8_gives_a_plain_id(tmp_path) -> None:
    """`caf\\xe9` arrives as a lone surrogate, which an id could not even be encoded with."""
    require_raw_byte_names(tmp_path)
    report = folders_with_requests(tmp_path, os.fsdecode(b"caf\xe9"))
    result = digest.build(report, redactor=digest.redactor_for(report))
    result.text.encode("utf-8")
    assert result.requests == {"caf#7"} and plain(result.ids)
    assert (result.shas, result.requests, result.tickets) == written_ids(result.text)


def test_the_tokenizer_reads_ids_and_nothing_glued_to_them() -> None:
    text = (
        "- 1a2b3c4d 2026-05-04 [app] fix PROJ-12; see app#12, webapp!42 → my-tool.v2!3\n"
        "not ids: x1a2b3c4d, 1a2b3c4dz, path/1a2b3c4d, PROJ-12x, a@b!3, app#, #12, !42, abc1234"
    )
    found = [m.group() for m in digest.ID_PATTERN.finditer(text)]
    assert found == ["1a2b3c4d", "PROJ-12", "app#12", "webapp!42", "my-tool.v2!3"]


def test_a_short_sha_grows_until_no_two_listed_commits_share_it() -> None:
    shas = ["abcdef01" + "0" * 32, "abcdef01" + "1" * 32, "12345678" + "2" * 32]
    assert digest._sha_width(shas) == 9
    assert digest._sha_width(shas[1:]) == digest.SHORT_SHA


def test_two_repositories_with_one_name_are_told_apart(make_repo, tmp_path) -> None:
    first = started(lambda name: RepoBuilder(tmp_path / "a" / name), "app")
    second = started(lambda name: RepoBuilder(tmp_path / "b" / name), "app")
    third = started(lambda name: RepoBuilder(tmp_path / "c" / name), "app-2")
    for n, repo in enumerate((first, second, third)):
        repo.commit(f"feat: in clone {n}", at=at(4 + n), files={f"{n}.txt": "x"})
    text = text_of(build(first.path, second.path, third.path))
    assert "### app — 1 commit in all; 1 below" in text  # a/app: first by path
    assert "### app-2 — 1 commit in all; 1 below" in text  # b/app
    assert "### app-2-2 — 1 commit in all; 1 below" in text  # the folder named app-2
    assert (
        '"=" gives the folder\'s name where it differs): app, app-2 = app, app-2-2 = app-2\n'
        in (text)
    )


def test_a_request_never_names_a_namespace_or_an_owner(make_repo, tmp_path) -> None:
    """Privacy: a merge request in a colleague's own GitLab namespace, or a pull request
    in a colleague's GitHub repository, named them in `group/project!N` / `owner/repo#N`. A
    request is cited by its repository's own id instead: `tool!3`, `kit#7`."""
    tool = started(make_repo, "tool")
    tool.branch("feature/x", "main")
    tool.switch("feature/x")
    tool.commit("feat: help with the tool", at=at(4), files={"x.txt": "x"})
    tool.switch("main")
    tool.gitlab_mr_merge("feature/x", number=3, title="Help", at=at(5), who=COLLEAGUE,
                         project="sam.colleague/tool")  # fmt: skip
    tool.publish(tmp_path / "remotes" / "tool.git", "--all")
    tool.git("remote", "set-url", "origin", "git@gitlab.com:sam.colleague/tool.git")
    kit = started(make_repo, "kit")
    kit.branch("fix", "main")
    kit.switch("fix")
    kit.commit("fix: typo in the kit", at=at(4), files={"x.txt": "x"})
    kit.switch("main")
    kit.github_pr_merge("fix", number=7, title="Fix typo", at=at(5), owner="samcolleague")
    kit.publish(tmp_path / "remotes" / "kit.git", "--all")
    kit.git("remote", "set-url", "origin", "git@github.com:samcolleague/kit.git")
    result = digest.build(build(tool.path, kit.path))  # unredacted: the id itself must not name
    assert result.requests == {"tool!3", "kit#7"}
    assert "sam.colleague" not in result.text and "samcolleague" not in result.text


# ------------------------------------------------------------------------------ determinism


def test_the_same_input_gives_the_same_bytes(make_repo, tmp_path) -> None:
    report, _ = scenario(make_repo, tmp_path)
    paths = [rc.repo.path for rc in report.collection.repos]
    again = build(*paths)
    assert text_of(report).encode() == text_of(report).encode() == text_of(again).encode()


def test_the_order_roots_are_given_in_changes_nothing(make_repo, tmp_path) -> None:
    report, _ = scenario(make_repo, tmp_path)
    paths = sorted(rc.repo.path for rc in report.collection.repos)
    assert text_of(build(roots=paths)) == text_of(build(roots=paths[::-1])) == text_of(report)


CHILD = """
import sys
from datetime import date
from pathlib import Path
from sijl import digest
from sijl.git import GitRunner
from sijl.identity import IdentitySet
from sijl.report import build_report
from sijl.window import Window
report = build_report(
    roots=[], paths=[Path(p) for p in sys.argv[1:]],
    identities=IdentitySet.from_emails(["alex.example@acme.test",
                                        "40400001+alexgh@users.noreply.github.com"]),
    window=Window(date(2026, 4, 1), date(2026, 6, 30)), git=GitRunner(),
    generated_on=date(2026, 10, 8), person="Alex Example", jira_projects=["PROJ"],
)
sys.stdout.buffer.write(digest.build(report, redactor=digest.redactor_for(report)).text.encode())
"""


def test_set_and_dict_order_change_nothing(make_repo, tmp_path) -> None:
    """Python orders sets of strings by a hash seeded anew for every process (PYTHONHASHSEED):
    three processes with three seeds write the same bytes as this one."""
    report, _ = scenario(make_repo, tmp_path)
    paths = [str(rc.repo.path) for rc in report.collection.repos]
    outputs = {
        subprocess.run(
            [sys.executable, "-c", CHILD, *paths],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            check=True,
        ).stdout
        for seed in ("0", "1", "4242")
    }
    assert outputs == {text_of(report).encode()}


# ---------------------------------------------------------------------------------- network


def test_building_a_digest_makes_no_network_call(make_repo, tmp_path, network) -> None:
    """The guard refuses every connection and lookup (tests/conftest.py); none was attempted."""
    report, _ = scenario(make_repo, tmp_path)
    digest.build(report, redactor=digest.redactor_for(report), max_chars=3_000)
    assert (network.attempts, network.used) == ([], [])


# ----------------------------------------------------------------------------------- budget

MARKER = re.compile(r"\[… ([0-9,]+) more omitted …\]")
SHOWING = re.compile(r" \(showing ([0-9,]+) of ([0-9,]+)\)$")


def sections(text: str) -> dict[str, list[str]]:
    """Each section's lines after its `## ` heading, by the heading's first word."""
    found = {}
    for block in text.split("\n\n## ")[1:]:
        heading, *lines = block.rstrip("\n").split("\n")
        found[heading.split()[0]] = lines
    return found


def evidence(lines: list[str]) -> list[str]:
    """A section's lines of evidence — headings and commit lines — without "(showing …)"."""
    return [SHOWING.sub("", line) for line in lines if line.startswith(("### ", "- "))]


def check_budget(report: Report, full: str, max_chars: int) -> digest.Digest:
    """Everything a budget promises, for one `max_chars`."""
    result = digest.build(report, redactor=digest.redactor_for(report), max_chars=max_chars)
    text = result.text
    assert len(text) <= max_chars
    whole, cut = sections(full), sections(text)
    assert list(cut) == list(whole) == ["Tickets", "Requests", "Repositories", "Not"]
    for name, lines in cut.items():
        kept, everything = evidence(lines), evidence(whole[name])
        assert all(line in everything for line in kept)  # shown as in the whole digest
        missing = len(everything) - len(kept)
        if missing:  # never a silent cut: the section ends with how much it left out
            assert MARKER.fullmatch(lines[-1]) and lines[-1] == f"[… {missing:,} more omitted …]"
        else:
            assert not any(MARKER.search(line) for line in lines)
        for index, line in enumerate(lines):  # a group that lost lines says how many it shows
            if (showing := SHOWING.search(line)) is not None:
                shown = 0
                while index + 1 + shown < len(lines) and lines[index + 1 + shown].startswith("- "):
                    shown += 1
                assert int(showing[1].replace(",", "")) == shown
                assert int(showing[2].replace(",", "")) > shown
    assert (result.shas, result.requests, result.tickets) == written_ids(text)
    assert plain(result.ids)
    if result.complete:
        assert text == full
    else:
        o, n = result.omitted, result.listed
        assert (
            f"Omitted to fit {max_chars:,} characters: {o.commits:,} of {n.commits:,} commits, "
            f"{o.requests:,} of {n.requests:,} requests, {o.tickets:,} of {n.tickets:,} tickets."
        ) in text
        assert n.commits - o.commits == len(set(COMMIT_LINE.findall(text)))
    return result


def test_over_budget_every_cut_section_ends_with_an_omission_marker(make_repo, tmp_path) -> None:
    report, _ = scenario(make_repo, tmp_path)
    full = text_of(report)
    result = check_budget(report, full, len(full) - 700)
    text = result.text
    assert not result.complete and result.omitted.commits > 0
    assert sum(1 for line in text.split("\n") if MARKER.fullmatch(line)) >= 2
    # Fair shares: the small section keeps everything while the larger ones are cut.
    assert sections(text)["Not"] == sections(full)["Not"]


def test_every_budget_keeps_every_promise(make_repo, tmp_path) -> None:
    """From the smallest budget that holds the header to the whole digest, in steps: never over
    the budget, never a silent cut, ids exactly the ones written, the same bytes twice."""
    report, _ = scenario(make_repo, tmp_path)
    full = text_of(report)
    with pytest.raises(digest.BudgetTooSmall) as refused:
        digest.build(report, redactor=digest.redactor_for(report), max_chars=1)
    smallest = refused.value.smallest
    for max_chars in [*range(smallest, len(full), 37), len(full) - 1, len(full)]:
        result = check_budget(report, full, max_chars)
        assert result.text == text_of(report, max_chars=max_chars)
    assert check_budget(report, full, len(full)).complete


def test_a_budget_too_small_for_the_header_is_refused(make_repo, tmp_path) -> None:
    report, _ = scenario(make_repo, tmp_path)
    with pytest.raises(digest.BudgetTooSmall) as refused:
        text_of(report, max_chars=500)
    smallest = refused.value.smallest
    assert str(refused.value) == (
        f"--max-chars 500 is too small: the smallest digest that fits takes {smallest:,} characters"
    )
    assert len(text_of(report, max_chars=smallest)) <= smallest
    with pytest.raises(digest.BudgetTooSmall):
        text_of(report, max_chars=smallest - 1)


def test_within_budget_nothing_changes(make_repo, tmp_path) -> None:
    report, _ = scenario(make_repo, tmp_path)
    full = text_of(report)
    assert text_of(report, max_chars=len(full)) == full
    assert text_of(report, max_chars=10 * len(full)) == full


def test_fair_shares_meet_small_demands_in_full() -> None:
    assert digest.shares([10, 100, 1000], 400) == [10, 100, 290]
    assert digest.shares([10, 100, 1000], 2000) == [10, 100, 1000]
    assert digest.shares([50, 50, 50], 100) == [33, 33, 34]
    assert digest.shares([], 100) == [] and digest.shares([5], -3) == [0]


def test_the_smallest_budget_named_is_the_smallest_that_works(make_repo, tmp_path) -> None:
    """A two-commit digest of N characters was once refused at N - 1 with a minimum
    above N — the room set aside for markers that a whole digest never needs. The minimum
    named is the least budget that works, for a digest smaller than its reserve or larger."""
    small = make_repo("tiny")
    small.commit("feat: one", at=at(4), files={"a": "x"})
    small.commit("feat: two", at=at(5), files={"b": "x"})
    large, _ = scenario(make_repo, tmp_path)
    for report in (build(small.path), large):
        full = text_of(report)
        with pytest.raises(digest.BudgetTooSmall) as refused:
            text_of(report, max_chars=1)
        smallest = refused.value.smallest
        assert len(text_of(report, max_chars=smallest)) <= smallest <= len(full)
        with pytest.raises(digest.BudgetTooSmall) as again:
            text_of(report, max_chars=smallest - 1)
        assert again.value.smallest == smallest
