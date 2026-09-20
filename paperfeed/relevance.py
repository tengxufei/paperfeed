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

DEFAULT_WEIGHTS = {
    "title_weight": 4.0,      # what a paper is ABOUT — the strongest signal
    "abstract_weight": 1.0,   # a passing mention is weak
    "breadth_bonus": 1.0,     # per extra distinct term; real, but must not
                              # let two passing mentions outrank a title hit
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

        if reason:
            hidden.append((paper, reason))
        else:
            kept.append(paper)
    return kept, hidden


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def score_paper(paper, keyword_set, weights=None, today=None):
    """Attach paper.score (0-10) and paper.score_reasons (list of phrases)."""
    settings = dict(DEFAULT_WEIGHTS)
    settings.update(weights or {})

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
        reasons.append("%d different terms matched" % len(matched))

    for name in keyword_set.get("authors", []) or []:
        if author_matches(name, paper.authors):
            raw += settings["author_bonus"]
            reasons.append("by %s, who you follow" % name)
            break

    age = _days_old(paper.published, today)
    if age is not None and age <= 3:
        raw += settings["recency_bonus"]
        reasons.append("published %s" % ("today" if age <= 0 else "%d days ago" % age))
    elif age is not None and age <= 7:
        raw += settings["recency_bonus"] / 2
        reasons.append("published %d days ago" % age)

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


def top_n(papers, count=5):
    """The strongest papers across every keyword set, for the digest header."""
    return rank(papers)[:count]
