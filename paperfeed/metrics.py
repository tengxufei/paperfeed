"""Signals that help you triage a paper, each one labelled with its source.

WHERE EVERY NUMBER COMES FROM, and what may be done with it
-----------------------------------------------------------

OpenAlex (api.openalex.org) - free, no account, no key. The data is released
    under CC0: public domain, redistributable, no permission needed.
    https://github.com/ourresearch/openalex-docs/blob/main/license.md
    Anonymous use is metered. Measured from the reply headers on 2026-09-21:
    X-RateLimit-Limit 1000, resetting after about 11.5 hours, and ONE credit
    per request no matter how many papers that request asks about. Adding a
    mailto does not change the allowance, so this module batches hard.
    Supplies: citation count, year-normalised percentile, FWCI, open-access
    status and PDF link, work type, retraction flag, and journal-level
    2-year mean citedness, h-index and works count.

Europe PMC - free, no key. Already fetched for preprints; supplies
    citedByCount and full-text links for those records at no extra cost.

PubMed - free. PublicationType (review vs primary research) and ISSN-L
    arrive inside XML PaperFeed already downloads for every paper.

Journal Impact Factor - NOT HERE, and not obtainable honestly. The JIF is
    Clarivate's, published in Journal Citation Reports behind a paywall, and
    redistributing it is not permitted. PaperFeed ships no JIF table and
    scrapes no site for one. If you have licensed access, point
    metrics.journal_table at your own export: the file is read from your
    disk, never fetched, and never sent anywhere.

SJR / Scimago quartiles - also NOT fetched. Scimago publishes no API, the
    data derives from Elsevier's Scopus, and scimagojr.com answered an
    automated request with HTTP 403. Same route as the JIF: your own file,
    or nothing.

A WARNING ABOUT THE JOURNAL NUMBERS
-----------------------------------
OpenAlex's 2-year mean citedness is computed over everything a journal
publishes - meeting abstracts, errata and editorials included. Journals that
run large abstract supplements therefore read far below their reputation.
Checked live on 2026-09-21:

    Cell                40.78        26,944 works
    Science             21.01       394,263
    Nature              18.92       448,700
    PNAS                 8.56       172,928
    PLOS ONE             3.27       342,287
    Neuro-Oncology       1.14        30,090   <- SNO meeting abstracts
    Cancer Research      0.58       159,128   <- same problem, worse

Neuro-Oncology is not a weak journal; it publishes thousands of conference
abstracts that nobody cites. This is why nothing here is called an Impact
Factor, why the works count is always shown beside the number, and why no
ranking or sort anywhere in PaperFeed uses it.

CITATIONS AND NEW PAPERS
------------------------
A paper published last week has no citations because it is new, not because
it is weak. Below metrics.new_paper_days the count is suppressed entirely
and replaced with "too new to have citations" - never with a zero. Citation
counts never enter the relevance score.
"""

import csv
import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta

import requests

OPENALEX = "https://api.openalex.org"

# OpenAlex refuses more than 100 values in one OR filter. Fifty keeps the
# URL comfortable and still costs a single credit.
BATCH = 50
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
POLITE_PAUSE_SECONDS = 0.2

WORK_FIELDS = (
    "doi,ids,cited_by_count,cited_by_percentile_year,fwci,type,open_access,"
    "is_retracted,primary_location,publication_date"
)
SOURCE_FIELDS = (
    "id,display_name,issn_l,issn,summary_stats,works_count,is_in_doaj,is_oa,"
    "apc_usd,host_organization_name,type"
)


class Budget:
    """What is left of the anonymous OpenAlex allowance.

    OpenAlex reports the remaining credits on every reply. Reading them means
    the tool can stop while it still has room rather than being cut off, and
    can say how far it got instead of quietly enriching half the digest.
    """

    def __init__(self, floor=50):
        self.floor = floor
        self.remaining = None
        self.limit = None
        self.requests_made = 0
        self.stopped = False

    def note(self, headers):
        for key, attribute in (("X-RateLimit-Remaining", "remaining"),
                               ("X-RateLimit-Limit", "limit")):
            try:
                setattr(self, attribute, int(headers.get(key)))
            except (TypeError, ValueError):
                pass
        self.requests_made += 1

    def exhausted(self):
        if self.remaining is None:
            return False
        if self.remaining <= self.floor:
            self.stopped = True
        return self.stopped


