# PaperFeed 1.0 — manual

A literature alert tool. You give it keywords; it searches PubMed and the
preprint servers on a schedule, ranks what it finds, writes you a digest,
keeps the good ones, and charts what your fields are doing over time.

Python standard library plus `requests`. No account, no database server, no
build step. Everything it produces is a plain file on your Mac.

**First run:** `cp config.example.json config.json`, then edit your keywords.
Your own `config.json` is deliberately not tracked in git, so your settings
and email address stay local.

---

## 1. The shape of it

```
you edit           config.json          keywords, schedule, filters, email, AI
                        |
   run  ──────────────► sources.py      ask PubMed + Europe PMC
                        relevance.py    drop noise, score what is left
                        store.py        forget anything shown before
                        |
                        ├─► digests/latest.html      the digest
                        ├─► digests/dashboard*.html  charts, one per keyword set
                        ├─► library.db               papers worth keeping
                        └─► email                    optional
```

One command does all of it:

```bash
python3 paperfeed.py run
```

A `launchd` job runs that once a day. PaperFeed itself decides whether enough
days have passed, so **the schedule lives in `config.json`, not in the
scheduler** — change `interval_days` and you never touch launchd again.

### The modules

| File | Job |
|---|---|
| `paperfeed.py` | the command you run; orchestrates everything |
| `config.py` | reads `config.json`, explains mistakes in plain English |
| `sources.py` | talks to PubMed and Europe PMC; one function per source |
| `relevance.py` | filtering and the explainable 0–10 score |
| `store.py` | what has been shown before, so nothing repeats |
| `digest.py` | renders the digest, the email, the trend report |
| `library.py` | the papers you keep (SQLite) |
| `server.py` | the local web pages: digest, library, dashboards |
| `stats.py` | per-run numbers and subject history |
| `charts.py` | SVG charts, drawn in Python |
| `dashboard.py` | assembles the dashboard pages |
| `ai.py` | the optional AI features, across providers |
| `mailer.py` | sends the email |
| `phrasing.py` | "1 paper" not "1 papers" |

---

## 2. Commands

| Command | What it does |
|---|---|
| `python3 paperfeed.py run` | The normal run. Exits quietly if the interval has not elapsed. |
| `python3 paperfeed.py run --force` | Run now regardless. |
| `python3 paperfeed.py run --dry-run` | Show what it would find. Writes nothing, remembers nothing. |
| `python3 paperfeed.py run --set "name"` | Only one keyword set. |
| `python3 paperfeed.py check` | Validate the config. Also lists settings you have not written into your file. |
| `python3 paperfeed.py check --costs` | Add an estimate of what the AI features would cost. |
| `python3 paperfeed.py status` | Last run, next run, library size. |
| `python3 paperfeed.py serve` | Open the digest, library and dashboards in your browser. |
| `python3 paperfeed.py saved` | List the papers you have kept. |
| `python3 paperfeed.py search "text"` | Search your library. |
| `python3 paperfeed.py search "text" --online --since 2025-01-01` | Search the sources live. |
| `python3 paperfeed.py retro --from 2024-01-01 --to 2024-12-31` | Search a date range you choose and analyse it. |
| `python3 paperfeed.py trends` | Write a themed briefing on the last few months. Needs an API key. |
| `python3 paperfeed.py dashboard` | Print the path to the dashboard. |
| `python3 paperfeed.py test-email` | Check the email settings, then send one test. |

---

## 3. What a run does, in order

1. **Is it due?** If fewer than `interval_days` have passed, stop. Costs nothing.
2. **Fetch.** Each enabled keyword set queries each enabled source.
3. **Filter.** Drop anything matching your `exclude`, `mute` or journal rules.
4. **Deduplicate.** By DOI, and by a squashed title when the DOI is missing.
5. **Forget the familiar.** Anything shown in an earlier digest is removed.
6. **Score.** 0–10, with the reasons recorded.
7. **Write the digest.** Always, before anything else can fail.
8. **Collect.** Save the strong ones to your library, if the collector is on.
9. **Dashboards.** One per keyword set, plus an overall.
10. **Email.** If configured. A failure here is logged, never fatal.

