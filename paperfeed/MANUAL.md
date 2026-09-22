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
| `query.py` | reads a boolean query and translates it to both engines |
| `sources.py` | talks to PubMed and Europe PMC; one function per source |
| `relevance.py` | filtering and the explainable 0–10 score |
| `metrics.py` | citation and journal figures from OpenAlex |
| `email_digest.py` | the alert email — a separate render, not the web page |
| `collection.py` | what has accumulated: the library, the cache, the history |
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
| `python3 paperfeed.py check --explain` | Show how each keyword set reaches PubMed and Europe PMC, and how many it matches on each. |
| `python3 paperfeed.py query --cheatsheet` | Print the query syntax guide. |
| `python3 paperfeed.py query "..."` | Try a query out: read it back, show both translations, count the hits. |
| `python3 paperfeed.py migrate-queries` | Convert old `terms` lists into queries. Dry run until `--write`. |
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
  "name": "IDH1 in glioma",
  "query": "(IDH1 OR \"isocitrate dehydrogenase 1\") AND (glioma OR glioblastoma)",
  "require_title_groups": 1,
  "exclude": ["cathode"],
  "article_types": { "exclude": ["Review"], "only": [] },
  "journals": { "allow": [], "deny": [] },
  "fields": "title_abstract",
  "min_score": 4,
  "enabled": true
}
```

- **`query`** — what this topic is, as a boolean expression. See below.
- **`require_title_groups`** — how many of your bracketed concepts must be
  in the **title**, not merely mentioned. 0 by default.
- **`exclude`** — drop papers mentioning these, for this set only.
- **`article_types`** — filter on PubMed's own labels: `Review`, `Comment`,
  `Editorial`, `Letter`, `Case Reports`, `Clinical Trial`, `Meta-Analysis`.
- **`journals.deny` / `.allow`** — never / only these.
- **`fields`** — `"title_abstract"` (default) or `"all"`.
- **`min_score`** — hide weak matches, for this set only.

The older `terms` / `all_of` / `authors` lists still work. Convert them with
`python3 paperfeed.py migrate-queries`, which is a dry run until you add
`--write` and prints before/after hit counts on both sources so no
conversion changes what a set matches without telling you.

Nothing a filter removes is remembered, so relaxing a filter later brings
those papers back. Every digest says how many were hidden and why.

### Writing a query

```
  IDH1                       one word, looked for in title and abstract
  "isocitrate dehydrogenase" words in quotes are an exact phrase
  A OR B   A AND B   NOT A   operators, always in capitals
  ( ... )                    brackets group things together

  title:IDH1                 title only
  abstract:"survival"        abstract only
  author:"Baker D"           surname then initials, as PubMed writes them
  journal:"Neuro-Oncology"   journal or preprint server
  mesh:"Glioma"              PubMed's curated subject index
  all:CRISPR                 everywhere, including full text
