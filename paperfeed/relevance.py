"""Decide which papers matter, and in what order.

Two jobs, both deliberately transparent:

  filtering  drop papers you have said you do not want to see
  scoring    rank what is left, and be able to say WHY

The score is not a black box. Every point it awards is recorded as a short
phrase ("'RFdiffusion' in title"), and the digest shows those phrases on the
card. A ranking you cannot interrogate is worse than no ranking at all,
because you stop trusting the top of the list.
"""

from datetime import date

import phrasing
import query as query_module
import sources

DEFAULT_WEIGHTS = {
    "title_weight": 4.0,      # what a paper is ABOUT — the strongest signal
    "mesh_weight": 2.0,       # an indexer decided this paper is about it:
                              # stronger than a passing mention, weaker than
                              # the authors putting it in the title
    "abstract_weight": 1.0,   # a passing mention is weak
    "breadth_bonus": 1.0,     # only used by the older per-term scoring; see
                              # score_by_concept for why concept scoring has
                              # no equivalent
    "recency_bonus": 1.0,     # a tiebreaker: inside a 14-day lookback almost
                              # everything is recent, so this cannot drive the
                              # ranking or it just adds noise
    "author_bonus": 3.0,      # written by someone you follow
    "min_score": 0.0,
}

MAX_SCORE = 10.0


def search_terms(keyword_set):
    """Every term worth scoring against: the OR terms plus the required ones."""
    return list(keyword_set.get("terms", [])) + list(keyword_set.get("all_of", []))


def _contains(haystack, needle):
    return needle.lower() in (haystack or "").lower()


def author_matches(followed, authors):
    """Does this paper have the author you follow on it?

    Plain substring matching is wrong here: "Baker D" would match "Baker DA",
    who is a different person in a different field. Names must line up on a
    whole-initial boundary, so "Baker" still matches "Baker D" but "Baker D"
    does not match "Baker DA".

    (The search APIs are looser than this and will still return the other
    Baker. What this controls is whether the paper gets the follow bonus.)
    """
    target = " ".join((followed or "").lower().split())
    if not target:
        return False
    for author in authors or []:
        name = " ".join((author or "").lower().split())
        if name == target or name.startswith(target + " "):
            return True
    return False


def _days_old(published, today=None):
    """Age in days, or None when the date is missing or unparseable."""
    if not published or len(published) < 10:
        return None
    try:
        year, month, day = (int(part) for part in published[:10].split("-"))
        return ((today or date.today()) - date(year, month, day)).days
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------

def filter_papers(papers, keyword_set, cfg):
    """Return (kept, hidden) where hidden is a list of (paper, reason).

    Hidden papers are reported in the digest and are deliberately NOT marked
    as seen, so that loosening a filter later brings them back.
    """
    mute = cfg.get("mute", {}) or {}
    mute_terms = mute.get("terms", []) or []
    mute_journals = mute.get("journals", []) or []

    exclude = keyword_set.get("exclude", []) or []
    article_types = keyword_set.get("article_types", {}) or {}
    type_exclude = article_types.get("exclude", []) or []
    type_only = article_types.get("only", []) or []
    journals = keyword_set.get("journals", {}) or {}
    allow = journals.get("allow", []) or []
    deny = journals.get("deny", []) or []

    kept = []
    hidden = []
    for paper in papers:
        text = "%s %s" % (paper.title, paper.abstract)
        reason = None

        for term in exclude:
            if _contains(text, term):
                reason = "excluded by %r in this keyword set" % term
                break

        if reason is None:
            for term in mute_terms:
                if _contains(text, term):
                    reason = "muted term %r" % term
                    break

        if reason is None:
            for name in mute_journals:
                if _contains(paper.venue, name):
                    reason = "muted journal %r" % name
                    break

        if reason is None:
            for name in deny:
                if _contains(paper.venue, name):
                    reason = "journal %r is on this set's deny list" % name
                    break

        # An allow list, when present, is exclusive: nothing else gets through.
        if reason is None and allow:
            if not any(_contains(paper.venue, name) for name in allow):
                reason = "journal not on this set's allow list"

        # PubMed labels every record with what kind of article it is, and we
        # already have those labels - they arrive in the XML we fetch. Six of
        # thirty hits on a typical fortnight are reviews.
        kinds = paper.publication_types or []
        if reason is None and type_exclude:
            for wanted in type_exclude:
                if any(_contains(kind, wanted) for kind in kinds):
                    reason = "it is a %s, which this set excludes" % wanted
                    break
        if reason is None and type_only and kinds:
            if not any(_contains(kind, wanted)
                       for kind in kinds for wanted in type_only):
                reason = ("it is a %s, and this set only wants %s"
                          % (", ".join(kinds), " or ".join(type_only)))

        if reason:
            hidden.append((paper, reason))
        else:
            kept.append(paper)
    return kept, hidden


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Scoring by concept
# --------------------------------------------------------------------------
#
# A query is a list of concepts, each usually written several ways:
#
#     (IDH1 OR "isocitrate dehydrogenase 1") AND (glioma OR glioblastoma)
#
# Scoring per WORD rewards a paper for saying one thing three ways. That is
# not a hypothetical: a general review that mentions glioma, glioblastoma and
# astrocytoma used to collect a breadth bonus for each, and outranked a paper
# whose title was actually about IDH1. Scoring per CONCEPT fixes that - each
# bracketed group can be satisfied once, by its best available evidence.

