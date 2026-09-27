"""The library, kept in a private GitHub repository so the website can reach it.

On the Mac the library is library.db and `paperfeed serve` answers the page.
A website has no server behind it, so there the library lives as one JSON
file in a private repository, read and written directly from the browser by
web/pf-web.js with a token that only the owner holds.

The scheduled run takes part through this module. `library-sync pull` copies
the file into library.db before a run, where the collector, the email footer
and the dashboards read it exactly as they always have; `library-sync push`
afterwards sends back what the run added, and the badge and metrics HTML for
every card, which the browser cannot compute for itself.

The run only ever ADDS papers and refreshes figures. It never deletes, never
changes a status or a summary, and never revives a paper that was removed in
the browser while the run was going: it adds only papers that did not exist
when it pulled. Removing things is the owner's job, done on the page.
"""

import base64
import json
import os
import time

import requests

import ai
import digest as digest_module
import library

API = "https://api.github.com"
LIBRARY_FILE = "library.json"
SETTINGS_FILE = "settings.json"
FORMAT_VERSION = 1


class StoreError(Exception):
    """Something the owner can act on, worded for them."""


class Conflict(StoreError):
    """The file changed between reading it and writing it."""


class Remote:
    """One private repository, through GitHub's contents API."""

    def __init__(self, repo, token, timeout=60):
        if not repo or "/" not in repo:
            raise StoreError("the library repository must be named owner/name, "
                             "got %r" % (repo or ""))
        if not token:
            raise StoreError("no token for the library repository: set "
                             "PAPERFEED_LIBRARY_TOKEN")
        self.repo = repo
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": "Bearer %s" % token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PaperFeed",
        })

    def _url(self, path):
        return "%s/repos/%s/contents/%s" % (API, self.repo, path)

    def _request(self, method, url, **kwargs):
        last = None
        for attempt in range(3):
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as error:
                last = "could not reach GitHub (%s)" % error
            else:
                if response.status_code < 500:
                    return response
                last = "GitHub answered HTTP %d" % response.status_code
            time.sleep(2 * (attempt + 1))
        raise StoreError(last)

    def get(self, path):
        """(text, sha), or (None, None) when the file does not exist yet."""
        response = self._request("GET", self._url(path),
                                 headers={"Accept": "application/vnd.github+json"})
        if response.status_code == 404:
            if self._request("GET", "%s/repos/%s" % (API, self.repo)).status_code == 404:
                raise StoreError(
                    "GitHub cannot see %s - it does not exist, or the token is not "
                    "allowed to read it" % self.repo)
            return None, None
        if response.status_code in (401, 403):
            raise StoreError("GitHub refused the token for %s (HTTP %d)"
                             % (self.repo, response.status_code))
        response.raise_for_status()
        meta = response.json()
        if meta.get("content"):
            return base64.b64decode(meta["content"]).decode("utf-8"), meta["sha"]
        # Over 1 MB the contents API leaves "content" empty on purpose and
        # the file has to be asked for raw. Measured: a 2.1 MB file came
        # back empty here and byte-identical the raw way.
        raw = self._request("GET", self._url(path),
                            headers={"Accept": "application/vnd.github.raw+json"})
        raw.raise_for_status()
        return raw.content.decode("utf-8"), meta["sha"]

    def put(self, path, text, sha, message):
        """Write the file, refusing if it changed since sha was read."""
        body = {"message": message,
                "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        if sha:
            body["sha"] = sha
        response = self._request("PUT", self._url(path), json=body)
        if response.status_code in (409, 422) and sha:
            raise Conflict("%s changed while it was being written" % path)
        if response.status_code in (401, 403, 404):
            raise StoreError("GitHub refused to write %s in %s (HTTP %d) - the token "
                             "needs Contents: read and write on that repository"
                             % (path, self.repo, response.status_code))
        response.raise_for_status()
        return response.json()["content"]["sha"]


def remote_from_env(repo=None):
    return Remote(repo or os.environ.get("PAPERFEED_LIBRARY_REPO", ""),
                  os.environ.get("PAPERFEED_LIBRARY_TOKEN", ""))


# --------------------------------------------------------------------------
# The file
# --------------------------------------------------------------------------

def _parse(text):
    if not text:
        return {"version": FORMAT_VERSION, "records": []}
    try:
        data = json.loads(text)
    except ValueError as error:
        raise StoreError("%s is not valid JSON (%s); it has not been touched"
                         % (LIBRARY_FILE, error))
    if isinstance(data, list):          # tolerate a bare list
        data = {"version": FORMAT_VERSION, "records": data}
    data.setdefault("records", [])
    return data


def _dump(data):
    data["records"].sort(key=lambda record: record.get("saved_at") or "", reverse=True)
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def _substance(data):
    return {key: value for key, value in data.items() if key != "sourcing"}


def _identities(records):
    found = set()
    for record in records:
        found.add(record.get("key"))
        if record.get("alt_key"):
            found.add(record["alt_key"])
    found.discard(None)
    found.discard("")
    return found


def _pull_record_path(cfg):
    return os.path.join(os.path.dirname(cfg["state_path"]), "library_pull.json")


# --------------------------------------------------------------------------
# What the browser cannot compute: badges and metrics for each card
# --------------------------------------------------------------------------

def card_extras(records, cfg):
    """{key: badge + metrics HTML}, rendered by the same code as the Mac's
    library page, so a card looks the same in both places."""
    import server                           # late: server imports a great deal
    enriched, sourcing = server._metrics_for_library(records, cfg)
    return (
        {key: digest_module._badges(paper) + digest_module._metrics_row(paper)
         for key, paper in enriched.items()},
        sourcing,
    )


def settings_payload(cfg):
    """What the page needs to run the AI buttons: which models, and the
    interest lines to judge against. Private, so it lives beside the library
    and never on the public site."""
    ai_cfg = cfg.get("ai") or {}
    main, trends = ai.settings_for(cfg), ai.settings_for(cfg, "trends")
    return {
        "version": FORMAT_VERSION,
        "ai_enabled": bool(ai_cfg.get("enabled")),
        "provider": main["provider"],
        "base_url": main["base_url"],
        "models": {"ai": main["model"], "trends": trends["model"]},
        "interests": {
            "general": ai_cfg.get("interests", ""),
            "by_set": ai.interests_for(cfg.get("keyword_sets") or [], ai_cfg),
        },
    }


# --------------------------------------------------------------------------
# pull and push
# --------------------------------------------------------------------------

def pull(cfg, remote):
    """Replace library.db with the repository's library. Returns the count."""
    text, sha = remote.get(LIBRARY_FILE)
    data = _parse(text)
    library.import_records(cfg["library_path"], data["records"])
    path = _pull_record_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"identities": sorted(_identities(data["records"])), "sha": sha}, handle)
    return len(data["records"])


