"""sijl.redact: before text leaves the machine, only the user's own identities are kept.
Every email address that is not one of yours becomes "[email N]"; every name Sijl knows
from the history (authors, co-authors) that is not yours becomes "[person N]" — whole words
only, so a look-alike name never touches yours. Labels are stable within one command, so the
digest and a question about the same colleague use the same label."""

import pytest

from sijl.redact import Redactor
from tests.gitfactory import COLLEAGUE, LOOKALIKE, ME, ME_GITHUB
from tests.scaling import best, growth_problem


def mine(**kwargs) -> Redactor:
    return Redactor(
        keep_emails=[ME.email, ME_GITHUB.email], keep_names=[ME.name, ME_GITHUB.name], **kwargs
    )


def test_colleagues_emails_are_replaced_and_yours_are_kept() -> None:
    redact = mine()
    text = f"by {ME.email}, {COLLEAGUE.email} and {ME_GITHUB.email}; cc {LOOKALIKE.email}"
    assert redact(text) == f"by {ME.email}, [email 1] and {ME_GITHUB.email}; cc [email 2]"
    assert (redact.emails, redact.names) == (2, 0)


def test_your_emails_are_matched_without_case() -> None:
    assert mine()("ALEX.Example@ACME.test") == "ALEX.Example@ACME.test"


def test_known_names_are_replaced_as_whole_words_and_yours_are_kept() -> None:
    redact = mine(names=[COLLEAGUE.name, LOOKALIKE.name])
    text = f"{ME.name} paired with {COLLEAGUE.name}; {LOOKALIKE.name} reviewed; sam colleague ok"
    assert redact(text) == f"{ME.name} paired with [person 1]; [person 2] reviewed; [person 1] ok"
    assert (redact.emails, redact.names) == (0, 2)


def test_a_look_alike_name_never_touches_yours() -> None:
    """A colleague's name can be close to the user's: it must never be mistaken for it."""
    redact = mine(names=[LOOKALIKE.name, "Alex"])  # and a colleague called just "Alex"
    text = f"{ME.name}, {LOOKALIKE.name} and Alex"
    assert redact(text) == f"{ME.name}, [person 1] and [person 2]"


def test_names_inside_other_words_are_left_alone() -> None:
    assert mine(names=["Sam"])("Samsung SAM sam-cli") == "Samsung [person 1] [person 1]-cli"


def test_a_git_identity_loses_both_halves() -> None:
    redact = mine(names=[COLLEAGUE.name])
    trailer = f"Co-authored-by: {COLLEAGUE.name} <{COLLEAGUE.email}>"
    assert redact(trailer) == "Co-authored-by: [person 1] <[email 1]>"


def test_an_email_is_replaced_whole_before_any_name_inside_it() -> None:
    redact = mine(names=["Sam", "Alex"])
    assert redact(f"{COLLEAGUE.email} {ME.email}") == f"[email 1] {ME.email}"


def test_labels_are_stable_across_texts_of_one_command() -> None:
    redact = mine(names=[COLLEAGUE.name, LOOKALIKE.name])
    digest = redact(f"- {LOOKALIKE.name}: x\n- {COLLEAGUE.name}: y")
    question = redact(f"What did I do with {COLLEAGUE.name}?")
    assert digest == "- [person 1]: x\n- [person 2]: y"
    assert question == "What did I do with [person 2]?"


def test_the_same_input_gives_the_same_output() -> None:
    text = f"{COLLEAGUE.name} <{COLLEAGUE.email}>, {LOOKALIKE.email}"
    assert mine(names=[COLLEAGUE.name])(text) == mine(names=[COLLEAGUE.name])(text)


def test_a_colleague_who_shares_your_name_keeps_it() -> None:
    """A name that is also yours cannot be removed without removing yours: kept, and the
    email still tells the two apart."""
    assert mine(names=[ME.name])(ME.name) == ME.name


def test_blank_and_one_letter_names_are_ignored() -> None:
    assert mine(names=["", "  ", "a"])("a cat") == "a cat"


def test_text_with_nothing_to_redact_is_unchanged() -> None:
    text = "feat(report): add caveats (DEV-12)\n`abc12345` 2026-05-04"
    redact = mine(names=[COLLEAGUE.name])
    assert redact(text) == text
    assert (redact.emails, redact.names) == (0, 0)


# Addresses a first, simpler pattern missed or cut.


@pytest.mark.parametrize(
    ("text", "redacted"),
    [
        ("from sam@sam-laptop", "from [email 1]"),  # git's default user@hostname: no dot
        ("from sam@buildbox.(none)", "from [email 1]"),  # git's default without a domain
        ("cc sean.o'brien@acme.test", "cc [email 1]"),  # RFC 5322 allows ' in a local part
        ("cc ops/sam@acme.test", "cc [email 1]"),  # and /
        ("cc first+tag=x@acme.test.", "cc [email 1]."),  # a sentence's full stop stays
        ("sam.colleague%40acme.test", "[email 1]"),  # percent-encoded @
    ],
)
def test_every_shape_of_address_is_replaced_whole(text, redacted) -> None:
    assert mine()(text) == redacted


