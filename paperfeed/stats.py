"""Turn a run's papers into the numbers the dashboard draws.

Also keeps a small history of subject counts per run, which is the only way
to say whether a topic is rising or fading. That history holds counts, not
papers - a few hundred bytes a run - so it stays consistent with the rule
that PaperFeed only keeps papers you chose to save.
"""

import json
import os
from collections import Counter
from datetime import datetime, timezone

MAX_RUNS_KEPT = 200
SUBJECTS_PER_PAPER = 4     # cap co-occurrence so one paper cannot dominate
MIN_TO_BE_RISING = 3


def subjects_of(paper):
    """The subject terms for one paper, MeSH preferred over author keywords."""
    terms = list(getattr(paper, "mesh_terms", None) or [])
    if not terms:
        terms = list(getattr(paper, "keywords", None) or [])
    cleaned, seen = [], set()
    for term in terms:
        text = " ".join(str(term).split())
        if not text or len(text) < 3:
            continue
        low = text.lower()
        # Age and sex MeSH headings are on everything and say nothing.
        if low in ("humans", "male", "female", "animals", "adult", "aged",
                   "middle aged", "young adult", "child", "adolescent"):
            continue
        if low in seen:
            continue
        seen.add(low)
        cleaned.append(text)
    return cleaned


def _canonicalise(counter):
    """Merge terms that differ only in case, keeping the commonest spelling.

    MeSH headings are Title Case while author keywords are usually lower
    case, so "Glioblastoma" and "glioblastoma" arrive as different strings
    and would otherwise show up as two nodes on the graph.
    """
    groups = {}
    for term, count in counter.items():
        groups.setdefault(term.lower(), []).append((term, count))
    merged = Counter()
    display = {}
    for low, variants in groups.items():
        total = sum(count for _, count in variants)
        best = max(variants, key=lambda pair: (pair[1], pair[0].istitle()))[0]
        merged[best] = total
        for term, _ in variants:
            display[term] = best
    return merged, display


def run_stats(papers, keyword_sets):
    """Everything the dashboard needs about one run."""
    per_set = Counter(paper.set_name for paper in papers)
    per_source = Counter(paper.source for paper in papers)

    buckets = [("0-1", 0), ("2-3", 0), ("4-5", 0), ("6-7", 0), ("8-10", 0)]
    edges = [(0, 1.99), (2, 3.99), (4, 5.99), (6, 7.99), (8, 10.01)]
    counts = [0] * len(edges)
    for paper in papers:
        for index, (low, high) in enumerate(edges):
            if low <= paper.score <= high:
                counts[index] += 1
                break
    score_buckets = [(name, counts[i]) for i, (name, _) in enumerate(buckets)]

    subjects = Counter()
    pairs = Counter()
    for paper in papers:
        terms = subjects_of(paper)[:SUBJECTS_PER_PAPER]
        subjects.update(terms)
        for i in range(len(terms)):
            for j in range(i + 1, len(terms)):
                pairs[tuple(sorted((terms[i], terms[j])))] += 1

    subjects, canonical = _canonicalise(subjects)
    folded = Counter()
    for (a, b), weight in pairs.items():
        a, b = canonical.get(a, a), canonical.get(b, b)
        if a != b:
            folded[tuple(sorted((a, b)))] += weight
    pairs = folded

    return {
        "total": len(papers),
        "per_set": dict(per_set),
        "per_source": dict(per_source),
        "score_buckets": score_buckets,
        "subjects": subjects,
        "pairs": pairs,
        "open_access": sum(1 for p in papers if p.free_fulltext),
        "sets_order": [entry["name"] for entry in keyword_sets],
    }


# --------------------------------------------------------------------------
# Subject history, for "is this rising?"
# --------------------------------------------------------------------------

def _load(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle) or []
    except (json.JSONDecodeError, OSError):
        return []


ALL = "__all__"


def record(path, buckets, totals):
    """Append this run's subject counts, per keyword set and overall.

    buckets: {bucket_name: Counter}, including ALL for the whole run.
    totals:  {bucket_name: int}

    Stored per set because a researcher following several fields needs to
    know that one of them is heating up, which an overall count hides.
    """
    history = _load(path)
    history.append(
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": totals.get(ALL, 0),
            "sets": {
                name: {
                    "total": totals.get(name, 0),
                    "subjects": {
                        term: int(count) for term, count in counter.most_common(80)
                    },
                }
                for name, counter in buckets.items()
            },
        }
    )
    history = history[-MAX_RUNS_KEPT:]
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(history, handle)
    os.replace(temporary, path)
    return history


def _bucket_subjects(entry, bucket):
    """Read one bucket out of a history entry, tolerating the older shape."""
    sets = entry.get("sets")
    if isinstance(sets, dict):
        return (sets.get(bucket) or {}).get("subjects") or {}
    # entries written before per-set history existed only had an overall count
    return entry.get("subjects") or {} if bucket == ALL else {}


