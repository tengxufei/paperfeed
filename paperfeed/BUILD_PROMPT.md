# Build prompt: a literature alert tool

Copy everything below the line and give it to a coding agent.

---

Build me a literature alert tool called **PaperFeed**. I'm a researcher, not a
strong coder: explain your choices in plain language and keep the design as
simple as it can be while still working.

## What it does

I give it keywords. It searches for recent papers, shows me only ones I
haven't seen, ranks them by relevance, keeps the good ones, and charts what
my field is doing over time. It runs on a schedule and emails me a digest.

## Hard constraints

- **Python, standard library plus `requests` only.** No other dependencies —
  no chart library, no web framework, no ORM, no AI SDK.
- **Config-driven.** Every setting I might change — keywords, schedule,
  filters, email, AI — lives in one config file I can edit. Nothing
  hardcoded, so another researcher could reuse this by editing config alone.
- **Always write a local digest file**, before attempting email or anything
  else that can fail. That file is the deliverable; email is a convenience.
- **Secrets never live in the project.** Passwords and API keys come from
  environment variables, named in config.

## Build it in phases, and commit each one separately

Make a git baseline commit of nothing-but-scaffolding first, so any phase can
be rolled back independently.

### Phase 1 — it works

- Fetch from **PubMed** (NCBI E-utilities: `esearch` then `efetch`, XML) and
  **preprints** (Europe PMC REST, `SRC:PPR` — one keyword-searchable endpoint
  covering bioRxiv, medRxiv, Research Square and others). Both are free and
  need no account.
- Deduplicate by DOI, falling back to a normalised title when the DOI is
  missing.
- Remember what has been shown, so each paper appears exactly once ever.
- Write an HTML digest. Run on a config-driven interval via `launchd`/cron.
- Email it, optionally.

### Phase 2 — signal

- An **explainable** 0–10 relevance score. Every card must show the reasons
  behind its number. A ranking you cannot interrogate is one you stop
  trusting.
- Filters: per-set exclude terms, global mutes, journal allow/deny lists.
- A digest that is readable at 200 papers: one collapsible section per
  keyword set showing its top 5, the rest behind a second fold, a sticky
  jump bar, and a compact one-line mode.

### Phase 3 — a collection and a dashboard

- A local web UI (`http.server`, bound to `127.0.0.1` only) that re-serves the
  digest already on disk with a **Save** button per paper, writing to SQLite.
- A collector that also saves papers above a score threshold automatically,
  recording whether each arrived by a click or by itself.
- **Dashboards, one per keyword set plus an overall one.** Charts as inline
  SVG generated in Python — no chart library, no CDN, because the file must
  work offline. Show: score distribution, volume over time, most common
  subjects, a subject co-occurrence graph, and trend alerts comparing each
  subject against its own past.

### Phase 4 — optional AI

Off by default; everything above must work with no key. Support more than one
provider (Anthropic Messages API, OpenAI chat-completions, Google Gemini) via
a config setting, talking to them over plain HTTP with `requests`.

Use it for: explaining a saved paper, suggesting research directions from the
whole library, answering a question about my own work from my saved papers,
and a periodic trend briefing.

## Traps that cost real time — handle these from the start

These are all bugs that actually happened building this. Each one was silent.

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

## How I want you to work

- **Plan first and wait for my approval** before writing code.
- **Verify by running it, not by reading it.** Open the pages in a browser
  and look at them. Several of the bugs above are invisible in the source and
  obvious on screen.
- When you find a bug, **tell me what it was and how you proved it is fixed**,
  with the actual output.
- If something is uncertain or you had to guess, say so. Do not quote API
  prices or model identifiers from memory — check them.
