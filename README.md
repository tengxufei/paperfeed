# paperfeed-state

PaperFeed's memory when it runs on GitHub. Not code - never merge into main.

- `state/seen.json` - every paper already sent, and when the last digest went out
- `state/subjects.json`, `state/topics.json` - history behind "rising in your field"
- `digests/` - every digest and dashboard page so far; this is what the website shows

The workflow on `main` reads these before each run and commits them back after.
Seeded on 2026-09-24 from the laptop's copy, so GitHub carried on where the
laptop left off instead of re-sending the last 14 days.

Deleting this branch makes the next run a first run: everything from the last
`lookback_days` counts as new and arrives in one large email.