The order in steps 7–10 is deliberate: **the digest file is on disk before
email or AI is attempted**, so nothing downstream can cost you the results.

---

## 4. Configuration

Everything lives in `config.json`. `python3 paperfeed.py check` validates it
and names anything wrong, including which line if the JSON itself is broken.

### Keyword sets

```json
{
  "name": "de novo protein design",
  "terms": ["de novo protein design", "RFdiffusion"],
  "all_of": ["protein"],
  "authors": ["Baker D"],
  "exclude": ["cathode"],
  "journals": { "allow": [], "deny": [] },
  "fields": "title_abstract",
  "min_score": 4,
  "enabled": true
}
```

- **`terms`** — OR'ed. Searched as exact phrases in title and abstract.
- **`all_of`** — every one must *also* appear.
- **`authors`** — follow people. Written as PubMed writes them: `Baker D`.
- **`exclude`** — drop papers mentioning these, for this set only.
- **`journals.deny` / `.allow`** — never / only these.
- **`fields`** — `"title_abstract"` (default) or `"all"`.
- **`min_score`** — hide weak matches, for this set only.

Nothing a filter removes is remembered, so relaxing a filter later brings
those papers back. Every digest says how many were hidden and why.

### How papers are scored

| Signal | Points |
|---|---|
| Term in the **title** | +4 |
| Term in the **abstract** | +1 |
| Each extra distinct term | +1 |
| Published in the last 3 days | +1 |
| By an author you follow | +3 |

A title match is worth more than two abstract mentions on purpose: the title
is what a paper is *about*. Recency is deliberately small — inside a 14-day
window almost everything is recent, so a large bonus would be noise.

Every card shows the reasons behind its score. Weights live under `ranking`.

### Schedule and volume

| Setting | Meaning |
|---|---|
| `interval_days` | Minimum days between digests. |
| `lookback_days` | How far back each run searches. Keep it larger than `interval_days`. |
| `max_per_set` | Cap per keyword set per source. If a set matches more, the digest **tells you** rather than silently dropping the rest. |

### The collector

```json
"collect": { "enabled": true, "min_score": 6.0, "max_per_run": 25 }
```

Saves the strong papers automatically each run. Auto-saved papers are marked
`auto`, ones you clicked are marked `you`. Use the score histogram on the
dashboard to choose the threshold.

---

## 5. The pages

`python3 paperfeed.py serve` opens `http://127.0.0.1:8931`. It is bound to
your Mac only — not reachable from your network, which is why it needs no
login. Ctrl-C stops it. If the port is busy it steps to the next free one.

### The digest

Each keyword set is a collapsible section showing its **top 5**, with the
rest behind a second fold. A sticky bar at the top jumps between topics, and
**compact list** collapses every card to one line. Papers with free full text
are badged and linked. Each card carries a **+ Save** button.

Printing gives a clean page: navigation drops out, abstracts expand, and each
link's URL is printed after its title.

### The library

Your kept papers. Set each to **unread / reading / read** and filter by it.
With an API key, each paper gets an **Explain this** button, and two
library-wide tools: **suggest research directions** and **ask about a problem
in your own work**.

### The dashboards

**No AI is involved and no key is needed.** Every number is arithmetic and
every chart is SVG drawn by Python. One dashboard per keyword set plus an
overall, switchable at the top, because a topic heating up is invisible in a
combined count.

Each shows: tiles, source split, **score distribution** (this is how you pick
`min_score` from evidence), **volume over time**, **trend alerts** — subjects
behaving differently from their own past, flagged *new*, *rising*, *fading* —
a **subject graph** linking terms that share papers, the commonest subjects,
and your library's status.

Trend alerts need a few runs of history before they mean anything.

---

## 5b. Searching a date range

The feed answers *"what is new to me?"*. This answers *"what was published
between these dates?"* — a different question, and it searches a different
date field: the journal's publication date rather than the date the record
reached PubMed.

