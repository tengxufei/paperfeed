"""The dashboard: what this run looked like, and how it compares to before.

Rebuilt by every run, from data already in hand plus the small subject
history in state/topics.json. Everything is inline SVG, so the file works
offline and prints.
"""

import html
import json
import os

import charts
import collection
import digest as digest_module
import stats as stats_module

DASH_CSS = """
.panel { background:#fff; border:1px solid #e2e4e8; border-radius:8px;
         padding:16px 18px; margin:0 0 16px; }
.panel h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em;
            color:#55606d; border:0; margin:0 0 4px; padding:0; }
.panel p.hint { font-size:12.5px; color:#77818d; margin:0 0 10px; }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.tiles { display:flex; flex-wrap:wrap; gap:10px; margin:0 0 16px; }
.tile { flex:1 1 130px; background:#fff; border:1px solid #e2e4e8;
        border-radius:8px; padding:12px 14px; }
.tile b { display:block; font-size:24px; line-height:1.15; color:#11467f; }
.tile span { font-size:12px; color:#6a727c; }
.alert { display:flex; gap:10px; align-items:baseline; padding:7px 0;
         border-bottom:1px solid #eceff3; font-size:13.5px; }
.alert:last-child { border-bottom:0; }
.flag { font-size:11px; font-weight:700; text-transform:uppercase;
        letter-spacing:.04em; padding:2px 8px; border-radius:10px; }
.flag.new { background:#d9ead3; color:#2c5d1e; }
.flag.rising { background:#f4ecdf; color:#7a5a1e; }
.flag.fading { background:#eceff3; color:#6a727c; }
.alert .det { color:#77818d; font-size:12.5px; margin-left:auto; }
.legend { font-size:12px; color:#77818d; margin-top:8px; }
.headline { font-size:15.5px; line-height:1.55; color:#2a3038; background:#fff;
            border:1px solid #e2e4e8; border-left:3px solid #5b87c4;
            border-radius:8px; padding:14px 16px; margin:0 0 16px; }
.verdict { font-size:13px; color:#55606d; margin:10px 0 0; }
.verdict b { color:#2a3038; }
.dtable { width:100%; border-collapse:collapse; font-size:13px; }
.dtable th { text-align:left; font-weight:600; font-size:10.5px;
             text-transform:uppercase; letter-spacing:.05em; color:#8a929c;
             padding:0 10px 6px 0; border-bottom:1px solid #eceff3; }
.dtable td { padding:7px 10px 7px 0; border-bottom:1px solid #f4f6f8;
             vertical-align:top; }
.dtable tr:last-child td { border-bottom:0; }
.dtable .num { text-align:right; font-variant-numeric:tabular-nums;
               white-space:nowrap; }
.dtable a { color:#11467f; text-decoration:none; }
.dtable a:hover { text-decoration:underline; }
.dtable .sub { display:block; color:#8a929c; font-size:11.5px; margin-top:2px; }
.empty-note { font-size:13px; color:#77818d; background:#f7f8fa;
              border:1px dashed #d8dde4; border-radius:7px; padding:12px 14px;
              line-height:1.55; }
.spark { display:inline-block; vertical-align:middle; margin-left:auto; }
.pips { display:inline-flex; gap:2px; align-items:flex-end; height:16px; }
.pips i { width:4px; background:#9db4d4; border-radius:1px; display:block; }
.dot { display:inline-block; width:9px; height:9px; border-radius:50%;
       margin:0 4px 0 10px; vertical-align:middle; }
@media (max-width:640px) { .grid { grid-template-columns:1fr; } }
@media (prefers-color-scheme: dark) {
  .panel, .tile { background:#1f2228; border-color:#33373d; }
  .panel h2 { color:#b9bec6; } .tile b { color:#86b3ec; }
  .alert { border-bottom-color:#2a2f36; }
  .headline { background:#1f2228; border-color:#33373d; color:#d3d8de; }
  .verdict { color:#a8aeb6; } .verdict b { color:#e7e9ec; }
  .dtable th { border-bottom-color:#2a2f36; color:#79828d; }
  .dtable td { border-bottom-color:#24282e; }
  .dtable a { color:#86b3ec; }
  .empty-note { background:#1b1e23; border-color:#343941; color:#8d949c; }
  .flag.new { background:#26381f; color:#a6cf92; }
  .flag.rising { background:#3b3325; color:#d7b377; }
  .flag.fading { background:#282c33; color:#9aa1aa; }
}
@media print {
  body { background:#fff; }
  .panel, .tile { break-inside:avoid; border-color:#ccc; }
}
"""


