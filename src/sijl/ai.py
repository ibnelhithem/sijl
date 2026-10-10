"""The AI tier (Tier 2): the feature flag, the payload and its preview, and the Claude client.

`status()` is the one feature flag. AI is **off** when the user turns
it off (`--no-ai`, `SIJL_AI=off`, `[ai] enabled = false`); otherwise it is **on** exactly
when the Anthropic SDK resolves Claude credentials: `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN`, an `ant auth login` profile, or workload identity federation. An unset
`ANTHROPIC_API_KEY` alone does not mean "no credentials" — the SDK's own resolution decides, so
Sijl never re-implements (and never drifts from) its rules. Resolving reads environment
variables and local files only; nothing is sent.

The `anthropic` package is imported lazily, inside the functions that need it: Tier 1 commands
never pay for importing it, and a broken install turns AI off with the reason instead of
breaking `sijl report`.

`Session` is the consent gate: every send shows the full preview first, the first one asks —
one confirmation per command — unless the user said yes for the whole command (`--yes`,
`[ai] confirm = false`). `connect()` builds the command's one client before the preview
(locally: no request), and the preview names that client's destination, so the place approved
is the place the request goes.

A request is one `Payload`, built by `build_payload()` (which runs the redactor) and used twice:
`render_preview()` shows every text in it, and the client sends `Payload.params()`. Both read
the same frozen fields, so the preview cannot differ from what is sent. The estimate is local —
one token per three bytes, rounded up, priced from `PRICES` — because the API's own token
counter (`count_tokens`) would upload the text before the user said yes.
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import os
import re
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Protocol
from urllib.parse import quote, unquote

from sijl.redact import Redactor
from sijl.text import printable, printable_block, utf8_safe

DEFAULT_MODEL = "claude-opus-5-5"  # the `ai.model` default
EFFORT = "high"  # explicit: Claude Opus 5.5 defaults to medium
MAX_OUTPUT_TOKENS = 64_000  # answer and thinking together; streamed, so no HTTP timeout
# `sijl ask`'s default output limit (`ai.answer_limit`): an answer about the
# digest rarely needs more, and the preview's cost ceiling is priced at it.
ANSWER_LIMIT = 16_000
ANSWER_LIMITS = (1_024, 128_000)  # what `ai.answer_limit` may be: Claude's 128K output at most
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # the header for `fallbacks: "default"`
# The models that take server-side `fallbacks: "default"`, each with the models Anthropic may
# re-run a declined request on, in order (Claude API reference, 2026-09); None: not published.
# Any other model is sent without fallbacks rather than refused.
FALLBACK_CHAINS: dict[str, tuple[str, ...] | None] = {
    "claude-opus-5-5": ("claude-opus-5", "claude-opus-4-8"),
    "claude-fable-5-1": ("claude-opus-5", "claude-opus-4-8"),
    "claude-opus-5": ("claude-opus-4-8",),
    "claude-sonnet-5-5": None,
}
FALLBACK_MODELS = frozenset(FALLBACK_CHAINS)
# `sijl ai check`: one fixed line, the cheapest way to prove the credentials work end to end.
CHECK_QUESTION = "Sijl is checking that it can reach you. Reply with the single word OK."
CHECK_EFFORT = "low"
CHECK_MAX_TOKENS = 1_024
NO_CREDENTIALS = (
    "no Claude credentials found (set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN, or run "
    "`ant auth login`)"
)


# An address in text: a scheme, "://", then anything up to a space, a quote or an angle bracket;
# and an address inside quotes, which may hold a space (SDK errors quote the URL: 'http://…').
# A scheme is matched from the start of its run of scheme characters (the run's leading digits,
# `+`, `.` and `-` ride along and stay as written): free to start at any letter of a long run,
# `sub()` walked the rest of the run from each one — quadratic (the regex guard).
_URL = re.compile(r"(?<![A-Za-z0-9+.-])[0-9+.-]*[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]*")
_QUOTED_URL = re.compile(r"""(['"])([A-Za-z][A-Za-z0-9+.-]*://[^'"]*?)\1""")
# Headers the SDK adds to every request that describe this machine; Sijl removes them.
PLATFORM_HEADERS = (
    "X-Stainless-OS",
    "X-Stainless-Arch",
    "X-Stainless-Runtime",
    "X-Stainless-Runtime-Version",
)
PLAIN_HTTP = (
    "the base URL uses plain http to a host that is not this machine — your key and evidence "
    "would travel unencrypted"
)
UNREADABLE_URL = (
    "the base URL (ANTHROPIC_BASE_URL or your Anthropic profile's base_url) could not be read"
)


def _secret_parts(url: str) -> set[str]:
    """The parts of `url` that may hold a key — user name and password, query, fragment — as
    written, percent-encoded and decoded. Parts shorter than three characters are left out:
    replacing them would cut ordinary words."""
    rest = url.partition("://")[2]
    head = re.split(r"[?#]", rest, maxsplit=1)[0]  # authority and path
    host_and_path = re.split(r"@|%40", head, flags=re.IGNORECASE)[-1]
    userinfo = head[: len(head) - len(host_and_path)]  # up to the last @ or %40, kept with it
    userinfo = re.sub(r"(@|%40)$", "", userinfo, flags=re.IGNORECASE)
    decoded = unquote(userinfo)
    query_and_fragment = rest[len(head) + 1 :]
    parts = {userinfo, *userinfo.split(":", 1), decoded, *decoded.split(":", 1)}
    parts |= {query_and_fragment, *re.split(r"[?#]", query_and_fragment)}
    return {
        form
        for part in parts
        for form in (part, quote(part, safe=""), quote(part, safe=":@=&"), unquote(part))
        if len(form) >= 3
    }


def scrub(text: str) -> str:
    """`text` without the secrets an address can carry — a textual pass, independent of any URL
    parser, so an address parsed differently (or not at all) still loses them:

    - the parts of ANTHROPIC_BASE_URL that may hold a key, raw, percent-encoded or decoded, are
      replaced wherever they appear (the SDK's errors quote it);
    - every `scheme://…` — also inside quotes, where it may hold a space — loses everything up
      to the last `@` or `%40` before the query (user name and password, however many `@`, `/`
      or `\\` they contain), its query and its fragment; an authority still holding `%3A` (an
      encoded colon: user:password without an `@`) or a space is hidden whole.

    The host and path stay. Applied to every `Status`, every `AiError` and the preview's
    destination."""
    configured = os.environ.get("ANTHROPIC_BASE_URL", "")
    for secret in sorted(_secret_parts(configured), key=len, reverse=True):
        text = text.replace(secret, "***")
    text = _QUOTED_URL.sub(lambda m: m.group(1) + _scrub_url(m.group(2)) + m.group(1), text)
    return _URL.sub(lambda match: _scrub_url(match.group()), text)


def _scrub_url(url: str) -> str:
    scheme, _, rest = url.partition("://")
    head = re.split(r"[?#]", rest, maxsplit=1)[0]  # authority and path; query and fragment go
    head = re.split(r"@|%40", head, flags=re.IGNORECASE)[-1]  # after the last @ or %40
    authority = re.split(r"[/\\]", head, maxsplit=1)[0]
    if re.search(r"%3a|\s", authority, flags=re.IGNORECASE):
        authority = "(hidden)"
    return f"{scheme}://{authority}{_elided(head[len(authority) :])}"


def _elided(path: str) -> str:
    """A path is never shown — a gateway token can live in it: `/…` when there is one."""
    return "/…" if path.strip("/\\") else ""


def _loopback(host: str) -> bool:
    """Is httpx's parsed host this machine? Only `localhost` and a literal loopback address
    (127.0.0.0/8, ::1) count. A name that merely starts like one (`localhost.evil.test`,
    `127.0.0.1.evil.test`), a hex or decimal spelling (`0x7f000001`, `2130706433`, `127.1` —
    some resolvers read them as 127.0.0.1, httpx keeps them as names) and look-alikes do not:
    when in doubt, the answer is no."""
    if host == "localhost":  # httpx lower-cases host names
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _transport_refusal(url: Any) -> str:
    """Why Sijl will not send to `url` (httpx's parse), or "": https anywhere, plain http
    only to this machine (a local gateway)."""
    if url.scheme == "https" or (url.scheme == "http" and _loopback(url.host)):
        return ""
    return PLAIN_HTTP if url.scheme == "http" else f"the base URL uses {url.scheme}, not https"


def _destination(url: Any) -> str:
    """Where requests go, from httpx's own parse of the client's base URL — the parser the SDK
    connects with, so what is shown is where it connects: scheme, host and port; a path is
    shown as `/…` (a gateway token can live in it), and user name, password, query and
    fragment never. A URL with no host is unreadable."""
    host = url.host
    if not host:
        raise AiError(UNREADABLE_URL)
    host = f"[{host}]" if ":" in host else host
    port = f":{url.port}" if url.port is not None else ""
    return f"{url.scheme}://{host}{port}{_elided(url.path)}"


@dataclass(frozen=True)
class Status:
    """The feature flag's answer: on or off, why, and where requests would go when on. Both
    texts are scrubbed when the Status is made (`scrub()`)."""

    on: bool
    reason: str
    destination: str = ""  # the API's base URL: a profile or ANTHROPIC_BASE_URL can change it

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", scrub(self.reason))
        object.__setattr__(self, "destination", scrub(self.destination).rstrip("/"))


class AiError(Exception):
    """Claude could not be used for this request; the message says why, in plain words. It is
    scrubbed when the error is made (`scrub()`): SDK and API texts can quote an address."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


def _sdk() -> ModuleType:
    """The Anthropic SDK, imported on first use (see the module docstring)."""
    import anthropic

    return anthropic


def _new_client(anthropic: ModuleType, http_client: Any = None) -> Any:
    """`anthropic.Anthropic()`: the SDK's own credential chain, which reads the environment and
    the profile files under ~/.config/anthropic — tokens are exchanged or refreshed only when a
    request is sent. Any failure is an AiError saying why, never a traceback: a named profile
    that is missing, a profile that is not private, an ANTHROPIC_BASE_URL that is not a URL."""
    import httpx2  # the SDK's HTTP library, installed with it

    # The SDK sends the operating system, processor and Python version with every request;
    # `omit` in default_headers removes a header (its own documented override layer).
    options: dict[str, Any] = {"default_headers": dict.fromkeys(PLATFORM_HEADERS, anthropic.omit)}
    if http_client is not None:
        options["http_client"] = http_client
    try:
        return anthropic.Anthropic(**options)
    except httpx2.InvalidURL as exc:  # its message quotes the URL: never printed
        raise AiError(UNREADABLE_URL) from exc
    except anthropic.AnthropicError as exc:
        raise AiError(f"Claude credentials could not be read: {exc}") from exc
    except Exception as exc:  # anything else from the SDK's set-up: say what, scrubbed
        name = type(exc).__name__
        raise AiError(f"the Claude client could not be set up: {name}: {exc}") from exc


def connect(
    *, enabled: bool, disabled_by: str = "", http_client: Any = None
) -> tuple[Status, Claude | None]:
    """The feature flag and, when AI is on, the one client this command will send with.

    Building the client runs the SDK's own credential chain, which reads the environment and the
    profile files under ~/.config/anthropic — tokens are exchanged or refreshed only when a
    request is sent — so it is safe before the preview. It is built once, and the preview's
    destination comes from it, so what the user approves is where the request goes: a profile
    switched while the question waits changes nothing.
    `enabled` is the `ai.enabled` setting; `disabled_by` names where a `false` came from
    (`--no-ai`, `SIJL_AI=off`, `ai.enabled = false in …`); `http_client` is for tests."""
    if not enabled:
        return Status(False, f"turned off by {disabled_by or 'ai.enabled = false'}"), None
    try:
        anthropic = _sdk()
    except ImportError as exc:
        reason = f"the Anthropic SDK (the anthropic package) could not be loaded: {exc}"
        return Status(False, reason), None
    try:
        client = _new_client(anthropic, http_client)  # reads; never sends
    except AiError as exc:
        return Status(False, str(exc)), None
    try:
        source = _credential_source(anthropic, client)
        claude = Claude(client)
        destination = claude.destination
    except AiError as exc:  # a base URL with no host
        client.close()
        return Status(False, str(exc)), None
    # Decided on the client's own parsed URL, never the raw string: the SDK's https check for
    # profile addresses accepts `http://[::1]:x@host/`, where httpx connects to `host`.
    refusal = _transport_refusal(client.base_url)
    if refusal:
        client.close()
        return Status(False, refusal), None
    if source is None:
        client.close()
        return Status(False, NO_CREDENTIALS), None
    return Status(True, f"Claude credentials found: {source}", destination), claude


def status(*, enabled: bool, disabled_by: str = "") -> Status:
    """Is AI on, and why? `connect()` without keeping the client: for `sijl ai status`."""
    state, claude = connect(enabled=enabled, disabled_by=disabled_by)
    if claude is not None:
        claude.close()
    return state


def _credential_source(anthropic: ModuleType, client: Any) -> str | None:
    """Which credential the SDK chose, in its own precedence: an API key beats an auth token,
    which beats a profile or workload identity. None when it found nothing."""
    if client.api_key:
        return "ANTHROPIC_API_KEY"
    if client.auth_token:
        return "ANTHROPIC_AUTH_TOKEN"
    provider = client.credentials
    if provider is None:
        return None
    credentials = anthropic.lib.credentials
    if isinstance(provider, credentials.CredentialsFile):
        return f"the `ant auth login` profile {provider.profile!r}"
    if isinstance(provider, credentials.WorkloadIdentityCredentials):
        return "workload identity federation"
    return type(provider).__name__


TIER1_FILE = "digest.txt"  # where the Tier 1 route writes the digest


def tier1_route(question: str = "", flags: Sequence[str] | None = None) -> str:
    """How to ask Claude about your work without Sijl sending anything (Tier 1): write the
    digest, its instructions first (`sijl digest --with-prompt`), to a file and ask Claude Code
    about it — there, the user sees and approves what it reads. `question` (the one `sijl ask`
    could not send) is filled in, quoted for the shell. `flags` are the period and scope flags
    the command was given, carried over as they are, so the digest covers the same work; None
    when the command has no period (`ai status`, `ai check`)."""
    prompt = f"Read {TIER1_FILE} and " + (
        f"answer: {question}" if question else "summarise what I delivered"
    )
    digest = shlex.join(["sijl", "digest", "--with-prompt", "--out", TIER1_FILE, *(flags or ())])
    if flags is None:
        digest += "    (with your usual period flags)"
    return (
        "You can still ask Claude about your work without Sijl sending anything:\n"
        f"  1. {digest}\n"
        f"  2. claude {shlex.quote(prompt)}    (Claude Code, in the same folder)\n"
    )


# ------------------------------------------------------------------------- payload, estimate


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens. A cache write is the 5-minute one: 1.25 x input."""

    input: float
    output: float
    cache_write: float
    cache_read: float


PRICES_AS_OF = "2026-09-25"  # the pricing table of the Claude API reference used for PRICES
PRICES = {
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_write=5.00, cache_read=0.20),
    "claude-opus-5": Price(input=5.00, output=25.00, cache_write=6.25, cache_read=0.50),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_write=2.50, cache_read=0.20),
    "claude-fable-5-1": Price(input=10.00, output=50.00, cache_write=12.50, cache_read=0.25),
    "claude-opus-4-8": Price(input=5.00, output=25.00, cache_write=6.25, cache_read=0.50),
}


