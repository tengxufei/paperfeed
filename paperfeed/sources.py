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
    published: str = ""       # YYYY-MM-DD where known
    venue: str = ""           # journal name, or preprint server
    source: str = ""          # "PubMed" or "Preprint"
    set_name: str = ""        # which keyword set found it

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


def _get(url, params, contact_email, expect="json"):
    """One HTTP GET with retries and a polite pause. Returns parsed JSON or text."""
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
            return response.json() if expect == "json" else response.text
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

def _pubmed_query(terms):
    """OR the terms together.

    A term containing a square bracket is assumed to be deliberate PubMed
    syntax and passed through untouched; anything else is searched as a quoted
    phrase in the title and abstract, which keeps the noise down.
    """
    parts = []
    for term in terms:
        if "[" in term:
            parts.append("(%s)" % term)
        else:
            parts.append('"%s"[Title/Abstract]' % term.replace('"', ""))
    return " OR ".join(parts)


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

        authors = []
        for author in article.findall(".//Author"):
            last = _text(author, "LastName")
            initials = _text(author, "Initials")
            collective = _text(author, "CollectiveName")
            if last:
                authors.append("%s %s" % (last, initials) if initials else last)
            elif collective:
                authors.append(collective)

        doi = ""
        for identifier in article.findall(".//ArticleId"):
            if identifier.get("IdType") == "doi":
                doi = _normalize_doi(identifier.text)
                break
        if not doi:
            for location in article.findall(".//ELocationID"):
                if location.get("EIdType") == "doi":
                    doi = _normalize_doi(location.text)
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
                venue=_text(article, ".//Journal/Title"),
                source="PubMed",
                set_name=set_name,
            )
        )
    return papers


def fetch_pubmed(keyword_set, lookback_days, max_results, contact_email=""):
    start, end = _window(lookback_days)
    search = _get(
        EUTILS + "/esearch.fcgi",
        {
            "db": "pubmed",
            "term": _pubmed_query(keyword_set["terms"]),
            "retmax": max_results,
            "retmode": "json",
            "sort": "date",
            "datetype": "edat",  # date added to PubMed
            "mindate": start.strftime("%Y/%m/%d"),
            "maxdate": end.strftime("%Y/%m/%d"),
        },
        contact_email,
    )

    ids = search.get("esearchresult", {}).get("idlist", [])
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

def _preprint_query(terms, start, end):
    """SRC:PPR restricts results to preprints: bioRxiv, medRxiv, arXiv,
    Research Square and others, all searchable through one endpoint."""
    joined = " OR ".join('"%s"' % term.replace('"', "") for term in terms)
    return '(%s) AND (SRC:PPR) AND (FIRST_PDATE:[%s TO %s])' % (
        joined,
        start.isoformat(),
        end.isoformat(),
    )


def _preprint_server(item):
    """Which preprint server this came from: bioRxiv, medRxiv, Research Square...

    Europe PMC files it under bookOrReportDetails, not the top-level publisher
    field. Returns "" rather than a placeholder, so the digest simply omits it
    when it is genuinely unknown.
    """
    details = item.get("bookOrReportDetails") or {}
    name = details.get("publisher") or item.get("publisher") or ""
    return str(name).strip()


def fetch_preprints(keyword_set, lookback_days, max_results, contact_email=""):
    start, end = _window(lookback_days)
    payload = _get(
        EUROPEPMC,
        {
            "query": _preprint_query(keyword_set["terms"], start, end),
            "format": "json",
            "resultType": "core",
            "pageSize": min(max_results, 100),
            "sort": "P_PDATE_D desc",
        },
        contact_email,
    )

    papers = []
    for item in payload.get("resultList", {}).get("result", [])[:max_results]:
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
                venue=_preprint_server(item),
                source="Preprint",
                set_name=keyword_set["name"],
            )
        )
    return papers


SOURCES = {
    "pubmed": ("PubMed", fetch_pubmed),
    "preprints": ("Preprints", fetch_preprints),
}


def fetch_all(keyword_set, cfg):
    """Run every enabled source for one keyword set.

    Returns (papers, errors). A source that fails does not stop the others —
    the failure is reported in the digest so a short list is never mistaken
    for a quiet week.
    """
    papers = []
    errors = []
    for key, (label, fetcher) in SOURCES.items():
        if not cfg["sources"].get(key):
            continue
        try:
            papers.extend(
                fetcher(
                    keyword_set,
                    cfg["lookback_days"],
                    cfg["max_per_set"],
                    cfg.get("contact_email", ""),
                )
            )
        except Exception as error:
            errors.append("%s / %s: %s" % (label, keyword_set["name"], error))
    return papers, errors