def _esc(value):
    # `value or ""` would turn a real 0 into an empty string, which is how a
    # "new this run" tile ended up with a caption and no number on it.
    return html.escape("" if value is None else str(value))


def _history(digest_dir):
    path = os.path.join(digest_dir, "index.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle) or []
    except (json.JSONDecodeError, OSError):
        return []


def _tile(value, caption):
    return '<div class="tile"><b>%s</b><span>%s</span></div>' % (
        _esc(value), _esc(caption)
    )


def slug(name):
    import re as _re
    return "dashboard-%s.html" % _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


def _pips(series, height=16):
    """A two-line-tall bar strip, for a trend that needs no axis."""
    values = [value for _, value in series]
    if not values or not any(values):
        return ""
    biggest = max(values) or 1
    return '<span class="pips">%s</span>' % "".join(
        '<i style="height:%dpx" title="%s: %d"></i>'
        % (max(2, int(round(value / biggest * height))), _esc(label), value)
        for label, value in series
    )


def _panel(title, hint, body, extra=""):
    if not body:
        return ""
    return ('<div class="panel%s"><h2>%s</h2><p class="hint">%s</p>%s</div>'
            % (extra, _esc(title), hint, body))


def _feed_panel(feed, scope):
    """Whether the alerting - the tool's first job - is actually working."""
    if not feed["runs"]:
        return _panel(
            "Is the alert working?",
            "This fills in from the second run onward.",
            '<p class="empty-note">No runs recorded yet.</p>')

    chart = charts.sparkline(feed["series"], width=560, label="papers per run")
    verdict = (
        "<p class=\"verdict\"><b>%d runs</b> recorded, finding <b>%s papers</b> "
        "in total. %d of those runs found nothing%s.%s</p>"
        % (feed["runs"], format(feed["found"], ","), feed["quiet"],
           " - which is normal on a short interval" if feed["quiet"] else "",
           (" Busiest: %s with %d." % (_esc(feed["busiest"][0]), feed["busiest"][1]))
           if feed["busiest"] and feed["busiest"][1] else "")
    )
    return _panel(
        "Is the alert working?",
        "Papers found per run, oldest on the left. Quiet runs are counted too "
        "- a feed that only shows its good days is not telling you anything.",
        (chart or "") + verdict)


def _growth_panel(coll):
    """Does the library actually grow without being told to?"""
    if not coll["total"]:
        return _panel(
            "Is the collection growing?",
            "Nothing saved yet.",
            '<p class="empty-note">Turn on <code>collect</code> in config.json '
            "so papers above your score threshold are kept automatically, or "
            "click <b>+ Save</b> on anything worth keeping.</p>")

    line = charts.sparkline(coll["cumulative"], width=560,
                            label="papers kept, running total")
    weeks = coll["per_week"]
    auto = sum(row[1] for row in weeks)
    yours = sum(row[2] for row in weeks)
    split = charts.stacked(
        [("collected for you", auto, 0), ("saved by you", yours, 2)],
        label="who added them")
    verdict = (
        '<p class="verdict">Of <b>%d</b> papers kept, <b>%d</b> arrived '
        "without you having to do anything.%s</p>"
        % (coll["total"], auto,
           " The collector is off, so the library only grows when you click."
           if not auto else "")
    )
    return _panel(
        "Is the collection growing?",
        "The running total, then who put each paper there. The point of the "
        "collector is that this line climbs while you are not looking.",
        (line or "") + split + verdict)


def _freshness_panel(coll):
    """How long a paper had been out before it reached you."""
    if coll["median_age"] is None:
        return ""
    buckets = [(label, count) for label, count in coll["age_buckets"] if count]
    verdict = (
        '<p class="verdict">Half your papers reached you within <b>%d day%s</b> '
        "of publication.%s</p>"
        % (coll["median_age"], "" if coll["median_age"] == 1 else "s",
           " That is the alerting doing its job."
           if coll["median_age"] <= 21 else
           " If that feels slow, shorten interval_days or widen lookback_days.")
    )
    return _panel(
        "How quickly news reaches you",
        "Each paper's age when it landed in your library. This is the number "
        "that says whether you are keeping up or catching up.",
        charts.histogram(buckets, width=560, label="age when saved") + verdict)


def _unread_panel(coll):
    """The papers that have been waiting longest."""
    rows = coll["unread_oldest"]
    if not rows:
        if coll["total"]:
            return _panel("Waiting for you", "Nothing unread. All of it.",
                          '<p class="empty-note">Your library is fully '
                          "triaged.</p>")
        return ""
    cells = "".join(
        "<tr><td><a href=\"%s\">%s</a><span class=\"sub\">%s &middot; saved %s"
        "</span></td><td class=\"num\">%s</td></tr>"
        % (_esc(row.get("url") or "#"), _esc(row.get("title") or ""),
           _esc(row.get("venue") or row.get("source") or ""),
           _esc((row.get("saved_at") or "")[:10]),
           _esc(row.get("set_name") or ""))
        for row in rows)
    return _panel(
        "Waiting for you",
        "The %d unread papers that have been sitting longest. "
        '<a href="/library">Open the library</a> to mark them off.'
        % coll["untouched"],
        '<table class="dtable"><tr><th>Paper</th><th class="num">Topic</th></tr>'
        "%s</table>" % cells)


def _journals_panel(coll):
    """Where the collection comes from, with each journal's own figures."""
    if not coll["journals"]:
        return ""
    cells = []
    for entry in coll["journals"]:
        if entry["citedness"] is None:
            figure = '<span class="muted">&mdash;</span>'
        else:
            figure = "%.1f<span class=\"sub\">over %s works</span>" % (
                entry["citedness"], format(entry["works"] or 0, ","))
        cells.append(
            "<tr><td>%s%s</td><td class=\"num\">%d</td><td class=\"num\">%s</td></tr>"
            % (_esc(entry["name"]),
               '<span class="sub">preprint server</span>' if entry["preprint"]
               else ('<span class="sub">in DOAJ</span>' if entry["in_doaj"] else ""),
               entry["count"], figure))
    return _panel(
        "Where your collection comes from",
        "Journals you have kept most from, with OpenAlex's 2-year mean "
        "citedness beside each. Read that number with the works count next to "
        "it: it divides by everything a journal prints, meeting abstracts "
        "included, so abstract-heavy journals read far below their reputation. "
        "It is not an Impact Factor and nothing here is sorted by it.",
        '<table class="dtable"><tr><th>Journal</th><th class="num">Kept</th>'
        '<th class="num">Citedness</th></tr>%s</table>' % "".join(cells))


def _cited_panel(coll):
    """The most-cited papers you have kept - or why there are none yet."""
    if coll["ranked"]:
        cells = "".join(
            "<tr><td><a href=\"%s\">%s</a><span class=\"sub\">%s &middot; %s"
            "</span></td><td class=\"num\">%d</td><td class=\"num\">%s</td></tr>"
            % (_esc(row.get("url") or "#"), _esc(row.get("title") or ""),
               _esc(row.get("venue") or ""), _esc((row.get("published") or "")[:10]),
               row["_cited"],
               ("%.1f&times;" % row["_m"]["fwci"]) if row["_m"].get("fwci")
               else "&mdash;")
            for row in coll["ranked"])
        return _panel(
            "The most-cited papers you have kept",
            "Citation counts from OpenAlex, with each paper's field-weighted "
            "impact beside it - 1.0&times; is typical for its field and year, "
            "so it does not punish a recent paper.",
            '<table class="dtable"><tr><th>Paper</th><th class="num">Cited</th>'
            '<th class="num">vs field</th></tr>%s</table>' % cells)

    if not coll["total"]:
        return ""
    return _panel(
        "The most-cited papers you have kept",
        "Nothing here yet, and that is the correct answer.",
        '<p class="empty-note">All %d of your saved papers are less than %d '
        "days old, so none has had the chance to be cited. Showing zeros "
        "would read as a judgement where there has simply been no "
        "opportunity. This panel fills itself in as your collection "
        "ages.</p>" % (coll["too_new"], collection.TOO_NEW_DAYS))


def _reading_panel(coll):
    if not coll["total"]:
        return ""
    by_status = [(name, coll["by_status"].get(name, 0))
                 for name in ("unread", "reading", "read")]
    return _panel(
        "Have you read any of it?",
        "A collection that grows on its own is only worth having if it also "
        "gets read.",
        charts.donut(by_status, width=220, height=220,
                     label="reading status")
        + '<p class="verdict"><b>%d%%</b> marked read. %s</p>'
        % (round(coll["read_share"]),
           "Open the library to change a status." if coll["untouched"]
           else "Nothing outstanding."))


def _topics_panel(coll):
    """Which of your topics the collection is actually made of."""
    rows = coll["by_set"].most_common(8) if coll["by_set"] else []
    if not rows:
        return ""
    return _panel(
        "Which topic it came from",
        "A topic with nothing kept from it is either too narrow, or not "
        "really one of your interests any more.",
        charts.bars(rows, width=420, label="saved per topic"))


def _alerts_panel(alerts, history, bucket):
    if not alerts:
        return _panel(
            "What is moving in your field",
            "Subjects behaving differently from their own recent history.",
            '<p class="empty-note">Nothing unusual yet. This needs a few runs '
            "of history before a change means anything rather than being "
            "noise.</p>")
    rows = "".join(
        '<div class="alert"><span class="flag %s">%s</span><span>%s</span>'
        '<span class="det">%s%d this run &middot; %s</span></div>'
        % (verdict, verdict, _esc(name),
           _pips(collection.subject_history(history, name, bucket)),
           now, _esc(detail))
        for name, now, verdict, detail in alerts)
    return _panel(
        "What is moving in your field",
        "Subjects appearing more or less often than their own recent history "
        "would predict. The strip beside each is that subject across recent "
        "runs.", rows)


def render(run, history_rows, alerts, library_summary, meta,
           coll=None, history=None, subject_cache="", landscape=None):
    """run: stats.run_stats for this run. coll: collection.snapshot.

    The two are deliberately different things. `run` is what arrived in the
    last hour and is usually nothing; `coll` is everything that has
    accumulated. The page leads with the second, because a dashboard that
    reads zero whenever a run is quiet is a dashboard nobody opens twice.
    """
    scope = meta.get("scope")            # None = every topic, else a set name
    bucket = scope if scope else stats_module.ALL
    verdicts = {name: verdict for name, _, verdict, _ in alerts}

    # The subject panels need papers to describe, and most runs on a short
    # interval have none. Falling back to the last run that DID find papers
    # keeps them alive, dated so it is never mistaken for now.
    landscape = landscape or run
    subjects, pairs, stale = landscape["subjects"], landscape["pairs"], None
    if not subjects and subject_cache:
        subjects, pairs, stale = collection.recall(subject_cache, bucket)
    nodes, edges = stats_module.graph_data(subjects, pairs, verdicts=verdicts)
    as_of = ("" if stale is None else
             " Taken from the last run that found papers, %s."
             % ("today" if stale <= 0 else
                "%d day%s ago" % (stale, "" if stale == 1 else "s")))
    coll = coll or {"total": 0, "headline": "", "journals": [], "ranked": [],
                    "unread_oldest": [], "by_status": {}, "untouched": 0,
                    "read_share": 0.0, "median_age": None, "age_buckets": [],
                    "cumulative": [], "per_week": [], "too_new": 0,
                    "open_share": 0.0, "open_access": 0, "added_30d": 0,
                    "added_30d_auto": 0, "measured": 0}
    feed = collection.activity(history_rows, scope)

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>PaperFeed &mdash; dashboard</title>",
        "<style>%s%s%s%s</style></head><body class=\"shell\">"
        % (digest_module.STYLE, digest_module.SHELL_CSS, charts.CHART_CSS, DASH_CSS),
        digest_module.sidebar(
            dict(meta, show_scores=False, library_total=library_summary.get("total"),
                 sources_label="dashboard"),
            "dashboard",
            topics=[
                {"label": "All topics", "href": "dashboard.html",
                 "active": scope is None},
            ] + [
                {"label": name, "href": slug(name), "active": scope == name,
                 "count": run["per_set"].get(name)}
                for name in (meta.get("sets_order") or run["sets_order"])
            ],
            stats=[
                (coll["total"], "papers kept"),
                (coll["untouched"], "unread"),
                (run["total"], "new this run"),
                (feed["found"], "seen in all"),
            ],
        ),
        '<div class="wrap">',
        "<h1>%s</h1>" % (_esc(scope) if scope else "Dashboard"),
        '<p class="meta">%s</p>' % _esc(meta.get("date_label", "")),
    ]

    if coll["headline"]:
        parts.append('<p class="headline">%s</p>' % _esc(coll["headline"]))

    # The number that explains a quiet run, which "0 new" on its own does not.
    if landscape["total"]:
        parts.append(
            '<p class="verdict"><b>%s papers</b> match your %s in the current '
            "%s-day window; <b>%d</b> of them had not been shown to you "
            "before.%s</p>"
            % (format(landscape["total"], ","),
               "query" if scope else "queries",
               meta.get("lookback_days", 14), run["total"],
               "" if run["total"] else
               " A run finding nothing new is the normal state of a feed that "
               "is up to date, not a fault."))

    parts.append(
        '<div class="tiles">%s</div>'
        % "".join([
            _tile(coll["total"], "papers kept"),
            _tile(coll["added_30d_auto"], "collected for you, 30 days"),
            _tile(coll["untouched"], "still unread"),
            _tile("%d%%" % round(coll["open_share"]), "free to read"),
            _tile(len(coll["journals"]), "journals covered"),
            _tile(run["total"], "new this run"),
        ]))

    parts.append(_feed_panel(feed, scope))
    parts.append(_growth_panel(coll))
    parts.append(_freshness_panel(coll))
    parts.append(_unread_panel(coll))
    parts.append(_cited_panel(coll))
    parts.append(_journals_panel(coll))
    parts.append(_alerts_panel(alerts, history, bucket))

    if nodes:
        parts.append(_panel(
            "How your subjects connect",
            "Subjects that appear on the same paper are linked. Bigger means "
            "more papers; thicker lines mean the pair turns up together more "
            "often." + as_of,
            charts.graph(nodes, edges)
            + '<div class="legend">'
              '<span class="dot" style="background:%s"></span>steady'
              '<span class="dot" style="background:#c2703a"></span>rising'
              '<span class="dot" style="background:#5b9c62"></span>new'
              "</div>" % charts.PALETTE[0]))

    top_subjects = subjects.most_common(14)
    if top_subjects:
        parts.append(_panel(
            "What your searches actually pulled in",
            "The subjects on the papers your queries brought back, which is "
            "not always what you thought you had asked for. A surprise near "
            "the top usually means a query wider than intended." + as_of,
            charts.bars(top_subjects, label="subject frequency")))

    if run["total"]:
        left = _panel(
            "Where this run's papers scored",
            "The 0-10 spread for this run. Mass piled at the bottom means the "
            "query is matching things it should not - tighten it, or raise "
            "min_score.",
            charts.histogram(run["score_buckets"], width=420,
                             label="score distribution"))
        ordered = [(name, run["per_set"].get(name, 0), index)
                   for index, name in enumerate(run["sets_order"])
                   if run["per_set"].get(name)]
        right = _panel(
            "Which topic brought them in",
            "This run only, by keyword set and then by source.",
            (charts.stacked(ordered, label="papers by keyword set")
             if not scope and ordered else "")
            + charts.stacked(
                [(name, count, 3 + index) for index, (name, count)
                 in enumerate(sorted(run["per_source"].items()))],
                label="papers by source"))
        parts.append('<div class="grid">%s%s</div>' % (left, right))

    parts.append('<div class="grid">%s%s</div>'
                 % (_reading_panel(coll), _topics_panel(coll)))

    parts.append(
        '<p class="footer">Rebuilt by every run from your library, the metric '
        "cache and the run history &mdash; not from this run alone, so it "
        "still says something on a quiet day. Charts are drawn into the page "
        "itself, so this file works offline and prints.</p>"
    )
    parts.append("</div>")
    parts.append(digest_module.shell_close())
    parts.append("<script>%s</script>" % digest_module.SHELL_JS)
    parts.append("</body></html>")
    return "\n".join(part for part in parts if part)


