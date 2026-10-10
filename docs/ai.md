# AI assist (Tier 2): asking Claude from inside Sijl — `sijl ask`

## What it is

Sijl's report is exact and needs no AI: every number in it is counted by Sijl itself. On top of
it, there are two ways to **ask an AI questions** about your work — "what did I deliver in Q3?",
"summarise my half year in five bullet points":

- **Tier 1 — free, no API key.** `sijl digest --with-prompt` writes your evidence to a file; you
  give it to Claude Code or any AI chat yourself ([Ask an AI about your work — `sijl
  digest`](digest.md)). Sijl sends nothing.
- **Tier 2 — `sijl ask`, with an Anthropic API key.** Sijl builds the same digest, shows you
  exactly what it will send to Claude, Anthropic's AI model, with a cost estimate, and sends it
  only after your yes. It then checks every id the answer cites against your evidence.

This page is about Tier 2. One switch decides whether it is available: with Claude credentials,
the AI features are on; without them — or if you turn them off — they are off, and every AI
command tells you why and how to get the same help through Tier 1, without Sijl sending anything.

Nothing reaches Claude before you have seen exactly what will be sent, with a cost estimate, and
said yes. The use of the API is billed to your own Anthropic account.

## Who it's for

- Anyone preparing a review who wants to **ask questions** about months of their own work instead
  of reading a long report line by line.
- People who **already use Claude** — with an API key, or logged in with Anthropic's `ant` tool.
- People who **cannot or would rather not** send their data from Sijl: Tier 1 gives them the same
  help through Claude Code or another AI tool, where they control what it reads.

## Why it matters

- **Faster answers.** A six-month review period is hundreds of commits; Claude can read all of
  it at once and answer a question in seconds.
- **Trust.** Claude's answer is shown as a quoted, AI-written block, and an answer that was cut
  off or declined is never shown as if it were complete. After it, Sijl — not Claude — lists
  every id the answer cited that it found in your evidence, each with your digest's own line for
  it. Ids that are not there — invented, mis-copied, written with look-alike characters — are
  marked in the answer.
- **Privacy by default.** Colleagues' names and email addresses are replaced before anything is
  sent; you see the result before you agree.
- **No surprises on cost.** You see an estimate before sending and the real usage after.

## How to use it

**1. See whether AI is on, and why:**

```bash
sijl ai status
```

```text
AI: on — Claude credentials found: ANTHROPIC_API_KEY
Model: claude-opus-5-5 · effort high · requests go to https://api.anthropic.com
Before anything is sent, Sijl shows all of it and asks first; colleagues' names and emails are redacted.
Nothing has been sent: `sijl ai check` sends one test line, after asking.
```

When it is off, it says why and what to do instead — write the digest and ask Claude Code about
it ([Tier 1](digest.md)):

```text
AI: off — no Claude credentials found (set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN, or run `ant auth login`)
You can still ask Claude about your work without Sijl sending anything:
  1. sijl digest --with-prompt --out digest.txt    (with your usual period flags)
  2. claude 'Read digest.txt and summarise what I delivered'    (Claude Code, in the same folder)
```

`sijl ai status` itself never sends anything: it only looks for credentials on your computer.

**2. Give Sijl Claude credentials** — any one of these, the same ones Anthropic's own tools use:

