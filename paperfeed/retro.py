"""Search a date range you choose, instead of only the last few days.

The feed answers "what is new to me?". This answers "what was published
between these dates?", which is a different question: it searches on
publication date, it pages through everything rather than taking the newest
few, and it never touches the feed's memory.

Two rules, both deliberate:

  * state/seen.json is never read or written here. Looking back at 2024 must
    not make your next digest skip those papers.
  * state/topics.json is never written here either, or historical volume
    would corrupt the baselines that decide what is "rising".

Results are cached per keyword set, per source, per month, so re-analysing a
period costs nothing and works offline. The cache is a working file, not your
library: delete it and you lose only fetch time.
"""

import dataclasses
import json
import os
import re
from datetime import date, timedelta

import sources
import store


def month_slices(start, end):
    """Break a range into calendar months.

    PubMed cannot page past 10,000 results for one query, and a busy year of
    a broad term goes well beyond that. Month-sized queries stay under the
    ceiling, give honest progress, and make the cache reusable when ranges
    overlap.
    """
    slices = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        if cursor.month == 12:
            following = date(cursor.year + 1, 1, 1)
        else:
            following = date(cursor.year, cursor.month + 1, 1)
        slices.append((max(cursor, start), min(following - timedelta(days=1), end)))
        cursor = following
    return slices


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:48]


def cache_dir(base_dir):
    return os.path.join(base_dir, "cache")


def _cache_file(base_dir, set_name, source, month):
    return os.path.join(
        cache_dir(base_dir), "%s__%s__%s.json" % (_slug(set_name), source, month)
    )


def _load(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            rows = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    papers = []
    for row in rows:
        try:
            papers.append(sources.Paper(**row))
        except TypeError:
            # written by an older version with different fields; refetch
            return None
    return papers


def _save(path, papers):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump([dataclasses.asdict(paper) for paper in papers], handle)
    os.replace(temporary, path)


def clear_cache(base_dir):
    folder = cache_dir(base_dir)
    removed = 0
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.endswith(".json"):
                os.remove(os.path.join(folder, name))
                removed += 1
    return removed


def search(cfg, keyword_sets, start, end, refresh=False, progress=None):
    """Every paper published in [start, end] for these keyword sets.

    Returns (papers, report) where report records what came from the cache,
    what was fetched, and anything that failed.
    """
    report = {"months": 0, "from_cache": 0, "fetched": 0, "errors": [], "totals": {}}
    collected = []

    for keyword_set in keyword_sets:
        for first, last in month_slices(start, end):
            month = first.strftime("%Y-%m")
            report["months"] += 1
            for source_key, label in (("pubmed", "PubMed"), ("preprints", "Preprints")):
                if not cfg["sources"].get(source_key):
                    continue

                path = _cache_file(cfg["base_dir"], keyword_set["name"], source_key, month)
                if not refresh:
                    cached = _load(path)
                    if cached is not None:
                        collected.extend(cached)
                        report["from_cache"] += len(cached)
                        if progress:
                            progress(keyword_set["name"], month, label,
                                     len(cached), "cached")
                        continue

                try:
                    if source_key == "pubmed":
                        papers, total = sources.fetch_pubmed_range(
                            keyword_set, first, last, cfg.get("contact_email", "")
                        )
                    else:
                        papers, total = sources.fetch_preprints_range(
                            keyword_set, first, last, cfg.get("contact_email", "")
                        )
                except Exception as error:
                    report["errors"].append(
                        "%s / %s / %s: %s" % (label, keyword_set["name"], month, error)
                    )
                    continue

                _save(path, papers)
                collected.extend(papers)
                report["fetched"] += len(papers)
                report["totals"][label] = report["totals"].get(label, 0) + total
                if progress:
                    progress(keyword_set["name"], month, label, len(papers), "fetched")

    return store.deduplicate(collected), report
