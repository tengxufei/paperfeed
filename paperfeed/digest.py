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
import re

import phrasing
from datetime import datetime

HIGHLIGHT_COUNT = 5           # papers shown per keyword set before the fold
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
.wrap { max-width: 820px; margin: 0 auto; }

/* jump-nav (A3) */
.jump {
  position: sticky; top: 0; z-index: 20; margin: 0 -16px 18px;
  padding: 9px 16px; background: rgba(246,247,249,.94);
  backdrop-filter: blur(6px); border-bottom: 1px solid #e0e3e8;
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
  font-size: 12.5px;
}
.jump a { color: #33507a; text-decoration: none; padding: 3px 9px;
          border-radius: 11px; background: #eceff3; }
.jump a:hover { background: #dfe5ee; }
.jump a b { color: #6a727c; font-weight: 600; }
.jump .spacer { margin-left: auto; }
.jump a.pf-page {
  background: none; color: #6a727c; text-decoration: underline;
  text-underline-offset: 3px; padding: 3px 4px; font-weight: 500;
}
.jump a.pf-page:hover { background: none; color: #11467f; }
.jump .sep { color: #c3c8cf; }
.jump label { cursor: pointer; user-select: none; color: #55606d; }

/* compact mode (A1) - pure CSS, driven by a checkbox, so the file still
   needs no JavaScript to work */
#compact { position: absolute; opacity: 0; pointer-events: none; }
#compact:checked ~ .wrap .paper {
  padding: 6px 12px; margin-bottom: 5px; border-radius: 5px;
}
#compact:checked ~ .wrap .paper .byline,
#compact:checked ~ .wrap .paper .why,
#compact:checked ~ .wrap .paper .chips,
#compact:checked ~ .wrap .paper details,
#compact:checked ~ .wrap .paper .ai-why { display: none; }
#compact:checked ~ .wrap .paper .head { align-items: center; }
#compact:checked ~ .wrap .paper a.title { font-size: 14px; font-weight: 500; }
#compact:checked ~ .wrap .paper .tags {
  margin: 2px 0 0 48px; font-size: 11px; opacity: .8;
}
#compact:checked ~ .wrap .showing { margin-bottom: 6px; }
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
details.set { margin: 0 0 18px; }
details.set > summary {
  list-style: none; cursor: pointer; user-select: none;
  /* no `display` here: in WebKit it stops <summary> toggling at all */
  padding: 11px 14px; border-radius: 8px;
  background: #e9edf2; border: 1px solid #d6dbe2;
  font-size: 15px; font-weight: 600; color: #26456f;
}
details.set > summary::-webkit-details-marker { display: none; }
.setbar { display: flex; align-items: baseline; gap: 10px; }
.setbar::before { content: "\u25b8"; font-size: 12px; color: #7d8894;
  transition: transform .15s ease; display: inline-block; }

details.set[open] > summary .setbar::before { transform: rotate(90deg); }
details.set > summary:hover { background: #dfe5ee; }
.setcount {
  margin-left: auto; font-weight: 600; font-size: 12px; color: #55606d;
  background: #fff; border-radius: 10px; padding: 1px 9px;
}
.setbody { padding: 14px 0 0; }
.showing { font-size: 12px; color: #7d8894; margin: 0 0 10px 2px; }
details.rest > summary {
  cursor: pointer; font-size: 13px; color: #11467f; padding: 8px 2px;
  width: fit-content;
}
details.rest { margin-top: 4px; }
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
.paper a.title { font-size: 16.5px; font-weight: 600; color: #11467f;
                 text-decoration: none; line-height: 1.35; }
.paper a.title:hover { text-decoration: underline; }
.byline { font-size: 13px; color: #555; margin: 5px 0 0; line-height: 1.45; }
.affil { color: #777; font-style: italic; }
.why { font-size: 12px; color: #6b7a52; margin: 5px 0 0; }
.ai-why { font-size: 13px; color: #573a78; margin: 6px 0 0; }
.theme { background: #fff; border: 1px solid #e2e4e8; border-radius: 8px;
         padding: 18px 20px; margin-bottom: 16px; border-left: 4px solid #6f86a8; }
.theme h3 { margin: 0 0 10px; font-size: 18px; color: #11467f;
            line-height: 1.3; letter-spacing: -.01em; }
.theme .num { display: inline-block; width: 26px; height: 26px; margin-right: 9px;
              border-radius: 50%; background: #e8edf4; color: #33507a;
              font-size: 13px; line-height: 26px; text-align: center;
              font-weight: 700; vertical-align: 2px; }
.theme p { line-height: 1.6; }
.theme ol { margin: 0; padding-left: 20px; font-size: 13px; }
.theme ol li { margin-bottom: 5px; }
.theme .reflabel { font-size: 11.5px; text-transform: uppercase;
                   letter-spacing: .05em; color: #77818d; margin: 12px 0 5px; }
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
@media print {
  body { background: #fff; color: #000; padding: 0; }
  .jump, .pf-bar, .pf-save { display: none !important; }
  .paper { break-inside: avoid; border-color: #ccc; box-shadow: none; }
  details { display: block; }
  details > summary { display: none; }
  a { color: #000; text-decoration: none; }
  .paper a.title::after { content: " (" attr(href) ")"; font-size: 10px;
                          font-weight: 400; color: #555; word-break: break-all; }
}
@media (prefers-color-scheme: dark) {
  body { background: #16181c; color: #e6e6e6; }
  .jump { background: rgba(22,24,28,.94); border-bottom-color: #2e333a; }
  .jump a { background: #282c33; color: #9dbbe4; }
  .jump a:hover { background: #333941; }
  .jump label { color: #98a0a8; }
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
  details.set > summary { background: #23272e; border-color: #353a42; color: #a8c4e8; }
  details.set > summary:hover { background: #2a2f37; }
  .setcount { background: #16181c; color: #98a0a8; }
  .showing { color: #79828d; }
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


def _slug(name):
    return "s-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


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
            '<span class="%s" style="--fill:%d%%" title="%s">%.1f</span>'
            % (
                _score_class(paper.score),
                int(min(100, paper.score * 10)),
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
        "<style>%s</style></head><body>" % STYLE,
        '<input type="checkbox" id="compact">',
        '<div class="wrap">',
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

    jump = [
        '<a href="#%s">%s <b>%d</b></a>' % (_slug(name), _escape(name), len(papers))
        for name, papers in groups
        if papers
    ]
    if jump:
        parts.append(
            '<nav class="jump">%s<span class="spacer"></span>'
            '<label for="compact">compact list</label>'
            '<span class="sep">|</span>'
            '<a class="pf-page" href="dashboard.html">dashboard</a></nav>'
            % "".join(jump)
        )

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
        # One collapsible section per keyword set. Each opens on its best
        # few papers; the rest of that set stays behind a second fold, so a
        # 200-paper run is still a page you can read top to bottom.
        cut = meta.get("highlight_count", HIGHLIGHT_COUNT)
        for name, papers in groups:
            if not papers:
                continue
            best, rest = papers[:cut], papers[cut:]
            parts.append(
                '<details class="set" id="%s" open><summary><div class="setbar">'
                % _slug(name)
            )
            parts.append("<span>%s</span>" % _escape(name))
            parts.append(
                '<span class="setcount">%d new</span></div></summary>'
                '<div class="setbody">' % len(papers)
            )
            if rest:
                parts.append(
                    '<p class="showing">Top %d of %d, best first</p>'
                    % (len(best), len(papers))
                )
            parts.extend(_paper_html(paper, show_scores) for paper in best)
            if rest:
                parts.append(
                    '<details class="rest"><summary>Show the other %d</summary>'
                    % len(rest)
                )
                parts.extend(_paper_html(paper, show_scores) for paper in rest)
                parts.append("</details>")
            parts.append("</div></details>")

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
        cut = meta.get("highlight_count", HIGHLIGHT_COUNT)
        for name, papers in groups:
            if not papers:
                continue
            parts.append(
                '<h2 style="%s">%s (%d)</h2>' % (E_H2, _escape(name), len(papers))
            )
            parts.extend(_paper_email(paper, show_scores) for paper in papers[:cut])
            if len(papers) > cut:
                parts.append(
                    '<p style="font-size:13px;color:#777;margin:0 0 12px;">'
                    "and %d more in this topic &mdash; see the full digest on "
                    "your Mac.</p>" % (len(papers) - cut)
                )

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
        "%s, searched the last %s"
        % (
            phrasing.count(meta.get("total_new", 0), "new paper"),
            phrasing.count(meta.get("lookback_days", 0), "day"),
        ),
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
        '<p class="meta">%s &middot; from %s across %s</p>'
        % (
            _escape(meta.get("date_label", "")),
            phrasing.count(meta.get("paper_count", 0), "paper"),
            phrasing.count(meta.get("months", 0), "month"),
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

    for number, theme in enumerate(themes, 1):
        block = ['<div class="theme">']
        block.append(
            '<h3><span class="num">%d</span>%s</h3>'
            % (number, _escape(theme.get("theme", "")))
        )
        if theme.get("summary"):
            block.append("<p>%s</p>" % _escape(theme["summary"]))
        if theme.get("papers"):
            block.append('<div class="reflabel">Representative papers</div>')
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