def test_an_address_inside_a_link_is_replaced_and_the_link_kept() -> None:
    redact = mine()
    link = (
        "https://gitlab.acme.test/dashboard/merge_requests?author_email=sam.colleague%40acme.test"
    )
    text = f"{link} and sam.colleague@acme.test"
    assert redact(text) == (
        "https://gitlab.acme.test/dashboard/merge_requests?author_email=[email 1] and [email 1]"
    )


def test_your_own_encoded_address_is_kept() -> None:
    for text in ("alex.example%40acme.test", "https://x.test/?q=alex.example%40acme.test"):
        assert mine()(text) == text


def test_a_name_can_never_cut_into_an_address() -> None:
    """Addresses are found first; names are matched only in the text between them."""
    redact = mine(names=["Kim Lee", "Jo Sam"])
    assert redact("cc Kim lee.kim@acme.test") == "cc Kim [email 1]"
    assert redact("cc Jo sam@acme.test") == "cc Jo [email 2]"


def test_version_pins_are_not_addresses() -> None:
    assert mine()("bump lodash@4.17.21 and react@18") == "bump lodash@4.17.21 and react@18"


# Names beside a colleague's address, and names written differently.


@pytest.mark.parametrize(
    ("text", "redacted"),
    [
        ("Reviewed-by: Jane Reviewer <jane.r@acme.test>", "Reviewed-by: [person 1] <[email 1]>"),
        ("Signed-off-by: Priya Patel <ppatel@acme.test>", "Signed-off-by: [person 1] <[email 1]>"),
        ("Author: sam <sam@buildbox.(none)>", "Author: [person 1] <[email 1]>"),
        ("Jane Reviewer<jane@acme.test>", "[person 1]<[email 1]>"),
        ("cc: Jane Reviewer <jane@acme.test>, Priya Patel <ppatel@acme.test>",
         "cc: [person 1] <[email 1]>, [person 2] <[email 2]>"),
    ],
)  # fmt: skip
def test_the_name_beside_a_colleagues_address_goes_with_it(text, redacted) -> None:
    """Any trailer: an unknown name is still a person when it stands beside their address."""
    assert mine()(text) == redacted


def test_your_own_name_and_address_stay_together() -> None:
    text = f"Co-authored-by: {ME.name} <{ME.email}>"
    assert mine()(text) == text
    assert mine()(f"{ME.name} <alex@personal.test>") == f"{ME.name} <[email 1]>"


def test_a_pair_and_a_free_mention_of_a_known_name_share_the_label() -> None:
    redact = mine(names=[COLLEAGUE.name])
    text = f"Co-authored-by: {COLLEAGUE.name} <{COLLEAGUE.email}>\nThanks {COLLEAGUE.name}!"
    assert redact(text) == "Co-authored-by: [person 1] <[email 1]>\nThanks [person 1]!"


def test_a_sentence_before_an_address_is_not_taken_for_a_name() -> None:
    text = "thanks to the whole team <team@acme.test>"
    assert mine()(text) == "thanks to the whole team <[email 1]>"


@pytest.mark.parametrize("space", ["\n", "  ", "\xa0", "\t", " \n "])
def test_a_known_name_matches_across_any_whitespace(space) -> None:
    redact = mine(names=[COLLEAGUE.name])
    assert redact(f"Paired with Sam{space}Colleague today") == "Paired with [person 1] today"
    assert redact(f"Alex{space}Example kept") == f"Alex{space}Example kept"


def test_a_name_matches_in_either_unicode_form() -> None:
    """é can be one character (NFC) or e plus an accent (NFD); both are the same name."""
    import unicodedata

    redact = mine(names=["Zoë Doe"])
    nfd = unicodedata.normalize("NFD", "Zoë Doe")
    assert redact(f"Thanks {nfd} and Zoë Doe") == "Thanks [person 1] and [person 1]"


def test_a_list_bullet_is_not_part_of_the_name() -> None:
    assert mine()("- Jane Reviewer <jane@acme.test>") == "- [person 1] <[email 1]>"
    assert mine()("* Jane <jane@acme.test>") == "* [person 1] <[email 1]>"


# an address written with another at sign. NFKC, Unicode's compatibility form, folds the
# FULLWIDTH COMMERCIAL AT (U+FF20) and the SMALL COMMERCIAL AT (U+FE6B) — the only characters it
# maps to "@" — and full-width letters, dots, hyphens and angle brackets into ASCII; matching
# reads that form.