@dataclass(frozen=True)
class Turn:
    """One earlier question of a conversation and Claude's answer to it, as they were sent and
    received: a follow-up request carries them after the digest."""

    question: str
    answer: str


@dataclass(frozen=True)
class Payload:
    """Everything one request sends. Build it with `build_payload()`; the preview and the client
    both read it, so what is shown is what is sent."""

    model: str
    system: str  # Sijl's instructions ("" for none)
    digest: str  # the evidence: first, and cached so follow-up questions re-read it cheaply
    question: str
    effort: str = EFFORT
    max_tokens: int = MAX_OUTPUT_TOKENS
    redaction: bool = False  # whether a redactor ran over the digest and the question
    redacted_names: int = 0
    redacted_emails: int = 0
    about: str = ""  # shown in the preview, never sent: what the evidence covers
    history: tuple[Turn, ...] = ()  # the conversation so far, oldest first

    def params(self) -> dict[str, Any]:
        """The exact keyword arguments for `client.beta.messages.stream()`.

        The digest is the first block, marked for the cache; the first question follows it in
        the same message, then each earlier answer and the next question — so a follow-up's
        request starts with exactly the bytes of the first one (append-only), and Claude reads
        the instructions and the digest from its cache."""
        questions = [turn.question for turn in self.history] + [self.question]
        content: list[dict[str, Any]] = []
        if self.digest:
            block = {"type": "text", "text": self.digest, "cache_control": {"type": "ephemeral"}}
            content.append(block)
        content.append({"type": "text", "text": questions[0]})
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        for turn, question in zip(self.history, questions[1:], strict=True):
            messages += [
                {"role": "assistant", "content": [{"type": "text", "text": turn.answer}]},
                {"role": "user", "content": [{"type": "text", "text": question}]},
            ]
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
            "messages": messages,
        }
        if self.system:
            params["system"] = self.system
        if self.model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def texts(self) -> list[tuple[str, str, str]]:
        """(name, note, text) for every text the request carries, in the order it is sent."""
        found = [("instructions", "", self.system), ("evidence", "cached", self.digest)]
        texts = [entry for entry in found if entry[2]]
        for number, turn in enumerate(self.history, 1):
            texts += [
                (f"question {number}", "earlier", turn.question),
                (f"answer {number}", "earlier, Claude's", turn.answer),
            ]
        return [*texts, ("question", "", self.question)]


