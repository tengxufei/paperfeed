"""The dashboard: what this run looked like, and how it compares to before.

Rebuilt by every run, from data already in hand plus the small subject
history in state/topics.json. Everything is inline SVG, so the file works
offline and prints.
"""

import html
import json
import os

import charts
import digest as digest_module
import stats as stats_module

DASH_CSS = """
.panel { background:#fff; border:1px solid #e2e4e8; border-radius:8px;
         padding:16px 18px; margin:0 0 16px; }
.panel h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em;
            color:#55606d; border:0; margin:0 0 4px; padding:0; }
.panel p.hint { font-size:12.5px; color:#77818d; margin:0 0 10px; }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.dnav { display:flex; flex-wrap:wrap; gap:7px; margin:0 0 18px; }
.dnav a { font-size:12.5px; padding:5px 11px; border-radius:13px;
          background:#eceff3; color:#55606d; text-decoration:none; }
.dnav a:hover { background:#dfe5ee; }
.dnav a.on { background:#11467f; color:#fff; }
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
.dot { display:inline-block; width:9px; height:9px; border-radius:50%;
       margin:0 4px 0 10px; vertical-align:middle; }
@media (max-width:640px) { .grid { grid-template-columns:1fr; } }
@media (prefers-color-scheme: dark) {
  .panel, .tile { background:#1f2228; border-color:#33373d; }
  .dnav a { background:#282c33; color:#9aa1aa; }
  .dnav a.on { background:#2c5b96; color:#fff; }
  .panel h2 { color:#b9bec6; } .tile b { color:#86b3ec; }
  .alert { border-bottom-color:#2a2f36; }
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
    return html.escape(str(value or ""))


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


def _nav(sets_order, active):
    links = ['<a class="%s" href="dashboard.html">All topics</a>'
             % ("on" if active is None else "")]
    for name in sets_order:
        links.append(
            '<a class="%s" href="%s">%s</a>'
            % ("on" if active == name else "", slug(name), _esc(name))
        )
    return '<nav class="dnav">%s</nav>' % "".join(links)


def render(run, history_rows, alerts, library_summary, meta):
    """run: the dict from stats.run_stats. history_rows: newest-first index.json."""
    scope = meta.get("scope")            # None = every topic, else a set name
    subjects = run["subjects"]
    verdicts = {name: verdict for name, _, verdict, _ in alerts}
    nodes, edges = stats_module.graph_data(subjects, run["pairs"], verdicts=verdicts)

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>PaperFeed &mdash; dashboard</title>",
        "<style>%s%s%s</style></head><body><div class=\"wrap\">"
        % (digest_module.STYLE, charts.CHART_CSS, DASH_CSS),
        "<h1>%s</h1>" % (_esc(scope) if scope else "Dashboard"),
        '<p class="meta">%s &middot; <a href="latest.html">this run\'s digest</a> '
        '&middot; <a href="index.html">all digests</a></p>' % _esc(meta.get("date_label", "")),
        _nav(meta.get("sets_order") or run["sets_order"], scope),
    ]

    # --- the numbers at a glance ---
    open_share = (
        "%d%%" % round(100.0 * run["open_access"] / run["total"]) if run["total"] else "0%"
    )
    parts.append(
        '<div class="tiles">%s</div>'
        % "".join(
            [
                _tile(run["total"], "new this run"),
                _tile(len(run["per_set"]), "topics with hits"),
                _tile(open_share, "free full text"),
                _tile(meta.get("hidden", 0), "hidden by filters"),
                _tile(library_summary.get("total", 0), "in your library"),
            ]
        )
    )

    # --- where the run came from ---
    if run["per_set"] and not scope:
        ordered = [
            (name, run["per_set"].get(name, 0), i)
            for i, name in enumerate(run["sets_order"])
            if run["per_set"].get(name)
        ]
        parts.append(
            '<div class="panel"><h2>Where this run came from</h2>'
            '<p class="hint">Papers by keyword set, then by source.</p>%s%s</div>'
            % (
                charts.stacked(ordered, label="papers by keyword set"),
                charts.stacked(
                    [
                        (name, count, 3 + i)
                        for i, (name, count) in enumerate(
                            sorted(run["per_source"].items())
                        )
                    ],
                    label="papers by source",
                ),
            )
        )

    if scope:
        parts.append(
            '<div class="panel"><h2>Where this topic came from</h2>%s</div>'
            % charts.stacked(
                [
                    (name, count, 3 + i)
                    for i, (name, count) in enumerate(sorted(run["per_source"].items()))
                ],
                label="papers by source",
            )
        )

    # --- score distribution + volume over time ---
    left = (
        '<div class="panel"><h2>How relevant, really</h2>'
        '<p class="hint">Where this run sits on the 0-10 score. If the mass is '
        "low, tighten the keywords or raise min_score.</p>%s</div>"
        % charts.histogram(run["score_buckets"], width=420, label="score distribution")
    )
    if scope:
        series = [
            (row.get("date_label", "")[:10] or "?",
             (row.get("sets") or {}).get(scope, 0))
            for row in reversed(history_rows[:14])
        ]
    else:
        series = [
            (row.get("date_label", "")[:10] or "?", row.get("total", 0))
            for row in reversed(history_rows[:14])
        ]
    right = (
        '<div class="panel"><h2>Volume over time</h2>'
        '<p class="hint">New papers per run. A flat line near zero means a '
        "keyword set has stopped earning its place.</p>%s</div>"
        % (
            charts.sparkline(series, width=420, label="papers per run")
            or '<p class="hint">Not enough runs yet &mdash; this fills in from '
               "the second run onward.</p>"
        )
    )
    parts.append('<div class="grid">%s%s</div>' % (left, right))

    # --- trend alerts ---
    if alerts:
        rows = "".join(
            '<div class="alert"><span class="flag %s">%s</span>'
            '<span>%s</span><span class="det">%d this run &middot; %s</span></div>'
            % (verdict, verdict, _esc(name), now, _esc(detail))
            for name, now, verdict, detail in alerts
        )
        parts.append(
            '<div class="panel"><h2>Trend alerts</h2>'
            '<p class="hint">Subjects behaving differently from their own '
            "history.</p>%s</div>" % rows
        )
    else:
        parts.append(
            '<div class="panel"><h2>Trend alerts</h2><p class="hint">Nothing '
            "unusual yet. Alerts need a few runs of history before they mean "
            "anything.</p></div>"
        )

    # --- the knowledge graph ---
    if nodes:
        parts.append(
            '<div class="panel"><h2>How your subjects connect</h2>'
            '<p class="hint">Subjects that appear on the same paper are linked. '
            "Bigger means more papers; thicker lines mean the pair co-occurs "
            "more often.</p>%s"
            '<div class="legend">'
            '<span class="dot" style="background:%s"></span>steady'
            '<span class="dot" style="background:#c2703a"></span>rising'
            '<span class="dot" style="background:#5b9c62"></span>new'
            "</div></div>" % (charts.graph(nodes, edges), charts.PALETTE[0])
        )

    # --- most common subjects ---
    top_subjects = subjects.most_common(12)
    if top_subjects:
        parts.append(
            '<div class="panel"><h2>Most common subjects</h2>'
            '<p class="hint">What your keyword sets actually pulled in, which '
            "is not always what you thought you asked for.</p>%s</div>"
            % charts.bars(top_subjects, label="subject frequency")
        )

    # --- the library ---
    if library_summary.get("total"):
        by_status = [
            (name, library_summary["status"].get(name, 0))
            for name in ("unread", "reading", "read")
        ]
        parts.append(
            '<div class="grid">'
            '<div class="panel"><h2>Your library</h2>'
            '<p class="hint">%d saved. <a href="/library">Open it</a>.</p>%s</div>'
            '<div class="panel"><h2>Saved by topic</h2>%s</div>'
            "</div>"
            % (
                library_summary["total"],
                charts.donut(by_status, label="reading status"),
                charts.bars(
                    library_summary.get("by_set", [])[:8], label="saved per topic"
                )
                or '<p class="hint">No topic breakdown yet.</p>',
            )
        )

    parts.append(
        '<p class="footer">Rebuilt by every run. Charts are drawn into the '
        "page itself, so this file works offline and prints.</p>"
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)


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
