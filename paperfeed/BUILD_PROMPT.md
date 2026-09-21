# Build prompt

A plain starting prompt for a coding agent. Deliberately short: it describes
the tool you want on day one, and you add the rest through conversation as
you see what you actually need.

---

We're building a tool called **PaperFeed**. I'm a researcher, not a strong
coder: explain choices in plain language and keep the design as simple as it
can be while still working.

## What PaperFeed is

A literature alert tool: I give it keywords, it finds recent matching papers,
and emails me the new ones on a schedule.

## Features to plan

- Keyword sets I can easily add / edit / remove.
- Fetch recent papers, covering PubMed and preprints. Deduplicate across
  sources by DOI.
- "New since last time": remember what it has already shown me, so each paper
  appears once, across runs.
- Runs on a schedule: every 3 days by default, but I can change the interval
  myself.
- Email me a readable digest.
- ALWAYS write a local digest file too (HTML or markdown), so if email fails I
  still see the results. Treat that file as the thing we check to verify it
  worked.
- Let me search a date range I choose, not only the last few days, so I can
  look back over a period and analyse what was published.

## Hard constraints

- Python, standard library + `requests` only.
- Config-driven: all settings I might change — keywords, the schedule
  interval, and email settings — live in a config file I can edit, not
  hardcoded, so any researcher could reuse this by editing config.

## How to work

Give me a plan first and wait for my approval before writing any code.
Verify by running it and looking at the output, not by reading the code back
to me. When something is uncertain, say so rather than guessing.

---

## Things to add later, once the above works

Raise these one at a time, in conversation, so each gets thought about
properly:

- Relevance ranking, with the reasons shown on each paper.
- Filters: exclude terms, muted journals, following specific authors.
- A local web page for browsing the digest and saving papers worth keeping.
- A dashboard: score distribution, volume over time, subject frequency.
- Optional AI features, off by default, for summarising and triage.