```

`python3 paperfeed.py query --cheatsheet` prints this, and
`python3 paperfeed.py query "..."` tries one out — reading it back as an
outline, showing what each engine will be sent, and counting the hits —
before you commit it to `config.json`.

The shape that works is **synonyms grouped, groups AND'ed**:

```
(concept one, written every way) AND (concept two, written every way)
```

Two things the engines will not tell you, so PaperFeed does:

- **PubMed has no abstract-only field.** `abstract:` widens to title+abstract
  there, and the digest says so. Europe PMC can do it properly.
- **MeSH is assigned after indexing.** Of the thirty newest papers on a
  typical IDH1 query, twenty-two had no MeSH headings at all, and preprints
  never have any. So put `mesh:` in an *OR* beside plain words, never on its
  own. A query that needs MeSH in every branch cannot match a preprint, and
  the preprint search is skipped with that reason printed.

A broken query is never rejected by either engine — both answer HTTP 200
and search for something else. PaperFeed refuses it first, and marks the
spot.

### How papers are scored

Scoring works per **concept** — each bracketed group in your query — not per
word.

| Signal | Points |
|---|---|
| A concept present in the **title** | +4 |
| A concept present as a **MeSH heading** | +2 |
| A concept present in the **abstract** | +1 |
| Published in the last 3 days | +1 |
| By an author you follow | +3 |

A concept can only be satisfied once, however many ways you wrote it. This
is the point: `(glioma OR glioblastoma OR astrocytoma)` is one idea, and a
review that says all three used to out-score a paper whose title was about
IDH1. Measured on the live set — synonym-stuffed review **1.0**, real IDH1
paper **8.0**.

A title match beats any number of abstract mentions, on purpose: the title
is what a paper is *about*. Recency is deliberately small — inside a 14-day
window almost everything is recent, so a large bonus would be noise.
**Citation counts never enter this score.**

Every card shows the reasons behind its score. Weights live under `ranking`.

### Paper and journal figures

Under `metrics`. On by default, free, and needing no key.

| Shown | From | Notes |
|---|---|---|
| citation count | OpenAlex | withheld under 90 days old |
| × field average (FWCI) | OpenAlex | 1.0 = typical for that field and year |
| open access + PDF link | OpenAlex, Europe PMC | |
| retracted | OpenAlex and PubMed | both are checked |
| review / comment / case report | PubMed | already in the XML we fetch |
| journal 2-yr mean citedness, h-index | OpenAlex | see the warning below |
| in DOAJ | OpenAlex | |

**No Impact Factor is fetched or shipped.** The JIF is Clarivate's,
published in Journal Citation Reports behind a paywall, and redistributing
it is not permitted. SJR is no better: Scimago publishes no API and its site
refuses automated requests. If you have licensed access to either, point
`metrics.journal_table` at your own CSV — it is read from your disk, never
fetched, and never sent anywhere:

```json
"journal_table": {
  "path": "~/jcr_2025.csv",
  "issn_column": "ISSN",
  "value_columns": { "JIF": "2024 JIF", "Quartile": "JIF Quartile" },
  "label": "JCR 2024 (my licensed copy)"
}
```

**Read the journal number carefully.** OpenAlex's 2-year mean citedness
counts *every item* a journal publishes — meeting abstracts, errata,
editorials. Journals with large abstract supplements therefore read far
below their reputation:

| | citedness | indexed works |
|---|---|---|
| Cell | 40.8 | 26,944 |
| Nature | 18.9 | 448,700 |
| PNAS | 8.6 | 172,928 |
| Neuro-Oncology | **1.1** | 30,090 |
| Cancer Research | **0.6** | 159,128 |

Neuro-Oncology is not a weak journal; it prints thousands of conference
abstracts nobody cites. This is why the works count is always beside the
number, why it is never called an Impact Factor, and why nothing in
PaperFeed is ever sorted by it. Treat all of these as triage, not verdicts.

A paper under `new_paper_days` (90) shows *"too new to have citations"*
rather than a zero. A zero would read as a judgement when there has simply
been no opportunity.

Anonymous OpenAlex use is metered: 1000 requests per about 11.5 hours, one
credit per request however many papers it asks about. PaperFeed batches 50
at a time and caches, so a normal run costs two or three. When the
allowance runs low it stops and says how far it got.

### What counts as "new"

PaperFeed does not diff against the previous digest. It keeps a memory file,
`state/seen.json`, holding a fingerprint per paper with the date it was first
shown to you. A paper is **new** if none of its fingerprints is in that file.

Each paper gets more than one fingerprint:

- its **DOI**, normalised for case, `https://doi.org/` prefixes and stray
  trailing dots
- a **squashed title** — lowercased, punctuation stripped — but only if it is
  at least 40 characters

That catches three things a digest-to-digest diff would miss: a paper you
were shown five runs ago, the same paper arriving from both sources, and a
**preprint later published in a journal** — different DOIs, same paper. The
40-character floor stops generic titles ("Correction", "Editorial Board")
collapsing unrelated work together. Fingerprints are forgotten after 400
days. Papers a filter removed are deliberately *not* remembered, so loosening
a filter brings them back.

**What the digest shows** is a wider thing than what is new. It shows every
paper from the last `digest_days` (3 by default), whether or not you have
seen it, plus anything older within `lookback_days` that you have never been
shown. Papers new to you carry a green **new** badge, and the header says
both numbers: *"12 papers from the last 3 days, 2 new to you"*.

The catch-up half matters: with a strict three-day window, being away for
five days would quietly cost you the papers from days four and five. The
window is a floor, not a ceiling.

**The email carries only the new ones**, so a paper never reaches your inbox
twice however often the job runs.

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

### The sidebar, on every page

The digest, the library and the dashboards share one left-hand rail:

- **Digest / Library / Dashboard / All digests**, with the current page
  marked. The Library entry needs `serve` to be running; opened straight off
  disk it dims itself and says so rather than being a dead link.
- **At a glance** — the numbers for that page.
- **Topics** — jump to a section on the digest, switch dashboards, see how
  your library splits.