- an API key in the `ANTHROPIC_API_KEY` environment variable (create one at
  [console.anthropic.com](https://console.anthropic.com); usage is billed to that account);
- an access token in `ANTHROPIC_AUTH_TOKEN`;
- a login with Anthropic's command-line tool: `ant auth login` (no variable needed afterwards —
  "no API key set" does not mean "no credentials").

Credentials are never written in Sijl's config file; Sijl refuses a key there.

**3. Check that they work:**

```bash
sijl ai check
```

Sijl shows exactly what it will send — one fixed line asking Claude to reply "OK" — with the
cost estimate (well under a cent), and asks `Send this to Claude? [y/N]`. Answer `y` and Claude's
reply appears, labelled as AI-written, followed by the real usage. Answer anything else, or
nothing, and nothing is sent.

**4. Ask about your work:**

```bash
sijl ask "What did I deliver in Q3?" --quarter 2026Q3
```

`sijl ask` reads your work for the period exactly as `sijl report` and `sijl digest` do — the
same period, folders and email options, or your config file — builds the digest (the compact
summary of your commits, tickets and merge requests that [`sijl digest`](digest.md) describes),
and asks Claude your question about it. Before anything is sent you see what will be sent and the
cost, and answer `Send this to Claude? [y/N]`. Then (an invented example):

```text
Claude's answer — AI-written by claude-opus-5-5, not verified by Sijl:
│ In Q3 you delivered 31 merge requests across webapp and api. The largest piece of work was
│ [PROJ-12], the billing flow: [webapp!42] ([1a2b3c4d]), …

───── Evidence Sijl checked: from your digest, not written by AI ─────
- PROJ-12: ### PROJ-12 — 14 commits, 3 requests; 2026-07-02 to 2026-08-20; requests: webapp!42, …
- webapp!42: ### webapp!42 — 2026-07-30 [webapp]; merged by a colleague; 6 of 6 commits yours; tickets: PROJ-12; title: Billing
- 1a2b3c4d: - 1a2b3c4d 2026-07-28 [webapp] feat(PROJ-12): billing flow

Evidence check (by Sijl, not AI): Sijl verified 3 ids (listed under Evidence Sijl checked). Treat any other id-like text in the answer as unverified.
```

followed, on the screen only, by what it really cost — for example "Claude used 67,400 input
tokens (67,317 written to its cache) and 1,150 output tokens: about $0.3599 at the local prices
for claude-opus-5-5". **Everything after the `│` block is Sijl's; everything inside it is
Claude's.** The answer goes to standard output, so `sijl ask "…" > answer.md` saves it — there
Claude's answer is a Markdown code block, shown as plain text whatever it holds, and Sijl's lines
are Markdown with your digest's text escaped: the same boundary in a document, and nothing in the
answer can hide what Sijl wrote after it.

**Follow-up questions** — add `-i`, or leave the question out to be asked for one:

```bash
sijl ask -i "What did I deliver in Q3?" --quarter 2026Q3
sijl ask --quarter 2026Q3
```

After each answer Sijl asks `Your question (an empty line ends):`. You say yes once, before the
first question; every later question is still shown before it goes. Follow-ups are cheap: Claude
keeps your evidence in its short-term cache, so a question within five minutes of the last one
re-reads it for about a twentieth of the price — the line under each answer shows "read from its
cache". Claude also sees the earlier questions and answers, so "and in June?" works.

**Useful options:** `--answer-limit 8000` (a shorter longest answer, and a lower cost ceiling),
`--max-chars 100000` (send less evidence: what does not fit is left out and the preview says how
much), `--yes` (do not ask; the preview is still shown), `--no-ai` (show the Tier 1 route
instead).

## What is sent, and when

**Nothing, until you say yes.** Before every request Sijl prints, on your screen:

- **where it goes** — normally `https://api.anthropic.com`; if your Anthropic profile or the
  `ANTHROPIC_BASE_URL` variable points elsewhere (a company gateway), that address — shown as its
  scheme, host and port only: a user name, password, `?…` part or path it may carry (a gateway
  can hide a token in any of them) is never printed, and a path shows as `/…`;
- **how** — the model, how hard it should think ("effort"), and the longest answer allowed;
- **whether another Claude model may answer** — if the chosen model declines a request for safety
  reasons, Anthropic can re-run the same request on another Claude model in the same call; Sijl
  allows this for the models that support it and says so;
- **what was redacted** — how many names and email addresses were replaced;
- **every word that will be sent, in full** — Sijl's instructions to Claude, the evidence (the
  digest that `sijl digest` writes — [Tier 1](digest.md)), the earlier questions and answers of a
  `sijl ask` session, and your question — each with its size;
- **the estimate**, then the question `Send this to Claude? [y/N]`.

**When it is too long to read on screen** — six months of evidence is usually a few thousand
lines — `sijl ask` shows a summary instead, and puts every word in a file you can open while the
question waits. For an illustrative six months of work (about 1,000 commits and 300 merge
requests):

