# PaperFeed

Keyword-driven literature alerts. You give it keywords, it checks PubMed and the
preprint servers every few days, and it writes you a digest of papers you have
not already seen.

Python standard library plus `requests`. No account, no API key, no database.

## Quick start

```bash
cd /Users/xufeiteng/ai_completion/research_tools/paperfeed
python3 paperfeed.py check      # is my config valid?
python3 paperfeed.py run        # fetch and write a digest
open digests/latest.html
```

`digests/latest.html` is always written, even when email is off and even when
nothing new turned up. If you ever wonder whether a run worked, that file is
the answer. `digests/index.html` lists every past digest.

Each digest opens with the **top 5 papers of that run** across all your
topics, then the full list per topic. Abstracts are collapsed behind a
click, papers with free full text are badged and linked straight to the PDF,
and each card carries the senior author's institution plus subject chips.

## The commands

| Command | What it does |
|---|---|
| `python3 paperfeed.py run` | The normal run. Exits quietly if the interval has not elapsed. |
| `python3 paperfeed.py run --force` | Run now, ignoring the interval. |
| `python3 paperfeed.py run --dry-run` | Show what it would find. Writes nothing, remembers nothing. |
| `python3 paperfeed.py run --set "name"` | Only one keyword set. Good for testing a new one. |
| `python3 paperfeed.py status` | Last run, next run, how many papers it remembers. |
| `python3 paperfeed.py check` | Validate config.json and print it back in plain English. |
| `python3 paperfeed.py serve` | Open the latest digest in your browser with Save buttons. |
| `python3 paperfeed.py saved` | List the papers you have kept. |
| `python3 paperfeed.py search "text"` | Search your saved papers. |
| `python3 paperfeed.py search "text" --online` | Search PubMed and the preprint servers live. |
| `python3 paperfeed.py trends` | Write a themed summary of the last few months (needs a key). |
| `python3 paperfeed.py set-key` | Store your Anthropic API key. |
| `python3 paperfeed.py test-email` | Send one test message. |

The digest emailed to you is a separate, simpler rendering: mail clients
routinely strip stylesheets and ignore media queries, so the email version
writes its styling directly onto each element and skips the collapsible
abstracts. It is built for a phone screen. The full version stays on your Mac.

## Keeping papers

PaperFeed does not hoard. A run writes its digest and forgets every paper in
it. The only papers that persist are the ones you choose:

```bash
python3 paperfeed.py serve
```

That opens your latest digest at `http://127.0.0.1:8931` with a **+ Save**
button on each paper. Click it and the paper goes into `library.db`, a plain
SQLite file you can open with any SQLite browser or simply delete. Click again
to remove it. Ctrl-C stops the server.

It is bound to `127.0.0.1`, so the page is reachable only from this Mac — not
from your network, not from the internet. That is why it needs no login. It
serves the digest file that is already on disk; nothing is fetched and nothing
is uploaded.

If port 8931 is busy it quietly steps to the next free one and tells you which
it used. `--port` picks your own.

## Working with what you saved

`paperfeed serve` then **View library**. Every paper gets:

- **unread / reading / read** — click to set. The filter pills at the top let
  you pull up just what you have not read yet.
- **remove** — drop it from the library.
- **Explain this** — a short, concrete take on the paper: what they actually
  did, why it matters, the limitation to probe first, and who should read the
  full text. The answer is cached, so you pay for it once.

Two library-wide tools sit above the list:

- **Suggest research directions** — reads everything you have saved and
  proposes 3–5 directions the collection points toward but does not close,
  each with a concrete first step and the papers it came from. What you
  choose to keep is a signal, and this reads that signal back to you.
- **Stuck on something?** — describe a problem in your own work and get an
  answer grounded in your saved papers. It is told to distinguish what the
  papers actually support from its own reasoning, and to say plainly when
  your saved papers do not bear on the question rather than stretching them.

