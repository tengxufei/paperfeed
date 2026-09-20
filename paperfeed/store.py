"""Remember which papers have already been shown, so each one appears once.

Identity is the whole problem here. A paper can reach us twice in one run
(matching two keyword sets, or indexed by both sources), and it can reach us
again weeks later. Worse, a preprint and its published version are the *same
paper* to a reader but carry *different DOIs*.

So each paper gets a small set of keys rather than a single one:

  - its DOI, when it has one
  - a squashed version of its title, when that title is long enough to be
    distinctive

A paper counts as already-seen if *any* of its keys is already known. That is
what catches the preprint-then-journal case. The length floor on titles stops
generic ones ("Correction", "Editorial Board") from collapsing unrelated work.
"""

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

# Fingerprints older than this are dropped so the file cannot grow forever.
FORGET_AFTER_DAYS = 400

# A squashed title shorter than this is too generic to use as an identity.
MIN_TITLE_KEY_CHARS = 40


def _normalize_doi(value):
    """Lowercase, and strip whatever URL wrapper the source put in front.

    Done here rather than only in the fetchers, so two records mean the same
    paper no matter which code path built them.
    """
    doi = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi.strip().rstrip(".")


def _squash_title(title):
    text = unicodedata.normalize("NFKD", title or "").lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def fingerprint(paper):
    """The single best key for a paper, used for logging and display."""
    doi = _normalize_doi(paper.doi)
    if doi:
        return "doi:" + doi
    squashed = _squash_title(paper.title)
    return "title:" + squashed if squashed else "url:" + paper.url


def keys(paper):
    """Every key this paper should be recognised by."""
    result = [fingerprint(paper)]
    squashed = _squash_title(paper.title)
    if len(squashed) >= MIN_TITLE_KEY_CHARS:
        title_key = "title:" + squashed
        if title_key not in result:
            result.append(title_key)
    return result


def deduplicate(papers):
    """Collapse repeats within a single run.

    The first copy wins, so a paper matching two keyword sets is filed under
    whichever set is listed first in the config. PubMed is fetched before
    preprints, so the published version is kept over the preprint.
    """
    unique = []
    seen = set()
    for paper in papers:
        paper_keys = keys(paper)
        if any(key in seen for key in paper_keys):
            continue
        seen.update(paper_keys)
        unique.append(paper)
    return unique


def _now():
    return datetime.now(timezone.utc)


class Store:
    """The small JSON file at state/seen.json. Plain text, safe to inspect."""

    def __init__(self, path):
        self.path = path
        self.last_run = None          # datetime or None
        self.seen = {}                # key -> ISO date first seen
        self.paper_count = 0          # distinct papers, not keys
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError):
            # A corrupt state file should not stop a run; worst case we show
            # a few papers twice. The old file is kept for inspection.
            try:
                os.replace(self.path, self.path + ".corrupt")
            except OSError:
                pass
            return

        self.seen = data.get("seen", {}) or {}
        self.paper_count = data.get("paper_count", 0) or 0
        stamp = data.get("last_run")
        if stamp:
            try:
                self.last_run = datetime.fromisoformat(stamp)
            except ValueError:
                self.last_run = None

    def is_new(self, paper):
        return not any(key in self.seen for key in keys(paper))

    def filter_new(self, papers):
        return [paper for paper in papers if self.is_new(paper)]

    def mark_seen(self, papers):
        today = _now().date().isoformat()
        for paper in papers:
            paper_keys = keys(paper)
            # One paper can add two keys, so count papers separately or the
            # totals we report back would be inflated.
            if not any(key in self.seen for key in paper_keys):
                self.paper_count += 1
            for key in paper_keys:
                self.seen.setdefault(key, today)

    def due(self, interval_days):
        """Has enough time passed since the last digest?"""
        if self.last_run is None:
            return True, "first run"
        elapsed = _now() - self.last_run
        if elapsed >= timedelta(days=interval_days):
            return True, "%.1f days since last run" % (elapsed.total_seconds() / 86400)
        remaining = timedelta(days=interval_days) - elapsed
        return False, "next run in %.1f days" % (remaining.total_seconds() / 86400)

    def _prune(self):
        cutoff = (_now() - timedelta(days=FORGET_AFTER_DAYS)).date().isoformat()
        self.seen = {
            key: first_seen
            for key, first_seen in self.seen.items()
            if first_seen >= cutoff
        }

    def save(self, record_run=True):
        self._prune()
        if record_run:
            self.last_run = _now()
        payload = {
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "paper_count": self.paper_count,
            "key_count": len(self.seen),
            "seen": self.seen,
        }
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # Write to a temporary file first so an interrupted run cannot leave
        # a half-written state file behind.
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(temporary, self.path)