```text
What Sijl will send to Claude — too long to read here: every byte of it is in the file named below
To: https://api.anthropic.com
Model: claude-opus-5-5 · effort high · answer up to 16,000 tokens
Evidence: 8 repositories, 1,000 commits, 300 requests of yours, 40 tickets, 2026-04-01 to 2026-09-30
Fallback: if claude-opus-5-5 declines, Anthropic may re-run this request on another Claude model
Redaction: on — 12 names and 4 email addresses replaced; only your own names and emails are kept
In the order it is sent:
  instructions    1,940 bytes
  evidence      200,009 bytes, cached
  question           25 bytes: what did I deliver in Q3?
Every byte of it, exactly as it will be sent:
  /tmp/sijl-ask-xxxxxxxx/request-1.txt  (deleted when this command ends)
  2,401 lines, 203,357 bytes, sha256 0123abcd0123abcd0123abcd0123abcd0123abcd0123abcd0123abcd0123abcd
  Its texts are the exact characters sent, terminal controls included: read it in an editor or with `less`, not `cat`.
Estimate (local — nothing has been sent to count it):
  …
Send this to Claude? [y/N]
```

(The temporary folder's name and the sha256 are different on every run.) Open the file in another
terminal or an editor (or press Ctrl-Z, `less <file>`, then `fg`) and read it before you answer.
It holds every text of the request exactly as it will be sent — byte for byte, hidden control
codes included, so read it with an editor or `less`, not `cat` — between Sijl's headings;
`sha256sum <file>` prints the same code as the summary, so you know it is this request's file.
Only you can read it, and it is deleted when the command ends — also if the command is stopped
(Ctrl-C, Ctrl-\\, `kill`), its terminal closed, or the program reading its output gone
(`sijl ask … | head`) — so your evidence does not linger.

Only `y` or `yes` sends. One yes covers the rest of that command — in interactive mode you are
asked once, before the first question, and every later question is still shown before it goes.
To skip the question (for example in a script), use `--yes` for one command, or turn asking off
with `confirm = false` under `[ai]` or `SIJL_AI_CONFIRM=false` in your shell; the preview is still
printed, followed by a line saying nothing was asked, and `sijl ai status` says where asking was
turned off.

**Your commit messages are sent as written.** Redaction replaces people's names and email
addresses only; anything else in a subject or a title — a customer's name, an internal host name,
a password someone committed by mistake — goes to Claude exactly as it is. The preview shows every
word, so read it before you answer `y`. Check your employer's policy on AI tools before you send
work information to any of them.

Odd characters that a terminal would act on (hidden control codes in a commit message), or that a
terminal shows as nothing (zero-width and other invisible characters), are shown as visible codes
such as `\x1b` or `\u200b` (U+200B, a zero-width space) — in the preview, in Claude's answer and everywhere else Sijl prints —
so nothing can be tampered with; the request (and the saved file) contains the real characters.

## Cost

Claude is paid per **token** — a few characters of text — and the bill goes to the Anthropic
account your credentials belong to. Sijl estimates on your computer, before anything is sent. For
`sijl ask "what did I deliver in Q3?"` over the illustrative six months above (a digest of about
200,000 characters):

```text
Estimate (local — nothing has been sent to count it):
  input   ~67,326 tokens: $0.34 (67,317 of them written to Claude's 5-minute cache)
  output  up to 16,000 tokens: at most $0.32 (Claude's thinking counts as output)
  total   at most $2.30 — the worst case: if claude-opus-5-5 declines part-way, that attempt is billed and Anthropic may re-run the request on claude-opus-5, then claude-opus-4-8
          if claude-opus-5-5 answers: at most $0.66
Prices as of 2026-09-25, per million tokens — claude-opus-5-5: input $4.00, cache write $5.00, output $20.00; claude-opus-5: input $5.00, cache write $6.25, output $25.00; claude-opus-4-8: input $5.00, cache write $6.25, output $25.00.
The real usage is shown after the answer.
```

A follow-up question adds one line under the input: "$0.02 instead if Claude's cache still holds
them: it keeps them for 5 minutes after each use".

- **Rough costs for six months of work** (estimates — the preview always shows the real one for
  your data): about **$0.40–0.80** for the first question, depending on how much you did and how
  long the answer is, and about **$0.02** for each follow-up within five minutes, plus its own
  answer. Real answers are usually far shorter than the limit, and you pay only for what is
  generated. `--answer-limit` and `--max-chars` lower the ceiling.
- **Input** is what you send. Sijl counts about one token per three characters, which leans
  high. (Anthropic offers an exact counter, but using it would send your text to Anthropic before
  you agreed — so Sijl never uses it before your yes.)
- **The cache.** The evidence is most of what a question sends, and it is marked for Claude's
  short-term cache: the first question pays a little more for it (in the example, $0.34 to send
  and cache about 67,000 tokens), and follow-up questions within five minutes re-read it for about
  a twentieth of the price (about $0.02).
- **Output** is Claude's answer, including its thinking. Its cost is shown as a ceiling — the
  longest answer allowed — rounded up; real answers are usually much shorter, and you pay only for
  what is generated.
- **The total is a true worst case.** If the model declines a request part-way for safety
  reasons, Anthropic can re-run it on another Claude model — and you pay for the declined attempt
  *and* the new one, at the other model's prices. So the total adds every model that might run
  (for Opus 5.5: Opus 5, then Opus 4.8) and says so; the line below it gives the ceiling when the
  chosen model simply answers, which is the usual case. When Anthropic does not publish which
  models may take over, or Sijl has no price for one, the estimate says the total cannot be
  computed rather than show a number that is not a ceiling.
- **After the answer** (or after an incomplete one — it was billed too), Sijl prints the real
  usage Anthropic reported, every attempt included, each at its own model's prices: "Claude used
  1,012 input tokens … and 40 output tokens: about $0.0048".
- The prices are copied into Sijl with their date, which the estimate shows. Your bill comes from
  Anthropic and is the reference.

## What Sijl checks in an answer

Claude is asked to answer only from your evidence, to say **"not in the data"** when your evidence
does not hold the answer, and to cite every id it uses — a commit's short SHA, a merge or pull
request, a ticket — in one strict form: **`[[ID]]`**, exactly one id from your digest between
double square brackets.

**What you can rely on** — the part Sijl writes, after Claude's quoted answer:

- **"Evidence Sijl checked"** lists every id the answer cited as `[[ID]]` that is, **exactly as
  written, character for character**, one of your digest's ids — and next to each, your digest's
  own line for it (the commit's date, repository and subject; the request's or ticket's heading).
  Those lines come from your data, never from Claude's text. Inside the answer such a citation
  shows as `[ID]`, in single brackets, so nothing written right next to it can change the id you
  read. This section is what Sijl verified.
- **The summary line** says only what Sijl verified: "Sijl verified 3 ids (listed under Evidence
  Sijl checked). Treat any other id-like text in the answer as unverified." It never says that
  every id is right, nor that the answer cites none. When something is not in your evidence it
  says how many — "NOT in your evidence, marked in the answer above: 1 [[…]] citation and 2 other
  id-like texts" — and never repeats Claude's words: they stay inside the block.
- **The boundary**: Claude's answer is a block — every line starts with `│` on the screen; a
  Markdown code block when the output goes to a file — and Claude cannot write a line outside it.
  A "section" or a "summary" inside the block that looks like Sijl's is just Claude's text.

**What Sijl also flags, as best it can** — inside the answer:

- a `[[…]]` that is not exactly one of your ids (`[[PROJ-99]]`, `[[ PROJ-12]]`, a `[[` never
  closed) is shown with `(sijl: NOT in your evidence)`;
- anything else that looks like an id but is not in your evidence — invented, mis-copied
  (`1a2b3c4` for `1a2b3c4d`), written without its repository (`#12`), written with characters
  that only look the same (a Cyrillic letter, full-width digits, an invisible character inside),
  or split by formatting so that it only shows as an id once the Markdown is rendered — is marked
  the same way and counted under the summary;
- a `[[ID]]` with letters, digits or other characters written right against it (`x[[PROJ-12]]`,
  `[[PROJ-12]]3`) is marked too: once the brackets are gone, as a Markdown viewer may show them,
  it reads as another id. Claude is asked to leave a space or punctuation around each citation.

This detection is strict on purpose, and best effort by nature: it may mark something that only
looks like an id (a hex-looking word, `top-10`, a seven-digit number, "#1" meaning "number one"),
and no detector can promise to catch every invented id in free text — which is why only the ids
Sijl lists after the answer count as checked.

## Privacy

- **Your colleagues are redacted by default.** Every email address that is not one of yours
  (`identity.emails`) becomes `[email 1]`, `[email 2]`, …; every colleague's name Sijl knows from
  your repositories' history becomes `[person 1]`, … . Your own name and addresses stay. The same
  colleague keeps the same label throughout one command, so Claude can still say "you worked with
  [person 1] on PROJ-12". A colleague whose name looks like yours is never confused with you:
  names are matched whole, never as part of another name.
- **What redaction catches:** every email address in any common shape — including git's default
  `name@laptop` addresses, addresses at an IP address, addresses inside links, and addresses typed
  with a full-width `＠` or other look-alike at sign — the user name in a link (and any password
  with it), and the name written beside a colleague's address (`Reviewed-by: Jane Reviewer
  <jane@…>`, `"Jane Reviewer" <jane@…>`, `reported by Jane Reviewer <jane@…>`), even when Sijl
  did not know that name. Known names are found however they are spaced or wrapped across lines,
  with or without accents, with typographic apostrophes and hyphens, and in full-width letters.
  For the digest (and `sijl ask`), Sijl knows everyone in your repositories' whole history —
  authors, committers, reviewers and everyone else a commit's closing lines name — and the user
  names in their addresses.
- **What is sent as written:** your own name and email addresses, your commit subjects, merge
  request and pull request titles, repository names, ticket keys, dates and counts, and your
  question.
- **What redaction cannot catch** — read the preview, it shows every word:
  - a name Sijl does not know, typed on its own ("thanks Jane", a nickname);
  - a known name written another way: last name first ("Colleague, Sam"), joined by a hyphen or
    an underscore (`sam-colleague`, `sam_colleague` in a branch name), or encoded in a link
    (`Sam%20Colleague`);
  - GitHub `@mentions`, and user names inside links;
  - anything else personal or secret that is written in your commit messages — a customer's name,
    a phone number, a password someone committed by mistake. **It is sent exactly as written**;
    redaction only knows people's names and email addresses.
- **Turning redaction off** (`redact = false` under `[ai]`) sends names and addresses as they are;
  the preview then says "Redaction: off".
- **Where your data goes:** only to the address shown in the preview, using your own Claude
  account. What Anthropic does with requests is governed by your agreement with Anthropic — and
  whether you may send work information there at all, by your employer's policy.
- **What else goes with a request:** besides the text in the preview, each request carries the
  usual technical details of Anthropic's software — its name and version (for example
  "Anthropic/Python 1.12.1"), the programming language, time-outs and the retry count — and your
  credentials. That software would also send your operating system, processor type and Python
  version; Sijl removes those. Sijl itself collects nothing (no usage statistics), and without an
  AI command it never contacts Anthropic. Checking whether credentials exist (`ai status`) happens
  entirely on your computer.

## Configuration

Under `[ai]` in `~/.config/sijl/config.toml` (see [Configuration](configuration.md#ai-settings)):

```toml
[ai]
enabled = true                 # false turns every AI feature off (same as SIJL_AI=off)
confirm = true                 # false (or SIJL_AI_CONFIRM=false): never ask — the preview is still shown; --yes for one command
model = "claude-opus-5-5"      # the Claude model to use
redact = true                  # false: send colleagues' names and emails as they are
answer_limit = 16000           # the longest answer `sijl ask` may get, in tokens
```

`--no-ai` on an AI command turns AI off for that command; `SIJL_AI=off` for a whole shell.
`--answer-limit` sets `answer_limit` for one `sijl ask`.

## Behaviour & limits

- **Only over an encrypted connection.** Sijl sends only to an `https://` address, or over plain
  `http://` to a program on your own computer (`http://127.0.0.1:8080`, a local gateway). If your
  profile or `ANTHROPIC_BASE_URL` points anywhere else over plain http, AI is off and `sijl ai
  status` says why: your key and your evidence would travel unencrypted.
- **AI is off** when you turn it off, when no credentials are found, when a named Anthropic
  profile cannot be read, or when the Anthropic software Sijl uses cannot be loaded. Each reason
  is stated, with the Tier 1 route.
- **Answers are labelled** as AI-written, with the model's name ("AI-written by claude-opus-5-5").
  If another Claude model answered after the first declined, the label says so.
- **An incomplete answer is never shown as an answer.** If Claude declines, is cut off at the
  answer limit, or the request is too large, Sijl says what happened, shows the Tier 1 route and
  exits with code 4 — it does not print the partial text as if it were the answer. A cut-off says
  how to allow a longer answer (`--answer-limit`). In a `sijl ask -i` session the session goes on
  and you can ask again; that question is not part of the conversation.
- **When `sijl ask` cannot use Claude** — AI off, no credentials, an API error — it prints why,
  then the Tier 1 route with your question already filled in and **every period and scope flag
  you gave carried over**, so the digest covers exactly the same work (exit code 4). For example,
  `sijl ask "What did I deliver in Q3?" --quarter 2026Q3 --root /Users/you/code` without
  credentials suggests:

  ```text
  You can still ask Claude about your work without Sijl sending anything:
    1. sijl digest --with-prompt --out digest.txt --quarter 2026Q3 --root /Users/you/code
    2. claude 'Read digest.txt and answer: What did I deliver in Q3?'    (Claude Code, in the same folder)
  ```

  `sijl ai status` and `sijl ai check`, which have no period of their own, say "(with your usual
  period flags)" instead.
