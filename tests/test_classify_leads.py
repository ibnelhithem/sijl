"""sijl.classify: a ticket key or a backlog id written before the subject is a lead —
read and set aside before the conventional prefix, never mistaken for an area."""

import pytest

from sijl.classify import parse_conventional
from sijl.tickets import find_keys


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        # (kind, scope, area, breaking, description, conventional, lead)
        ("ABC-123: fix login", ("other", "", "", False, "fix login", False, "ABC-123")),
        ("ABC-123 feat: x", ("feat", "", "", False, "x", True, "ABC-123")),
        ("[ABC-123] fix: y", ("fix", "", "", False, "y", True, "ABC-123")),
        ("[ABC-123] fix(api)!: y", ("fix", "api", "", True, "y", True, "ABC-123")),
        ("ABC-123: feat: x", ("feat", "", "", False, "x", True, "ABC-123")),
        ("ABC-1, ABC-2: fix: y", ("fix", "", "", False, "y", True, "ABC-1, ABC-2")),
        ("[ABC-1][ABC-2] docs: z", ("docs", "", "", False, "z", True, "ABC-1, ABC-2")),
        ("ABC-1 ABC-2 feat: x", ("feat", "", "", False, "x", True, "ABC-1, ABC-2")),  # spaces
        ("[ABC-1 ABC-2] fix: y", ("fix", "", "", False, "y", True, "ABC-1, ABC-2")),
        ("[ABC-1, UTF-8] fix: y", ("fix", "", "", False, "y", True, "ABC-1, UTF-8")),  # brackets
        ("ABC-123 - fix login", ("other", "", "", False, "fix login", False, "ABC-123")),
        ("ABC-123 \u2014 fix login", ("other", "", "", False, "fix login", False, "ABC-123")),
        ("ABC-123 Add login page", ("other", "", "", False, "Add login page", False, "ABC-123")),
        ("ABC-123  feat:  spaced", ("feat", "", "", False, "spaced", True, "ABC-123")),
        # backlog ids: a capital and a number, then a colon — a work item, not an area
        ("B12: add the report", ("other", "", "", False, "add the report", False, "B12")),
        ("C7: feat: scaffold", ("feat", "", "", False, "scaffold", True, "C7")),
        # unchanged: not a lead
        ("b12: lower case", ("other", "", "b12", False, "lower case", True, "")),
        ("s3: bucket policy", ("other", "", "s3", False, "bucket policy", True, "")),
        ("V8 engine upgrade", ("other", "", "", False, "V8 engine upgrade", False, "")),
        ("UTF-8: fix encoding", ("other", "", "utf-8", False, "fix encoding", True, "")),
        ("UTF-8 ABC-1 feat: x", ("other", "", "", False, "UTF-8 ABC-1 feat: x", False, "")),
        ("ABC-123", ("other", "", "", False, "ABC-123", False, "")),  # nothing after it
        ("[WIP] feat: x", ("other", "", "", False, "[WIP] feat: x", False, "")),
        ("feat(ABC-1): x", ("feat", "ABC-1", "", False, "x", True, "")),  # a scope, not a lead
        ('Revert "ABC-1: x"', ("revert", "", "", False, "ABC-1: x", True, "")),
    ],
)
def test_a_leading_ticket_key_is_read_before_the_prefix(subject: str, expected) -> None:
    c = parse_conventional(subject, f"{subject}\n")
    assert (c.kind, c.scope, c.area, c.breaking, c.description, c.conventional, c.lead) == expected


def test_a_standard_in_a_bracketed_lead_is_refused_and_the_rest_still_leads() -> None:
    subject = "[ABC-1, UTF-8] fix: y"
    c = parse_conventional(subject, f"{subject}\n")
    found = find_keys(subject, f"{subject}\n", scope=c.scope, lead=c.lead)
    assert dict(found.keys) == {"ABC-1": frozenset({"lead"})}
    assert found.rejected == frozenset({"UTF-8"})  # listed in Caveats, never counted
