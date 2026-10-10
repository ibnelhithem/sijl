"""`sijl ask` end to end: without credentials, the Tier 1 route with the question filled
in; with them, the digest of the period, the preview (or its summary and a private file with
every byte), one yes per command, the answer on stdout labelled as AI-written with its ids
checked, and the usage on stderr — in one-shot and interactive mode. A fake client takes the SDK
client's place; the network guard refuses any real connection, and the fake refuses to send
before the user's yes."""

import hashlib
import re
import tempfile
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sijl import ai, ask, citations, cli, digest, report
from sijl.cli import app
from tests.gitfactory import ME, ME_GITHUB, PERIOD_ARGS, RepoBuilder
from tests.test_cli_digest import library
from tests.test_deliveries import at, started
from tests.test_digest import three_squashes, webapp

runner = CliRunner()
KEY = {"ANTHROPIC_API_KEY": "sk-test-not-a-key"}
TODAY = date(2026, 10, 8)
LABEL = "Claude's answer — AI-written by claude-opus-5-5, not verified by Sijl:"
SUMMARY = (
    "Evidence check (by Sijl, not AI): Sijl verified {} (listed under Evidence Sijl "
    "checked). Treat any other id-like text in the answer as unverified."
)
QUESTION = "What did I deliver for PROJ-12?"


class FakeClaude:
    """The command's client: answers each payload with the next reply in `replies` — text, stop
    reason, tokens written to and read from the cache."""

    destination = "https://api.anthropic.com"

    def __init__(self, network) -> None:
        self.network = network
        self.replies: list[tuple[str, str, int, int]] = []
        self.sent: list[ai.Payload] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def send(self, payload: ai.Payload) -> ai.Reply:
        self.network.use("send to Claude")
        self.sent.append(payload)
        text, stop, written, read = self.replies.pop(0)
        return ai.Reply(text, stop, payload.model, payload.model, payload.max_tokens,
                        input_tokens=20, output_tokens=40, cache_write_tokens=written,
                        cache_read_tokens=read)  # fmt: skip


@pytest.fixture
def claude(monkeypatch, network) -> FakeClaude:
    """As in test_cli_ai: the real flag decides on or off; when on, the fake replaces the SDK
    client, and the user's "y" (`cli._ask`) is the test's consent."""
    fake = FakeClaude(network)
    real_connect, real_ask = cli._connect, cli._ask

    def connect(loaded):
        state, client = real_connect(loaded)
        if client is None:
            return state, None
        client.close()
        return state, fake

    def confirm(question: str) -> bool:
        yes = real_ask(question)
        if yes:
            network.consent()
        return yes

    monkeypatch.setattr(cli, "_connect", connect)
    monkeypatch.setattr(cli, "_ask", confirm)
    return fake


@pytest.fixture
def repos(make_repo, tmp_path, monkeypatch) -> list[Path]:
    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    return [webapp(make_repo, tmp_path)[0].path, three_squashes(make_repo, tmp_path)[0].path]


def args(repos: list[Path], *more: str) -> list[str]:
    where = [arg for path in repos for arg in ("--repo", str(path))]
    who = ["--author-email", ME.email, "--author-email", ME_GITHUB.email]
    return ["ask", *more, *where, *who, *PERIOD_ARGS, "--jira-project", "PROJ"]


def shown(text: str, evidence: digest.Digest) -> str:
    """What `ask` prints for a complete answer `text` (not on a terminal)."""
    reply = ai.Reply(text, "end_turn", "claude-opus-5-5", "claude-opus-5-5", 16_000)
    return ask.Answer(reply, citations.read(text, evidence), evidence).render()


def billing(repos: list[Path]) -> str:
    """The digest's id of merge request !42 (PROJ-12's), however the digest writes it."""
    return next(r for r in library(repos).requests if r.endswith("!42"))


# ------------------------------------------------------------------------- without Claude