def _short_month(label):
    """2024-03 -> Mar, and Jan carries its year so the axis stays readable."""
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    try:
        year, month = label.split("-")
        name = names[int(month) - 1]
        return "%s %s" % (name, year[2:]) if month == "01" else name
    except (ValueError, IndexError):
        return label


def render_retro(papers, run, monthly, authors, institutions, comparison, meta):
    """The page for a date-range search."""
    import digest as _digest

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>PaperFeed &mdash; %s</title>" % _esc(meta.get("range_label", "")),
        "<style>%s%s%s</style></head><body>"
        % (_digest.STYLE, charts.CHART_CSS, DASH_CSS),
        '<input type="checkbox" id="compact">',
        '<div class="wrap">',
        "<h1>%s</h1>" % _esc(meta.get("range_label", "")),
        '<p class="meta">%s &middot; searched by publication date &middot; '
        '<a href="dashboard.html">the live dashboard</a></p>'
        % _esc(", ".join(meta.get("sets") or [])),
    ]

    if meta.get("errors"):
        parts.append(
            '<div class="errors"><strong>Some months did not come back.</strong>'
            "<ul>%s</ul></div>"
            % "".join("<li>%s</li>" % _esc(e) for e in meta["errors"][:6])
        )

    monthly, outside = monthly
    span = len(monthly)
    busiest = max(monthly, key=lambda row: row[1])[0] if monthly else "-"
    parts.append(
        '<div class="tiles">%s</div>'
        % "".join([
            _tile(run["total"], "papers found"),
            _tile(span, "months covered"),
            _tile(_short_month(busiest), "busiest month"),
            _tile(
                "%d%%" % round(100.0 * run["open_access"] / run["total"])
                if run["total"] else "0%",
                "free full text",
            ),
        ])
    )

    if monthly:
        parts.append(
            '<div class="panel"><h2>Published per month</h2>'
            '<p class="hint">By the journal\'s own issue date, not when the '
            "record was indexed. This is the shape of the field over the "
            "period.%s</p>%s</div>"
            % (
                (
                    " %s carry an issue date outside this window - usually "
                    "ahead-of-print assigned to a later issue." % _esc(
                        "%d paper%s" % (outside, "" if outside == 1 else "s")
                    )
                )
                if outside
                else "",
                charts.histogram(
                    [(_short_month(m), n) for m, n in monthly],
                    width=620, height=170, label="papers per month",
                ),
            )
        )

    left = (
        '<div class="panel"><h2>Most published authors</h2>%s</div>'
        % (charts.bars(authors, label="papers per author")
           or '<p class="hint">No author data.</p>')
    )
    right = (
        '<div class="panel"><h2>Where the work came from</h2>'
        '<p class="hint">Senior author\'s institution. Affiliations are free '
        "text, so read this as indicative.</p>%s</div>"
        % (charts.bars(institutions, label="papers per institution")
           or '<p class="hint">No affiliation data.</p>')
    )
    parts.append('<div class="grid">%s%s</div>' % (left, right))

    if comparison:
        risen, fallen, appeared, vanished = comparison["changes"]
        rows = []
        for label, group, tone in (("appeared", appeared, "new"),
                                   ("rising", risen, "rising"),
                                   ("fading", fallen, "fading"),
                                   ("gone", vanished, "fading")):
            for name, before, after in group[:5]:
                rows.append(
                    '<div class="alert"><span class="flag %s">%s</span>'
                    '<span>%s</span><span class="det">%d then, %d now</span></div>'
                    % (tone, label, _esc(name), before, after)
                )
        parts.append(
            '<div class="panel"><h2>What changed against %s</h2>'
            '<p class="hint">Compared as shares, not raw counts, so a longer '
            "period does not look like growth everywhere.</p>%s</div>"
            % (_esc(comparison["label"]),
               "".join(rows) or '<p class="hint">Nothing moved much.</p>')
        )

    if run["subjects"]:
        verdicts = {}
        nodes, edges = stats_module.graph_data(
            run["subjects"], run["pairs"], verdicts=verdicts
        )
        parts.append(
            '<div class="panel"><h2>Subjects over the period</h2>%s</div>'
            % charts.bars(run["subjects"].most_common(12), label="subjects")
        )
        if nodes:
            parts.append(
                '<div class="panel"><h2>How the subjects connect</h2>%s</div>'
                % charts.graph(nodes, edges)
            )

    shown = papers[: meta.get("list_limit", 60)]
    if shown:
        parts.append(
            '<details class="set" open><summary><div class="setbar">'
            "<span>The papers</span>"
            '<span class="setcount">%d of %d</span></div></summary>'
            '<div class="setbody">' % (len(shown), run["total"])
        )
        parts.extend(_digest._paper_html(paper, True) for paper in shown)
        parts.append("</div></details>")

    parts.append(
        '<p class="footer">A retrospective search. It does not change what '
        "your feed considers new, and it does not affect the trend baselines "
        "on the live dashboard.</p>"
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)
