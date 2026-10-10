"""sijl.ids: the digest's id grammar in one module, the single source the digest writes
with and the citation check reads with — a commit's short SHA, a request as its
repository's id and number, a ticket key — and its whole-token tokenizer."""

import pytest

from sijl import digest, ids


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("9ddb7043", "commit"), ("9ddb7043" * 5, "commit"),
        ("webapp!42", "request"), ("app#12", "request"), ("my-tool.v2_x!3", "request"),
        ("PROJ-12", "ticket"), ("OPS_2-7", "ticket"),
    ],
)  # fmt: skip
def test_every_kind_of_id_is_one_token_of_the_grammar(text, kind) -> None:
    assert ids.ID.match(text)
    [match] = ids.ID_PATTERN.finditer(f"see {text}, then")
    assert (match.group(), match.lastgroup) == (text, kind)


@pytest.mark.parametrize(
    "text",
    ["9ddb704", "9DDB7043", "9ddb7043" * 6, "group/webapp!42", "!42", "#12", "proj-12",
     "PROJ-", "[person 1]!3", "café#7"],
)  # fmt: skip
def test_what_is_not_an_id(text) -> None:
    assert not ids.ID.match(text)


def test_the_digest_writes_and_reads_with_this_grammar() -> None:
    assert digest.ID_PATTERN is ids.ID_PATTERN
