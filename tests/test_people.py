"""Everyone Sijl can see, for the digest's redactor: every author and
committer of a repository's whole history — not only pass 1's window — as written and after
.mailmap, every person a trailer names (Reviewed-by, Signed-off-by, Acked-by, …), and the user
names hidden in their addresses (an address's local part, a GitHub noreply login)."""

from datetime import date

from sijl import digest
from sijl.collect import read_people
from sijl.git import GitRunner, parse_people_z
from sijl.report import Report, build_report
from tests.gitfactory import COLLEAGUE, ME, ME_GITHUB, REVIEW_PERIOD, Who
from tests.test_deliveries import at
from tests.test_digest import MINE

JANE = Who("Jane Older", "jane.older@acme.test")  # wrote here long before the period
CORA = Who("Cora Committer", "cora.committer@acme.test")  # only ever a committer
SAMC = Who("samc", "1234+samc@users.noreply.github.com")  # a GitHub web identity


def test_records_give_every_identity_and_every_person_a_trailer_names() -> None:
    record = "\x1f".join([
        "Ann Author", "ann@acme.test", "Ann A", "ann@acme.test",
        "Cy Committer", "cy@acme.test", "Cy C", "cy@acme.test",
        "Reviewed-by: Rita Reviewer <rita@acme.test>\x1dAcked-by: Ben\x1dRefs: PROJ-12"
        "\x1dSigned-off-by: Ann Author <ann@acme.test>",
    ])  # fmt: skip
    assert parse_people_z(f"{record}\x00\n".encode()) == {
        ("Ann Author", "ann@acme.test"), ("Ann A", "ann@acme.test"),
        ("Cy Committer", "cy@acme.test"), ("Cy C", "cy@acme.test"),
        ("Rita Reviewer", "rita@acme.test"), ("Ben", ""),
    }  # fmt: skip


def history(make_repo):
    """A repository whose colleagues the period itself barely shows."""
    repo = make_repo("webapp")
    repo.commit("chore: start", at="2025-06-01T09:00:00+01:00", who=JANE)  # before the lookback
    repo.commit("feat: Jane's parser", at="2025-07-01T09:00:00+01:00", who=JANE, files={"p": "x"})
    repo.commit("fix: a typo", at="2026-03-02T09:00:00+01:00", who=COLLEAGUE, committer=CORA,
                files={"t": "x"}, trailers=["Reviewed-by: Rita Reviewer <rita.reviewer@acme.test>",
                                            "Acked-by: Ben Acker"])  # fmt: skip
    repo.commit("docs: web edit", at="2026-03-03T09:00:00+01:00", who=SAMC, files={"w": "x"})
    subjects = [
        "fix: regression from Jane Older's parser",
        "fix: follow-up on Rita Reviewer's review, as Cora Committer asked",
        "chore: thanks Ben Acker; ask sam.colleague or samc about it",
    ]
    for n, subject in enumerate(subjects):
        repo.commit(subject, at=at(4 + n), files={f"s{n}": "x"})
    return repo


def build(path) -> Report:
    return build_report(roots=[], paths=[path], identities=MINE, window=REVIEW_PERIOD,
                        git=GitRunner(), generated_on=date(2026, 10, 8),
                        person=ME.name)  # fmt: skip


def test_a_repository_gives_everyone_in_its_whole_history(make_repo) -> None:
    repo = history(make_repo)
    people = read_people(GitRunner(), build(repo.path).collection.repos[0].repo)
    assert {JANE.name, CORA.name, COLLEAGUE.name, "Rita Reviewer", "Ben Acker", "samc"} <= {
        name for name, _ in people
    }


def test_the_digest_redacts_everyone_sijl_can_see(make_repo) -> None:
    report = build(history(make_repo).path)
    text = digest.build(report, redactor=digest.redactor_for(report, git=GitRunner())).text
    for gone in ("Jane", "Older", "Rita", "Cora", "Ben Acker", "sam.colleague", "samc"):
        assert gone not in text
    assert ME.name in text and ME_GITHUB.email in text


def test_without_the_history_only_the_people_pass_1_read_are_known(make_repo) -> None:
    """`history=False`: Jane wrote before the lookback, so only her name in a subject gives her
    away. Reading the history is the default."""
    report = build(history(make_repo).path)
    without = digest.redactor_for(report, history=False)
    assert "Jane Older" in digest.build(report, redactor=without).text
    assert "Jane Older" not in digest.build(report, redactor=digest.redactor_for(report)).text


def test_generic_and_own_user_names_are_never_taken_for_colleagues() -> None:
    redact = digest.redactor_from_people(
        [("Dev Bot", "dev@acme.test"), ("Alex Example", ME.email), ("alexgh", ME_GITHUB.email),
         ("Sam Colleague", COLLEAGUE.email)],
        emails=[ME.email, ME_GITHUB.email],
        person=ME.name,
    )  # fmt: skip
    text = "dev server fix by alex.example and alexgh, with sam.colleague"
    assert redact(text) == "dev server fix by alex.example and alexgh, with [person 1]"
