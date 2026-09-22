# PaperFeed

Literature alerts and analysis over PubMed + preprints. Python stdlib +
`requests` only. Everything it produces is a plain file.

## Commands

```bash
cd paperfeed
python3 paperfeed.py check            # validate config, list unset settings
python3 paperfeed.py run --dry-run    # fetch and report, write nothing
python3 paperfeed.py run --force      # full run: digest, collector, dashboards
python3 paperfeed.py serve            # digest + library + dashboards at :8931
python3 paperfeed.py retro --from D --to D   # date-range search
```

There is no test suite. **Verify by running the tool and opening the page**,
not by reading the code back.

## Where things live

`paperfeed.py` CLI · `config.py` settings + friendly errors · `query.py`
boolean queries → both engines · `sources.py` PubMed/Europe PMC ·
`relevance.py` filters + score · `metrics.py` OpenAlex figures ·
`store.py` seen-memory · `digest.py` web HTML + the shared page shell ·
`email_digest.py` the alert email · `collection.py` what has accumulated ·
`library.py` saved papers ·
`server.py` local pages · `stats.py` numbers · `charts.py` SVG ·
`dashboard.py` pages · `retro.py` date-range · `ai.py` optional AI ·
`phrasing.py` plurals.

## Rules that are load-bearing

- **The digest file is written before email or AI is attempted.** Nothing
  downstream may cost the user their results.
- **AI is optional.** Every feature except explain/directions/troubleshoot/
  trends must work with no API key.
- **Secrets come from environment variables** named in config
  (`PAPERFEED_AI_KEY`, `PAPERFEED_SMTP_PASSWORD`). Never write one to a file.
- **A filter must say what it hid.** Silent truncation and silent filtering
  are bugs, not features.
- **`config.json` is not tracked.** Edit `config.example.json` for anything
  a new user should see.
- **Every displayed metric names its source.** No scraping, and no shipped
  Impact Factor table — the JIF is Clarivate's and paywalled, SJR has no
  API. Licensed numbers come only from a file the user points at.
- **A query is validated before it is sent.** Neither PubMed nor Europe PMC
  rejects a broken query; both answer HTTP 200 with the wrong papers.
- **The email is not the web page with inlined styles.** Mail clients strip
  `<style>`, ignore flex and grid, drop SVG and refuse `<details>`, and Gmail
  clips at 102,400 bytes. `email_digest.py` renders it in tables, with a
  size budget that trims and says so.

## Traps

Read `paperfeed/MANUAL.md` §10 before touching `sources.py` (PubMed reference
lists corrupt paper identity), `server.py` routes (prefix matching swallows
sibling pages), or embedded CSS/JS (double escaping silently kills handlers).

Three more, all measured against the live APIs:

- Europe PMC answers about one call in twelve with `{"version": "6.9"}` and
  nothing else — HTTP 200, no results. `_get(..., require="resultList")`
  catches it; without that a whole source vanishes from a run in silence.
- PubMed ignores what it cannot parse and returns a different search. It
  records the fact in `warninglist` / `errorlist`; read them.
- OpenAlex anonymous access is metered (1000 requests per ~11.5h, one credit
  per request regardless of batch size). Batch by 50, cache, and read
  `X-RateLimit-Remaining`.
