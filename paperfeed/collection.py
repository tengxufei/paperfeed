"""What your collection looks like, as opposed to what one run looked like.

The dashboard used to be computed entirely from the papers a single run
brought back. On an hourly schedule most runs bring back nothing, so most of
the time every panel read zero - not because there was no data, but because
it was looking at the wrong thing. There were 23 saved papers, 74 cached
citation records, 35 journals and 27 recorded runs sitting on disk while the
page said "0 new this run".

So this module reads what has accumulated: the library, the metric cache
beside it, and the run history. Those answer the questions actually worth
asking - what have I collected, is it any good, am I reading it, and what is
moving in my field - none of which depend on this particular hour being
busy.

Everything here is read-only and tolerates missing data: no metrics cache,
no history and an empty library all produce a usable (if quiet) result
rather than an exception.
"""

import json
import os
import sqlite3
import statistics
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta

# Same rule the cards use: under this age a paper has had no opportunity to
# be cited, so its count is withheld rather than shown as a zero.
TOO_NEW_DAYS = 90


def _rows(path):
    if not path or not os.path.exists(path):
        return []
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM saved ORDER BY saved_at DESC")]
        connection.close()
        return rows
    except sqlite3.Error:
        return []


def _metric_cache(path):
    """Everything the metrics module has already looked up, by key."""
    works, journals = {}, {}
    if not path or not os.path.exists(path):
        return works, journals
    try:
        connection = sqlite3.connect(path)
        for kind, key, payload in connection.execute(
                "SELECT kind, key, payload FROM entries"):
            try:
                value = json.loads(payload)
            except ValueError:
                continue
            (works if kind == "work" else journals)[key] = value
        connection.close()
    except sqlite3.Error:
        pass
    return works, journals


def _day(text):
    try:
        return date(*(int(part) for part in str(text)[:10].split("-")))
    except (TypeError, ValueError):
        return None


def _week_start(when):
    return when - timedelta(days=when.weekday())


def snapshot(library_path, metrics_path, scope=None, today=None):
    """Everything the dashboard needs about the papers you have kept."""
    today = today or date.today()
    rows = _rows(library_path)
    if scope:
        rows = [row for row in rows if row.get("set_name") == scope]

    works, journals = _metric_cache(metrics_path)
    for row in rows:
        doi = (row.get("doi") or "").strip().lower()
        row["_m"] = works.get("doi:%s" % doi, {}) if doi else {}
        issn = (row["_m"].get("journal_issn") or "").strip()
        row["_j"] = journals.get(issn, {}) if issn else {}

    out = {
        "total": len(rows),
        "scope": scope,
        "by_status": Counter(row.get("status") or "unread" for row in rows),
        "by_origin": Counter(row.get("origin") or "you" for row in rows),
        "by_source": Counter(row.get("source") or "?" for row in rows),
        "by_set": Counter(row.get("set_name") or "unsorted" for row in rows),
        "measured": sum(1 for row in rows if row["_m"]),
    }
    out.update(_reading(rows))
    out.update(_growth(rows, today))
    out.update(_quality(rows, today))
    out["journals"] = _journals(rows)
    out["headline"] = _headline(out, rows, today)
    return out


# --------------------------------------------------------------------------
# Are you reading it?
# --------------------------------------------------------------------------

def _reading(rows):
    """The reading funnel, and what has been sitting unread the longest.

    A collection that grows on its own is only useful if it is also being
    read, so the dashboard should be willing to say when it is not.
    """
    unread = [row for row in rows if (row.get("status") or "unread") == "unread"]
    oldest = sorted(unread, key=lambda row: row.get("saved_at") or "")[:6]
    done = sum(1 for row in rows if row.get("status") == "read")
    return {
        "unread_oldest": oldest,
        "read_share": (100.0 * done / len(rows)) if rows else 0.0,
        "untouched": len(unread),
    }