def test_without_credentials_ask_prints_the_tier1_route_with_the_question(claude, network) -> None:
    """Acceptance: no credentials → why, and the Tier 1 route with the question filled in; no
    repository is even read (none is named here)."""
    result = runner.invoke(app, ["ask", "what's left of PROJ-12?"])
    assert result.exit_code == cli.EXIT_AI
    assert result.stdout == ""
    assert "sijl: AI is off: no Claude credentials found" in result.stderr
    assert "sijl digest --with-prompt --out digest.txt" in result.stderr
    assert "claude 'Read digest.txt and answer: what'\"'\"'s left of PROJ-12?'" in result.stderr
    assert claude.sent == [] and network.used == []


def test_no_ai_turns_ask_off_with_the_route(claude, repos) -> None:
    result = runner.invoke(app, args(repos, QUESTION, "--no-ai"), env=KEY)
    assert result.exit_code == cli.EXIT_AI
    assert "AI is off: turned off by --no-ai" in result.stderr
    assert f"answer: {QUESTION}" in result.stderr and claude.sent == []


def test_the_route_repeats_every_period_and_scope_flag_given(claude, tmp_path) -> None:
    """The digest of the route covers the same work as the `ask` that could not send: the
    period, the folders, the identities and every other scope flag, as given."""
    config = tmp_path / "my config.toml"
    config.write_text("")
    result = runner.invoke(app, [
        "ask", "what did I ship?", "--quarter", "2026Q3", "--root", "/srv/code",
        "--repo", "/srv/one", "--author-email", "you@example.com", "--no-include-worktrees",
        "--dedupe", "patch-id", "--jira-project", "PROJ", "--config", str(config), "--no-ai",
    ])  # fmt: skip
    assert result.exit_code == cli.EXIT_AI
    expected = (
        "  1. sijl digest --with-prompt --out digest.txt --quarter 2026Q3 --root /srv/code "
        "--repo /srv/one --author-email you@example.com --no-include-worktrees --dedupe patch-id "
        f"--jira-project PROJ --config '{config}'\n"
    )
    assert expected in result.stderr
    assert "usual period flags" not in result.stderr and claude.sent == []


def test_interactive_without_credentials_prints_the_route_without_a_question(claude) -> None:
    result = runner.invoke(app, ["ask"])
    assert result.exit_code == cli.EXIT_AI
    assert "claude 'Read digest.txt and summarise what I delivered'" in result.stderr


# --------------------------------------------------------------------------------- one-shot


def test_ask_previews_the_request_and_a_no_sends_nothing(claude, repos, network) -> None:
    result = runner.invoke(app, args(repos, QUESTION), env=KEY, input="n\n")
    assert result.exit_code == 0, result.output
    assert "What Sijl will send to Claude\n" in result.stderr
    assert library(repos).text.splitlines()[-1] in result.stderr  # small: shown whole
    assert "Send this to Claude? [y/N]" in result.stderr
    assert result.stderr.rstrip().endswith("Nothing was sent to Claude.")
    assert result.stdout == "" and claude.sent == [] and network.used == []
    assert claude.closed


def test_ask_sends_the_digest_and_shows_the_checked_answer(claude, repos) -> None:
    built = library(repos)
    text = f"You delivered [[{billing(repos)}]] for [[PROJ-12]]."
    claude.replies = [(text, "end_turn", 2_000, 0)]
    result = runner.invoke(app, args(repos, QUESTION), env=KEY, input="y\n")
    assert result.exit_code == 0, result.output
    assert result.stdout == shown(text, built)
    assert f"\n```\nYou delivered [{billing(repos)}] for [PROJ-12].\n```\n" in result.stdout
    for known in (billing(repos), "PROJ-12"):  # the digest's lines, as escaped Markdown
        assert f"- `{known}`: {report._md(built.line_for(known))}\n" in result.stdout
    assert SUMMARY.format("2 ids") in result.stdout
    (sent,) = claude.sent
    assert (sent.system, sent.digest, sent.question) == (ask.SYSTEM, built.text, QUESTION)
    assert (sent.model, sent.effort, sent.max_tokens) == ("claude-opus-5-5", "high", 16_000)
    assert sent.about == built.about
    assert "Claude used 2,020 input tokens (2,000 written to its cache)" in result.stderr


