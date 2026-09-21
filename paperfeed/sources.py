"""Fetch recent papers from PubMed and from preprint servers.

Each source is one function that takes a keyword set and returns a list of
Paper objects. Adding a third source later means writing one more function
with the same shape; nothing else has to change.

Both APIs are free and need no account.
"""

import html as html_module
import re
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional

import requests

import phrasing
import query

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# NCBI allows 3 requests/second without an API key. We stay well under that.
POLITE_PAUSE_SECONDS = 0.4
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3


class SourceError(Exception):
    """A source could not be reached or returned something unusable."""


@dataclass
class Paper:
    title: str
    authors: List[str] = field(default_factory=list)
    abstract: str = ""
    doi: str = ""
    url: str = ""
    published: str = ""       # when the record appeared - what "new" means
    issued: str = ""          # when it was actually published, for history
    venue: str = ""           # journal name, or preprint server
    source: str = ""          # "PubMed" or "Preprint"
    set_name: str = ""        # which keyword set found it

    # Extra detail, all of it parsed from responses we already fetch.
    free_fulltext: bool = False
    fulltext_url: str = ""
    mesh_terms: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    affiliation: str = ""     # the senior (last) author's institution
    citations: Optional[int] = None   # Europe PMC only; None means unknown
    issn: str = ""            # ISSN-L: the key that finds the journal elsewhere
    pmid: str = ""            # the other key OpenAlex accepts
    publication_types: List[str] = field(default_factory=list)  # Review, etc.

    # Filled in by metrics.py when it is switched on. Empty otherwise, and
    # every reader must cope with it being empty - the digest is written
    # whether or not OpenAlex answered.
    metrics: dict = field(default_factory=dict)

    # Filled in later by relevance.py.
    score: float = 0.0
    score_reasons: List[str] = field(default_factory=list)
    total_concepts: int = 0   # how many concepts the query asks for
    title_concepts: int = 0   # how many of them are in this paper's title

    # Filled in by ai.py, only when AI scoring is switched on.
    ai_score: Optional[float] = None
    ai_reason: str = ""

    def author_line(self, limit=8):
        if not self.authors:
            return ""
        if len(self.authors) <= limit:
            return ", ".join(self.authors)
        return ", ".join(self.authors[:limit]) + ", et al."


def _describe(error):
    """Reduce a library exception to something a reader can act on."""
    if isinstance(error, requests.exceptions.Timeout):
        return "no reply within %d seconds" % TIMEOUT_SECONDS
    if isinstance(error, requests.exceptions.ConnectionError):
        return "could not connect (no network, or the service is down)"
    if isinstance(error, requests.exceptions.HTTPError):
        status = getattr(error.response, "status_code", "?")
        if status == 429:
            return "the service asked us to slow down (HTTP 429)"
        return "the service returned an error (HTTP %s)" % status
    if isinstance(error, ValueError):
        return "the reply was not readable"
    return str(error).split("\n")[0][:160]


def _get(url, params, contact_email, expect="json", require=None):
    """One HTTP GET with retries and a polite pause. Returns parsed JSON or text.

    `require` names a key the reply must contain. Europe PMC answers roughly
    one call in twelve with the body {"version": "6.9"} and nothing else -
    HTTP 200, no results, no error. Without this check that reply is
    indistinguishable from a quiet week, and a whole source silently
    disappears from a run. Measured: 12 identical requests, 11 returned 15
    preprints, one returned nothing at all.
    """
    agent = "PaperFeed/1.0 (literature alerts"
    agent += "; %s)" % contact_email if contact_email else ")"
    headers = {"User-Agent": agent}

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            time.sleep(POLITE_PAUSE_SECONDS)
            response = requests.get(
                url, params=params, headers=headers, timeout=TIMEOUT_SECONDS
            )
            response.raise_for_status()
            if expect != "json":
                return response.text
            payload = response.json()
            if require and require not in payload:
                raise ValueError(
                    "the reply was missing %r - it carried only %s"
                    % (require, ", ".join(sorted(payload)) or "nothing")
                )
            return payload
        except Exception as error:  # network, HTTP status, or bad JSON
            last_error = error
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 ** attempt)  # 2s, then 4s
    raise SourceError(
        "%s after %d attempts" % (_describe(last_error), MAX_ATTEMPTS)
    )


def _window(lookback_days):
    end = date.today()
    return end - timedelta(days=lookback_days), end