def _get(path, params, contact_email, budget):
    """One OpenAlex request, with retries. Returns parsed JSON."""
    agent = "PaperFeed/1.0 (literature alerts"
    agent += "; mailto:%s)" % contact_email if contact_email else ")"
    if contact_email:
        params = dict(params, mailto=contact_email)

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            time.sleep(POLITE_PAUSE_SECONDS)
            response = requests.get(
                OPENALEX + path, params=params,
                headers={"User-Agent": agent}, timeout=TIMEOUT_SECONDS,
            )
            budget.note(response.headers)
            response.raise_for_status()
            payload = response.json()
            if "results" not in payload:
                raise ValueError("the reply had no results list")
            return payload
        except Exception as error:
            last_error = error
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
    raise RuntimeError(str(last_error).split("\n")[0][:200])


# --------------------------------------------------------------------------
# Remembering what we already asked
# --------------------------------------------------------------------------

class Cache:
    """A small SQLite table so the same paper is not looked up twice.

    Paper-level numbers move slowly and journal-level numbers move about once
    a year, so the two have very different lifetimes. With a month-long
    journal cache a steady set of journals costs a handful of requests a
    month rather than one per paper per run.
    """

    def __init__(self, path):
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            " kind TEXT NOT NULL, key TEXT NOT NULL, fetched_at TEXT NOT NULL,"
            " payload TEXT NOT NULL, PRIMARY KEY (kind, key))"
        )
        self.connection.commit()

    def get(self, kind, key, max_age_days):
        row = self.connection.execute(
            "SELECT fetched_at, payload FROM entries WHERE kind=? AND key=?",
            (kind, key),
        ).fetchone()
        if not row:
            return None
        try:
            age = datetime.utcnow() - datetime.fromisoformat(row[0])
        except (TypeError, ValueError):
            return None
        if age > timedelta(days=max_age_days):
            return None
        try:
            return json.loads(row[1])
        except ValueError:
            return None

    def put(self, kind, key, payload):
        self.connection.execute(
            "INSERT OR REPLACE INTO entries (kind, key, fetched_at, payload)"
            " VALUES (?, ?, ?, ?)",
            (kind, key, datetime.utcnow().isoformat(), json.dumps(payload)),
        )

    def commit(self):
        self.connection.commit()

    def close(self):
        try:
            self.connection.commit()
            self.connection.close()
        except sqlite3.Error:
            pass


# --------------------------------------------------------------------------
# Your own journal table
# --------------------------------------------------------------------------

def _tidy_issn(value):
    text = "".join(ch for ch in str(value or "").strip().upper()
                   if ch.isdigit() or ch == "X")
    if len(text) != 8:
        return ""
    return text[:4] + "-" + text[4:]