@pytest.mark.parametrize(
    ("text", "redacted"),
    [
        ("cc sam\uff20acme.test", "cc [email 1]"),  # FULLWIDTH COMMERCIAL AT
        ("cc sam\ufe6bacme.test", "cc [email 1]"),  # SMALL COMMERCIAL AT
        ("cc sam.colleague\uff20sam-laptop", "cc [email 1]"),  # git's user@host, full-width @
        ("cc \uff53\uff41\uff4d\uff0e\uff43\uff4f\uff20\uff41\uff43\uff4d\uff45\uff0e\uff54",
         "cc [email 1]"),  # every character full-width
        ("sam\uff05\uff14\uff10acme.test", "[email 1]"),  # a full-width %40
        ("https://x.test/?author=sam\uff20acme.test", "https://x.test/?author=[email 1]"),
    ],
)  # fmt: skip
def test_an_at_sign_look_alike_still_makes_an_address(text, redacted) -> None:
    assert mine()(text) == redacted


def test_every_spelling_of_one_address_shares_its_label() -> None:
    redact = mine()
    text = "sam@acme.test, sam\uff20acme.test, SAM\ufe6bACME.TEST"
    assert redact(text) == "[email 1], [email 1], [email 1]"
    assert redact.emails == 1


def test_your_own_address_with_a_look_alike_at_sign_is_kept() -> None:
    text = "by alex.example\uff20acme.test"
    assert mine()(text) == text


def test_a_full_width_identity_loses_both_halves_and_keeps_its_brackets() -> None:
    text = "Co-authored-by: Jane Reviewer \uff1cjane\uff20acme.test\uff1e"
    assert mine()(text) == "Co-authored-by: [person 1] \uff1c[email 1]\uff1e"


def test_a_known_name_written_full_width_is_the_same_person() -> None:
    redact = mine(names=[COLLEAGUE.name])
    full_width = "\uff33\uff41\uff4d \uff23\uff4f\uff4c\uff4c\uff45\uff41\uff47\uff55\uff45"
    assert redact(f"{COLLEAGUE.name} and {full_width}") == "[person 1] and [person 1]"


def test_text_around_a_match_keeps_its_own_characters() -> None:
    """Matching reads the folded form; what is not replaced is returned exactly as written."""
    text = "\uff26\uff49\uff58 \u2460 caf\u00e9 \ufb01le \u00b2 sam\uff20acme.test"
    assert mine()(text) == "\uff26\uff49\uff58 \u2460 caf\u00e9 \ufb01le \u00b2 [email 1]"


def test_names_that_are_not_text_are_still_handled() -> None:
    """A folder name that is not UTF-8 arrives as lone surrogates; folding leaves them alone."""
    assert mine()("repo-\udcff sam\uff20acme.test") == "repo-\udcff [email 1]"


# A known name written with other punctuation or without its accents. The
# text is read folded (accents dropped, apostrophe and quote look-alikes as ', hyphen and dash
# look-alikes as -, whitespace runs as one blank, no case) and replaced in the text as written.


@pytest.mark.parametrize(
    ("known", "text", "redacted"),
    [
        ("Sean O'Brien", "revert Sean O\u2019Brien\u2019s fix", "revert [person 1]\u2019s fix"),
        ("Sean O'Brien", "thanks Sean O\u02bcBrien", "thanks [person 1]"),
        ("Jean-Luc Picard", "by Jean\u2010Luc Picard and Jean\u2011Luc Picard",
         "by [person 1] and [person 1]"),
        ("Jean-Luc Picard", "by Jean\u2014Luc  Picard", "by [person 1]"),
        ("Jos\u00e9 Garc\u00eda", "fix from Jose Garcia", "fix from [person 1]"),
        ("Jose Garcia", "fix from Jos\u00e9 Garc\u00eda!", "fix from [person 1]!"),
        ("Jose Garcia", "fix from Jose\u0301 Garci\u0301a", "fix from [person 1]"),  # NFD marks
        ("Sean O'Brien", "SEAN O\u2019BRIEN wrote it", "[person 1] wrote it"),
    ],
)  # fmt: skip
def test_a_known_name_matches_however_its_punctuation_and_accents_are_written(
    known, text, redacted
) -> None:
    assert mine(names=[known])(text) == redacted


def test_what_is_not_replaced_keeps_every_character_as_written() -> None:
    """Folding may change the text's length (a ligature becomes two letters, a mark goes, a run
    of blanks becomes one): matches are mapped back to the original positions."""
    text = "\ufb01x  for\u00a0Jos\u00e9\tGarc\u00eda \u2014 caf\u00e9\u2019s \u00b2"
    assert (
        mine(names=["Jose Garcia"])(text)
        == "\ufb01x  for\u00a0[person 1] \u2014 caf\u00e9\u2019s \u00b2"
    )