# --------------------------------------------------------------------------
# Is it growing?
# --------------------------------------------------------------------------

def _growth(rows, today):
    """Papers added per week, split by who added them, plus the running total.

    The split matters: the whole point of the collector is that the library
    grows without you, and this is the only place that claim is checked.
    """
    weeks = OrderedDict()
    for row in rows:
        when = _day(row.get("saved_at"))
        if not when:
            continue
        key = _week_start(when).isoformat()
        bucket = weeks.setdefault(key, {"auto": 0, "you": 0})
        bucket["auto" if row.get("origin") == "auto" else "you"] += 1

    ordered = sorted(weeks.items())
    running = 0
    cumulative = []
    for key, bucket in ordered:
        running += bucket["auto"] + bucket["you"]
        cumulative.append((key[5:], running))

    recent = [row for row in rows if (_day(row.get("saved_at")) or date.min)
              >= today - timedelta(days=30)]
    return {
        "per_week": [(key[5:], bucket["auto"], bucket["you"])
                     for key, bucket in ordered],
        "cumulative": cumulative,
        "added_30d": len(recent),
        "added_30d_auto": sum(1 for row in recent if row.get("origin") == "auto"),
    }


# --------------------------------------------------------------------------
# Is any of it any good?
# --------------------------------------------------------------------------

def _quality(rows, today):
    """Citation and access figures, with the too-new rule applied here too.

    The cache holds the raw count for every paper, including ones published
    days ago. Re-applying the rule at this end keeps the dashboard saying the
    same thing as the cards.
    """
    cited = []
    for row in rows:
        metrics = row["_m"]
        published = _day(row.get("published"))
        row["_age"] = (today - published).days if published else None
        row["_too_new"] = row["_age"] is not None and row["_age"] < TOO_NEW_DAYS
        count = metrics.get("citations")
        if count is not None and not row["_too_new"]:
            row["_cited"] = count
            cited.append(row)
        else:
            row["_cited"] = None

    open_access = sum(1 for row in rows
                      if row["_m"].get("is_oa") or row.get("source") == "Preprint")
    lag = [row["_age"] for row in rows if row["_age"] is not None]

    return {
        "ranked": sorted(cited, key=lambda row: -row["_cited"])[:8],
        "citable": len(cited),
        "too_new": sum(1 for row in rows if row["_too_new"]),
        "open_access": open_access,
        "open_share": (100.0 * open_access / len(rows)) if rows else 0.0,
        "retracted": [row for row in rows if row["_m"].get("retracted")],
        "median_age": int(statistics.median(lag)) if lag else None,
        "age_buckets": _age_buckets(rows),
        "reviews": sum(1 for row in rows
                       if (row["_m"].get("type") or "") == "review"),
    }


def _age_buckets(rows):
    """How current the collection is, as a histogram you can read at a glance."""
    edges = [(7, "this week"), (30, "this month"), (90, "3 months"),
             (365, "this year"), (10 ** 6, "older")]
    counts = OrderedDict((label, 0) for _, label in edges)
    for row in rows:
        if row.get("_age") is None:
            continue
        for limit, label in edges:
            if row["_age"] <= limit:
                counts[label] += 1
                break
    return list(counts.items())


def _journals(rows, limit=8):
    """Where your collection comes from, with each journal's own figures."""
    seen = {}
    for row in rows:
        name = (row["_j"].get("name") or row.get("venue") or "").strip()
        if not name:
            continue
        entry = seen.setdefault(name, {
            "name": name, "count": 0,
            "citedness": row["_j"].get("two_year_mean_citedness"),
            "works": row["_j"].get("works_count"),
            "in_doaj": row["_j"].get("in_doaj"),
            "preprint": row.get("source") == "Preprint",
        })
        entry["count"] += 1
    return sorted(seen.values(), key=lambda entry: -entry["count"])[:limit]


# --------------------------------------------------------------------------
# One sentence that says whether things are going well
# --------------------------------------------------------------------------

