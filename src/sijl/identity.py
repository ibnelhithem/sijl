"""Who "you" are: a set of exact email addresses, compared case-insensitively.

Never a name, and never a substring: real repositories hold a different person whose name looks
like yours. Both the raw author email and the one after the repository's .mailmap are checked.
A future version may suggest the addresses and logins you missed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sijl.models import RawCommit


@dataclass(frozen=True)
class IdentitySet:
    emails: frozenset[str]

    @classmethod
    def from_emails(cls, emails: Iterable[str]) -> IdentitySet:
        cleaned = frozenset(e.strip().casefold() for e in emails if e.strip())
        if not cleaned:
            raise ValueError("at least one author email is needed")
        return cls(cleaned)

    def matches(self, commit: RawCommit) -> bool:
        return self.matches_email(commit.author_email_raw, commit.author_email)

    def matches_email(self, *emails: str) -> bool:
        """True when any of `emails` (a commit's raw and mailmapped author email) is yours."""
        return any(email.casefold() in self.emails for email in emails)
