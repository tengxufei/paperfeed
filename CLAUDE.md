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

`paperfeed.py` CLI · `config.py` settings + friendly errors · `sources.py`
PubMed/Europe PMC · `relevance.py` filters + score · `store.py` seen-memory ·
`digest.py` HTML · `library.py` saved papers · `server.py` local pages ·
`stats.py` numbers · `charts.py` SVG · `dashboard.py` pages · `retro.py`
date-range · `ai.py` optional AI · `phrasing.py` plurals.

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

## Traps

Read `paperfeed/MANUAL.md` §10 before touching `sources.py` (PubMed reference
lists corrupt paper identity), `server.py` routes (prefix matching swallows
sibling pages), or embedded CSS/JS (double escaping silently kills handlers).