def _headline(out, rows, today):
    """A plain reading of the numbers, for the top of the page.

    A wall of figures leaves the reader to work out whether any of it is
    good news. This says it outright.
    """
    if not rows:
        return ("Nothing saved yet. Turn the collector on in config.json, or "
                "click Save on anything worth keeping in the digest.")

    bits = []
    if out["added_30d"]:
        share = out["added_30d_auto"]
        bits.append(
            "%d paper%s joined your library in the last 30 days%s"
            % (out["added_30d"], "" if out["added_30d"] == 1 else "s",
               ", %d of them collected without you" % share if share else "")
        )
    else:
        bits.append("nothing new has been saved in the last 30 days")

    if out["untouched"]:
        bits.append("%d %s still unread"
                    % (out["untouched"], "is" if out["untouched"] == 1 else "are"))
    if out["median_age"] is not None:
        bits.append("the typical one was published %d days before you saw it"
                    % out["median_age"])
    return "; ".join(bits).capitalize() + "."


# --------------------------------------------------------------------------
# What the runs themselves have been doing
# --------------------------------------------------------------------------

def activity(history_rows, scope=None, limit=24):
    """Papers found per run, oldest first, for the volume chart.

    index.json is newest-first and one entry per run, including the many
    that found nothing - which is the honest picture of a feed, and is also
    why a single run is a bad thing to build a dashboard on.
    """
    rows = list(reversed(history_rows or []))[-limit:]
    series, quiet = [], 0
    for row in rows:
        if scope:
            value = (row.get("sets") or {}).get(scope, 0)
        else:
            value = row.get("total", 0)
        label = (row.get("date_label") or "")[:16]
        series.append((label, value))
        if not value:
            quiet += 1
    return {
        "series": series,
        "runs": len(rows),
        "quiet": quiet,
        "found": sum(value for _, value in series),
        "busiest": max(series, key=lambda item: item[1]) if series else None,
    }


def subject_history(history, subject, bucket="__all__", limit=12):
    """One subject's count across recent snapshots, for a sparkline."""
    series = []
    for entry in (history or [])[-limit:]:
        counts = ((entry.get("sets") or {}).get(bucket) or {}).get("subjects") or {}
        series.append((str(entry.get("at", ""))[5:10], counts.get(subject, 0)))
    return series


# --------------------------------------------------------------------------
# The last run that actually found something
# --------------------------------------------------------------------------
#
# The subject panels need papers to describe, and a run on a short interval
# usually has none. Rather than let two panels blink out whenever a run is
# quiet, the last run that did find papers is kept here and reused, clearly
# dated so nobody mistakes it for now.

def remember(path, buckets, when=None):
    """Store each bucket's subjects and co-occurrence pairs, if non-empty."""
    keep = {
        name: {
            "total": data["total"],
            "subjects": dict(data["subjects"]),
            "pairs": [[a, b, weight] for (a, b), weight in data["pairs"].items()],
        }
        for name, data in buckets.items() if data["total"]
    }
    if not keep:
        return False
    payload = {"at": (when or datetime.utcnow()).isoformat(), "buckets": keep}
    try:
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temporary, path)
        return True
    except OSError:
        return False


def recall(path, bucket, today=None):
    """(subjects, pairs, how_many_days_ago) from the last run that found any."""
    if not path or not os.path.exists(path):
        return Counter(), Counter(), None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return Counter(), Counter(), None

    data = (payload.get("buckets") or {}).get(bucket)
    if not data:
        return Counter(), Counter(), None

    pairs = Counter()
    for entry in data.get("pairs") or []:
        try:
            a, b, weight = entry
        except (TypeError, ValueError):
            continue
        pairs[tuple(sorted((a, b)))] = weight

    age = None
    when = _day((payload.get("at") or "").replace("T", " "))
    if when:
        age = ((today or date.today()) - when).days
    return Counter(data.get("subjects") or {}), pairs, age
