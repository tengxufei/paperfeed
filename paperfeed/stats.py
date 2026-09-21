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


def record(path, subjects, total):
    """Append this run's subject counts. Returns the full history."""
    history = _load(path)
    history.append(
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": total,
            "subjects": {name: int(count) for name, count in subjects.most_common(120)},
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


def alerts(history, current, limit=8):
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
        for name, count in (entry.get("subjects") or {}).items():
            seen_before[name] += count
            appearances[name] += 1

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