All three need an Anthropic API key. You can paste it straight into the
library page — the panel at the top has a field for it — or use
`python3 paperfeed.py set-key` if you prefer the terminal. Either way it is
checked against the API before being saved, stored in your macOS Keychain,
and never written into this project. A link on the page removes it again.
Without a key the library still works; the panel just offers to set one. Every DOI these
tools cite is checked against your actual library — a citation that cannot be
traced to a paper you saved is dropped before you see it.

## Finding things again

```bash
python3 paperfeed.py search "disordered proteins"
python3 paperfeed.py search "protein hallucination" --online --since 2025-01-01
```

Without `--online` it searches the papers you saved. With `--online` it asks
PubMed and the preprint servers directly, over whatever window you give it,
and offers to save any of the results — useful for the paper you remember
seeing but never kept.

Several words are treated as "all of these must appear". Wrap the search in
double quotes inside the shell quotes to force an exact phrase:

```bash
python3 paperfeed.py search '"de novo protein design"' --online
```

## Editing your keywords

Open `config.json`. Each block under `keyword_sets` is one topic:

```json
{
  "name": "de novo protein design",
  "terms": ["de novo protein design", "RFdiffusion", "ProteinMPNN"],
  "enabled": true
}
```

- **Add a topic**: copy a block, change `name` and `terms`.
- **Remove a topic**: delete the block, or set `"enabled": false` to park it
  without losing the terms.
- **`terms` are OR'ed**: a paper matching any one term is a hit.
- A paper matching two sets appears once, under whichever set is listed first.

Terms are searched as exact phrases in the title and abstract. If you know
PubMed's query syntax you can use it: any term containing a square bracket is
passed through untouched, so `"CRISPR"[MeSH Terms]` works as written.

After editing, run `python3 paperfeed.py check` — it will tell you in plain
English if something is wrong, including which line if the JSON itself is
broken.

### Narrowing a noisy set

Three extra fields, all optional:

```json
{
  "name": "binder design",
  "terms": ["binder design"],
  "all_of": ["protein"],
  "exclude": ["cathode", "electrode"],
  "journals": { "deny": ["Journal of Power Sources"] }
}
```

- **`all_of`** — every term listed must *also* appear. `terms` says "any of
  these"; `all_of` says "and definitely this".
- **`exclude`** — drop papers mentioning these, for this set only.
- **`journals.deny` / `journals.allow`** — `deny` never shows them; `allow`, if
  used, shows *only* those. Matched as substrings, so `"Nature"` covers
  *Nature Methods*.
- **`mute`** at the top level does the same across every set. Good for an
  ambiguous word: "binder" also matches battery-electrode papers, so muting
  `"cathode"` cleans that up.

Anything a filter removes is **not** remembered, so if you delete a mute term
later those papers can come back. Every digest says how many were hidden and
why, so filters never eat things silently.

### Broad terms, and the cap

`max_per_set` limits how many papers each set fetches per source. When a set
matches more than that, PaperFeed **tells you**:

```
! PubMed: 'IDH1 glioblastoma' matched 180 papers but only the 30 most
  recent were fetched. Raise max_per_set, or narrow the set.
```

That warning matters more than it looks. Without it, a broad term quietly
returns only the newest slice, and the specific papers you actually wanted
can be crowded out by whatever happened to be indexed most recently.

Two ways to handle a broad set:

- **Narrow it** with `all_of` or `exclude`.
- **Keep it broad, raise `max_per_set`, and set a per-set `min_score`:**

```json
{
  "name": "IDH1 glioblastoma",
  "terms": ["glioblastoma"],
  "min_score": 4
}
```

`min_score` on a keyword set overrides the global one. Since a title match is
worth 4, `"min_score": 4` means *the paper must be about this, not merely
mention it*. This has to be per-set: a threshold that tames a broad term
where the word is usually in the title would silence a set whose matches are
legitimately in abstracts.

### Following people

```json
{ "name": "Baker lab", "authors": ["Baker D"], "terms": [] }
```

A set can search purely by author. Papers by someone you follow get a scoring
bonus and the card says so.

