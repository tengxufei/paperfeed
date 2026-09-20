"""Turn a list of papers into a readable digest.

One renderer produces both the local file and the email body, so the two can
never drift apart. There is also a plain-text version for mail clients that
refuse HTML.
"""

import html
from datetime import datetime

ABSTRACT_CHARS = 420

STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.55; margin: 0; padding: 24px 16px 64px;
  background: #f6f7f9; color: #1a1a1a;
}
.wrap { max-width: 760px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
.meta { color: #666; font-size: 13px; margin-bottom: 24px; }
h2 {
  font-size: 15px; text-transform: uppercase; letter-spacing: .06em;
  color: #444; border-bottom: 2px solid #d8dadd;
  padding-bottom: 6px; margin: 32px 0 12px;
}
.count { color: #888; font-weight: normal; text-transform: none; letter-spacing: 0; }
.paper {
  background: #fff; border: 1px solid #e2e4e8; border-radius: 8px;
  padding: 14px 16px; margin-bottom: 12px;
}
.paper a.title { font-size: 16px; font-weight: 600; color: #11467f; text-decoration: none; }
.paper a.title:hover { text-decoration: underline; }
.byline { font-size: 13px; color: #555; margin: 6px 0 0; }
.tags { font-size: 12px; color: #777; margin-top: 6px; }
.badge {
  display: inline-block; padding: 1px 7px; border-radius: 10px;
  background: #e8edf4; color: #33507a; font-weight: 600; margin-right: 6px;
}
.badge.preprint { background: #f4ecdf; color: #7a5a1e; }
.abstract { font-size: 13.5px; color: #333; margin: 10px 0 0; }
.empty, .errors {
  background: #fff; border: 1px solid #e2e4e8; border-left: 4px solid #9aa3ad;
  border-radius: 6px; padding: 14px 16px; font-size: 14px;
}
.errors { border-left-color: #c2703a; margin-bottom: 20px; }
.errors ul { margin: 6px 0 0; padding-left: 20px; }
.footer { margin-top: 40px; font-size: 12px; color: #888; }
@media (prefers-color-scheme: dark) {
  body { background: #16181c; color: #e6e6e6; }
  h2 { color: #b9bec6; border-bottom-color: #33373d; }
  .paper, .empty, .errors { background: #1f2228; border-color: #33373d; }
  .paper a.title { color: #86b3ec; }
  .byline { color: #a8aeb6; }
  .abstract { color: #c9ced5; }
  .badge { background: #2a3547; color: #9dbbe4; }
  .badge.preprint { background: #3b3325; color: #d7b377; }
}
"""


def _shorten(text, limit=ABSTRACT_CHARS):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _paper_html(paper):
    badge_class = "badge preprint" if paper.source == "Preprint" else "badge"
    bits = [
        '<div class="paper">',
        '<a class="title" href="%s">%s</a>' % (html.escape(paper.url), html.escape(paper.title)),
    ]
    byline = paper.author_line()
    if byline:
        bits.append('<p class="byline">%s</p>' % html.escape(byline))

    tags = ['<span class="%s">%s</span>' % (badge_class, html.escape(paper.source))]
    if paper.venue:
        tags.append(html.escape(paper.venue))
    if paper.published:
        tags.append(html.escape(paper.published))
    if paper.doi:
        tags.append("doi:" + html.escape(paper.doi))
    bits.append('<div class="tags">%s</div>' % " &middot; ".join(tags))

    if paper.abstract:
        bits.append('<p class="abstract">%s</p>' % html.escape(_shorten(paper.abstract)))
    bits.append("</div>")
    return "\n".join(bits)


def render_html(groups, meta):
    """groups is a list of (keyword set name, [Paper]) — including empty ones."""
    total = meta.get("total_new", 0)
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>PaperFeed &mdash; %s</title>" % html.escape(meta.get("date_label", "")),
        "<style>%s</style></head><body><div class=\"wrap\">" % STYLE,
        "<h1>PaperFeed</h1>",
        '<p class="meta">%s &middot; %d new paper%s &middot; searched the last %d days</p>'
        % (
            html.escape(meta.get("date_label", "")),
            total,
            "" if total == 1 else "s",
            meta.get("lookback_days", 0),
        ),
    ]

    if meta.get("errors"):
        parts.append('<div class="errors"><strong>Some sources did not respond.</strong>')
        parts.append("<ul>")
        for message in meta["errors"]:
            parts.append("<li>%s</li>" % html.escape(str(message)))
        parts.append("</ul><p>The list below may be incomplete for those sources.</p></div>")

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
        for name, papers in groups:
            if not papers:
                continue
            parts.append(
                '<h2>%s <span class="count">(%d)</span></h2>'
                % (html.escape(name), len(papers))
            )
            parts.extend(_paper_html(paper) for paper in papers)

    empty_names = [name for name, papers in groups if not papers]
    if empty_names and total:
        parts.append(
            '<p class="footer">Nothing new for: %s</p>'
            % html.escape(", ".join(empty_names))
        )

    parts.append(
        '<p class="footer">Generated by PaperFeed from %s. '
        "Edit config.json to change keywords or the schedule.</p>"
        % html.escape(meta.get("sources_label", ""))
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)


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
                lines.append(paper.title)
                if paper.author_line():
                    lines.append("  " + paper.author_line())
                detail = " | ".join(
                    bit for bit in (paper.source, paper.venue, paper.published) if bit
                )
                lines.append("  " + detail)
                lines.append("  " + paper.url)
                lines.append("")
    return "\n".join(lines)


def date_label(moment=None):
    return (moment or datetime.now()).strftime("%A %d %B %Y, %H:%M")
