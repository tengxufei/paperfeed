"""Your library: the papers you chose to keep.

PaperFeed deliberately does not hoard. A run renders its digest and forgets
every paper in it. The only papers that persist are the ones you click Save
on, and they live here, in a plain SQLite file you can open with any SQLite
browser or delete outright.

Identity reuses store.fingerprint, so a paper you save is recognised by the
same rules that decide whether a paper is "new" — DOI first, then a squashed
title. Saving the preprint and later the journal version will not give you
two rows.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved (
    key        TEXT PRIMARY KEY,
    alt_key    TEXT DEFAULT '',
    doi        TEXT,
    title      TEXT NOT NULL,
    authors    TEXT,
    abstract   TEXT,
    venue      TEXT,
    published  TEXT,
    source     TEXT,
    url        TEXT,
    set_name   TEXT,
    score      REAL,
    note       TEXT DEFAULT '',
    tags       TEXT DEFAULT '',
    saved_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS saved_by_date ON saved(saved_at DESC);
CREATE INDEX IF NOT EXISTS saved_by_alt ON saved(alt_key);
"""


class _Identity:
    """Minimal stand-in so a plain dict can go through store.fingerprint."""

    def __init__(self, payload):
        self.doi = payload.get("doi", "") or ""
        self.title = payload.get("title", "") or ""
        self.url = payload.get("url", "") or ""


def key_for(payload):
    return store.fingerprint(_Identity(payload))


def keys_for(payload):
    """Every key this paper should be recognised by — the same rules the run
    uses to decide a paper is not new, so a preprint and its later journal
    version collapse to one row here too."""
    return store.keys(_Identity(payload))


def connect(path):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def save(path, payload):
    """Add a paper. Returns (key, was_already_there)."""
    every_key = keys_for(payload)
    key = every_key[0]
    alt_key = every_key[1] if len(every_key) > 1 else ""
    with connect(path) as connection:
        slots = ",".join("?" for _ in every_key)
        existing = connection.execute(
            "SELECT key FROM saved WHERE key IN (%s) "
            "OR (alt_key != '' AND alt_key IN (%s))" % (slots, slots),
            every_key + every_key,
        ).fetchone()
        if existing:
            return existing["key"], True
        connection.execute(
            "INSERT INTO saved (key, alt_key, doi, title, authors, abstract, venue, "
            "published, source, url, set_name, score, saved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                alt_key,
                payload.get("doi", ""),
                payload.get("title", ""),
                json.dumps(payload.get("authors") or []),
                payload.get("abstract", ""),
                payload.get("venue", ""),
                payload.get("published", ""),
                payload.get("source", ""),
                payload.get("url", ""),
                payload.get("set_name", ""),
                float(payload.get("score") or 0),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return key, False


def remove(path, key):
    with connect(path) as connection:
        cursor = connection.execute("DELETE FROM saved WHERE key = ?", (key,))
        return cursor.rowcount > 0


def saved_keys(path):
    if not os.path.exists(path):
        return set()
    with connect(path) as connection:
        found = set()
        for row in connection.execute("SELECT key, alt_key FROM saved"):
            found.add(row["key"])
            if row["alt_key"]:
                found.add(row["alt_key"])
        return found


def count(path):
    if not os.path.exists(path):
        return 0
    with connect(path) as connection:
        return connection.execute("SELECT COUNT(*) AS n FROM saved").fetchone()["n"]


def _as_dict(row):
    record = dict(row)
    try:
        record["authors"] = json.loads(record.get("authors") or "[]")
    except (json.JSONDecodeError, TypeError):
        record["authors"] = []
    return record


def all_saved(path, limit=200):
    if not os.path.exists(path):
        return []
    with connect(path) as connection:
        rows = connection.execute(
            "SELECT * FROM saved ORDER BY saved_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_as_dict(row) for row in rows]


def search(path, text, limit=100):
    """Plain LIKE search across the fields worth searching.

    LIKE rather than FTS5 on purpose: it needs no special build of SQLite, it
    never needs reindexing, and a personal library is small enough that the
    difference is invisible.
    """
    if not os.path.exists(path):
        return []
    pattern = "%%%s%%" % text.strip()
    with connect(path) as connection:
        rows = connection.execute(
            "SELECT * FROM saved WHERE title LIKE ? OR abstract LIKE ? "
            "OR authors LIKE ? OR venue LIKE ? OR note LIKE ? OR tags LIKE ? "
            "ORDER BY saved_at DESC LIMIT ?",
            (pattern, pattern, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
    return [_as_dict(row) for row in rows]