def alerts(history, current, limit=8, bucket=ALL):
    """Classify subjects against their own past.

    Returns [(subject, now, verdict, detail)] where verdict is one of
    "new", "rising", "fading".
    """
    if not history:
        return []
    past = history[:-1] if len(history) > 1 else []
    if not past:
        return []

    seen_before = Counter()
    appearances = Counter()
    for entry in past:
        for name, count in _bucket_subjects(entry, bucket).items():
            seen_before[name] += count
            appearances[name] += 1
    if not appearances:
        return []

    out = []
    for name, now in current.most_common(60):
        if name not in seen_before:
            if now >= 2:
                out.append((name, now, "new", "not seen in %d earlier run%s"
                            % (len(past), "" if len(past) == 1 else "s")))
            continue
        average = seen_before[name] / max(1, appearances[name])
        if now >= MIN_TO_BE_RISING and now >= average * 1.6:
            out.append((name, now, "rising", "usually about %.0f a run" % average))
        elif average >= MIN_TO_BE_RISING and now <= average * 0.4:
            out.append((name, now, "fading", "usually about %.0f a run" % average))

    order = {"new": 0, "rising": 1, "fading": 2}
    out.sort(key=lambda row: (order[row[2]], -row[1]))
    return out[:limit]


def graph_data(subjects, pairs, node_limit=18, edge_limit=28, verdicts=None):
    """Nodes and edges for the subject graph, trimmed to stay readable."""
    verdicts = verdicts or {}
    top = [name for name, _ in subjects.most_common(node_limit)]
    allowed = set(top)
    nodes = [(name, subjects[name], verdicts.get(name, "")) for name in top]
    edges = [
        (a, b, weight)
        for (a, b), weight in pairs.most_common(edge_limit * 3)
        if a in allowed and b in allowed
    ][:edge_limit]
    return nodes, edges


# --------------------------------------------------------------------------
# Retrospective analysis
# --------------------------------------------------------------------------

def by_month(papers, start=None, end=None):
    """Papers per publication month. Returns (rows, outside_the_range).

    Uses the journal's issue date, not the date the record appeared: a paper
    issued in December and indexed in January belongs to December here.

    Those two dates genuinely disagree, so a search by PubMed's publication
    date returns some papers whose issue date sits outside the window you
    asked for - ahead-of-print articles assigned to a later issue, mostly.
    When a range is given the axis is held to it and the strays are counted
    separately, rather than stretching the chart to cover one outlier.
    """
    counts = Counter()
    outside = 0
    first = start.strftime("%Y-%m") if start else None
    last = end.strftime("%Y-%m") if end else None

    for paper in papers:
        when = (getattr(paper, "issued", "") or paper.published or "")[:7]
        if len(when) != 7:
            continue
        if first and not (first <= when <= last):
            outside += 1
            continue
        counts[when] += 1

    if first:
        rows, cursor = [], start.replace(day=1)
        while cursor <= end:
            label = cursor.strftime("%Y-%m")
            rows.append((label, counts.get(label, 0)))
            cursor = (
                cursor.replace(year=cursor.year + 1, month=1)
                if cursor.month == 12
                else cursor.replace(month=cursor.month + 1)
            )
        return rows, outside
    return sorted(counts.items()), outside


def top_authors(papers, limit=12):
    counts = Counter()
    for paper in papers:
        for name in paper.authors or []:
            cleaned = " ".join(str(name).split())
            if cleaned:
                counts[cleaned] += 1
    merged, _ = _canonicalise(counts)
    return merged.most_common(limit)


def top_institutions(papers, limit=12):
    """Where the work came from, by senior author's institution.

    Affiliations are free text, so the same place arrives spelled several
    ways; the same case-folding used for subjects merges the obvious ones.
    It will not catch everything - "MIT" and "Massachusetts Institute of
    Technology" stay separate - so read this as indicative.
    """
    counts = Counter()
    for paper in papers:
        place = " ".join((getattr(paper, "affiliation", "") or "").split())
        if place:
            counts[place] += 1
    merged, _ = _canonicalise(counts)
    return merged.most_common(limit)


def compare(before, after, limit=10):
    """What changed between two sets of papers.

    Returns (risen, fallen, appeared, vanished), each [(subject, before,
    after)]. Shares are compared rather than raw counts, because two periods
    rarely contain the same number of papers and a bigger period would
    otherwise look like growth everywhere.
    """
    first = Counter()
    second = Counter()
    for paper in before:
        first.update(subjects_of(paper)[:SUBJECTS_PER_PAPER])
    for paper in after:
        second.update(subjects_of(paper)[:SUBJECTS_PER_PAPER])
    first, _ = _canonicalise(first)
    second, _ = _canonicalise(second)

    total_first = sum(first.values()) or 1
    total_second = sum(second.values()) or 1

    risen, fallen, appeared, vanished = [], [], [], []
    for name in set(first) | set(second):
        a, b = first.get(name, 0), second.get(name, 0)
        if a + b < 3:                      # too rare to mean anything
            continue
        share_a = a / total_first
        share_b = b / total_second
        if a == 0:
            appeared.append((name, a, b))
        elif b == 0:
            vanished.append((name, a, b))
        elif share_b > share_a * 1.5:
            risen.append((name, a, b))
        elif share_a > share_b * 1.5:
            fallen.append((name, a, b))

    risen.sort(key=lambda row: -row[2])
    fallen.sort(key=lambda row: -row[1])
    appeared.sort(key=lambda row: -row[2])
    vanished.sort(key=lambda row: -row[1])
    return risen[:limit], fallen[:limit], appeared[:limit], vanished[:limit]