```bash
python3 paperfeed.py retro --from 2024-01-01 --to 2024-12-31
python3 paperfeed.py retro --from 2024-01-01 --to 2024-06-30 --set "IDH1 glioblastoma"
python3 paperfeed.py retro --from 2024-01-01 --to 2024-12-31 --compare 2023-01-01:2023-12-31
```

It writes `digests/retro-<from>_<to>.html`: a timeline of papers per month,
the most published authors, where the work came from, the subjects and how
they connect, and the papers themselves with Save buttons.

**Two things it will never do.** It does not touch `state/seen.json`, so
looking back at 2024 cannot make your next digest skip those papers. It does
not touch `state/topics.json` either, so historical volume cannot corrupt the
baselines that decide what is "rising" on the live dashboard.

**It pages properly.** PubMed cannot return more than 10,000 results for one
query, so the range is sliced into months and each month is paged through.
Nothing is silently truncated.

**It caches.** Results are stored per keyword set, per source, per month
under `cache/`. Re-running the same range is instant and works offline, and
an overlapping range only fetches the months it does not already have.
`--refresh` refetches; `--clear-cache` empties it. The cache is a working
file, not your library: deleting it costs only fetch time.

**One honest wrinkle.** PubMed's publication-date search and the journal's
own issue date do not always agree — ahead-of-print articles get assigned to
a later issue. So a search for January will return a few papers whose issue
date reads March. The timeline holds to the range you asked for and reports
how many fall outside it, rather than stretching the chart to cover one
outlier.

Papers found this way enter your library **only if you click Save**. The
auto-collector is deliberately not applied, so one historical sweep cannot
flood a library meant to record your ongoing reading.

---

## 6. Email

```json
"email": {
  "enabled": true,
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 465,
  "username": "you@gmail.com",
  "from_address": "you@gmail.com",
  "to_addresses": ["you@gmail.com"],
  "password_env": "PAPERFEED_SMTP_PASSWORD"
}
```

The password is **never** in this file. It lives in your shell:

```bash
echo "export PAPERFEED_SMTP_PASSWORD='your-app-password'" >> ~/.zshrc
```

Gmail needs an **app password**, not your account password, and 2-Step
Verification must be on. Google shows it as four groups of four; the spaces
are stripped automatically.

`test-email` checks the settings before sending and reports what it can
without ever revealing the password.

Two things that bite:

- **`from_address` must match `username`.** Gmail will not let you send as an
  address you are not logged in as.
- **Scheduled runs do not read `~/.zshrc`.** For launchd to send email, put
  the password in the plist's `EnvironmentVariables` block. Without it the
  digest is still written, the email is just skipped.

---

## 7. The AI features (optional, off by default)

Four things use a model: **Explain this**, **suggest research directions**,
**ask about a problem**, and the **trend report**. Everything else — digests,
scoring, filters, charts, dashboards — works with no key at all.

```json
"ai": {
  "enabled": true,
  "provider": "gemini",
  "base_url": "",
  "model": "gemini-2.5-flash",
  "api_key_env": "PAPERFEED_AI_KEY",
  "interests": "what you actually care about, in a sentence or two"
}
```

The key comes from the environment, exactly like the email password:

```bash
echo "export PAPERFEED_AI_KEY='your-api-key'" >> ~/.zshrc
```

| `provider` | `base_url` | example `model` |
|---|---|---|
| `anthropic` | blank | `claude-haiku-4-5` |
| `gemini` | blank | `gemini-2.5-flash` |
| `openai` | blank | `gpt-4o-mini` |
| `openai` | `https://api.groq.com/openai/v1` | Groq's models |
| `openai` | `http://localhost:11434/v1` | a local Ollama model |

`check --costs` estimates the spend before you switch anything on. Every run
logs the tokens it actually used. A model with no published price reports
cost as unknown rather than guessing.

**If the AI is unavailable** — no key, wrong key, no network, a garbled
reply — the run falls back to local ranking, notes what happened at the top
of the digest, and still writes the file.

---

