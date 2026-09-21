"""Optional AI features: per-paper relevance, and the quarterly trend report.

Everything here is optional and off by default. PaperFeed must work with no
API key at all, so every function in this module is written to fail soft: if
the key is missing, wrong, or the network is down, the caller logs it and
carries on with the local ranking. The digest is written either way.

Talking to the API over plain HTTP with `requests` rather than the official
anthropic SDK is a deliberate choice: this project's constraint is "standard
library plus requests", and adding an SDK would break it.

Your key is never written into config.json or any file in the project. It
goes to the macOS Keychain, with a 0600 file as the fallback.
"""

import json
import os
import re
import subprocess
import time

import requests

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

KEYCHAIN_SERVICE = "paperfeed-anthropic-key"
KEYCHAIN_ACCOUNT = "paperfeed"

# Prices per million tokens, from the Anthropic pricing table (2026-06).
# Used only to show you an estimate before you switch anything on.
PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00},
}

DEFAULT_SCORING_MODEL = "claude-haiku-4-5"
DEFAULT_TRENDS_MODEL = "claude-sonnet-5"

RETRYABLE = (429, 500, 502, 503, 529)
MAX_ATTEMPTS = 3


class AIError(Exception):
    """Something went wrong talking to the API. Never fatal to a run."""


class KeyRejected(AIError):
    """The API said no to this key. Worth telling the user loudly."""


# --------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------

def _fallback_path(base_dir):
    return os.path.join(base_dir, "state", "credentials.json")