def build_payload(
    *,
    model: str,
    system: str,
    digest: str,
    question: str,
    redactor: Redactor | None,
    effort: str = EFFORT,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    about: str = "",
    history: tuple[Turn, ...] = (),
) -> Payload:
    """The payload for one request. With a redactor (the default, `ai.redact`), it runs over the
    digest first, then the earlier questions and answers (`history`), then the question, so
    all use the same labels; Sijl's own instructions are not redacted. Text the redactor
    already produced comes back unchanged, so a session's digest keeps its bytes. `about`
    summarises the evidence for the preview (for example "12 repositories, 340 commits, …"); it
    is not sent."""
    if not question.strip():
        raise ValueError("the question is empty")
    # A name that is not UTF-8 arrives as lone surrogates, which no request (and no terminal)
    # can carry: shown as escapes here, so the preview and the request hold the same text.
    system, digest, question, about = map(utf8_safe, (system, digest, question, about))
    history = tuple(Turn(utf8_safe(t.question), utf8_safe(t.answer)) for t in history)
    if redactor is not None:
        digest = redactor(digest)
        history = tuple(Turn(redactor(t.question), redactor(t.answer)) for t in history)
        question = redactor(question)
    return Payload(
        model=model,
        system=system,
        digest=digest,
        question=question,
        effort=effort,
        max_tokens=max_tokens,
        redaction=redactor is not None,
        redacted_names=redactor.names if redactor else 0,
        redacted_emails=redactor.emails if redactor else 0,
        about=about,
        history=history,
    )