- **Filter** — type to narrow the papers on the page, plus switches for
  *free full text only*, *hide reviews and comments*, and (on the digest)
  *hide papers with no citations yet*. These stack with the library's
  unread/reading/read pills, and the count underneath says how many of how
  many are showing. Nothing is refetched; it is the page you already have.
- **What the numbers mean** — the key below, always on screen.

On a narrow screen the rail becomes a drawer behind the ☰ button. It is a
checkbox, not a script, so it works with JavaScript off.

### The two numbers in front of a title

| | |
|---|---|
| **AI 9** | How well the paper matches what you wrote in `interests`, judged by whichever model you configured, 0–10. Only appears when AI scoring is on, and the tooltip names the actual model. |
| **8.0** | PaperFeed's own score out of 10, with the bar behind it filled to match. A concept in the title is worth 4, a MeSH heading 2, a mention in the abstract 1, an author you follow 3. |

Papers are ordered by the AI score where there is one and by PaperFeed's own
score otherwise, so a run that only sent the first 40 papers to the API still
sorts sensibly. The reasons behind the local score are printed under every
title, not just hidden in a tooltip — a tooltip cannot be reached on a phone.

### The digest

Each keyword set is a collapsible section showing its **top 5**, with the
rest behind a second fold. **compact list** in the rail collapses every card
to one line. Papers with free full text are badged and linked. Each card
carries a **+ Save** button.

Printing gives a clean page: navigation drops out, abstracts expand, and each
link's URL is printed after its title.

### The library

Your kept papers. Set each to **unread / reading / read** and filter by it.
With an API key, each paper gets an **Explain this** button, and two
library-wide tools: **suggest research directions** and **ask about a problem
in your own work**.

This is also where citation counts mean something: a digest is full of
papers published this week, all correctly reported as too new to be cited,
while a library holds papers old enough for a count — or a retraction flag —
to tell you something.

### The dashboards

**No AI is involved and no key is needed.** Every number is arithmetic and
every chart is SVG drawn by Python. One dashboard per keyword set plus an
overall, switchable at the top, because a topic heating up is invisible in a
combined count.

It is built from three things that **accumulate** — your library, the metric
cache beside it, and the run history — not from the last run alone. That
distinction is the whole point: on a short interval most runs find nothing
new, and a dashboard computed from one run reads zero all day while there are
hundreds of papers on disk.

It opens with a sentence, not a number:

> *23 papers joined your library in the last 30 days, 17 of them collected
> without you; 20 are still unread; the typical one was published 4 days
> before you saw it.*

Then the panels, each of which asks a question and answers it underneath:

| Panel | What it tells you |
|---|---|
| **Is the alert working?** | Papers found per run, quiet runs included. A feed that only showed its good days would be telling you nothing. |
| **Is the collection growing?** | The running total, split by who added each paper. This is the only place the collector's claim is actually checked. |
| **How quickly news reaches you** | Each paper's age when it landed. The number that says whether you are keeping up or catching up. |
| **Waiting for you** | The unread papers that have sat longest, linked. |
| **The most-cited papers you have kept** | Citations and field-weighted impact. Says so plainly when everything is too new to have any, rather than showing zeros. |
| **Where your collection comes from** | Your journals, with OpenAlex citedness and the works count that qualifies it. |
| **What is moving in your field** | Subjects flagged *new*, *rising*, *fading*, each with a strip showing it across recent runs. |
| **How your subjects connect** | Terms that share papers, linked. |
| **What your searches actually pulled in** | The commonest subjects — a surprise near the top means a wider query than you intended. |
| **Have you read any of it?** | Reading status, and which topic your collection is made of. |

Two more panels appear only when a run actually found something: where those
papers scored (this is how you pick `min_score` from evidence) and which
topic brought them in.

The subject panels are built from **everything your queries matched in the
lookback window**, not just what was new to you — what a field is about does
not change according to whether you happen to have seen a paper already.
Trend alerts still need a few runs of history before they mean anything.

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

The alert is the only part of PaperFeed that reaches you away from the Mac,
so it carries everything the tool knows about a paper, not just its title.

**Subject** is the count plus the best paper's title — on a phone the
subject is often all you see. **Preheader** (the grey line beside it in the
inbox) names which topics moved and how strong the best match is.

The body: a **Start here** card for the single best paper, set larger, with
the AI's one-line reason quoted and *Read the paper* / *Free full text*
buttons. Then a line on anything **rising in your field**. Then each topic:
up to five full cards — labelled `AI 9` and `8.0` chips, authors and
institution, the AI reason, badges (preprint, review, **retracted**, free
full text, DOAJ), and the citation and journal figures — followed by the
rest as one-line rows so nothing is invisible. A footer with your library
count closes it.

