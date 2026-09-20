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
the answer.

## The commands

| Command | What it does |
|---|---|
| `python3 paperfeed.py run` | The normal run. Exits quietly if the interval has not elapsed. |
| `python3 paperfeed.py run --force` | Run now, ignoring the interval. |
| `python3 paperfeed.py run --dry-run` | Show what it would find. Writes nothing, remembers nothing. |
| `python3 paperfeed.py run --set "name"` | Only one keyword set. Good for testing a new one. |
| `python3 paperfeed.py status` | Last run, next run, how many papers it remembers. |
| `python3 paperfeed.py check` | Validate config.json and print it back in plain English. |
| `python3 paperfeed.py test-email` | Send one test message. |

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
showing where to put it — uncomment it and paste the password there, then
`chmod 600` the plist. If you would rather not have the password on disk at
all, leave email off for scheduled runs and read `digests/latest.html`.

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
state/seen.json       the memory (plain JSON, safe to read or delete)
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