def estimate_tokens(text: str) -> int:
    """A local estimate that leans high: one token per three bytes of UTF-8, rounded up. Claude's
    tokenizer usually needs fewer for English prose; hex SHAs and ids come close to it."""
    return math.ceil(len(text.encode("utf-8")) / 3)


@dataclass(frozen=True)
class Estimate:
    model: str
    cached_tokens: int  # instructions + digest: written to the 5-minute cache
    input_tokens: int  # the question, and the earlier questions and answers of a follow-up
    max_output_tokens: int
    chain: tuple[str, ...] | None = ()  # models that may run after a decline; None: unpublished
    follow_up: bool = False  # an earlier request of the session may have filled the cache

    @property
    def price(self) -> Price | None:  # None: no local price for this model
        return PRICES.get(self.model)

    @property
    def input_cost(self) -> float:
        assert self.price is not None
        return (
            self.cached_tokens * self.price.cache_write + self.input_tokens * self.price.input
        ) / 1e6

    @property
    def warm_input_cost(self) -> float:
        """The input when Claude's cache still holds the cached part: read, not written."""
        assert self.price is not None
        return (
            self.cached_tokens * self.price.cache_read + self.input_tokens * self.price.input
        ) / 1e6

    @property
    def max_output_cost(self) -> float:
        assert self.price is not None
        return self.max_output_tokens * self.price.output / 1e6

    def attempt_cost(self, model: str) -> float | None:
        """At most what one model's run of this request costs at its own prices: the cache
        write, the input and the whole output limit. None without a local price."""
        price = PRICES.get(model)
        if price is None:
            return None
        cached, rest = self.cached_tokens * price.cache_write, self.input_tokens * price.input
        return (cached + rest + self.max_output_tokens * price.output) / 1e6

    @property
    def worst_case(self) -> float | None:
        """The ceiling: the requested model declines part-way —
        that attempt is billed — and every model of the fallback chain runs too. None when the
        chain is not published or a model in it has no local price."""
        if self.chain is None:
            return None
        costs = [self.attempt_cost(model) for model in (self.model, *self.chain)]
        return None if None in costs else sum(c for c in costs if c is not None)