def test_an_invented_id_is_marked_and_named(claude, repos, network) -> None:
    network.consent()  # --yes: the user said yes before the command started
    invented = billing(repos).replace("!42", "!999")
    claude.replies = [(f"You delivered [[{billing(repos)}]] and {invented}.", "end_turn", 0, 0)]
    result = runner.invoke(app, args(repos, QUESTION, "--yes"), env=KEY)
    assert result.exit_code == cli.EXIT_UNVERIFIED, result.output  # fails closed
    assert f"{invented}{citations.MARK}." in result.stdout  # the answer is still shown
    assert SUMMARY.format("1 id") in result.stdout
    assert "marked in the answer above: 1 other id-like text." in result.stdout
    assert (
        "sijl: this answer cites 1 id that is NOT in your evidence (marked in it): check it "
        "before you use the answer."
    ) in result.stderr


def test_not_in_the_data_comes_through_as_written(claude, repos, network) -> None:
    network.consent()
    claude.replies = [("not in the data", "end_turn", 0, 0)]
    result = runner.invoke(app, args(repos, "Who reviewed my code in 2019?", "--yes"), env=KEY)
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"{LABEL}\n```\nnot in the data\n```\n\n")
    assert result.stdout.endswith(f"\n{SUMMARY.format('0 ids')}\n")


def test_a_cut_off_answer_is_an_error_that_says_how_to_allow_more(claude, repos, network) -> None:
    network.consent()
    claude.replies = [("You deliv", "max_tokens", 0, 0)]
    result = runner.invoke(app, args(repos, QUESTION, "--yes", "--answer-limit", "2048"), env=KEY)
    assert result.exit_code == cli.EXIT_AI
    assert result.stdout == ""
    assert "cut off at the output limit (2,048 tokens)" in result.stderr
    assert "--answer-limit" in result.stderr and "Claude used" in result.stderr  # billed
    assert f"answer: {QUESTION}" in result.stderr  # the Tier 1 route
    assert claude.sent[0].max_tokens == 2_048


def test_an_api_error_ends_the_command_with_the_route(monkeypatch, repos, network) -> None:
    class Failing(FakeClaude):
        def send(self, payload: ai.Payload) -> ai.Reply:
            raise ai.AiError("the API is overloaded (529): try later")

    failing = Failing(network)
    on = ai.Status(True, "Claude credentials found: ANTHROPIC_API_KEY", failing.destination)
    monkeypatch.setattr(cli, "_connect", lambda loaded: (on, failing))
    result = runner.invoke(app, args(repos, QUESTION, "--yes"), env=KEY)
    assert result.exit_code == cli.EXIT_AI
    assert "overloaded" in result.stderr and f"answer: {QUESTION}" in result.stderr
    assert failing.closed


def test_max_chars_bounds_the_evidence_and_the_preview_says_so(claude, repos, network) -> None:
    network.consent()
    budget = len(library(repos).text) - 600
    claude.replies = [("not in the data", "end_turn", 0, 0)]
    result = runner.invoke(app, args(repos, QUESTION, "--yes", "--max-chars", str(budget)),
                           env=KEY)  # fmt: skip
    assert result.exit_code == 0, result.output
    assert len(claude.sent[0].digest) <= budget
    assert re.search(r"Evidence: .*; to fit --max-chars [0-9,]+: [0-9]+ of", result.stderr)


def test_the_client_is_closed_when_the_scan_cannot_start(claude, repos) -> None:
    result = runner.invoke(app, ["ask", QUESTION, "--repo", str(repos[0])], env=KEY)
    assert result.exit_code == 2  # no --author-email
    assert claude.closed and claude.sent == []