One honest caveat: the search APIs are loose about initials, so `"Baker D"`
will also *return* papers by "Baker DA", a different researcher. PaperFeed
will not give those the follow bonus — matching requires the initials to line
up exactly — so they sink to the bottom rather than topping your digest.

## How papers are ordered

Papers are sorted by a relevance score out of 10, and **every card shows why
it scored what it did** ("'RFdiffusion' in title; 2 different terms matched").
A ranking you cannot interrogate is one you stop trusting.

| Signal | Points | Reasoning |
|---|---|---|
| Term in the **title** | +4 | The title is what a paper is *about* |
| Term in the **abstract** | +1 | A passing mention is weak evidence |
| Each extra distinct term | +1 | Breadth is real, but must not outrank a title hit |
| Published in the last 3 days | +1 | A tiebreaker only |
| By an author you follow | +3 | |

The weights sit in `config.json` under `ranking` if you want to tune them.
`"min_score": 3` hides weak matches; `"enabled": false` goes back to plain
date order.

Why a title match is worth more than two abstract mentions: in testing, a
paper with *"binder design"* in its title was being outranked by a review that
merely mentioned the phrase twice in passing. Recency is kept deliberately
small for the same reason — inside a 14-day window nearly everything is
recent, so a large recency bonus is noise rather than signal.

## Changing the schedule

`"interval_days": 3` in config.json. That is the only place the schedule lives.

The scheduler below wakes PaperFeed up once a day and PaperFeed decides whether
it is due, so changing 3 to 7 is a one-line edit — you never touch the
scheduler again.

`lookback_days` (14) is how far back each run searches. Keep it comfortably
larger than `interval_days` so nothing falls through a gap; papers you have
already been shown are filtered out anyway.

## Running it automatically

```bash
cp com.paperfeed.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.paperfeed.daily.plist
```

That runs PaperFeed at 08:00 every day. Most days it will exit within
milliseconds because the interval has not elapsed.

To check on it, or turn it off:

```bash
launchctl list | grep paperfeed
launchctl unload ~/Library/LaunchAgents/com.paperfeed.daily.plist
```

Note: a laptop that is asleep at 08:00 runs the job when it next wakes.

## Turning on email

1. Get an app password from your mail provider. For Gmail: Google Account →
   Security → 2-Step Verification → App passwords. Your normal password will
   not work.

2. Put it in your shell profile so it is available to every session:

   ```bash
   echo "export PAPERFEED_SMTP_PASSWORD='your-app-password'" >> ~/.zshrc
   source ~/.zshrc
   ```

3. In `config.json`, set `email.enabled` to `true` and fill in `smtp_host`,
   `username` and `from_address`.

4. `python3 paperfeed.py test-email`

PaperFeed never stores your password in a file. It reads the environment
variable named in `email.password_env` and nothing else.

**One catch with launchd**: background jobs do not read `~/.zshrc`, so the
scheduled run will not see the variable. The plist has a commented-out block
showing where to put it — uncomment it, paste the password there, then:

```bash
chmod 600 ~/Library/LaunchAgents/com.paperfeed.daily.plist
launchctl unload ~/Library/LaunchAgents/com.paperfeed.daily.plist
launchctl load ~/Library/LaunchAgents/com.paperfeed.daily.plist
```

If you would rather not have the password on disk at all, leave it out: the
scheduled run will write the digest and simply skip the email.

**A missing password never costs you a digest.** If email is enabled but the
variable is not set, PaperFeed logs a warning, skips the send, and writes the
file as usual. (This was once a fatal config error, which meant a scheduled
run aborted before writing anything — the exact failure the local file is
supposed to protect you from.)

By default PaperFeed only emails when there is something new. Set
`email.send_when_empty` to `true` if you would rather hear from it every time.

## What's in the folder