def estimate(payload: Payload) -> Estimate:
    """Tokens and cost of `payload`, worked out on this machine. With a digest, everything up to
    and including it is cached, so it is priced as a cache write; without one nothing is."""
    cached = estimate_tokens(payload.system) + estimate_tokens(payload.digest)
    uncached = estimate_tokens(payload.question) + (0 if payload.digest else cached)
    for turn in payload.history:
        uncached += estimate_tokens(turn.question) + estimate_tokens(turn.answer)
    return Estimate(
        model=payload.model,
        cached_tokens=cached if payload.digest else 0,
        input_tokens=uncached,
        max_output_tokens=payload.max_tokens,
        chain=FALLBACK_CHAINS.get(payload.model, ()),
        follow_up=bool(payload.history),
    )


# ---------------------------------------------------------------------------------- preview


def _count(n: int, noun: str, plural: str = "") -> str:
    return f"{n:,} {noun if n == 1 else plural or noun + 's'}"


def _usd(amount: float, *, at_most: bool = False) -> str:
    """Dollars to the cent; an upper bound is rounded up, never down."""
    if at_most:
        amount = math.ceil(amount * 100 - 1e-9) / 100
    return f"${amount:,.2f}" if amount >= 0.01 else "less than $0.01"


def _header(name: str, note: str, text: str) -> str:
    size = _count(len(text.encode("utf-8")), "byte")
    return f"───── {name} ({size}{', ' + note if note else ''}) ─────"


def _how(payload: Payload, destination: str) -> list[str]:
    """Where the request goes and how: the lines both the preview and its summary begin with."""
    if payload.redaction:
        replaced = (
            f"{_count(payload.redacted_names, 'name')} and "
            f"{_count(payload.redacted_emails, 'email address', 'email addresses')} replaced"
        )
        redaction = f"on — {replaced}; only your own names and emails are kept"
    else:
        redaction = "off (ai.redact = false) — names and email addresses are sent as they are"
    lines = [
        f"To: {scrub(destination)}",
        f"Model: {payload.model} · effort {payload.effort} · answer up to "
        f"{payload.max_tokens:,} tokens",
    ]
    if payload.about:
        lines.append(f"Evidence: {payload.about}")
    if payload.model in FALLBACK_MODELS:
        lines.append(
            f"Fallback: if {payload.model} declines, Anthropic may re-run this request on "
            "another Claude model"
        )
    return [*lines, f"Redaction: {redaction}"]


def render_preview(payload: Payload, *, destination: str) -> str:
    """What will be sent, in full: where to and how, each text exactly, then the local estimate.
    Terminal controls in any of it show as visible escapes (`text.printable()`); the request
    carries the characters themselves."""
    lines = ["What Sijl will send to Claude", *_how(payload, destination)]
    for name, note, text in payload.texts():
        lines += [_header(name, note, text), text.removesuffix("\n")]
    lines.append("───── end of what will be sent ─────")
    lines += _estimate_lines(estimate(payload))
    return printable_block("\n".join(lines)) + "\n"


def render_exact(payload: Payload, *, destination: str) -> str:
    """The file a request too long to read on screen is saved as: the preview's own lines,
    escaped, and between them each text exactly as it is sent — terminal controls and invisible
    characters included, since this is a file, not the terminal — after its header line, which
    gives its size in bytes, and before a newline. So the file holds the request's texts byte
    for byte, and its sha256 is theirs."""
    head = ["What Sijl will send to Claude", *_how(payload, destination)]
    parts = [printable_block("\n".join(head)) + "\n"]
    for name, note, text in payload.texts():
        parts += [printable(_header(name, note, text)) + "\n", text, "\n"]
    tail = ["───── end of what will be sent ─────", *_estimate_lines(estimate(payload))]
    return "".join([*parts, printable_block("\n".join(tail)) + "\n"])


