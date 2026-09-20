"""Turn a list of papers into something worth reading.

Three renderers share one set of helpers:

  render_html        the local file, the thing we always write
  render_email_html  the same content with styles inlined, for mail clients
  render_text        a plain-text fallback for mail clients that refuse HTML

The local file also embeds a small JSON blob per paper. It is invisible in the
browser, and it is what lets `paperfeed serve` offer a Save button later
without PaperFeed having to store papers you never asked it to keep.
"""

import html
import json
from datetime import datetime

ABSTRACT_CHARS = 700          # the file can afford a fuller abstract
EMAIL_ABSTRACT_CHARS = 320    # email should stay skimmable
MAX_CHIPS = 5

STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.55; margin: 0; padding: 24px 16px 64px;
  background: #f6f7f9; color: #1a1a1a;
}
.wrap { max-width: 780px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
.meta { color: #666; font-size: 13px; margin-bottom: 6px; }
.counts { color: #666; font-size: 13px; margin: 0 0 24px; }
.counts b { color: #333; }
h2 {
  font-size: 15px; text-transform: uppercase; letter-spacing: .06em;
  color: #444; border-bottom: 2px solid #d8dadd;
  padding-bottom: 6px; margin: 34px 0 12px;
}
h2 .count { color: #888; font-weight: normal; text-transform: none; letter-spacing: 0; }
.highlights { border-bottom-color: #b98b3a; color: #8a6420; }
.paper {
  background: #fff; border: 1px solid #e2e4e8; border-radius: 8px;
  padding: 14px 16px; margin-bottom: 12px;
}
.head { display: flex; gap: 10px; align-items: baseline; }
.score {
  flex: 0 0 auto; font-size: 12px; font-weight: 700; padding: 2px 8px;
  border-radius: 10px; background: #eceff3; color: #63696f;
}
.score.strong { background: #d9ead3; color: #2c5d1e; }
.score.ai { background: #ece3f5; color: #573a78; }
.score.medium { background: #e8edf4; color: #33507a; }
.paper a.title { font-size: 16px; font-weight: 600; color: #11467f; text-decoration: none; }
.paper a.title:hover { text-decoration: underline; }
.byline { font-size: 13px; color: #555; margin: 6px 0 0; }
.affil { color: #777; font-style: italic; }
.why { font-size: 12px; color: #6b7a52; margin: 5px 0 0; }
.ai-why { font-size: 13px; color: #573a78; margin: 6px 0 0; }
.theme { background: #fff; border: 1px solid #e2e4e8; border-radius: 8px;
         padding: 16px 18px; margin-bottom: 14px; }
.theme h3 { margin: 0 0 8px; font-size: 17px; color: #11467f; }
.theme p { margin: 0 0 10px; font-size: 14px; }
.theme ol { margin: 0; padding-left: 20px; font-size: 13px; }
.theme li { margin-bottom: 4px; }
.cooling { font-size: 13px; color: #7a5a1e; border-left: 3px solid #d7b377;
           padding-left: 10px; margin-top: 10px; }
.tags { font-size: 12px; color: #777; margin-top: 6px; }
.badge {
  display: inline-block; padding: 1px 7px; border-radius: 10px;
  background: #e8edf4; color: #33507a; font-weight: 600; margin-right: 6px;
}
.badge.preprint { background: #f4ecdf; color: #7a5a1e; }
.badge.oa { background: #d9ead3; color: #2c5d1e; }
.chips { margin-top: 8px; }
.chip {
  display: inline-block; font-size: 11px; padding: 1px 7px; margin: 0 4px 4px 0;
  border-radius: 4px; background: #f0f1f3; color: #5a6068;
}
details { margin-top: 9px; }
summary {
  cursor: pointer; font-size: 13px; color: #11467f; outline: none;
  width: fit-content;
}
details p { font-size: 13.5px; color: #333; margin: 8px 0 0; }
.empty, .errors, .hidden-note {
  background: #fff; border: 1px solid #e2e4e8; border-left: 4px solid #9aa3ad;
  border-radius: 6px; padding: 14px 16px; font-size: 14px;
}
.errors { border-left-color: #c2703a; margin-bottom: 20px; }
.hidden-note { border-left-color: #9aa3ad; margin-top: 24px; font-size: 13px; color: #555; }
.footer { margin-top: 40px; font-size: 12px; color: #888; }
.footer a { color: #11467f; }
@media (max-width: 480px) {
  body { padding: 16px 12px 48px; }
  .paper { padding: 12px; }
  .paper a.title { font-size: 17px; }
  summary { padding: 6px 0; }
}
@media (prefers-color-scheme: dark) {
  body { background: #16181c; color: #e6e6e6; }
  h2 { color: #b9bec6; border-bottom-color: #33373d; }
  .highlights { color: #d8ab63; border-bottom-color: #7a5a1e; }
  .paper, .empty, .errors, .hidden-note { background: #1f2228; border-color: #33373d; }
  .paper a.title { color: #86b3ec; }
  .byline { color: #a8aeb6; }
  .affil { color: #8d949c; }
  .why { color: #9fb07f; }
  details p { color: #c9ced5; }
  .badge { background: #2a3547; color: #9dbbe4; }
  .badge.preprint { background: #3b3325; color: #d7b377; }
  .badge.oa { background: #26381f; color: #a6cf92; }
  .chip { background: #282c33; color: #9aa1aa; }
  .score { background: #282c33; color: #9aa1aa; }
  .score.strong { background: #26381f; color: #a6cf92; }
  .score.ai { background: #322a3d; color: #c0a6dd; }
  .ai-why { color: #c0a6dd; }
  .theme { background: #1f2228; border-color: #33373d; }
  .theme h3 { color: #86b3ec; }
  .score.medium { background: #2a3547; color: #9dbbe4; }
  .counts b { color: #d8dce1; }
  .meta, .counts, .hidden-note { color: #98a0a8; }
}
"""


def _shorten(text, limit):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _escape(value):
    return html.escape(str(value or ""))


def _score_class(score):
    if score >= 7:
        return "score strong"
    if score >= 4:
        return "score medium"
    return "score"


def paper_payload(paper):
    """The machine-readable copy embedded in the page, used by `serve`."""
    return {
        "title": paper.title,
        "authors": paper.authors,
        "abstract": paper.abstract,
        "doi": paper.doi,
        "url": paper.url,
        "published": paper.published,
        "venue": paper.venue,
        "source": paper.source,
        "set_name": paper.set_name,
        "score": paper.score,
    }


def _chips(paper):
    terms = (paper.mesh_terms or [])[:MAX_CHIPS]
    if not terms:
        terms = (paper.keywords or [])[:MAX_CHIPS]
    if not terms:
        return ""
    return '<div class="chips">%s</div>' % "".join(
        '<span class="chip">%s</span>' % _escape(term) for term in terms
    )


def _badges(paper):
    badge_class = "badge preprint" if paper.source == "Preprint" else "badge"
    bits = ['<span class="%s">%s</span>' % (badge_class, _escape(paper.source))]
    if paper.free_fulltext:
        if paper.fulltext_url:
            bits.append(
                '<a class="badge oa" href="%s">free full text</a>'
                % _escape(paper.fulltext_url)
            )
        else:
            bits.append('<span class="badge oa">free full text</span>')

    tail = []
    if paper.venue:
        tail.append(_escape(paper.venue))
    if paper.published:
        tail.append(_escape(paper.published))
    if paper.citations:
        tail.append("cited by %d" % paper.citations)
    if paper.doi:
        tail.append("doi:" + _escape(paper.doi))
    return '<div class="tags">%s%s</div>' % (" ".join(bits), " &middot; ".join(tail))


def _paper_html(paper, show_scores):
    parts = ['<div class="paper" data-doi="%s">' % _escape(paper.doi)]

    head = ['<div class="head">']
    if getattr(paper, "ai_score", None) is not None:
        head.append(
            '<span class="score ai" title="Claude\'s relevance score">%.0f</span>'
            % paper.ai_score
        )
    if show_scores:
        head.append(
            '<span class="%s" title="%s">%.1f</span>'
            % (
                _score_class(paper.score),
                _escape("; ".join(paper.score_reasons) or "no matching terms"),
                paper.score,
            )
        )
    head.append(
        '<a class="title" href="%s">%s</a>' % (_escape(paper.url), _escape(paper.title))
    )
    head.append("</div>")
    parts.append("".join(head))

    byline = paper.author_line()
    if byline or paper.affiliation:
        line = _escape(byline)
        if paper.affiliation:
            line += ' <span class="affil">&mdash; %s</span>' % _escape(paper.affiliation)
        parts.append('<p class="byline">%s</p>' % line)

    if getattr(paper, "ai_reason", ""):
        parts.append('<p class="ai-why">%s</p>' % _escape(paper.ai_reason))

    # Say why it ranked where it did, visibly. A tooltip alone is useless on
    # a phone, and an unexplained ranking is one you stop trusting.
    if show_scores and paper.score_reasons:
        parts.append('<p class="why">%s</p>' % _escape("; ".join(paper.score_reasons)))

    parts.append(_badges(paper))
    parts.append(_chips(paper))

    if paper.abstract:
        parts.append(
            "<details><summary>Abstract</summary><p>%s</p></details>"
            % _escape(_shorten(paper.abstract, ABSTRACT_CHARS))
        )

    parts.append(
        '<script type="application/json" class="pf-paper">%s</script>'
        % json.dumps(paper_payload(paper)).replace("<", "\\u003c")
    )
    parts.append("</div>")
    return "\n".join(parts)


def _counts_line(meta):
    bits = []
    for label, number in sorted((meta.get("source_counts") or {}).items()):
        bits.append("<b>%d</b> from %s" % (number, _escape(label)))
    hidden = meta.get("hidden_count", 0)
    if hidden:
        bits.append("<b>%d</b> hidden by your filters" % hidden)
    return '<p class="counts">%s</p>' % " &middot; ".join(bits) if bits else ""


def render_html(groups, meta):
    """groups is a list of (keyword set name, [Paper]) — including empty ones."""
    total = meta.get("total_new", 0)
    show_scores = meta.get("show_scores", True)
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>PaperFeed &mdash; %s</title>" % _escape(meta.get("date_label", "")),
        "<style>%s</style></head><body><div class=\"wrap\">" % STYLE,
        "<h1>PaperFeed</h1>",
        '<p class="meta">%s &middot; %d new paper%s &middot; searched the last %d days</p>'
        % (
            _escape(meta.get("date_label", "")),
            total,
            "" if total == 1 else "s",
            meta.get("lookback_days", 0),
        ),
        _counts_line(meta),
    ]

    if meta.get("errors"):
        parts.append('<div class="errors"><strong>Some sources did not respond.</strong><ul>')
        for message in meta["errors"]:
            parts.append("<li>%s</li>" % _escape(message))
        parts.append("</ul><p>The list below may be incomplete for those sources.</p></div>")

    if meta.get("ai_note"):
        parts.append('<div class="errors">%s</div>' % _escape(meta["ai_note"]))

    if total == 0 and meta.get("errors"):
        parts.append(
            '<div class="empty">No new papers to show &mdash; but at least one '
            "source did not answer, so this may not be the full picture. "
            "The next run will pick up anything that was missed.</div>"
        )
    elif total == 0:
        parts.append(
            '<div class="empty">No new papers this time. '
            "Everything matching your keywords in the last %d days has already "
            "appeared in an earlier digest.</div>" % meta.get("lookback_days", 0)
        )
    else:
        highlights = meta.get("top_papers") or []
        if highlights and total > len(highlights):
            parts.append(
                '<h2 class="highlights">Top %d this run</h2>' % len(highlights)
            )
            parts.extend(_paper_html(paper, show_scores) for paper in highlights)

        for name, papers in groups:
            if not papers:
                continue
            parts.append(
                '<h2>%s <span class="count">(%d)</span></h2>'
                % (_escape(name), len(papers))
            )
            parts.extend(_paper_html(paper, show_scores) for paper in papers)

    empty_names = [name for name, papers in groups if not papers]
    if empty_names and total:
        parts.append(
            '<p class="footer">Nothing new for: %s</p>' % _escape(", ".join(empty_names))
        )

    for line in meta.get("hidden_examples") or []:
        parts.append('<div class="hidden-note">%s</div>' % _escape(line))

    parts.append(
        '<p class="footer">Generated by PaperFeed from %s. '
        'Edit config.json to change keywords, filters or the schedule. '
        '<a href="index.html">All digests</a>.</p>' % _escape(meta.get("sources_label", ""))
    )
    parts.append("</div></body></html>")
    return "\n".join(part for part in parts if part)


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

# Mail clients are not browsers: many strip <style> blocks, most ignore media
# queries, and <details> rarely works. So the email is built separately with
# its styles written directly onto each element.
E_BODY = "margin:0;padding:16px;background:#f6f7f9;font-family:-apple-system,Helvetica,Arial,sans-serif;color:#1a1a1a;line-height:1.5;"
E_CARD = "background:#ffffff;border:1px solid #e2e4e8;border-radius:8px;padding:14px;margin:0 0 12px;"
E_TITLE = "font-size:17px;font-weight:600;color:#11467f;text-decoration:none;display:block;margin-bottom:6px;"
E_BYLINE = "font-size:13px;color:#555;margin:0 0 6px;"
E_TAGS = "font-size:12px;color:#777;margin:0 0 8px;"
E_ABSTRACT = "font-size:14px;color:#333;margin:0;"
E_H2 = "font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:#444;border-bottom:2px solid #d8dadd;padding-bottom:6px;margin:28px 0 12px;"


def _paper_email(paper, show_scores):
    bits = ['<div style="%s">' % E_CARD]
    prefix = ""
    if show_scores:
        prefix = '<span style="font-size:12px;font-weight:700;color:#2c5d1e;">%.1f</span> ' % paper.score
    bits.append(
        '%s<a href="%s" style="%s">%s</a>'
        % (prefix, _escape(paper.url), E_TITLE, _escape(paper.title))
    )
    byline = paper.author_line(limit=5)
    if byline:
        bits.append('<p style="%s">%s</p>' % (E_BYLINE, _escape(byline)))

    tail = [paper.source]
    if paper.venue:
        tail.append(paper.venue)
    if paper.published:
        tail.append(paper.published)
    if paper.free_fulltext:
        tail.append("free full text")
    bits.append('<p style="%s">%s</p>' % (E_TAGS, _escape(" · ".join(tail))))

    if paper.abstract:
        bits.append(
            '<p style="%s">%s</p>'
            % (E_ABSTRACT, _escape(_shorten(paper.abstract, EMAIL_ABSTRACT_CHARS)))
        )
    bits.append("</div>")
    return "".join(bits)


def render_email_html(groups, meta):
    total = meta.get("total_new", 0)
    show_scores = meta.get("show_scores", True)
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>PaperFeed &mdash; %s</title>" % _escape(meta.get("date_label", "")),
        "</head>",
        '<body style="%s"><div style="max-width:640px;margin:0 auto;">' % E_BODY,
        '<h1 style="font-size:20px;margin:0 0 4px;">PaperFeed</h1>',
        '<p style="font-size:13px;color:#666;margin:0 0 20px;">%s &middot; %d new paper%s</p>'
        % (_escape(meta.get("date_label", "")), total, "" if total == 1 else "s"),
    ]

    if meta.get("errors"):
        parts.append(
            '<p style="background:#fff;border-left:4px solid #c2703a;padding:12px;'
            'font-size:13px;margin:0 0 16px;">Some sources did not respond: %s</p>'
            % _escape("; ".join(str(error) for error in meta["errors"]))
        )

    if total == 0:
        parts.append(
            '<p style="background:#fff;border:1px solid #e2e4e8;padding:14px;'
            'font-size:14px;">No new papers this time.</p>'
        )
    else:
        for name, papers in groups:
            if not papers:
                continue
            parts.append('<h2 style="%s">%s (%d)</h2>' % (E_H2, _escape(name), len(papers)))
            parts.extend(_paper_email(paper, show_scores) for paper in papers)

    parts.append(
        '<p style="font-size:12px;color:#888;margin-top:28px;">'
        "The full digest, with abstracts and filters, is on your Mac in "
        "paperfeed/digests/latest.html</p>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)


def render_text(groups, meta):
    lines = [
        "PaperFeed - %s" % meta.get("date_label", ""),
        "%d new papers, searched the last %d days"
        % (meta.get("total_new", 0), meta.get("lookback_days", 0)),
        "",
    ]
    if meta.get("errors"):
        lines.append("SOURCES THAT DID NOT RESPOND:")
        lines.extend("  - %s" % message for message in meta["errors"])
        lines.append("")

    if not meta.get("total_new"):
        lines.append("No new papers this time.")
    else:
        for name, papers in groups:
            if not papers:
                continue
            lines.append("%s (%d)" % (name.upper(), len(papers)))
            lines.append("-" * len(name))
            for paper in papers:
                lines.append("[%.1f] %s" % (paper.score, paper.title))
                if paper.author_line():
                    lines.append("  " + paper.author_line())
                detail = " | ".join(
                    bit for bit in (paper.source, paper.venue, paper.published) if bit
                )
                lines.append("  " + detail)
                lines.append("  " + paper.url)
                lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The index of past digests
# --------------------------------------------------------------------------

def render_index(records):
    """records: newest-first list of {file, date_label, total, sources, hidden}."""
    rows = []
    for record in records:
        counts = record.get("sources") or {}
        detail = ", ".join("%d %s" % (n, label) for label, n in sorted(counts.items()))
        rows.append(
            '<div class="paper"><div class="head">'
            '<a class="title" href="%s">%s</a></div>'
            '<div class="tags">%d new%s</div></div>'
            % (
                _escape(record.get("file", "")),
                _escape(record.get("date_label", "")),
                record.get("total", 0),
                (" &middot; " + _escape(detail)) if detail else "",
            )
        )

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>PaperFeed &mdash; all digests</title>",
            "<style>%s</style></head><body><div class=\"wrap\">" % STYLE,
            "<h1>PaperFeed</h1>",
            '<p class="meta">%d digest%s, newest first</p>'
            % (len(records), "" if len(records) == 1 else "s"),
            "\n".join(rows) if rows else '<div class="empty">No digests yet.</div>',
            '<p class="footer"><a href="latest.html">Latest digest</a></p>',
            "</div></body></html>",
        ]
    )


def render_trends(themes, meta):
    """The quarterly trend briefing."""
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>PaperFeed trends &mdash; %s</title>" % _escape(meta.get("period", "")),
        "<style>%s</style></head><body><div class=\"wrap\">" % STYLE,
        "<h1>What's been happening</h1>",
        '<p class="meta">%s &middot; from %d papers across %d month%s</p>'
        % (
            _escape(meta.get("date_label", "")),
            meta.get("paper_count", 0),
            meta.get("months", 0),
            "" if meta.get("months") == 1 else "s",
        ),
    ]

    if meta.get("errors"):
        parts.append(
            '<div class="errors">%s</div>'
            % _escape("; ".join(str(error) for error in meta["errors"]))
        )

    if not themes:
        parts.append(
            '<div class="empty">No themes could be produced this time. '
            "The papers were fetched, but the summary step did not complete.</div>"
        )

    for theme in themes:
        block = ['<div class="theme">']
        block.append("<h3>%s</h3>" % _escape(theme.get("theme", "")))
        if theme.get("summary"):
            block.append("<p>%s</p>" % _escape(theme["summary"]))
        if theme.get("papers"):
            block.append("<ol>")
            for paper in theme["papers"]:
                block.append(
                    '<li><a href="https://doi.org/%s">%s</a></li>'
                    % (_escape(paper.get("doi", "")), _escape(paper.get("title", "")))
                )
            block.append("</ol>")
        if theme.get("cooling"):
            block.append('<div class="cooling">%s</div>' % _escape(theme["cooling"]))
        block.append("</div>")
        parts.append("".join(block))

    parts.append(
        '<p class="footer">Written by %s from titles and abstracts only. '
        "Every linked paper was in the set it was given &mdash; citations it "
        "could not be traced back to a real paper were dropped.</p>"
        % _escape(meta.get("model", "the model"))
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)


def date_label(moment=None):
    return (moment or datetime.now()).strftime("%A %d %B %Y, %H:%M")