_TAG = re.compile(r"<[^>]+>")
_ABSTRACT_LABEL = re.compile(r"<title>\s*abstract\s*</title>", re.IGNORECASE)


def _clean_text(value):
    """Flatten the markup some records carry inside their text fields.

    Europe PMC returns abstracts in JATS XML, so a preprint abstract can begin
    literally "<title>Abstract</title> <p>...". Left alone it would be shown to
    the reader as tag soup.
    """
    text = value or ""
    text = _ABSTRACT_LABEL.sub(" ", text)
    text = re.sub(r"</(p|sec|title|abstract)>", " ", text, flags=re.IGNORECASE)
    text = _TAG.sub("", text)
    text = html_module.unescape(text)
    return " ".join(text.split())


_INSTITUTION_HINTS = (
    "University", "Universit", "Institute", "Institut", "Hospital", "College",
    "Laboratory", "Laboratoire", "School", "Center", "Centre", "Academy",
)


def _institution(affiliation):
    """Pull the recognisable institution out of a long affiliation string.

    Affiliations read "Department of X, Y University, City, Country". The
    department is rarely what you want at a glance; the institution is.
    """
    text = _clean_text(affiliation)
    if not text:
        return ""
    parts = [part.strip() for part in text.split(",") if part.strip()]
    for part in parts:
        if any(hint in part for hint in _INSTITUTION_HINTS):
            return part.rstrip(".")
    return parts[0][:70].rstrip(".") if parts else ""


def _normalize_doi(value):
    if not value:
        return ""
    doi = str(value).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi.strip()


# --------------------------------------------------------------------------
# PubMed
# --------------------------------------------------------------------------

def _bare(term):
    return term.replace('"', "")


def default_field(keyword_set):
    """Where an unscoped term in this set's query is looked for."""
    return "all" if keyword_set.get("fields") == "all" else "ti_ab"


def parsed_query(keyword_set):
    """The set's written query as a tree, or None if it uses the old fields.

    A set can say what it wants either as a boolean expression in 'query' or
    with the older terms / all_of / authors lists. When both are present the
    written query wins, because it is the more precise statement.
    """
    text = (keyword_set.get("query") or "").strip()
    if not text:
        return None
    return query.parse(text, default_field(keyword_set))


def _pubmed_query(keyword_set, notes=None):
    """Build one PubMed query from a keyword set.

        terms    OR'ed together  -> ("a" OR "b")
        all_of   each required   -> AND "c"
        authors  any of them     -> AND ("Baker D"[Author] OR ...)

    A term containing a square bracket is assumed to be deliberate PubMed
    syntax and passed through untouched. Otherwise terms are searched as
    quoted phrases in the title and abstract, which keeps the noise down;
    set "fields": "all" on the keyword set to search everywhere instead.
    """
    tree = parsed_query(keyword_set)
    if tree is not None:
        return query.to_pubmed(tree, notes)

    tag = "" if keyword_set.get("fields") == "all" else "[Title/Abstract]"

    def phrase(term):
        if "[" in term:
            return "(%s)" % term
        return '"%s"%s' % (_bare(term), tag)

    clauses = []
    terms = keyword_set.get("terms") or []
    if terms:
        clauses.append("(%s)" % " OR ".join(phrase(term) for term in terms))
    for term in keyword_set.get("all_of") or []:
        clauses.append(phrase(term))
    authors = keyword_set.get("authors") or []
    if authors:
        clauses.append(
            "(%s)" % " OR ".join('"%s"[Author]' % _bare(name) for name in authors)
        )
    return " AND ".join(clauses)


def _text(node, path):
    found = node.find(path)
    return "".join(found.itertext()).strip() if found is not None else ""