def render_summary(payload: Payload, *, destination: str, full: str, saved_to: str) -> str:
    """The preview of a request too long to read on screen: where and how, each text with
    its size — the question itself in full — then where `full` (`render_exact()`) was saved,
    with its lines, bytes and sha256 (`sha256sum` on the file gives the same), then the
    estimate. Every byte that will be sent is in the file; none of the evidence is here."""
    texts = payload.texts()
    sizes = [f"{len(text.encode('utf-8')):,}" for _, _, text in texts]
    name_width = max(len(name) for name, _, _ in texts)
    size_width = max(len(size) for size in sizes)
    lines = [
        "What Sijl will send to Claude — too long to read here: every byte of it is in the "
        "file named below",
        *_how(payload, destination),
        "In the order it is sent:",
    ]
    for (name, note, text), size in zip(texts, sizes, strict=True):
        what = f": {printable(text)}" if (name, note) == ("question", "") else ""
        what = what or (f", {note}" if note else "")
        lines.append(f"  {name:<{name_width}}  {size:>{size_width}} bytes{what}")
    data = full.encode("utf-8")
    lines += [
        "Every byte of it, exactly as it will be sent:",
        f"  {saved_to}",
        f"  {full.count(chr(10)):,} lines, {len(data):,} bytes, "
        f"sha256 {hashlib.sha256(data).hexdigest()}",
        "  Its texts are the exact characters sent, terminal controls included: read it in an "
        "editor or with `less`, not `cat`.",
    ]
    lines += _estimate_lines(estimate(payload))
    return printable_block("\n".join(lines)) + "\n"


def _estimate_lines(est: Estimate) -> list[str]:
    tokens = est.cached_tokens + est.input_tokens
    lines = ["Estimate (local — nothing has been sent to count it):"]
    if est.price is None:
        return [
            *lines,
            f"  input   ~{tokens:,} tokens",
            f"  output  up to {est.max_output_tokens:,} tokens",
            f"  no local price for {est.model}, so the cost is not estimated",
        ]
    cached = f" ({est.cached_tokens:,} of them written to Claude's 5-minute cache)"
    cached = cached if est.cached_tokens else ""
    answered = est.input_cost + est.max_output_cost
    lines.append(f"  input   ~{tokens:,} tokens: {_usd(est.input_cost)}{cached}")
    if est.follow_up and est.cached_tokens:
        lines.append(
            f"          {_usd(est.warm_input_cost)} instead if Claude's cache still holds them: "
            "it keeps them for 5 minutes after each use"
        )
    lines.append(
        f"  output  up to {est.max_output_tokens:,} tokens: at most "
        f"{_usd(est.max_output_cost, at_most=True)} (Claude's thinking counts as output)"
    )
    chain = est.chain
    if chain == ():
        lines.append(f"  total   at most {_usd(answered, at_most=True)}")
    else:
        if chain is None:
            total = (
                "cannot be computed: Anthropic does not publish which models may answer when "
                f"{est.model} declines"
            )
        elif est.worst_case is None:
            unpriced = ", ".join(m for m in chain if m not in PRICES)
            total = f"cannot be computed: no local price for {unpriced}"
        else:
            total = (
                f"at most {_usd(est.worst_case, at_most=True)} — the worst case: if "
                f"{est.model} declines part-way, that attempt is billed and Anthropic may "
                f"re-run the request on {', then '.join(chain)}"
            )
        lines += [
            f"  total   {total}",
            f"          if {est.model} answers: at most {_usd(answered, at_most=True)}",
        ]
    priced = [m for m in (est.model, *(chain or ())) if m in PRICES]
    rates = "; ".join(
        f"{m}: input {_usd(PRICES[m].input)}, cache write {_usd(PRICES[m].cache_write)}, "
        f"output {_usd(PRICES[m].output)}"
        for m in priced
    )
    return [
        *lines,
        f"Prices as of {PRICES_AS_OF}, per million tokens — {rates}.",
        "The real usage is shown after the answer.",
    ]


# ----------------------------------------------------------------------------------- client


@dataclass(frozen=True)
class Attempt:
    """One model's run within a request, as `usage.iterations` reports it: a declined attempt
    is billed too, at its model's own prices."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def cost(self) -> float | None:
        price = PRICES.get(self.model)
        if price is None:
            return None
        return (
            self.input_tokens * price.input
            + self.cache_write_tokens * price.cache_write
            + self.cache_read_tokens * price.cache_read
            + self.output_tokens * price.output
        ) / 1e6


@dataclass(frozen=True)
class Reply:
    """What came back. Only a reply that ended on its own (`end_turn`) with some text is a
    complete answer; anything else keeps its text here but is never rendered as an answer."""

    text: str  # the answer: the text after the last fallback boundary, if there was one
    stop_reason: str
    model: str  # the model that answered (a fallback model when one took over)
    requested_model: str
    max_tokens: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    refusal_category: str = ""
    fell_back: bool = False
    attempts: tuple[Attempt, ...] = ()  # every billed run; empty: one, the token fields above

    @property
    def billed(self) -> tuple[Attempt, ...]:
        """Every model's run that is billed, in order; the last one gave this reply."""
        if self.attempts:
            return self.attempts
        return (
            Attempt(
                self.model,
                self.input_tokens,
                self.output_tokens,
                self.cache_read_tokens,
                self.cache_write_tokens,
            ),
        )

    @property
    def complete(self) -> bool:
        return self.problem == ""

    @property
    def problem(self) -> str:
        """Why this is not a complete answer, or "" when it is."""
        shown = "not shown as an answer"
        if self.stop_reason == "refusal":
            category = f" (category: {self.refusal_category})" if self.refusal_category else ""
            return f"Claude declined to answer{category}; what it wrote is {shown}."
        if self.stop_reason == "max_tokens":
            return (
                f"Claude's answer was cut off at the output limit ({self.max_tokens:,} tokens), "
                f"so it is incomplete and {shown}."
            )
        if self.stop_reason == "model_context_window_exceeded":
            return (
                "The request was larger than the model's context window, so the answer is "
                f"incomplete and {shown}."
            )
        if self.stop_reason != "end_turn":
            return (
                f"Claude stopped before finishing ({self.stop_reason or 'no reason given'}), so "
                f"the answer is incomplete and {shown}."
            )
        if not self.text.strip():
            return "Claude returned no text."
        return ""