- **Errors are explained**: credentials not accepted, the model not known (check `model`), too
  many requests (try again later), Anthropic overloaded, no network — each with Anthropic's own
  message and request id, then the Tier 1 route. If the connection breaks while Claude is
  answering, or something other than Anthropic answers (a Wi-Fi login page), Sijl says "the answer
  is incomplete" — it never shows half an answer as if it were one.
- **Folder names that are not valid text** (rare, from old systems) are sent, and shown in the
  preview, as visible codes such as `\xff`.
- **Answers are only as complete as the digest.** If `--max-chars` left evidence out, the digest
  says so and Claude is told to say an answer may be incomplete. Commit message bodies are not in
  the digest at all ([Tier 1](digest.md)).
- The model must be one that supports "effort" and adaptive thinking (current Opus, Sonnet and
  Fable models); the cost estimate is shown only for models Sijl has prices for.
- The preview of a real digest is long — it is every word that will be sent; `sijl ask` shows a
  summary and puts it in a private file (above).
- **Exit codes** (`sijl ask`; the first that applies wins):
  - **5** — an answer cites an id that is not in your evidence (marked in it), or an answer Sijl
    could not check at all (one over 100,000 characters, say) — even if a later question failed;
  - **4** — AI is off, Claude could not be used, or a question got no complete answer (in
    interactive mode only when the questions come from a pipe or a file: on a terminal you see why
    and can simply ask again);
  - **3** — no repository found; **2** — a usage or configuration error; **1** — git missing or
    failing;
  - **0** — answered with no unknown citation detected, or you said no (nothing sent). Exit 0
    means "no unknown citation detected", **not** "every claim in the answer is true".

  `sijl ai check` uses 0 and 4 the same way.
- **Not yet:** a future version may add an AI-written *Key achievements* section to the report
  itself.

## Related

- [Tier 1 — `sijl digest`](digest.md): the same evidence, for Claude Code or any AI chat, with
  nothing sent by Sijl.
- [The report — `sijl report`](report.md) · [Configuration](configuration.md) ·
  [Install](install.md) · [All docs](README.md)