def push(cfg, remote, attempts=4):
    """Send back what this machine added, plus fresh card extras for everything.

    Papers that existed at pull time are never re-sent, so one removed in the
    browser meanwhile stays removed. With no pull record at all (a first
    upload), every local paper counts as added.

    Returns a dict describing what happened, for the log.
    """
    try:
        with open(_pull_record_path(cfg), "r", encoding="utf-8") as handle:
            before = set(json.load(handle).get("identities") or [])
    except (OSError, ValueError):
        before = set()

    local = library.export_records(cfg["library_path"])
    added = [record for record in local
             if record["key"] not in before
             and not (record.get("alt_key") and record["alt_key"] in before)]

    extras, sourcing = {}, []
    for attempt in range(attempts):
        text, sha = remote.get(LIBRARY_FILE)
        data = _parse(text)
        known = _identities(data["records"])
        fresh = [record for record in added
                 if record["key"] not in known
                 and not (record.get("alt_key") and record["alt_key"] in known)]
        data["records"].extend(dict(record) for record in fresh)

        missing = [record for record in data["records"] if record["key"] not in extras]
        if missing and (cfg.get("metrics") or {}).get("enabled"):
            more, sourcing = card_extras(missing, cfg)
            extras.update(more)
        for record in data["records"]:
            if record["key"] in extras:
                record["extras_html"] = extras[record["key"]]
        data["version"] = FORMAT_VERSION
        data["sourcing"] = sourcing or data.get("sourcing") or []

        new_text = _dump(data)
        # Compare meaning, not spacing: the browser writes the same data with
        # its own formatting, and a run must not rewrite it for that alone.
        # Nor for "sourcing", whose wording ("5 read today, the rest from the
        # cache") changes every run; it rides along when something real does.
        if text and _substance(json.loads(new_text)) == _substance(_parse(text)):
            return {"added": 0, "total": len(data["records"]), "written": False}
        message = ("PaperFeed run: %d paper%s added" % (len(fresh), "" if len(fresh) == 1 else "s")
                   if fresh else "PaperFeed run: refresh citation figures")
        try:
            remote.put(LIBRARY_FILE, new_text, sha, message)
        except Conflict:
            continue        # someone saved on the page meanwhile; read it again
        return {"added": len(fresh), "total": len(data["records"]), "written": True}
    raise StoreError("the library kept changing while it was being written "
                     "(%d attempts); nothing was lost, the next run will try again"
                     % attempts)


def push_settings(cfg, remote):
    """Write settings.json when it differs. Returns True if it was written."""
    text, sha = remote.get(SETTINGS_FILE)
    new_text = json.dumps(settings_payload(cfg), ensure_ascii=False, indent=1) + "\n"
    if new_text == (text or ""):
        return False
    remote.put(SETTINGS_FILE, new_text, sha, "PaperFeed: settings for the website")
    return True