def _clone_named_app(tmp_path: Path, folder: str) -> Path:
    """A repository named `app` under `folder`, with pull request #12 of yours merged."""
    repo = started(lambda name: RepoBuilder(tmp_path / folder / name), "app")
    repo.branch("fix/a", "main")
    repo.switch("fix/a")
    repo.commit("fix: a thing", at=at(4), files={f"{folder}.txt": "x"}, who=ME)
    repo.switch("main")
    repo.github_pr_merge("fix/a", number=12, title="Fix", at=at(5))
    repo.publish(tmp_path / "remotes" / f"{folder}.git", "--all")
    return repo.path


def test_two_repositories_named_alike_have_ids_cited_exactly_and_verified(
    claude, tmp_path, monkeypatch, network
) -> None:
    """Two clones named `app` once gave a request id with a space (`app (1)#12`) that the
    check could not cut out whole — exit 5 for an exact citation. The digest's grammar names
    the second one `app-2`; both, cited as written, are verified."""
    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    network.consent()
    paths = [_clone_named_app(tmp_path, "a"), _clone_named_app(tmp_path, "b")]
    requests = sorted(library(paths).requests)
    assert len(requests) == 2 and all(" " not in r for r in requests)
    claude.replies = [(f"You delivered [[{requests[0]}]] and [[{requests[1]}]].", "end_turn", 0, 0)]
    result = runner.invoke(app, args(paths, QUESTION, "--yes"), env=KEY)
    assert result.exit_code == 0, result.output
    assert SUMMARY.format("2 ids") in result.stdout


# ------------------------------------------------------------------------------ interactive


def test_follow_ups_reuse_the_cached_digest_with_one_yes(claude, repos) -> None:
    """Acceptance: in interactive mode the follow-up starts with the first request's bytes, and
    the usage the API reports — cache reads — is shown."""
    first_answer = f"You delivered {billing(repos)}."
    claude.replies = [
        (first_answer, "end_turn", 2_000, 0),
        ("PROJ-12.", "end_turn", 0, 2_000),
    ]
    result = runner.invoke(app, args(repos, QUESTION, "-i"), env=KEY,
                           input="y\nWhich tickets?\n\n")  # fmt: skip
    assert result.exit_code == 0, result.output
    assert result.stderr.count("Send this to Claude? [y/N]") == 1
    assert result.stderr.count("What Sijl will send to Claude") == 2  # each send previewed
    assert result.stderr.count("Your question (an empty line ends): ") == 2
    first, second = claude.sent
    assert second.history == (ai.Turn(QUESTION, first_answer),)
    assert second.params()["messages"][0] == first.params()["messages"][0]
    assert second.params()["system"] == first.params()["system"]
    assert "(2,000 written to its cache)" in result.stderr
    assert "(2,000 read from its cache)" in result.stderr
    assert "cache still holds them" in result.stderr  # the follow-up's estimate says so
    assert result.stdout.count(LABEL) == 2


def test_interactive_mode_asks_for_the_first_question_too(claude, repos) -> None:
    claude.replies = [("not in the data", "end_turn", 0, 0)]
    result = runner.invoke(app, args(repos), env=KEY, input=f"{QUESTION}\ny\n")
    assert result.exit_code == 0, result.output
    assert "Asking about: 2 repositories, " in result.stderr
    assert [p.question for p in claude.sent] == [QUESTION]
    assert result.stdout.startswith(LABEL)


def test_an_incomplete_answer_in_interactive_mode_lets_the_user_ask_again(
    claude, repos, network, monkeypatch
) -> None:
    """On a terminal the user saw why and could ask again: the session ends as its answers do."""
    monkeypatch.setattr(cli, "_scripted", lambda: False)
    network.consent()
    claude.replies = [("Here", "refusal", 0, 0), ("PROJ-12.", "end_turn", 0, 0)]
    result = runner.invoke(app, args(repos, QUESTION, "-i", "--yes"), env=KEY,
                           input="Which tickets?\n")  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "Claude declined to answer" in result.stderr
    assert claude.sent[1].history == ()  # the declined question is not part of it
    assert result.stdout.count(LABEL) == 1 and "PROJ-12." in result.stdout