```
config.json           the only file you need to edit
config.example.json   the same file with every option explained
paperfeed.py          the command you run
config.py             reads and checks config.json
sources.py            talks to PubMed and Europe PMC
store.py              decides what counts as "already seen"
digest.py             builds the HTML
mailer.py             sends it
ai.py                 the optional Anthropic API features
library.py            your saved papers
server.py             the local Save-button page
state/seen.json       the memory (plain JSON, safe to read or delete)
library.db            papers you saved (SQLite, safe to read or delete)
digests/              one file per run, plus latest.html
paperfeed.log         what happened, every run
```

## How it decides a paper is new

Each paper is identified by its DOI, and also by a squashed version of its
title when that title is long enough to be distinctive. A paper counts as
already-seen if either key is known.

The title key is what catches a preprint that later appears in a journal: the
two have different DOIs but the same title, so you are not shown the same
work twice. Titles shorter than 40 characters once punctuation is stripped
(`Correction`, `Editorial Board`) are not used as keys, so unrelated papers do
not get collapsed together.

`state/seen.json` is plain JSON. Delete it and the next run treats everything
in the lookback window as new — occasionally useful, e.g. after a big change
to your keywords.

## The optional AI features

Two features use the Anthropic API. **Both are off by default, and PaperFeed
works completely without them.** They cost money; nothing else here does.

```bash
python3 paperfeed.py check --costs
```

That prints an estimate before you commit to anything. At the default
settings it is roughly **$3-4 a year** — about 2p per digest for scoring, and
around 10p for each quarterly trend report. Every run logs what it actually
spent, so you are never guessing.

### Setting your key

```bash
python3 paperfeed.py set-key
```

It asks you to paste your key (hidden as you type), **checks it against the
API before storing it**, and puts it in your macOS Keychain. A wrong key
fails right there in front of you rather than silently at 08:00 three days
later. The key never goes into `config.json` or any file in this project.
`--forget` removes it.

### Relevance scoring

```json
"ai": {
  "enabled": true,
  "interests": "I design de novo protein binders. I care about experimental validation and hit rates, not new architectures for their own sake."
}
```

Each paper gets a 0-10 score against that description plus one line on why it
matters *to you*, shown in purple on the card next to the local score. Both
are kept: the AI score orders the list, the local score explains the match.

The more specific your `interests`, the better the triage. "Protein design"
will not help it much; the sentence above will.

`max_papers_per_run` (40) caps what gets sent, so cost stays predictable
regardless of how many papers a run finds.

### The trend report

```json
"trends": { "enabled": true, "months": 3, "interval_days": 30 }
```

Every 30 days, PaperFeed re-queries the last three months across all your
keyword sets and writes `digests/trends-YYYY-MM.html`: the three to six themes
that actually characterise the period, each with representative papers, plus
what seems to be quietening down.

It stores nothing extra — it re-fetches each time, which is what keeps it
consistent with only keeping papers you chose. Your existing daily scheduler
triggers it; there is no second cron job to set up.

**On trusting it:** every paper it cites is checked against the DOIs actually
fetched. A citation it cannot trace back to a real paper is dropped before
you see it. It reads titles and abstracts only, never full text.

### If the AI is unavailable

No key, a rejected key, a dead network, a garbled reply — all of them fall
back to the local ranking, note what happened at the top of the digest, and
**still write the file**. This is tested: the digest is written before the API
is ever called.

## When something goes wrong

- **A source is down.** The other one still runs, and the digest carries an
  orange banner naming what failed, so a short list is never mistaken for a
  quiet fortnight. Requests are retried three times before giving up.
- **Email fails.** The digest file was already written before email was
  attempted. The failure is logged and the run reports it.
- **`config.json` is broken JSON.** The error names the line and column and
  the usual culprits (a trailing comma, a missing comma, an unclosed quote).
- **Nothing in the digest.** Check `paperfeed.log`, then try
  `python3 paperfeed.py run --dry-run --set "your set"` to see the raw hits.

## Adding another source later

Write one function in `sources.py` that takes `(keyword_set, lookback_days,
max_results, contact_email)` and returns a list of `Paper` objects, then add it
to the `SOURCES` dictionary. Deduplication, memory, digests and email all work
on `Paper` objects and need no changes.