def read_key(base_dir=""):
    """Find the key: environment first, then Keychain, then the fallback file."""
    from_env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if from_env:
        return from_env

    try:
        result = subprocess.run(
            [
                "security", "find-generic-password",
                "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass      # no Keychain (not a Mac, or locked) - try the file

    path = _fallback_path(base_dir)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return (json.load(handle).get("anthropic_api_key") or "").strip()
        except (OSError, json.JSONDecodeError):
            return ""
    return ""


def store_key(key, base_dir=""):
    """Put the key in the Keychain, or a 0600 file if that is unavailable.

    Returns a short description of where it went.
    """
    try:
        result = subprocess.run(
            [
                "security", "add-generic-password",
                "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE,
                "-w", key, "-U",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return "macOS Keychain"
    except (OSError, subprocess.SubprocessError):
        pass

    path = _fallback_path(base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"anthropic_api_key": key}, handle)
    os.chmod(path, 0o600)
    return path


def forget_key(base_dir=""):
    removed = []
    try:
        result = subprocess.run(
            [
                "security", "delete-generic-password",
                "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE,
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            removed.append("Keychain")
    except (OSError, subprocess.SubprocessError):
        pass
    path = _fallback_path(base_dir)
    if os.path.exists(path):
        os.remove(path)
        removed.append(path)
    return removed


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------

def call(key, model, system, user_text, max_tokens=2000, effort=None, timeout=120):
    """One Messages API request. Returns (text, usage dict)."""
    if not key:
        raise AIError("no API key available")

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_text}],
    }
    if effort:
        body["output_config"] = {"effort": effort}

    headers = {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": API_VERSION,
    }

    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                API_URL, headers=headers, json=body, timeout=timeout
            )
        except requests.RequestException as error:
            last = "could not reach the API (%s)" % type(error).__name__
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
            continue

        if response.status_code == 401:
            raise KeyRejected(
                "the API rejected your key (401). Run "
                "'python3 paperfeed.py set-key' to enter a new one."
            )
        if response.status_code == 400:
            raise AIError("the API rejected the request (400): %s" % response.text[:200])
        if response.status_code in RETRYABLE:
            last = "the API was busy (HTTP %d)" % response.status_code
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
            continue
        if response.status_code != 200:
            raise AIError("unexpected HTTP %d: %s" % (response.status_code, response.text[:200]))

        payload = response.json()
        text = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        usage = payload.get("usage", {}) or {}
        return text, {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "model": model,
        }

    raise AIError("%s after %d attempts" % (last or "failed", MAX_ATTEMPTS))


def cost_of(usage):
    """Dollars for one call's usage, or None for a model we have no price for."""
    price = PRICING.get(usage.get("model", ""))
    if not price:
        return None
    return (
        usage.get("input_tokens", 0) / 1_000_000.0 * price["input"]
        + usage.get("output_tokens", 0) / 1_000_000.0 * price["output"]
    )


# Rough token sizes measured against real digests: a title plus a trimmed
# abstract runs ~320 tokens, a one-line verdict ~45.
TOKENS_PER_PAPER_IN = 320
TOKENS_PER_PAPER_OUT = 45
TOKENS_PER_BATCH_OVERHEAD = 260
TRENDS_TOKENS_PER_PAPER = 110
TRENDS_OUTPUT_TOKENS = 3000


def estimate_costs(cfg):
    """What the AI features would cost, per run and per year. Estimates only."""
    out = {}

    ai_cfg = cfg.get("ai", {})
    papers = min(
        int(ai_cfg.get("max_papers_per_run") or 40),
        int(cfg.get("max_per_set") or 30) * max(1, len(cfg.get("keyword_sets") or [])) * 2,
    )
    batch_size = max(1, int(ai_cfg.get("batch_size") or 10))
    batches = max(1, -(-papers // batch_size))
    scoring = {
        "model": ai_cfg.get("model") or DEFAULT_SCORING_MODEL,
        "input_tokens": papers * TOKENS_PER_PAPER_IN + batches * TOKENS_PER_BATCH_OVERHEAD,
        "output_tokens": papers * TOKENS_PER_PAPER_OUT,
    }
    scoring["per_run"] = cost_of(scoring)
    runs_per_year = 365.0 / max(1, int(cfg.get("interval_days") or 3))
    scoring["per_year"] = (
        scoring["per_run"] * runs_per_year if scoring["per_run"] is not None else None
    )
    scoring["papers"] = papers
    out["scoring"] = scoring

    trends_cfg = cfg.get("trends", {})
    count = int(trends_cfg.get("max_papers") or 300)
    report = {
        "model": trends_cfg.get("model") or DEFAULT_TRENDS_MODEL,
        "input_tokens": count * TRENDS_TOKENS_PER_PAPER,
        "output_tokens": TRENDS_OUTPUT_TOKENS,
    }
    report["per_run"] = cost_of(report)
    per_year_runs = 365.0 / max(1, int(trends_cfg.get("interval_days") or 30))
    report["per_year"] = (
        report["per_run"] * per_year_runs if report["per_run"] is not None else None
    )
    report["papers"] = count
    out["trends"] = report
    return out


def _extract_json_array(text):
    """Pull the JSON array out of a reply, tolerating any chat around it."""
    if not text:
        raise AIError("the API returned nothing")
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise AIError("no JSON array in the reply")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise AIError("the reply was not valid JSON (%s)" % error)
    if not isinstance(parsed, list):
        raise AIError("expected a JSON array")
    return parsed


# --------------------------------------------------------------------------
# Per-paper relevance
# --------------------------------------------------------------------------

SCORING_SYSTEM = (
    "You help a working researcher triage new papers. You are blunt and "
    "concrete. Most papers are not relevant to any given researcher; say so "
    "by scoring them low rather than being generous. Never invent details "
    "that are not in the title or abstract you are shown."
)

SCORING_TEMPLATE = """This researcher describes their interests as:

{interests}

Score each paper below from 0 to 10 for how much it matters to THIS person.
10 means drop everything and read it. 0 means irrelevant to them.

{papers}

Reply with ONLY a JSON array, one object per paper, no other text:
[{{"n": <paper number>, "score": <0-10>, "why": "<one sentence, at most 20 \
words, addressed to the researcher, saying why it matters or why not>"}}]"""


def _paper_block(index, paper, abstract_chars=900):
    abstract = " ".join((paper.abstract or "").split())[:abstract_chars]
    return "%d. TITLE: %s\n   ABSTRACT: %s" % (
        index,
        paper.title,
        abstract or "(no abstract available)",
    )


def score_papers(papers, ai_cfg, key, log=None):
    """Attach paper.ai_score and paper.ai_reason where possible.

    Returns (how_many_scored, total_usage, error_message_or_None). Never
    raises: a failure here must not cost you the digest.
    """
    interests = (ai_cfg.get("interests") or "").strip()
    if not interests:
        return 0, {}, "ai.interests is empty, so there is nothing to score against"

    model = ai_cfg.get("model") or DEFAULT_SCORING_MODEL
    limit = int(ai_cfg.get("max_papers_per_run") or 40)
    batch_size = max(1, int(ai_cfg.get("batch_size") or 10))
    candidates = papers[:limit]
    if not candidates:
        return 0, {}, None

    totals = {"input_tokens": 0, "output_tokens": 0, "model": model, "calls": 0}
    scored = 0
    problems = []

    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        listing = "\n\n".join(
            _paper_block(number, paper) for number, paper in enumerate(batch, 1)
        )
        prompt = SCORING_TEMPLATE.format(interests=interests, papers=listing)

        try:
            text, usage = call(
                key, model, SCORING_SYSTEM, prompt, max_tokens=180 * len(batch) + 200
            )
        except KeyRejected:
            raise
        except AIError as error:
            problems.append(str(error))
            continue

        totals["input_tokens"] += usage["input_tokens"]
        totals["output_tokens"] += usage["output_tokens"]
        totals["calls"] += 1

        try:
            rows = _extract_json_array(text)
        except AIError as error:
            problems.append(str(error))
            continue

        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                number = int(row.get("n"))
                value = float(row.get("score"))
            except (TypeError, ValueError):
                continue
            if not 1 <= number <= len(batch):
                continue
            paper = batch[number - 1]
            paper.ai_score = max(0.0, min(10.0, value))
            paper.ai_reason = str(row.get("why") or "").strip()[:300]
            scored += 1

        if log:
            log.info(
                "  AI scored %d/%d papers in this batch", len(rows), len(batch)
            )

    error = "; ".join(problems[:3]) if problems else None
    return scored, totals, error


# --------------------------------------------------------------------------
# The trend report
# --------------------------------------------------------------------------

TRENDS_SYSTEM = (
    "You are a senior researcher writing a short briefing for a colleague on "
    "what has been happening in their field. You are specific and grounded: "
    "every theme you name must be supported by papers in the list you are "
    "given, cited by their DOI. You never invent a paper or a DOI."
)

TRENDS_TEMPLATE = """Below are {count} papers published in the last {months} \
months that match this researcher's interests:

{interests}

Papers:
{papers}

Identify the 3 to 6 themes that genuinely characterise this period. For each,
give a short name, one paragraph explaining what is happening and why it
matters, and 3 to 5 representative papers from the list above.

Also note briefly what seems to be quietening down, if anything is.

Reply with ONLY a JSON array, no other text:
[{{"theme": "<short name>", "summary": "<one paragraph>", \
"papers": [{{"title": "<title>", "doi": "<doi exactly as given>"}}], \
"cooling": "<optional, may be omitted>"}}]"""


def trend_report(papers, trends_cfg, interests, key, log=None):
    """Returns (themes list, usage, error_message_or_None)."""
    if not papers:
        return [], {}, "no papers found in that window"

    model = trends_cfg.get("model") or DEFAULT_TRENDS_MODEL
    limit = int(trends_cfg.get("max_papers") or 300)
    months = int(trends_cfg.get("months") or 3)
    selection = papers[:limit]

    listing = "\n".join(
        "- %s | %s | doi:%s | %s"
        % (
            paper.title,
            paper.venue or paper.source,
            paper.doi or "none",
            " ".join((paper.abstract or "").split())[:300],
        )
        for paper in selection
    )
    prompt = TRENDS_TEMPLATE.format(
        count=len(selection),
        months=months,
        interests=(interests or "(not specified)").strip(),
        papers=listing,
    )

    try:
        text, usage = call(
            key, model, TRENDS_SYSTEM, prompt, max_tokens=8000, effort="medium",
            timeout=300,
        )
    except AIError as error:
        return [], {}, str(error)

    usage["calls"] = 1
    try:
        rows = _extract_json_array(text)
    except AIError as error:
        return [], usage, str(error)

    known_dois = {paper.doi.lower() for paper in selection if paper.doi}
    themes = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("theme"):
            continue
        cited = []
        for entry in row.get("papers") or []:
            if not isinstance(entry, dict):
                continue
            doi = re.sub(r"^doi:", "", str(entry.get("doi", "")).strip().lower())
            # Only keep citations we can trace back to a real fetched paper.
            # A briefing that cites a DOI nobody can open is worse than one
            # that cites nothing.
            if doi and doi in known_dois:
                cited.append({"title": str(entry.get("title", ""))[:250], "doi": doi})
        themes.append(
            {
                "theme": str(row["theme"])[:120],
                "summary": str(row.get("summary", ""))[:2000],
                "papers": cited,
                "cooling": str(row.get("cooling", ""))[:600],
            }
        )
    return themes, usage, None


# --------------------------------------------------------------------------
# Working with your saved library
# --------------------------------------------------------------------------

EXPLAIN_SYSTEM = (
    "You explain a paper to a working researcher who has already read the "
    "title and abstract. Skip preamble and restatement. Be concrete about "
    "method and evidence. If the abstract does not say something, say that "
    "it does not say, rather than filling the gap with a plausible guess."
)

EXPLAIN_TEMPLATE = """{context}Explain this paper.

TITLE: {title}
VENUE: {venue}
ABSTRACT: {abstract}

Write four short labelled sections, no more than two sentences each:

What they did - the actual method, concretely.
Why it matters - what changes if this holds up.
Watch out for - the limitation or missing control you would probe first.
Worth reading if - who should spend the time on the full text.

Plain prose. No bullet characters, no markdown, no headings beyond those four
labels followed by a colon."""

DIRECTIONS_SYSTEM = (
    "You are a senior colleague reading someone's collection of saved papers "
    "and suggesting where the field is open. You are specific and testable: "
    "a direction is only useful if someone could start on it next week. "
    "Ground every direction in papers from the list, cited by DOI. Never "
    "invent a paper."
)

DIRECTIONS_TEMPLATE = """{context}Here are the papers this researcher has chosen to keep:

{papers}

What they keep is a signal about what they care about. Identify 3 to 5
research directions that this collection points toward but that the papers
themselves have not resolved - gaps, unanswered questions, or combinations
nobody in this set has tried.

Be concrete enough to act on. Avoid "more work is needed" phrasing.

Reply with ONLY a JSON array, no other text:
[{{"direction": "<short name>", "why": "<one paragraph: what is open, and \
what in these papers suggests it>", "first_step": "<one concrete experiment \
or analysis to start with>", "papers": ["<doi>", "<doi>"]}}]"""

TROUBLESHOOT_SYSTEM = (
    "You help a researcher with a concrete problem in their own work, using "
    "only the papers they have saved as evidence. You distinguish clearly "
    "between what the papers actually support and what is your own "
    "suggestion. If the saved papers do not address the problem, say so "
    "plainly instead of stretching them to fit - that is a useful answer."
)

TROUBLESHOOT_TEMPLATE = """{context}The researcher's problem, in their words:

{question}

Their saved papers:

{papers}

Answer the problem. Where a saved paper is genuinely relevant, cite it by
DOI and say what it contributes. Where you are reasoning beyond the papers,
label that clearly as your own suggestion rather than something they support.
If none of the papers really bear on this, say that first.

Reply with ONLY a JSON object, no other text:
{{"answer": "<two to four short paragraphs>", \
"suggestions": ["<concrete thing to try>", "..."], \
"papers": ["<doi of a paper you actually used>"], \
"gap": "<what the saved papers do not tell them, or empty>"}}"""


def _context_line(interests):
    interests = (interests or "").strip()
    return "Background on this researcher: %s\n\n" % interests if interests else ""


def _library_listing(papers, abstract_chars=400, limit=80):
    lines = []
    for record in papers[:limit]:
        lines.append(
            "- %s | %s | doi:%s | %s"
            % (
                record.get("title", ""),
                record.get("venue") or record.get("source", ""),
                record.get("doi") or "none",
                " ".join((record.get("abstract") or "").split())[:abstract_chars],
            )
        )
    return "\n".join(lines)


def explain_paper(record, interests, key, model=None):
    """A short, concrete explanation of one saved paper."""
    model = model or DEFAULT_SCORING_MODEL
    prompt = EXPLAIN_TEMPLATE.format(
        context=_context_line(interests),
        title=record.get("title", ""),
        venue=record.get("venue") or record.get("source", ""),
        abstract=" ".join((record.get("abstract") or "").split())[:4000]
        or "(no abstract available - say so rather than guessing)",
    )
    try:
        text, usage = call(key, model, EXPLAIN_SYSTEM, prompt, max_tokens=700)
    except AIError as error:
        return "", {}, str(error)
    return text.strip(), usage, None


def _validated_dois(raw_list, known):
    out = []
    for value in raw_list or []:
        doi = re.sub(r"^doi:", "", str(value).strip().lower())
        if doi and doi in known and doi not in out:
            out.append(doi)
    return out


def research_directions(papers, interests, key, model=None):
    """Where this collection points that the papers have not gone."""
    if not papers:
        return [], {}, "there is nothing saved yet"
    model = model or DEFAULT_TRENDS_MODEL
    prompt = DIRECTIONS_TEMPLATE.format(
        context=_context_line(interests), papers=_library_listing(papers)
    )
    try:
        text, usage = call(
            key, model, DIRECTIONS_SYSTEM, prompt, max_tokens=4000,
            effort="medium", timeout=240,
        )
    except AIError as error:
        return [], {}, str(error)

    try:
        rows = _extract_json_array(text)
    except AIError as error:
        return [], usage, str(error)

    known = {p["doi"].lower() for p in papers if p.get("doi")}
    directions = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("direction"):
            continue
        directions.append(
            {
                "direction": str(row["direction"])[:160],
                "why": str(row.get("why", ""))[:2000],
                "first_step": str(row.get("first_step", ""))[:800],
                "papers": _validated_dois(row.get("papers"), known),
            }
        )
    return directions, usage, None


def troubleshoot(question, papers, interests, key, model=None):
    """Answer a problem in the researcher's own work from their saved papers."""
    question = (question or "").strip()
    if not question:
        return None, {}, "no question was asked"
    if not papers:
        return None, {}, "there is nothing saved yet to answer from"

    model = model or DEFAULT_TRENDS_MODEL
    prompt = TROUBLESHOOT_TEMPLATE.format(
        context=_context_line(interests),
        question=question[:2000],
        papers=_library_listing(papers),
    )
    try:
        text, usage = call(
            key, model, TROUBLESHOOT_SYSTEM, prompt, max_tokens=4000,
            effort="medium", timeout=240,
        )
    except AIError as error:
        return None, {}, str(error)

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        # Better to show prose we did not expect than to lose the answer.
        return {"answer": text.strip(), "suggestions": [], "papers": [], "gap": ""}, usage, None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {"answer": text.strip(), "suggestions": [], "papers": [], "gap": ""}, usage, None

    known = {p["doi"].lower() for p in papers if p.get("doi")}
    return (
        {
            "answer": str(parsed.get("answer", ""))[:4000],
            "suggestions": [str(s)[:400] for s in (parsed.get("suggestions") or [])][:6],
            "papers": _validated_dois(parsed.get("papers"), known),
            "gap": str(parsed.get("gap", ""))[:800],
        },
        usage,
        None,
    )