class Claude:
    """Sijl's one door to the Anthropic API. `send()` streams the request (long answers
    never hit an HTTP timeout) and reads the whole message with `get_final_message()`; SDK
    errors become `AiError`s that say what to do."""

    def __init__(self, client: Any) -> None:
        self._client = client  # an `anthropic.Anthropic`; tests give it a fake HTTP transport

    @property
    def destination(self) -> str:
        """Where this client sends, as httpx parsed its base URL (`_destination()`)."""
        return _destination(self._client.base_url)

    def close(self) -> None:
        self._client.close()

    def send(self, payload: Payload) -> Reply:
        anthropic = _sdk()
        try:
            with self._client.beta.messages.stream(**payload.params()) as stream:
                message = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            raise AiError(_status_error(anthropic, exc, payload.model)) from exc
        except anthropic.APIConnectionError as exc:  # timeouts included
            raise AiError(
                f"could not reach {_destination(self._client.base_url)} ({exc}); check the "
                "network connection"
            ) from exc
        except anthropic.AnthropicError as exc:  # e.g. a profile whose token cannot be read
            raise AiError(f"Claude could not be used: {exc}") from exc
        except Exception as exc:
            # The reply broke while it was read: the connection reset or timed out mid-stream
            # (httpx errors the SDK does not wrap), a gateway or captive portal answered with
            # HTML (the SDK's event decoder asserts), an event was not JSON. Whatever came
            # before is not an answer.
            cause = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            raise AiError(
                f"the answer is incomplete: the reply could not be read ({cause})"
            ) from exc
        return _reply(message, payload)