def _pubmed_date(article):
    """Prefer the date the record entered PubMed — that is what 'new' means here."""
    for status in ("pubmed", "entrez", "medline"):
        node = article.find(".//PubMedPubDate[@PubStatus='%s']" % status)
        if node is not None:
            year = _text(node, "Year")
            month = _text(node, "Month") or "1"
            day = _text(node, "Day") or "1"
            if year:
                try:
                    return date(int(year), int(month), int(day)).isoformat()
                except ValueError:
                    return year
    return _text(article, ".//PubDate/Year")


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _issue_date(article):
    """The journal's own publication date.

    Distinct from the date the record entered PubMed: a paper issued in
    December can be indexed in January, so a retrospective timeline built on
    the entry date would put it in the wrong month.
    """
    node = article.find(".//Article/Journal/JournalIssue/PubDate")
    if node is None:
        return ""
    year = _text(node, "Year")
    if not year:
        # e.g. <MedlineDate>2024 Mar-Apr</MedlineDate>
        medline = _text(node, "MedlineDate")
        match = re.search(r"(\d{4})", medline or "")
        if not match:
            return ""
        year = match.group(1)
        month_match = re.search(r"([A-Za-z]{3})", medline)
        month = _MONTHS.get(month_match.group(1).lower(), 1) if month_match else 1
        return "%s-%02d-01" % (year, month)

    raw_month = _text(node, "Month")
    if raw_month.isdigit():
        month = int(raw_month)
    else:
        month = _MONTHS.get(raw_month[:3].lower(), 1) if raw_month else 1
    day = _text(node, "Day")
    day = int(day) if day.isdigit() else 1
    try:
        return date(int(year), month, day).isoformat()
    except ValueError:
        return "%s-%02d-01" % (year, month)


def _parse_pubmed_xml(xml_text, set_name):
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise SourceError("PubMed returned XML we could not parse: %s" % error)

    papers = []
    for article in root.findall(".//PubmedArticle"):
        title = _clean_text(_text(article, ".//ArticleTitle"))
        if not title:
            continue

        abstract = _clean_text(
            " ".join(
                "".join(node.itertext()).strip()
                for node in article.findall(".//Abstract/AbstractText")
            )
        )

        # Same containment rule as the ids above: only this article's authors.
        author_list = article.find("MedlineCitation/Article/AuthorList")
        own_authors = author_list.findall("Author") if author_list is not None else []

        authors = []
        for author in own_authors:
            last = _text(author, "LastName")
            initials = _text(author, "Initials")
            collective = _text(author, "CollectiveName")
            if last:
                authors.append("%s %s" % (last, initials) if initials else last)
            elif collective:
                authors.append(collective)

        # Scope this to the article's OWN id list. './/ArticleId' also matches
        # every id in the reference list - one record here carried 190 of
        # them, 187 belonging to cited papers - and keeping the last of each
        # type silently adopted a random reference's DOI as this paper's
        # identity, breaking its links, its "free full text" badge, and the
        # deduplication that is keyed on DOI.
        ids = {}
        own_ids = article.find("PubmedData/ArticleIdList")
        if own_ids is not None:
            for identifier in own_ids.findall("ArticleId"):
                ids[identifier.get("IdType")] = (identifier.text or "").strip()
        doi = _normalize_doi(ids.get("doi"))
        if not doi:
            for location in article.findall(".//ELocationID"):
                if location.get("EIdType") == "doi":
                    doi = _normalize_doi(location.text)
                    break

        # A PMC identifier means the full text is readable without a
        # subscription, which is the practical question when you are deciding
        # whether to click.
        pmcid = ids.get("pmc", "")
        if pmcid and not pmcid.upper().startswith("PMC"):
            pmcid = "PMC" + pmcid

        mesh_terms = [
            node.text.strip()
            for node in article.findall(".//MeshHeading/DescriptorName")
            if node.text
        ]
        keywords = [
            _clean_text("".join(node.itertext()))
            for node in article.findall(".//KeywordList/Keyword")
        ]

        # Both of these are already in the reply. PublicationType is how a
        # review is told from primary research, and ISSNLinking is the key
        # that finds the journal in other databases. Neither costs a request.
        publication_types = [
            node.text.strip()
            for node in article.findall(
                "MedlineCitation/Article/PublicationTypeList/PublicationType")
            if node.text
        ]
        issn = (
            _text(article, "MedlineCitation/MedlineJournalInfo/ISSNLinking")
            or _text(article, "MedlineCitation/Article/Journal/ISSN")
        )

        # Walk backwards: the senior author is the last one with an address.
        affiliation = ""
        for node in reversed(own_authors):
            found = node.findtext(".//Affiliation")
            if found:
                affiliation = _institution(found)
                break

        pmid = _text(article, ".//PMID")
        url = (
            "https://doi.org/%s" % doi
            if doi
            else "https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid
        )

        papers.append(
            Paper(
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                url=url,
                published=_pubmed_date(article),
                issued=_issue_date(article),
                venue=_text(article, ".//Journal/Title"),
                source="PubMed",
                set_name=set_name,
                issn=issn,
                pmid=pmid,
                publication_types=publication_types,
                free_fulltext=bool(pmcid),
                fulltext_url=(
                    "https://www.ncbi.nlm.nih.gov/pmc/articles/%s/" % pmcid
                    if pmcid
                    else ""
                ),
                mesh_terms=mesh_terms,
                keywords=[word for word in keywords if word],
                affiliation=affiliation,
            )
        )
    return papers