class JournalTable:
    """Numbers you licensed yourself, read from a file on your disk.

    This exists so that a licensed Impact Factor can be shown without
    PaperFeed ever fetching, shipping or redistributing one. The file is
    yours, it is read locally, and nothing in it leaves this machine.
    """

    def __init__(self, settings):
        self.rows = {}
        self.label = (settings.get("label") or "your journal table").strip()
        self.columns = dict(settings.get("value_columns") or {})
        self.error = ""
        self.misses = 0

        path = os.path.expanduser((settings.get("path") or "").strip())
        if not path:
            return
        if not os.path.exists(path):
            self.error = "metrics.journal_table.path points at %s, which does not exist" % path
            return

        issn_column = (settings.get("issn_column") or "ISSN").strip()
        try:
            with open(path, newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    self.error = "%s has no header row" % path
                    return
                if issn_column not in reader.fieldnames:
                    self.error = (
                        "%s has no column called %r. Its columns are: %s"
                        % (path, issn_column, ", ".join(reader.fieldnames))
                    )
                    return
                missing = [name for name in self.columns.values()
                           if name not in reader.fieldnames]
                if missing:
                    self.error = (
                        "%s has no column(s) called %s. Its columns are: %s"
                        % (path, ", ".join(repr(m) for m in missing),
                           ", ".join(reader.fieldnames))
                    )
                    return
                for row in reader:
                    key = _tidy_issn(row.get(issn_column))
                    if key:
                        self.rows[key] = row
        except (OSError, csv.Error, UnicodeDecodeError) as error:
            self.error = "could not read %s: %s" % (path, error)

    def active(self):
        return bool(self.rows) and not self.error

    def lookup(self, issns):
        for issn in issns:
            row = self.rows.get(_tidy_issn(issn))
            if row:
                return {
                    label: (row.get(column) or "").strip()
                    for label, column in self.columns.items()
                    if (row.get(column) or "").strip()
                }
        self.misses += 1
        return {}


# --------------------------------------------------------------------------
# Reading OpenAlex replies
# --------------------------------------------------------------------------

# OpenAlex assembles a record over several days. A paper indexed within
# hours of reaching PubMed often arrives as a stub: primary_location pointing
# at PubMed itself rather than the publisher, and open_access reported as
# "closed" simply because nobody has resolved it yet. That is a record still
# being written, not a finding about the paper.
#
# Caching such a stub for the full week hides the free PDF during exactly the
# week the paper is new enough to appear in a digest, so an unsettled record
# is re-checked the next day instead. A positive OA verdict is never doubted;
# OpenAlex does not take those back.
PROVISIONAL_DAYS = 60          # how recent a paper must be to be doubted
PROVISIONAL_CACHE_DAYS = 1     # how long a doubted record is trusted
STUB_SOURCES = {"", "pubmed", "pubmed central", "europe pmc"}


def _provisional(summary):
    """True when this cached record looks half-assembled rather than settled."""
    summary = summary or {}
    try:
        issued = date.fromisoformat((summary.get("published") or "")[:10])
    except (TypeError, ValueError):
        return False
    if (date.today() - issued).days > PROVISIONAL_DAYS:
        return False
    if (summary.get("journal_name") or "").strip().lower() in STUB_SOURCES:
        return True
    return not summary.get("is_oa")


def _work_summary(work):
    access = work.get("open_access") or {}
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    percentile = work.get("cited_by_percentile_year") or {}
    return {
        "citations": work.get("cited_by_count"),
        "citations_source": "OpenAlex",
        "percentile_min": percentile.get("min"),
        "fwci": work.get("fwci"),
        "type": work.get("type") or "",
        "is_oa": access.get("is_oa"),
        "oa_url": access.get("oa_url") or "",
        "oa_status": access.get("oa_status") or "",
        "retracted": bool(work.get("is_retracted")),
        "journal_name": source.get("display_name") or "",
        "journal_issn": source.get("issn_l") or "",
        "published": work.get("publication_date") or "",
    }


def _source_summary(record):
    stats = record.get("summary_stats") or {}
    return {
        "name": record.get("display_name") or "",
        "issn_l": record.get("issn_l") or "",
        "issn": list(record.get("issn") or []),
        "two_year_mean_citedness": stats.get("2yr_mean_citedness"),
        "h_index": stats.get("h_index"),
        "works_count": record.get("works_count"),
        "in_doaj": record.get("is_in_doaj"),
        "fully_oa": record.get("is_oa"),
        "apc_usd": record.get("apc_usd"),
        "publisher": record.get("host_organization_name") or "",
        "source": "OpenAlex",
    }


def _chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _usable_doi(doi):
    """A DOI safe to put inside an OR filter.

    Commas and pipes separate values in an OpenAlex filter, so a DOI
    containing either cannot be batched. They are vanishingly rare, but
    smuggling one in would silently corrupt the whole batch.
    """
    return bool(doi) and "|" not in doi and "," not in doi


# --------------------------------------------------------------------------
# The one function the rest of PaperFeed calls
# --------------------------------------------------------------------------

def enrich(papers, cfg, log=None):
    """Attach paper.metrics to as many papers as the budget allows.

    Never raises. Metrics are a convenience laid on top of a digest that has
    to be produced whether or not any of this works, so every failure here
    is recorded and reported, and none of them stops a run.

    Returns a report dict describing exactly what happened, which the digest
    prints. Enriching 180 of 300 papers and saying so is fine; enriching 180
    and implying 300 is not.
    """
    report = {
        "enabled": False, "asked": 0, "enriched": 0, "no_identifier": 0,
        "rechecked": 0,
        "journals": 0, "budget_stopped": False, "requests": 0,
        "remaining": None, "errors": [], "table": "", "table_error": "",
        "table_misses": 0, "note": "", "papers": 0, "fetched_now": 0,
    }
    settings = cfg.get("metrics") or {}
    if not settings.get("enabled"):
        return report
    report["enabled"] = True

    papers = list(papers or [])
    for paper in papers:
        paper.metrics = {}
    report["papers"] = len(papers)
    if not papers:
        return report

    new_paper_days = int(settings.get("new_paper_days", 90) or 0)
    budget = Budget(int(settings.get("min_credits", 50) or 0))
    contact = (settings.get("mailto") or cfg.get("contact_email") or "").strip()

    cache = None
    try:
        cache = Cache(settings.get("path")
                      or os.path.join(os.path.dirname(cfg.get("config_path", ".")),
                                      "metrics.db"))
        _enrich_works(papers, settings, cache, budget, contact, report, log)
        _enrich_journals(papers, settings, cache, budget, contact, report, log)
    except Exception as error:      # a convenience must never break a run
        report["errors"].append(str(error)[:200])
        if log:
            log.warning("  ! metrics: %s", error)
    finally:
        if cache is not None:
            cache.close()

    _apply_journal_table(papers, settings, report)
    _mark_new_papers(papers, new_paper_days)

    report["requests"] = budget.requests_made
    report["remaining"] = budget.remaining
    report["budget_stopped"] = budget.stopped
    return report


def _enrich_works(papers, settings, cache, budget, contact, report, log):
    max_age = int(settings.get("work_cache_days", 7) or 7)
    wanted = {}

    for paper in papers:
        doi = (paper.doi or "").strip().lower()
        key = "doi:%s" % doi if _usable_doi(doi) else (
            "pmid:%s" % paper.pmid if getattr(paper, "pmid", "") else "")
        if not key:
            report["no_identifier"] += 1
            continue
        cached = cache.get("work", key, max_age)
        if cached is not None and _provisional(cached):
            # Trust an unsettled record for a day, not for a week.
            cached = cache.get("work", key, PROVISIONAL_CACHE_DAYS)
            if cached is None:
                report["rechecked"] += 1
        if cached is not None:
            paper.metrics.update(cached)
            report["enriched"] += 1
            continue
        wanted.setdefault(key, []).append(paper)

    report["asked"] = len(wanted)
    by_prefix = {"doi": [], "pmid": []}
    for key in wanted:
        prefix, _, value = key.partition(":")
        by_prefix[prefix].append(value)

    satisfied = set()
    for prefix, values in by_prefix.items():
        # A work returned by the DOI batch is indexed under BOTH its doi: and
        # pmid: keys, so a paper known only by PMID is already enriched by
        # the time the PMID pass runs. Asking again spent a credit against a
        # metered allowance and counted the same paper twice, which is how
        # "figures for 4 of the 2 papers below" got printed.
        values = [value for value in values
                  if "%s:%s" % (prefix, value) not in satisfied]
        for chunk in _chunks(values, BATCH):
            if budget.exhausted():
                if log:
                    log.info("  metrics: OpenAlex allowance nearly used up, "
                             "stopping with %s credits left", budget.remaining)
                return
            try:
                payload = _get("/works",
                               {"filter": "%s:%s" % (prefix, "|".join(chunk)),
                                "per-page": BATCH, "select": WORK_FIELDS},
                               contact, budget)
            except Exception as error:
                report["errors"].append("OpenAlex works lookup: %s" % error)
                if log:
                    log.warning("  ! metrics: %s", error)
                continue

            found = {}
            for work in payload.get("results") or []:
                summary = _work_summary(work)
                doi = (work.get("doi") or "").replace("https://doi.org/", "").lower()
                if doi:
                    found["doi:%s" % doi] = summary
                pmid_url = ((work.get("ids") or {}).get("pmid") or "")
                if pmid_url:
                    found["pmid:%s" % pmid_url.rstrip("/").split("/")[-1]] = summary

            for key, summary in found.items():
                cache.put("work", key, summary)
                if key in satisfied:
                    continue
                satisfied.add(key)
                for paper in wanted.get(key, []):
                    paper.metrics.update(summary)
                    report["enriched"] += 1
                    report["fetched_now"] += 1
            cache.commit()


def _enrich_journals(papers, settings, cache, budget, contact, report, log):
    max_age = int(settings.get("journal_cache_days", 30) or 30)
    wanted = {}

    for paper in papers:
        issn = _tidy_issn(paper.issn) or _tidy_issn(
            paper.metrics.get("journal_issn"))
        if not issn:
            continue
        paper.metrics["issn_l"] = issn
        cached = cache.get("journal", issn, max_age)
        if cached is not None:
            paper.metrics["journal"] = cached
            # Counted here as well as after a fetch: the report says how many
            # papers ended up WITH journal figures, not how many requests it
            # took to get them. A cache hit is still a figure on the card.
            report["journals"] += 1
            continue
        wanted.setdefault(issn, []).append(paper)

    for chunk in _chunks(sorted(wanted), BATCH):
        if budget.exhausted():
            return
        try:
            payload = _get("/sources",
                           {"filter": "issn:%s" % "|".join(chunk),
                            "per-page": BATCH, "select": SOURCE_FIELDS},
                           contact, budget)
        except Exception as error:
            report["errors"].append("OpenAlex journal lookup: %s" % error)
            if log:
                log.warning("  ! metrics: %s", error)
            continue

        for record in payload.get("results") or []:
            summary = _source_summary(record)
            # Only claim a record for an ISSN the record actually carries.
            # Asking for a made-up ISSN does return *something*, and
            # attaching it to the wrong journal would be worse than nothing.
            owned = {_tidy_issn(value) for value in
                     list(summary["issn"]) + [summary["issn_l"]]}
            owned.discard("")
            # Cache under EVERY ISSN this record answers to, not only the one
            # that was asked for. A journal asked for as 2998-4165 but whose
            # issn_l is 1545-5963 was stored under the first and looked up
            # under the second, so the figures sat in the cache unused.
            for issn in owned:
                cache.put("journal", issn, summary)
            for issn in owned & set(chunk):
                for paper in wanted.get(issn, []):
                    paper.metrics["journal"] = summary
                    report["journals"] += 1
        cache.commit()


def _apply_journal_table(papers, settings, report):
    table = JournalTable(settings.get("journal_table") or {})
    if table.error:
        report["table_error"] = table.error
        return
    if not table.active():
        return
    report["table"] = table.label
    for paper in papers:
        issns = [paper.issn, paper.metrics.get("issn_l")]
        journal = paper.metrics.get("journal") or {}
        issns.extend(journal.get("issn") or [])
        issns.append(journal.get("issn_l", ""))
        values = table.lookup([value for value in issns if value])
        if values:
            paper.metrics["table"] = {"label": table.label, "values": values}
    report["table_misses"] = table.misses


def _as_date(stamp):
    try:
        return date(*(int(part) for part in str(stamp or "")[:10].split("-")))
    except (TypeError, ValueError):
        return None


def age_in_days(paper, today=None):
    """How long this paper has really been readable.

    The two dates disagree more often than you would expect. A volume in a
    book series carries a journal date of 2026-01-01 while the record only
    reached PubMed in September: take the journal date and it looks 264 days
    old with nothing citing it, when in truth nobody could read it until
    three days ago. The LATER of the two is the honest answer, so whichever
    field is odd, the paper is never accused of being ignored.
    """
    known = [day for day in (_as_date(paper.issued), _as_date(paper.published))
             if day]
    if not known:
        return None
    return ((today or date.today()) - max(known)).days


def _mark_new_papers(papers, new_paper_days):
    """Hide the citation count on papers too young to have one.

    Showing "cited 0 times" on a paper published four days ago reads as a
    verdict. It is not one - there has been no opportunity. The count is
    withheld and the reason given instead.
    """
    if not new_paper_days:
        return
    today = date.today()
    for paper in papers:
        age = age_in_days(paper, today)
        if age is not None and age < new_paper_days:
            paper.metrics["too_new"] = True
            paper.metrics.pop("citations", None)


def describe(report):
    """The lines the digest prints about where its numbers came from."""
    if not report.get("enabled"):
        return []

    lines = []
    if report["enriched"]:
        total = report.get("papers") or report["enriched"]
        # Most figures on most runs come from the cache, which holds them for
        # up to 30 days. Stamping the lot with today's date claimed a
        # provenance they do not have.
        fresh = report.get("fetched_now", 0)
        if fresh >= report["enriched"]:
            when = "read from OpenAlex (CC0) on %s" % date.today().isoformat()
        elif fresh:
            when = ("from OpenAlex (CC0); %d read today, the rest from the "
                    "local cache" % fresh)
        else:
            when = "from OpenAlex (CC0), out of the local cache"
        lines.append(
            "Citation and journal figures for %d of the %d papers below are "
            "%s.%s"
            % (report["enriched"], total, when,
               "" if report["enriched"] >= total
               else " The rest are not in OpenAlex or could not be looked up.")
        )
    if report["budget_stopped"]:
        lines.append(
            "The free OpenAlex allowance ran low (%s credits left), so the "
            "rest of the papers have no figures this run. It resets within "
            "about twelve hours." % report["remaining"]
        )
    if report["no_identifier"]:
        lines.append(
            "%d paper(s) carry neither a DOI nor a PubMed id, so they could "
            "not be looked up." % report["no_identifier"]
        )
    if report["table"]:
        lines.append("Impact figures labelled %r come from your own file and "
                     "were never fetched." % report["table"])
    if report["table_misses"]:
        lines.append("%d paper(s) had no row in your journal table."
                     % report["table_misses"])
    if report["table_error"]:
        lines.append("Your journal table was not used: %s" % report["table_error"])
    for error in report["errors"]:
        lines.append("OpenAlex was not reachable: %s" % error)
    return lines


DISCLAIMER = (
    "These are triage signals, not verdicts. A journal-level number says "
    "nothing about any single paper, and OpenAlex's mean citedness counts "
    "every meeting abstract a journal prints, so journals with large "
    "abstract supplements read far below their reputation."
)