def test_a_session_read_from_a_pipe_exits_4_when_a_question_got_no_answer(
    claude, repos, network
) -> None:
    """Questions piped in have no one to ask again — a script must not read
    exit 0 when one of them got no complete answer."""
    network.consent()
    claude.replies = [("Here", "refusal", 0, 0), ("PROJ-12.", "end_turn", 0, 0)]
    result = runner.invoke(app, args(repos, QUESTION, "-i", "--yes"), env=KEY,
                           input="Which tickets?\n")  # fmt: skip
    assert result.exit_code == cli.EXIT_AI, result.output
    assert result.stdout.count(LABEL) == 1 and "PROJ-12." in result.stdout  # the session went on
    assert "sijl: 1 of 2 questions got no complete answer" in result.stderr


def test_exit_5_wins_over_a_question_with_no_answer(claude, repos, network) -> None:
    network.consent()
    claude.replies = [("PROJ-99.", "end_turn", 0, 0), ("Here", "max_tokens", 0, 0)]
    result = runner.invoke(app, args(repos, QUESTION, "-i", "--yes"), env=KEY,
                           input="More?\n")  # fmt: skip
    assert result.exit_code == cli.EXIT_UNVERIFIED, result.output
    assert "sijl: 1 of 2 questions got no complete answer" in result.stderr


def test_an_unverified_answer_in_a_session_makes_the_session_exit_5(claude, repos) -> None:
    claude.replies = [("You also did PROJ-99.", "end_turn", 0, 0), ("PROJ-12.", "end_turn", 0, 0)]
    result = runner.invoke(app, args(repos, QUESTION, "-i"), env=KEY, input="y\nMore?\n\n")
    assert result.exit_code == cli.EXIT_UNVERIFIED, result.output
    assert result.stdout.count(LABEL) == 2  # the session went on after the warning
    assert f"PROJ-99{citations.MARK}." in result.stdout
    assert result.stderr.count("NOT in your evidence (marked in it)") == 1


def test_a_no_ends_an_interactive_session(claude, repos, network) -> None:
    result = runner.invoke(app, args(repos, QUESTION, "-i"), env=KEY, input="n\nmore?\n")
    assert result.exit_code == 0, result.output
    assert "Nothing was sent to Claude." in result.stderr
    assert "Your question" not in result.stderr and claude.sent == [] and network.used == []


# ----------------------------------------------------------------- a preview too long to read


def test_a_long_preview_is_in_a_private_file_until_the_command_ends(
    claude, repos, monkeypatch, tmp_path
) -> None:
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    monkeypatch.setattr(ai, "PREVIEW_MAX_CHARS", 1_000)
    seen: list[tuple[Path, str, int, int]] = []
    real_ask = cli._ask

    def confirm(question: str) -> bool:
        """The user reads the file while the question waits."""
        (path,) = folder.glob("sijl-ask-*/request-1.txt")
        modes = (path.stat().st_mode & 0o777, path.parent.stat().st_mode & 0o777)
        seen.append((path, path.read_text(encoding="utf-8"), *modes))
        return real_ask(question)

    monkeypatch.setattr(cli, "_ask", confirm)
    claude.replies = [("PROJ-12.", "end_turn", 0, 0)]
    result = runner.invoke(app, args(repos, QUESTION), env=KEY, input="y\n")
    assert result.exit_code == 0, result.output
    ((path, text, file_mode, folder_mode),) = seen
    assert (file_mode, folder_mode) == (0o600, 0o700)  # yours alone: it holds your evidence
    assert text == ai.render_exact(claude.sent[0], destination=claude.destination)
    assert "too long to read here" in result.stderr
    assert f"  {path}  (deleted when this command ends)" in result.stderr
    assert hashlib.sha256(text.encode()).hexdigest() in result.stderr
    assert library(repos).text.splitlines()[-1] not in result.stderr  # the evidence is not
    assert list(folder.iterdir()) == []  # removed when the command ended