def _pubmed_complaints(result):
    """What PubMed quietly tells you it did not like.

    PubMed never fails a query. It answers HTTP 200 and searches for
    something else, recording what it ignored in warninglist / errorlist
    where nothing ever reads it. One live example from this very config:
    "glioblastoma multi-omics" has never matched a single paper, and every
    run said so in a field PaperFeed threw away.
    """
    found = []
    warnings = result.get("warninglist") or {}
    errors = result.get("errorlist") or {}

    for phrase in warnings.get("quotedphrasesnotfound") or []:
        found.append(
            "PubMed has no record of the phrase %s anywhere in its index, so "
            "that part of the query matched nothing. Check the spelling, or "
            "search for the words separately." % phrase
        )
    for word in warnings.get("phrasesignored") or []:
        found.append("PubMed ignored %r in the query." % word)
    for message in warnings.get("outputmessages") or []:
        # "No items found." is an ordinary empty result, not a complaint.
        # These messages can be about any part of the request, not only the
        # query, so they are passed through rather than interpreted.
        if message.strip().rstrip(".").lower() != "no items found":
            found.append("PubMed said: %s" % message)
    for key in ("phrasesnotfound", "fieldsnotfound"):
        for item in errors.get(key) or []:
            found.append("PubMed did not recognise %r in the query." % item)
    return found