def _status_error(anthropic: ModuleType, exc: Any, model: str) -> str:
    """The API's refusal in plain words, most specific first, with its own message and the
    request id (which Anthropic support asks for)."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    said = str(error.get("message") or exc.message)
    request_id = getattr(exc, "request_id", None)
    said += f" (request id {request_id})" if request_id else ""
    code = exc.status_code
    if isinstance(exc, anthropic.AuthenticationError):
        return (
            f"the API did not accept the credentials ({code}): {said} — check ANTHROPIC_API_KEY, "
            "or run `ant auth login` again"
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return f"these credentials are not allowed ({code}) to use {model}: {said}"
    if isinstance(exc, anthropic.NotFoundError):
        return f"the API does not know {model} ({code}): {said} — check ai.model"
    if isinstance(exc, anthropic.RateLimitError):
        return f"the API is rate-limiting these credentials ({code}): {said} — try again later"
    if isinstance(exc, anthropic.BadRequestError):
        return f"the API refused the request ({code}): {said}"
    if code == 529:
        return f"the API is overloaded ({code}): {said} — try again in a few minutes"
    return f"the API returned an error ({code}): {said} — try again later"


def _reply(message: Any, payload: Payload) -> Reply:
    blocks = list(message.content)
    # After a decline mid-answer, the declined model's partial text comes before a `fallback`
    # block: only what follows the last boundary is the answer.
    boundary = max((i for i, block in enumerate(blocks) if block.type == "fallback"), default=-1)
    text = "".join(block.text for block in blocks[boundary + 1 :] if block.type == "text")
    usage = message.usage
    # With fallbacks, top-level usage covers only the attempt that answered; `iterations` lists
    # every billed run, the declined ones included.
    attempts = tuple(
        Attempt(
            model=getattr(it, "model", None) or payload.model,
            input_tokens=it.input_tokens or 0,
            output_tokens=it.output_tokens or 0,
            cache_read_tokens=it.cache_read_input_tokens or 0,
            cache_write_tokens=it.cache_creation_input_tokens or 0,
        )
        for it in usage.iterations or []
        if it.type in ("message", "fallback_message")
    ) or (
        Attempt(
            message.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_input_tokens or 0,
            usage.cache_creation_input_tokens or 0,
        ),
    )
    details = message.stop_details
    return Reply(
        text=text,
        stop_reason=message.stop_reason or "",
        model=message.model,
        requested_model=payload.model,
        max_tokens=payload.max_tokens,
        input_tokens=sum(a.input_tokens for a in attempts),
        output_tokens=sum(a.output_tokens for a in attempts),
        cache_read_tokens=sum(a.cache_read_tokens for a in attempts),
        cache_write_tokens=sum(a.cache_write_tokens for a in attempts),
        refusal_category=(details.category or "") if details is not None else "",
        fell_back=boundary >= 0 or any(a.model != payload.model for a in attempts),
        attempts=attempts,
    )


def reply_label(reply: Reply) -> str:
    """The line every answer starts with: AI-written, by which model — the fallback named when
    one answered — and a reminder to check it."""
    by = reply.model
    if reply.fell_back:
        by += f" (Anthropic's fallback after {reply.requested_model} declined)"
    return printable(f"*AI-written by {by} — check it against your evidence.*")


def shown_text(reply: Reply) -> str:
    """The answer's text exactly as the terminal shows it: trimmed, every line `printable()`.
    What the citation check reads, so what it verifies is what the user reads."""
    return printable_block(reply.text.strip())


def render_reply(reply: Reply) -> str:
    """A complete answer, labelled as AI-written. Anything else is refused: an incomplete reply
    is never rendered as an answer."""
    if not reply.complete:
        raise ValueError(f"not a complete answer: {reply.problem}")
    return f"{reply_label(reply)}\n\n{shown_text(reply)}\n"


def render_usage(reply: Reply) -> str:
    """The real usage the API reported — every billed attempt, declined ones included — priced
    locally at each model's own prices (the bill is Anthropic's). A model without a local price
    is named; no number is made up."""
    attempts = reply.billed
    cache_write = sum(a.cache_write_tokens for a in attempts)
    cache_read = sum(a.cache_read_tokens for a in attempts)
    total_input = sum(a.input_tokens for a in attempts) + cache_write + cache_read
    cache = []
    if cache_write:
        cache.append(f"{cache_write:,} written to its cache")
    if cache_read:
        cache.append(f"{cache_read:,} read from its cache")
    line = f"Claude used {total_input:,} input tokens"
    line += f" ({', '.join(cache)})" if cache else ""
    line += f" and {sum(a.output_tokens for a in attempts):,} output tokens"
    if len(attempts) > 1:
        last = "answered" if reply.complete else "stopped"
        roles = [f"{a.model} declined" for a in attempts[:-1]] + [f"{attempts[-1].model} {last}"]
        line += f" over {len(attempts)} attempts ({', '.join(roles)})"
    unpriced = list(dict.fromkeys(a.model for a in attempts if a.cost() is None))
    if unpriced:
        names = ", ".join(unpriced)
        return printable_block(f"{line}; the cost cannot be computed: no local price for {names}.")
    cost = sum(a.cost() or 0.0 for a in attempts)
    prices = (
        f"the local prices for {attempts[0].model}" if len(attempts) == 1 else "the local prices"
    )
    return printable_block(f"{line}: about ${cost:,.4f} at {prices}.")


# ---------------------------------------------------------------------------------- consent

CONFIRM_QUESTION = "Send this to Claude?"
# A preview longer than this many characters (about 250 lines) is saved whole to a file and
# summarised on screen, when the command gives Session a place to save it.
PREVIEW_MAX_CHARS = 20_000
PRE_APPROVED = (
    "Sending without asking: you said yes for this command (--yes, SIJL_AI_CONFIRM=false or "
    "ai.confirm = false)."
)


class Sender(Protocol):
    @property
    def destination(self) -> str: ...

    def send(self, payload: Payload) -> Reply: ...

    def close(self) -> None: ...


class Session:
    """One command's use of Claude, and the gate in front of it: nothing reaches Claude before
    the preview and the user's yes.

    `sender` is the one client of the command (`connect()`'s `Claude`, or a fake in tests):
    the preview names its destination and the request goes through it, so the place shown is
    the place used. `show` receives the preview (the CLI writes it to stderr); `confirm` asks
    the question and returns the answer, or is None when the user already said yes for the
    command. `save` keeps the request whole where the user can open it, when its preview
    is longer than `PREVIEW_MAX_CHARS` — `render_exact()`: its texts byte for byte — and returns
    where, a file's path; the screen gets `render_summary()`. Without it, every preview is shown
    whole."""

    def __init__(
        self,
        *,
        sender: Sender,
        show: Callable[[str], None],
        confirm: Callable[[str], bool] | None,
        save: Callable[[str], str] | None = None,
    ) -> None:
        self._sender, self._show, self._confirm, self._save = sender, show, confirm, save
        self._agreed = confirm is None

    def send(self, payload: Payload) -> Reply | None:
        """Preview `payload`, ask if nobody has yet, and send it: the reply, or None when the
        user said no — then nothing was sent."""
        destination = self._sender.destination
        full = render_preview(payload, destination=destination)
        if self._save is not None and len(full) > PREVIEW_MAX_CHARS:
            exact = render_exact(payload, destination=destination)
            saved_to = self._save(exact)  # whole, before anything is asked or sent
            full = render_summary(payload, destination=destination, full=exact, saved_to=saved_to)
        self._show(full)
        if self._confirm is None:
            self._show(PRE_APPROVED)
        elif not self._agreed:
            if not self._confirm(CONFIRM_QUESTION):
                return None
            self._agreed = True
        return self._sender.send(payload)