_KINDS = {
    "title": "title_weight",
    "mesh": "mesh_weight",
    "abstract": "abstract_weight",
    "journal": "abstract_weight",
    "author": "author_bonus",
}


def _leaf_match(leaf, paper):
    """How, if at all, this paper satisfies one written term."""
    text = leaf.text
    if leaf.field in ("ti_ab", "title", "all") and _contains(paper.title, text):
        return "title"
    if leaf.field == "title":
        return None                      # title:X means the title, or nothing
    if leaf.field == "mesh":
        return "mesh" if any(
            _contains(term, text) for term in paper.mesh_terms or []) else None
    if leaf.field == "author":
        return "author" if author_matches(text, paper.authors) else None
    if leaf.field == "journal":
        return "journal" if _contains(paper.venue, text) else None
    if leaf.field in ("ti_ab", "abstract", "all") and _contains(paper.abstract, text):
        return "abstract"
    if leaf.field == "all" and any(
            _contains(word, text) for word in paper.keywords or []):
        return "abstract"
    return None


def _describe_match(kind, term):
    if kind == "title":
        return "%r in title" % term
    if kind == "mesh":
        return "indexed under %r" % term
    if kind == "abstract":
        return "%r in abstract" % term
    if kind == "journal":
        return "published in %s" % term
    return "by %s, who you follow" % term


def _best_in_group(group, paper, settings):
    """The strongest evidence that this paper satisfies one concept.

    A concept can be met once. Which of its synonyms did it is irrelevant to
    the score, so the best one wins and the rest are ignored.
    """
    best = None
    for leaf in query_module.positive_leaves(group):
        kind = _leaf_match(leaf, paper)
        if kind is None:
            continue
        weight = settings[_KINDS[kind]]
        if best is None or weight > best[0]:
            best = (weight, kind, leaf.text)
    return best


def score_by_concept(paper, tree, settings, today=None):
    """Score against a written query, one point-award per concept."""
    groups = query_module.concept_groups(tree)
    raw = 0.0
    reasons = []
    in_title = 0

    for group in groups:
        best = _best_in_group(group, paper, settings)
        if best is None:
            continue
        weight, kind, term = best
        raw += weight
        if kind == "title":
            in_title += 1
        reasons.append(_describe_match(kind, term))

    paper.total_concepts = len(groups)
    paper.title_concepts = in_title

    # Worth saying out loud on the card: a paper carrying both halves of the
    # question in its title is a different thing from one that mentions them.
    if len(groups) > 1 and in_title:
        reasons.append(
            "%d of %d concepts in the title" % (in_title, len(groups)))

    raw += _recency(paper, settings, reasons, today)
    paper.score = round(min(raw, MAX_SCORE), 1)
    paper.score_reasons = reasons
    return paper.score


def _recency(paper, settings, reasons, today):
    age = _days_old(paper.published, today)
    if age is not None and age <= 3:
        reasons.append("published %s" % phrasing.days_ago(age))
        return settings["recency_bonus"]
    if age is not None and age <= 7:
        reasons.append("published %s" % phrasing.days_ago(age))
        return settings["recency_bonus"] / 2
    return 0.0