def test_your_own_name_is_kept_however_it_is_written() -> None:
    redact = Redactor(keep_emails=[ME.email], keep_names=["Zo\u00eb Example"], names=["Zoe"])
    assert redact("Zoe Example and ZO\u00cb EXAMPLE, not Zoe") == (
        "Zoe Example and ZO\u00cb EXAMPLE, not [person 1]"
    )


# Performance: one alternation of every known name, tried at every position, took 15 s
# for 10,000 names on 150 KB; the digest of 20,000 commits by 1,000 authors took 26.7 s against
# 2.2 s unredacted. Redaction is linear in the text and nearly flat in the number of names.

FIRST = ("Sam", "Alex", "Jane", "Omar", "Lina", "Karim", "Nour", "Mona", "Yusuf", "Dana")
WORDS = ("fix", "add", "refactor", "parser", "billing", "flow", "retry", "tidy", "docs", "update",
         "the", "for", "and", "with")  # fmt: skip


def many_names(count: int) -> list[str]:
    return [f"{FIRST[i % len(FIRST)]} Surname{i}" for i in range(count)]


def subjects(size: int) -> str:
    """About `size` characters of commit subjects, one in ten naming a known colleague."""
    lines, total, n = [], 0, 0
    while total < size:
        line = " ".join(WORDS[(n * 7 + k) % len(WORDS)] for k in range(9))
        if n % 10 == 0:
            line += f" with {FIRST[n % len(FIRST)]} Surname{n % 3000}"
        lines.append(line)
        total += len(line) + 1
        n += 1
    return "\n".join(lines)


def test_redaction_is_linear_in_the_text_with_thousands_of_names() -> None:
    redact = mine(names=many_names(3_000))
    sizes = (125_000, 250_000, 500_000, 1_000_000)
    assert growth_problem(redact, subjects, sizes, cap=2.0) == ""
    assert "[person " in redact(subjects(2_000)) and "Surname0" not in redact(subjects(2_000))


def test_redaction_time_hardly_depends_on_how_many_names_are_known() -> None:
    text = subjects(200_000)
    few, many = mine(names=many_names(30)), mine(names=many_names(10_000))
    assert best(many, text) < 3 * best(few, text) + 0.05


# Shapes that once kept a colleague's name or address.


@pytest.mark.parametrize(
    ("text", "redacted"),
    [
        ('reviewed: "Rita Reviewer" <rita@acme.test>', 'reviewed: "[person 1]" <[email 1]>'),
        ("cc: \u201cRita Reviewer\u201d <rita@acme.test>",
         "cc: \u201c[person 1]\u201d <[email 1]>"),
        ("reported by Rita Reviewer <rita@acme.test>", "reported by [person 1] <[email 1]>"),
        ("thanks to Rita Reviewer (rita@acme.test)", "thanks to [person 1] ([email 1])"),
        ("thanks to the whole team <team@acme.test>", "thanks to the whole team <[email 1]>"),
        ("ping sam.colleague@192.168.1.5 today", "ping [email 1] today"),
        ("ping sam@[10.0.0.1] or sam@[IPv6:2001:db8::1]", "ping [email 1] or [email 2]"),
        ("pin lodash@4.17.21, pkg@1.2.3.4.5", "pin lodash@4.17.21, pkg@1.2.3.4.5"),  # versions
        ("clone https://sam.colleague:t0ken@git.acme.test/x.git",
         "clone https://[person 1]@git.acme.test/x.git"),
        ("clone https://sam.colleague@git.acme.test/x.git",
         "clone https://[person 1]@git.acme.test/x.git"),
        ("clone https://sam%40acme.test:t0ken@git.acme.test/x",
         "clone https://[person 1]@git.acme.test/x"),
    ],
)  # fmt: skip
def test_every_shape_of_a_colleague_beside_an_address_is_replaced(text, redacted) -> None:
    assert mine()(text) == redacted


def test_your_own_user_in_a_link_stays_and_a_password_never_does() -> None:
    redact = mine()
    assert redact("https://alex.example@git.acme.test/x") == "https://alex.example@git.acme.test/x"
    assert "secret" not in redact("https://alex.example:secret@git.acme.test/x")
    assert redact("https://alexgh@github.com/x") == "https://alexgh@github.com/x"  # noreply login


def test_a_capitalised_name_is_taken_only_when_the_address_is_a_colleagues() -> None:
    assert (
        mine()(f"written by Alex Example <{ME.email}>") == f"written by Alex Example <{ME.email}>"
    )
    assert mine()("see Rita Reviewer, <rita@acme.test>") == "see Rita Reviewer, <[email 1]>"