def fetch_pubmed(keyword_set, lookback_days, max_results, contact_email="", stats=None):
    start, end = _window(lookback_days)
    notes = []
    term = _pubmed_query(keyword_set, notes)
    if stats is not None:
        stats["query"] = term
    search = _get(
        EUTILS + "/esearch.fcgi",
        {
            "db": "pubmed",
            "term": term,
            "retmax": max_results,
            "retmode": "json",
            # No "sort": PubMed rejects "date" as a sort schema and says so in
            # a warning nobody was reading. Its default order is PMID
            # descending, and a PMID is assigned when a record enters PubMed -
            # which, with datetype=edat, is exactly the newest-first order the
            # feed wants. Asking for sort=pub_date would order by journal
            # publication date instead, which is a different question.
            "datetype": "edat",  # date added to PubMed
            "mindate": start.strftime("%Y/%m/%d"),
            "maxdate": end.strftime("%Y/%m/%d"),
        },
        contact_email,
    )

    result = search.get("esearchresult", {}) or {}
    ids = result.get("idlist", [])
    notes.extend(_pubmed_complaints(result))
    if stats is not None:
        try:
            stats["available"] = int(result.get("count", 0))
        except (TypeError, ValueError):
            pass
        stats["fetched"] = len(ids)
        stats["notes"] = notes
    if not ids:
        return []

    xml_text = _get(
        EUTILS + "/efetch.fcgi",
        {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
        contact_email,
        expect="text",
    )
    return _parse_pubmed_xml(xml_text, keyword_set["name"])


# --------------------------------------------------------------------------
# Preprints, via Europe PMC
# --------------------------------------------------------------------------

def _europepmc_terms(keyword_set, notes=None, tree=None):
    """The same terms / all_of / authors logic, in Europe PMC's syntax."""
    if tree is None:
        tree = parsed_query(keyword_set)
    if tree is not None:
        return query.to_europepmc(tree, notes)

    everywhere = keyword_set.get("fields") == "all"

    def phrase(term):
        # PubMed field syntax means nothing here. Search the plain text part
        # instead of sending "CRISPR[MeSH Terms]" as a literal phrase, which
        # would quietly match nothing at all.
        bare = _bare(term.split("[")[0].strip() if "[" in term else term)
        if not bare:
            return ""
        if everywhere:
            return '"%s"' % bare
        return '(TITLE:"%s" OR ABSTRACT:"%s")' % (bare, bare)

    clauses = []
    rendered = [phrase(term) for term in (keyword_set.get("terms") or [])]
    rendered = [part for part in rendered if part]
    if rendered:
        clauses.append("(%s)" % " OR ".join(rendered))
    for term in keyword_set.get("all_of") or []:
        part = phrase(term)
        if part:
            clauses.append(part)
    authors = keyword_set.get("authors") or []
    if authors:
        clauses.append(
            "(%s)" % " OR ".join('AUTH:"%s"' % _bare(name) for name in authors)
        )
    return " AND ".join(clauses)


def _preprint_query(keyword_set, start, end, notes=None):
    """SRC:PPR restricts results to preprints: bioRxiv, medRxiv, arXiv,
    Research Square and others, all searchable through one endpoint.

    Returns None when the query cannot match a preprint at all - which
    happens when it requires a MeSH heading, since preprints are never
    MeSH-indexed. Saying so beats running a search that must return nothing.
    """
    tree = parsed_query(keyword_set)
    if tree is not None:
        tree = query.for_preprints(tree, notes)
        if tree is None:
            return None

    terms = _europepmc_terms(keyword_set, notes, tree)
    if not terms.startswith("("):
        terms = "(%s)" % terms
    return "%s AND (SRC:PPR) AND (FIRST_PDATE:[%s TO %s])" % (
        terms, start.isoformat(), end.isoformat()
    )


def _europepmc_fulltext(item):
    """(free_fulltext, url) - prefer a PDF, fall back to any free copy."""
    urls = (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []
    free = [entry for entry in urls if entry.get("availability") == "Free"]
    is_open = item.get("isOpenAccess") == "Y" or bool(free)
    for entry in free:
        if entry.get("documentStyle") == "pdf":
            return is_open, entry.get("url", "")
    if free:
        return is_open, free[0].get("url", "")
    return is_open, ""


def _europepmc_senior_affiliation(item):
    """The last author's institution, falling back to the record's own."""
    authors = (item.get("authorList") or {}).get("author") or []
    for author in reversed(authors):
        details = author.get("authorAffiliationDetailsList") or {}
        entries = details.get("authorAffiliation") or []
        if entries and entries[0].get("affiliation"):
            return _institution(entries[0]["affiliation"])
    return _institution(item.get("affiliation", ""))


def _preprint_server(item):
    """Which preprint server this came from: bioRxiv, medRxiv, Research Square...

    Europe PMC files it under bookOrReportDetails, not the top-level publisher
    field. Returns "" rather than a placeholder, so the digest simply omits it
    when it is genuinely unknown.
    """
    details = item.get("bookOrReportDetails") or {}
    name = details.get("publisher") or item.get("publisher") or ""
    return str(name).strip()


def _parse_europepmc(items, set_name):
    """Turn Europe PMC result items into Paper objects."""
    papers = []
    for item in items:
        title = _clean_text(item.get("title")).rstrip(".")
        if not title:
            continue

        authors = []
        author_list = (item.get("authorList") or {}).get("author") or []
        for author in author_list:
            name = author.get("fullName") or author.get("collectiveName")
            if name:
                authors.append(name)
        if not authors and item.get("authorString"):
            authors = [
                part.strip()
                for part in item["authorString"].rstrip(".").split(",")
                if part.strip()
            ]

        free_fulltext, fulltext_url = _europepmc_fulltext(item)
        mesh_terms = [
            entry.get("descriptorName", "")
            for entry in (item.get("meshHeadingList") or {}).get("meshHeading") or []
            if entry.get("descriptorName")
        ]
        keywords = (item.get("keywordList") or {}).get("keyword") or []

        citations = item.get("citedByCount")
        try:
            citations = int(citations) if citations is not None else None
        except (TypeError, ValueError):
            citations = None

        publication_types = [
            str(entry) for entry in
            (item.get("pubTypeList") or {}).get("pubType") or [] if entry
        ]
        journal = (item.get("journalInfo") or {}).get("journal") or {}
        issn = (journal.get("issn") or journal.get("essn") or "").strip()
        pmid = str(item.get("pmid") or "").strip()

        doi = _normalize_doi(item.get("doi"))
        identifier = item.get("id") or ""
        url = (
            "https://doi.org/%s" % doi
            if doi
            else "https://europepmc.org/article/PPR/%s" % identifier
        )

        papers.append(
            Paper(
                title=title,
                authors=authors,
                abstract=_clean_text(item.get("abstractText")),
                doi=doi,
                url=url,
                published=(item.get("firstPublicationDate") or "").strip(),
                issued=(item.get("firstPublicationDate") or "").strip(),
                venue=_preprint_server(item),
                source="Preprint",
                set_name=set_name,
                free_fulltext=free_fulltext,
                fulltext_url=fulltext_url,
                mesh_terms=mesh_terms,
                keywords=[str(word) for word in keywords if word],
                affiliation=_europepmc_senior_affiliation(item),
                citations=citations,
                issn=issn,
                pmid=pmid,
                publication_types=publication_types,
            )
        )
    return papers


def fetch_preprints(keyword_set, lookback_days, max_results, contact_email="", stats=None):
    start, end = _window(lookback_days)
    notes = []
    term = _preprint_query(keyword_set, start, end, notes)
    if stats is not None:
        stats["notes"] = notes
        stats["query"] = term or ""
    if term is None:
        # Not an error and not an empty week: this query can never match a
        # preprint, and the note already explains why.
        return []
    payload = _get(
        EUROPEPMC,
        {
            "query": term,
            "format": "json",
            "resultType": "core",
            "pageSize": min(max_results, 100),   # API maximum per page
            "sort": "P_PDATE_D desc",
        },
        contact_email,
        require="resultList",
    )

    items = payload.get("resultList", {}).get("result", [])[:max_results]
    if stats is not None:
        if "hitCount" not in payload:
            # Europe PMC leaves the total out of some replies. Reading a
            # missing total as zero would quietly hide the "only the newest N
            # were fetched" warning, so say that the total is unknown.
            notes.append(
                "Europe PMC did not report how many papers matched, so the "
                "total below counts only what was fetched."
            )
        else:
            try:
                stats["available"] = int(payload.get("hitCount", 0))
            except (TypeError, ValueError):
                pass
        stats["fetched"] = len(items)

    return _parse_europepmc(items, keyword_set["name"])


SOURCES = {
    "pubmed": ("PubMed", fetch_pubmed),
    "preprints": ("Preprints", fetch_preprints),
}


# --------------------------------------------------------------------------
# Counting without fetching
# --------------------------------------------------------------------------
#
# Asking "how many would this match?" is the cheapest way to see whether a
# query says what you meant. Both of these fetch no papers at all.

def count_pubmed(keyword_set, lookback_days, contact_email=""):
    """Returns (how_many, sent_query, complaints)."""
    start, end = _window(lookback_days)
    notes = []
    term = _pubmed_query(keyword_set, notes)
    payload = _get(
        EUTILS + "/esearch.fcgi",
        {
            "db": "pubmed",
            "term": term,
            "retmax": 0,
            "retmode": "json",
            "datetype": "edat",
            "mindate": start.strftime("%Y/%m/%d"),
            "maxdate": end.strftime("%Y/%m/%d"),
        },
        contact_email,
    )
    result = payload.get("esearchresult", {}) or {}
    notes.extend(_pubmed_complaints(result))
    try:
        total = int(result.get("count", 0))
    except (TypeError, ValueError):
        total = 0
    return total, term, notes


def count_preprints(keyword_set, lookback_days, contact_email=""):
    """Returns (how_many, sent_query, complaints). how_many is None when the
    query cannot match a preprint at all."""
    start, end = _window(lookback_days)
    notes = []
    term = _preprint_query(keyword_set, start, end, notes)
    if term is None:
        return None, "", notes
    payload = _get(
        EUROPEPMC,
        {"query": term, "format": "json", "pageSize": 1},
        contact_email,
        require="resultList",
    )
    if "hitCount" not in payload:
        notes.append("Europe PMC did not report a total for this query.")
        return 0, term, notes
    try:
        return int(payload.get("hitCount", 0)), term, notes
    except (TypeError, ValueError):
        return 0, term, notes


def fetch_all(keyword_set, cfg):
    """Run every enabled source for one keyword set.

    Returns (papers, errors). A source that fails does not stop the others —
    the failure is reported in the digest so a short list is never mistaken
    for a quiet week.
    """
    enabled = dict(cfg["sources"])
    override = keyword_set.get("sources")
    if isinstance(override, dict):
        enabled.update(override)      # e.g. this set is preprints-only

    papers = []
    errors = []
    notices = []
    for key, (label, fetcher) in SOURCES.items():
        if not enabled.get(key):
            continue
        stats = {}
        try:
            papers.extend(
                fetcher(
                    keyword_set,
                    cfg["lookback_days"],
                    cfg["max_per_set"],
                    cfg.get("contact_email", ""),
                    stats,
                )
            )
        except Exception as error:
            errors.append("%s / %s: %s" % (label, keyword_set["name"], error))
            continue

        for note in stats.get("notes") or []:
            notices.append("%s / %s: %s" % (label, keyword_set["name"], note))

        # Say so when a cap threw papers away. Silently keeping the newest N
        # of a much larger set is how a keyword set quietly stops working.
        available = stats.get("available", 0)
        fetched = stats.get("fetched", 0)
        if available and fetched and available > fetched:
            notices.append(
                "%s: %r matched %s but only the %d most recent were "
                "fetched. Raise max_per_set, or narrow the set with all_of "
                "or exclude."
                % (label, keyword_set["name"], phrasing.count(available, "paper"), fetched)
            )
    return papers, errors, notices


# --------------------------------------------------------------------------
# Searching an arbitrary date range
# --------------------------------------------------------------------------
#
# The feed asks "what is new to me?", so it searches by the date a record
# entered PubMed. A retrospective search asks "what was published then?",
# which is a different question and a different date field - so these
# functions default to the publication date instead.
#
# They also page. A single esearch cannot reach past 10,000 results, and a
# busy year of a broad term goes well beyond that, so the caller slices the
# range into months and these page within each slice.

PUBMED_PAGE = 1000          # ids per esearch page
PUBMED_FETCH_CHUNK = 200    # ids per efetch call
EUROPEPMC_PAGE = 100        # the API maximum


def fetch_pubmed_range(keyword_set, start, end, contact_email="",
                       datetype="pdat", progress=None):
    """Every PubMed paper published in [start, end]. Returns (papers, total)."""
    search = _pubmed_query(keyword_set)
    ids = []
    total = 0
    offset = 0

    while True:
        search = _get(
            EUTILS + "/esearch.fcgi",
            {
                "db": "pubmed",
                "term": search,
                "retmode": "json",
                "datetype": datetype,
                "mindate": start.strftime("%Y/%m/%d"),
                "maxdate": end.strftime("%Y/%m/%d"),
                "retstart": offset,
                "retmax": PUBMED_PAGE,
            },
            contact_email,
        )
        result = search.get("esearchresult", {}) or {}
        page = result.get("idlist") or []
        try:
            total = int(result.get("count", 0))
        except (TypeError, ValueError):
            total = total or len(page)
        ids.extend(page)
        offset += len(page)
        if progress:
            progress("PubMed", len(ids), total)
        # esearch refuses retstart + retmax beyond 10,000.
        if not page or offset >= total or offset + PUBMED_PAGE > 10000:
            break

    papers = []
    for index in range(0, len(ids), PUBMED_FETCH_CHUNK):
        chunk = ids[index : index + PUBMED_FETCH_CHUNK]
        xml_text = _get(
            EUTILS + "/efetch.fcgi",
            {"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"},
            contact_email,
            expect="text",
        )
        papers.extend(_parse_pubmed_xml(xml_text, keyword_set["name"]))
    return papers, total


def fetch_preprints_range(keyword_set, start, end, contact_email="",
                          progress=None):
    """Every preprint first published in [start, end]. Returns (papers, total)."""
    search = _preprint_query(keyword_set, start, end)
    if search is None:
        # The query needs a MeSH heading, so no preprint can match it.
        return [], 0
    papers = []
    cursor = "*"
    total = 0

    while True:
        payload = _get(
            EUROPEPMC,
            {
                "query": search,
                "format": "json",
                "resultType": "core",
                "pageSize": EUROPEPMC_PAGE,
                "cursorMark": cursor,
                "sort": "P_PDATE_D desc",
            },
            contact_email,
            require="resultList",
        )
        try:
            total = int(payload.get("hitCount", 0))
        except (TypeError, ValueError):
            total = total or 0
        items = (payload.get("resultList", {}) or {}).get("result", []) or []
        papers.extend(_parse_europepmc(items, keyword_set["name"]))
        if progress:
            progress("Preprints", len(papers), total)

        following = payload.get("nextCursorMark")
        # The API repeats the cursor on the last page, which is how it says
        # "that is everything".
        if not items or not following or following == cursor:
            break
        cursor = following
    return papers, total