Without an API key there is no AI score or reason; the cards fall back to
the local score and its reasons and still read properly.

**Why it is a separate render from the web page.** Mail clients strip
`<style>` blocks, ignore flex and grid, drop SVG, and refuse `<details>`. So
`email_digest.py` writes every rule onto its element and lays out in tables,
and no chart or dashboard ever reaches the inbox. Gmail also clips a message
at 102,400 bytes and hides the rest behind "View entire message", cutting
mid-tag — so the email is budgeted to 90 KB, shortens itself if it must, and
says how many papers it left out. Eighty papers fit uncut.

Dark mode is honest rather than perfect: the message declares
`color-scheme: light dark` and puts an explicit background on every cell,
but Gmail on Android force-inverts and nothing prevents that. The aim is
still readable, not identical.


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

2. **Europe PMC sometimes answers with nothing at all.** About one call in
   twelve returns the body `{"version": "6.9"}` — HTTP 200, no `resultList`,
   no error. Measured: twelve identical requests, eleven returned fifteen
   preprints and one returned none. Read as a quiet week, that silently
   removes a whole source from a run. `_get(..., require="resultList")`
   treats the missing key as a failure and retries.

3. **Neither engine rejects a broken query.** All of these returned HTTP 200:
   an unbalanced bracket (2088 hits, bracket dropped), `IDH1[notafield]`
   (2254 hits, tag dropped), `ANDD` for `AND` (0 hits), and Europe PMC's
   `BOGUSFIELD:"glioma"` (0 hits). A wrong query returns wrong papers, never
   an error, which is why `query.py` validates before sending. PubMed does
   record what it ignored, in `warninglist` and `errorlist` — read them.
   `"glioblastoma multi-omics"` sat in this config for weeks matching
   nothing while PubMed reported it as `quotedphrasesnotfound` every run.

4. **PubMed rejects `sort=date`.** It says so in `warninglist.outputmessages`
   and sorts by its default instead. That default is PMID descending, which
   with `datetype=edat` is the order the feed wants anyway — so the request
   is simply not made. `sort=pub_date` would be wrong: it orders by journal
   publication date, a different question.

5. **A journal-level citation average is not an Impact Factor.** OpenAlex's
   2-year mean citedness counts every meeting abstract and erratum a journal
   prints. Neuro-Oncology reads 1.1 and Cancer Research 0.6. Always show the
   works count beside it, never sort by it, never call it an IF.

6. **Silent truncation.** If a keyword matches more papers than your
   per-query cap, you fetch the newest N and lose the rest with no
   indication — so the specific papers wanted get crowded out. Both APIs
   return a total count: compare it against what you fetched and say so.

7. **Author name matching.** `"Baker D"` substring-matches `"Baker DA"`, a
   different researcher. Require the initials to line up on a whole-token
   boundary.

8. **A single global relevance threshold cannot serve several topics.** A
   cut-off that tames a broad term (where the word is usually in the title)
   silences a set whose matches are legitimately in abstracts. Make the
   threshold settable per keyword set.

9. **Case-variant subjects.** MeSH headings are Title Case, author keywords
   are not, so `Glioblastoma` and `glioblastoma` become two nodes on a
   subject graph. Merge case-insensitively, keeping the commonest spelling.

10. **Do not set `display` on a `<summary>` element.** In WebKit it stops
   working as a disclosure control and the section silently will not toggle.
   Style an inner wrapper.

11. **Escaping, twice.** If you generate source files with a script, embedded
   CSS and JS pass through Python's escape rules twice. `"\25b8"` in a Python
   string is an octal escape, not a CSS one; a `\\n` in a regex became a real
   newline and split the literal across two lines, which is a parse error
   that kills *every* handler on the page. Use raw strings for embedded JS
   and CSS, and prefer literal characters over escapes.

12. **Route matching with `startswith`.** `"/dashboard-topic.html".startswith("/dashboard")`
   is true, so a prefix route swallows every sibling page. Match exactly.

13. **A missing email password must not be fatal.** Scheduled jobs do not read
   your shell profile. Treating it as a config error means the scheduled run
   aborts before writing anything — exactly the failure the local file exists
   to prevent. Warn, skip the email, write the file.

14. **Provider error codes differ.** Google answers an invalid API key with
    HTTP 400, not 401. Read the error body, not just the status.

15. **Pluralisation.** "published 1 days ago" makes a tool feel unfinished.
    One helper, used everywhere.