## 8. Files it creates

| Path | What |
|---|---|
| `digests/latest.html` | the most recent digest |
| `digests/YYYY-MM-DD-HHMM.html` | one per run, kept |
| `digests/index.html` | every past digest |
| `digests/dashboard*.html` | the dashboards |
| `digests/trends-YYYY-MM.html` | trend briefings |
| `library.db` | papers you kept (SQLite, safe to open or delete) |
| `state/seen.json` | what has been shown before |
| `state/topics.json` | subject counts per run, for trend alerts |
| `paperfeed.log` | what happened, every run |

Delete `state/seen.json` and the next run treats everything in the lookback
window as new. Delete `library.db` and you lose your saved papers.

---

## 9. When something goes wrong

| Symptom | Cause |
|---|---|
| Digest has no papers from a new keyword set | The digest on disk predates your edit. `run --force`. `serve` warns about this in orange. |
| "matched N papers but only the M most recent were fetched" | Raise `max_per_set`, or narrow the set. |
| Email rejected the login | Not an app password, or `from_address` differs from `username`. `test-email` says which. |
| AI says the key was rejected | Wrong key, or a key for a different provider than `ai.provider`. |
| A source did not respond | Reported in an orange banner on the digest. The other source still ran. |
| Everything looks stale | `serve` shows the file on disk. It does not search live. |

`paperfeed.log` records every run. `check` validates the config and lists
settings you have not written into your file.

---

## 10. Hard-won details

Each of these was a real bug in this tool, and every one was silent. They
are recorded here because anyone changing the relevant code will meet them
again.

1. **PubMed's XML contains the reference list.** `.//ArticleId` matches the
   article's identifiers *and* every cited paper's — 190 elements on one
   record, 187 of them references. Taking the last of each type gives the
   article a random cited paper's DOI. Scope id and author lookups to
   `PubmedData/ArticleIdList` and `MedlineCitation/Article/AuthorList`.
   Because DOI is the paper's identity, getting this wrong also corrupts
   deduplication.

2. **Silent truncation.** If a keyword matches more papers than your
   per-query cap, you fetch the newest N and lose the rest with no
   indication — so the specific papers wanted get crowded out. Both APIs
   return a total count: compare it against what you fetched and say so.

3. **Author name matching.** `"Baker D"` substring-matches `"Baker DA"`, a
   different researcher. Require the initials to line up on a whole-token
   boundary.

4. **A single global relevance threshold cannot serve several topics.** A
   cut-off that tames a broad term (where the word is usually in the title)
   silences a set whose matches are legitimately in abstracts. Make the
   threshold settable per keyword set.

5. **Case-variant subjects.** MeSH headings are Title Case, author keywords
   are not, so `Glioblastoma` and `glioblastoma` become two nodes on a
   subject graph. Merge case-insensitively, keeping the commonest spelling.

6. **Do not set `display` on a `<summary>` element.** In WebKit it stops
   working as a disclosure control and the section silently will not toggle.
   Style an inner wrapper.

7. **Escaping, twice.** If you generate source files with a script, embedded
   CSS and JS pass through Python's escape rules twice. `"\25b8"` in a Python
   string is an octal escape, not a CSS one; a `\\n` in a regex became a real
   newline and split the literal across two lines, which is a parse error
   that kills *every* handler on the page. Use raw strings for embedded JS
   and CSS, and prefer literal characters over escapes.

8. **Route matching with `startswith`.** `"/dashboard-topic.html".startswith("/dashboard")`
   is true, so a prefix route swallows every sibling page. Match exactly.

9. **A missing email password must not be fatal.** Scheduled jobs do not read
   your shell profile. Treating it as a config error means the scheduled run
   aborts before writing anything — exactly the failure the local file exists
   to prevent. Warn, skip the email, write the file.

10. **Provider error codes differ.** Google answers an invalid API key with
    HTTP 400, not 401. Read the error body, not just the status.

11. **Pluralisation.** "published 1 days ago" makes a tool feel unfinished.
    One helper, used everywhere.