def score_paper(paper, keyword_set, weights=None, today=None):
    """Attach paper.score (0-10) and paper.score_reasons (list of phrases)."""
    settings = dict(DEFAULT_WEIGHTS)
    settings.update(weights or {})

    tree = sources.parsed_query(keyword_set)
    if tree is not None:
        return score_by_concept(paper, tree, settings, today)

    raw = 0.0
    reasons = []
    matched = set()

    for term in search_terms(keyword_set):
        if _contains(paper.title, term):
            raw += settings["title_weight"]
            matched.add(term.lower())
            reasons.append("%r in title" % term)
        elif _contains(paper.abstract, term):
            raw += settings["abstract_weight"]
            matched.add(term.lower())
            reasons.append("%r in abstract" % term)

    # Matching several different terms is a better signal than matching one
    # term repeatedly, so breadth earns its own bonus.
    if len(matched) > 1:
        raw += settings["breadth_bonus"] * (len(matched) - 1)
        reasons.append("%s matched" % phrasing.count(len(matched), "different term"))

    for name in keyword_set.get("authors", []) or []:
        if author_matches(name, paper.authors):
            raw += settings["author_bonus"]
            reasons.append("by %s, who you follow" % name)
            break

    age = _days_old(paper.published, today)
    if age is not None and age <= 3:
        raw += settings["recency_bonus"]
        reasons.append("published %s" % phrasing.days_ago(age))
    elif age is not None and age <= 7:
        raw += settings["recency_bonus"] / 2
        reasons.append("published %s" % phrasing.days_ago(age))

    paper.score = round(min(raw, MAX_SCORE), 1)
    paper.score_reasons = reasons
    return paper.score


def score_all(papers, keyword_sets, weights=None, today=None):
    """Score every paper against the keyword set that found it."""
    by_name = {entry["name"]: entry for entry in keyword_sets}
    for paper in papers:
        keyword_set = by_name.get(paper.set_name)
        if keyword_set:
            score_paper(paper, keyword_set, weights, today)
    return papers


def rank(papers):
    """Highest score first; ties broken by date, newest first."""
    return sorted(
        papers,
        key=lambda paper: (paper.score, paper.published or ""),
        reverse=True,
    )


def sort_key(paper):
    """AI score where we have one, local score otherwise.

    Both are on the same 0-10 scale, so a run where only the first 40 papers
    were sent to the API still sorts sensibly: the local score carries the
    rest, and the local score breaks ties.
    """
    ai = getattr(paper, "ai_score", None)
    return (ai if ai is not None else paper.score, paper.score, paper.published or "")


def rank_with_ai(papers):
    return sorted(papers, key=sort_key, reverse=True)


def drop_below(papers, min_score):
    """Split into (kept, hidden) at the minimum score, if one is set."""
    if not min_score:
        return papers, []
    kept = [paper for paper in papers if paper.score >= min_score]
    hidden = [
        (paper, "scored %.1f, below your minimum of %.1f" % (paper.score, min_score))
        for paper in papers
        if paper.score < min_score
    ]
    return kept, hidden


def drop_below_per_set(papers, keyword_sets, global_min):
    """Apply each set's own min_score, falling back to the global one.

    A single global threshold cannot serve sets with different vocabularies:
    a cut-off that tames a broad term like "glioblastoma" (where the word is
    usually in the title) will silence a set whose matches are legitimately
    in abstracts.
    """
    thresholds = {}
    for entry in keyword_sets:
        value = entry.get("min_score")
        thresholds[entry["name"]] = global_min if value is None else value

    kept, hidden = [], []
    for paper in papers:
        threshold = thresholds.get(paper.set_name, global_min)
        if not threshold or paper.score >= threshold:
            kept.append(paper)
        else:
            hidden.append(
                (
                    paper,
                    "scored %.1f, below the %.1f minimum set for %r"
                    % (paper.score, threshold, paper.set_name),
                )
            )
    return kept, hidden


def drop_below_title_concepts(papers, keyword_sets):
    """Apply each set's require_title_groups.

    A blunter instrument than min_score and easier to reason about: it asks
    how much of your question is in the paper's title, rather than how many
    points the paper accumulated. On a two-concept query, 1 means the paper
    must be at least partly about your topic and 2 means it must be about
    both halves of it.
    """
    wanted = {
        entry["name"]: int(entry.get("require_title_groups") or 0)
        for entry in keyword_sets
    }
    kept, hidden = [], []
    for paper in papers:
        need = wanted.get(paper.set_name, 0)
        if not need or paper.title_concepts >= need:
            kept.append(paper)
        else:
            hidden.append((
                paper,
                "only %d of your %d concepts are in its title, and %r asks "
                "for %d" % (paper.title_concepts, paper.total_concepts,
                            paper.set_name, need),
            ))
    return kept, hidden


def top_n(papers, count=5):
    """The strongest papers across every keyword set, for the digest header."""
    return rank(papers)[:count]
