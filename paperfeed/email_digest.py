"""The alert email: the only part of PaperFeed that reaches you elsewhere.

This is a separate render from the web digest because mail clients are not
browsers, and the differences are not cosmetic:

  * <style> blocks are stripped by Gmail in several contexts, so every rule
    is written onto the element it applies to.
  * display:flex and CSS grid are unreliable, so layout is tables.
  * SVG is stripped, so no chart ever reaches the inbox.
  * <details> and JavaScript do not work, so everything is visible or absent.
  * Gmail clips a message at 102,400 bytes and hides the rest behind "View
    entire message", cutting mid-tag. We budget to 90 KB and trim ourselves,
    out loud, rather than let it happen silently.

What it used to send was a bare score, a title and an abstract - it dropped
the AI's one-line reason, the ranking reasons, every metric and every badge,
all of which had already been computed and paid for. That, rather than the
styling, was what made it useless: sixteen identical blocks with nothing to
say which to read first.
"""

from digest import _article_kind, _escape, _shorten

# Gmail clips at 102,400 bytes. Leave real headroom; the count is the
# message source, and quoted-printable encoding inflates it further.
SIZE_BUDGET = 90_000
CARDS_PER_TOPIC = 5
LEAD_ABSTRACT = 260
CARD_ABSTRACT = 170

# Deliberately not pure white on pure black: several clients invert dark
# mode automatically and the extremes inverted look worse than these do.
PAGE = "#f4f6f8"
CARD = "#ffffff"
INK = "#1f2429"
DIM = "#6a727c"
LINE = "#e2e5ea"
ACCENT = "#11467f"

CHIP_AI = ("#efe7f4", "#5c3f73")
CHIP_LOCAL = ("#eef1f5", "#3d4550")
CHIP_OA = ("#d9ead3", "#2c5d1e")
CHIP_PREPRINT = ("#f4ecdf", "#7a5a1e")
CHIP_KIND = ("#eae7f2", "#514266")
CHIP_BAD = ("#fbe4e4", "#8f2020")
CHIP_PLAIN = ("#f1f3f6", "#5a6068")


def _chip(text, colours, bold=True):
    background, ink = colours
    return (
        '<span style="display:inline-block;background:%s;color:%s;'
        "font-size:11px;font-weight:%s;line-height:1.5;padding:2px 8px;"
        'border-radius:9px;margin:0 5px 4px 0;">%s</span>'
        % (background, ink, "700" if bold else "600", text)
    )


def _cell(content, padding="13px 15px", extra=""):
    return (
        '<tr><td bgcolor="%s" style="background:%s;padding:%s;%s">%s</td></tr>'
        % (CARD, CARD, padding, extra, content)
    )


def _table(rows, extra=""):
    return (
        '<table role="presentation" width="100%%" cellpadding="0" '
        'cellspacing="0" border="0" style="width:100%%;border-collapse:'
        'separate;%s">%s</table>' % (extra, rows)
    )


def _card_table(rows, accent=False):
    return _table(
        rows,
        "background:%s;border:1px solid %s;border-radius:8px;margin:0 0 10px;%s"
        % (CARD, LINE,
           ("border-left:4px solid %s;" % ACCENT) if accent else ""),
    )


# --------------------------------------------------------------------------
# The pieces of a card
# --------------------------------------------------------------------------

def _scores(paper):
    """The same two labelled chips the web page shows, for the same reason:
    two bare numbers in front of a title is a puzzle, and there is no hover
    on a phone."""
    bits = []
    if getattr(paper, "ai_score", None) is not None:
        bits.append(_chip("AI %.0f" % paper.ai_score, CHIP_AI))
    if paper.score:
        bits.append(_chip("%.1f" % paper.score, CHIP_LOCAL))
    return "".join(bits)


def _badges(paper):
    """Preprint, review, open access, retracted - the shape of the thing."""
    bits = []
    if paper.source == "Preprint":
        bits.append(_chip("preprint", CHIP_PREPRINT))
    kind = _article_kind(paper)
    if kind and kind != "Preprint":
        bits.append(_chip(_escape(kind.lower()), CHIP_KIND))
    data = getattr(paper, "metrics", None) or {}
    if data.get("retracted"):
        bits.append(_chip("retracted", CHIP_BAD))
    if paper.free_fulltext or data.get("is_oa"):
        bits.append(_chip("free full text", CHIP_OA))
    if data.get("in_doaj") or (data.get("journal") or {}).get("in_doaj"):
        bits.append(_chip("in DOAJ", CHIP_PLAIN, bold=False))
    return "".join(bits)


def _metric_line(paper, full=False):
    """Citations and the journal figure.

    The caveat on the journal number - that OpenAlex counts every meeting
    abstract a journal prints - is real and load-bearing, but repeating it
    on twenty-two cards buries everything else. It rides on the lead card,
    where it will be read, and the works count carries it elsewhere: a
    journal with 19,824 indexed works is visibly not being measured on its
    research papers alone.
    """
    data = getattr(paper, "metrics", None) or {}
    bits = []
    if data.get("too_new"):
        bits.append("too new to have citations")
    elif data.get("citations") is not None:
        line = "cited %d (OpenAlex)" % data["citations"]
        if data.get("fwci"):
            line += ", %.1f&times; the average for its field and year" % data["fwci"]
        bits.append(line)
    elif paper.citations is not None:
        bits.append("cited %d (Europe PMC)" % paper.citations)

    journal = data.get("journal") or {}
    citedness = journal.get("two_year_mean_citedness")
    if citedness is not None:
        works = journal.get("works_count")
        bits.append(
            "journal citedness %.1f%s &mdash; OpenAlex%s"
            % (citedness, (" over %s works" % format(works, ",")) if works else "",
               ", counted over everything the journal prints, not only its "
               "research papers" if full else "")
        )
    if not bits:
        return ""
    return (
        '<p style="margin:7px 0 0;font-size:11.5px;color:%s;line-height:1.5;">'
        "%s</p>" % (DIM, " &nbsp;&middot;&nbsp; ".join(bits))
    )


def _why(paper, big=False):
    """The AI's one line about this paper, or the ranking reasons.

    This is the sharpest thing PaperFeed produces and the old email threw it
    away. When there is no API key there is no AI reason, so the local
    reasons stand in and the card still says something.
    """
    reason = (getattr(paper, "ai_reason", "") or "").strip()
    if reason:
        return (
            '<p style="margin:8px 0 0;font-size:%s;color:#43506a;'
            "line-height:1.55;border-left:2px solid #cdd7e6;padding-left:10px;"
            'font-style:italic;">%s</p>'
            % ("14px" if big else "13px", _escape(reason))
        )
    if paper.score_reasons:
        return (
            '<p style="margin:7px 0 0;font-size:12px;color:%s;'
            'line-height:1.5;">%s</p>'
            % (DIM, _escape("; ".join(paper.score_reasons)))
        )
    return ""


def _byline(paper):
    line = _escape(paper.author_line(limit=5))
    if paper.affiliation:
        line += ' <span style="color:%s;">&mdash; %s</span>' % (
            DIM, _escape(paper.affiliation))
    if not line:
        return ""
    return ('<p style="margin:5px 0 0;font-size:12.5px;color:#55606d;'
            'line-height:1.5;">%s</p>' % line)


def _where(paper):
    tail = [paper.venue or paper.source]
    if paper.published:
        tail.append(paper.published[:10])
    return ('<p style="margin:5px 0 0;font-size:11.5px;color:%s;">%s</p>'
            % (DIM, _escape(" &middot; ".join(bit for bit in tail if bit))
               .replace("&amp;middot;", "&middot;")))


def _button(href, label, primary=True):
    if primary:
        style = ("background:%s;color:#ffffff;border:1px solid %s;"
                 % (ACCENT, ACCENT))
    else:
        style = "background:%s;color:%s;border:1px solid %s;" % (CARD, ACCENT, LINE)
    return (
        '<a href="%s" style="display:inline-block;%stext-decoration:none;'
        "font-size:12.5px;font-weight:600;padding:8px 14px;border-radius:6px;"
        'margin:12px 8px 0 0;">%s</a>' % (_escape(href), style, label)
    )


def _actions(paper):
    bits = [_button(paper.url, "Read the paper &rarr;")]
    data = getattr(paper, "metrics", None) or {}
    pdf = paper.fulltext_url or data.get("oa_url") or ""
    if pdf and pdf != paper.url:
        bits.append(_button(pdf, "Free full text", primary=False))
    return "".join(bits)


def _title(paper, size):
    return (
        '<a href="%s" style="font-size:%s;font-weight:600;color:%s;'
        'text-decoration:none;line-height:1.35;">%s</a>'
        % (_escape(paper.url), size, ACCENT, _escape(paper.title))
    )


# --------------------------------------------------------------------------
# Three densities
# --------------------------------------------------------------------------

def _lead_card(paper):
    """The one paper to read first, set larger and given the reason in full."""
    inner = [
        '<p style="margin:0 0 6px;font-size:10.5px;font-weight:700;'
        'letter-spacing:.08em;text-transform:uppercase;color:%s;">'
        "Start here</p>" % DIM,
        '<p style="margin:0 0 6px;">%s</p>' % _scores(paper),
        _title(paper, "18px"),
        _byline(paper),
        _where(paper),
        _why(paper, big=True),
        '<p style="margin:9px 0 0;">%s</p>' % _badges(paper) if _badges(paper) else "",
        _metric_line(paper, full=True),
    ]
    if paper.abstract:
        inner.append(
            '<p style="margin:9px 0 0;font-size:13px;color:#3c444d;'
            'line-height:1.55;">%s</p>'
            % _escape(_shorten(paper.abstract, LEAD_ABSTRACT))
        )
    inner.append(_actions(paper))
    return _card_table(_cell("".join(bit for bit in inner if bit),
                             padding="15px 17px"), accent=True)


def _card(paper):
    inner = [
        '<p style="margin:0 0 5px;">%s</p>' % _scores(paper) if _scores(paper) else "",
        _title(paper, "15.5px"),
        _byline(paper),
        _where(paper),
        _why(paper),
        '<p style="margin:8px 0 0;">%s</p>' % _badges(paper) if _badges(paper) else "",
        _metric_line(paper),
    ]
    if paper.abstract:
        inner.append(
            '<p style="margin:8px 0 0;font-size:12.5px;color:#4a525b;'
            'line-height:1.5;">%s</p>'
            % _escape(_shorten(paper.abstract, CARD_ABSTRACT))
        )
    return _card_table(_cell("".join(bit for bit in inner if bit)))


def _rows(papers):
    """The tail of a topic, one line each.

    These used to be invisible behind "and 3 more in this topic", which told
    you a number and nothing else. A title and a score is enough to decide.
    """
    if not papers:
        return ""
    lines = []
    for paper in papers:
        marks = []
        if getattr(paper, "ai_score", None) is not None:
            marks.append("AI %.0f" % paper.ai_score)
        if paper.score:
            marks.append("%.1f" % paper.score)
        lines.append(
            '<tr><td bgcolor="%s" width="72" valign="top" style="background:%s;'
            "padding:8px 6px 8px 14px;font-size:11px;color:%s;"
            'white-space:nowrap;border-top:1px solid %s;">%s</td>'
            '<td bgcolor="%s" style="background:%s;padding:8px 14px 8px 0;'
            'font-size:13px;line-height:1.45;border-top:1px solid %s;">'
            '<a href="%s" style="color:%s;text-decoration:none;">%s</a></td></tr>'
            % (CARD, CARD, DIM, LINE, " &middot; ".join(marks) or "&nbsp;",
               CARD, CARD, LINE, _escape(paper.url), ACCENT,
               _escape(paper.title))
        )
    return _table(
        "".join(lines),
        "background:%s;border:1px solid %s;border-radius:8px;margin:0 0 10px;"
        % (CARD, LINE),
    )


# --------------------------------------------------------------------------
# Subject line and preheader
# --------------------------------------------------------------------------

def _best(groups):
    """The paper the subject line should name: AI score first, then local."""
    everything = [paper for _, papers in groups for paper in papers]
    if not everything:
        return None
    return max(
        everything,
        key=lambda paper: (
            getattr(paper, "ai_score", None) if getattr(paper, "ai_score", None)
            is not None else -1,
            paper.score,
        ),
    )


def subject_line(groups, meta, prefix="[PaperFeed]"):
    """Count plus the best paper's title.

    On a phone the subject is often the whole message you see, so
    "16 new papers - 22 Sep" was a wasted line. A newline in a subject would
    let a header be injected, so they are stripped before anything else.
    """
    total = sum(len(papers) for _, papers in groups)
    head = "%s %d new" % (prefix, total) if total else "%s nothing new" % prefix

    best = _best(groups)
    if best is None:
        return "%s &middot; %s" % (head, meta.get("date_label", "")).replace(
            "&middot;", "-")

    title = " ".join((best.title or "").split())
    room = max(24, 96 - len(head) - 3)
    if len(title) > room:
        title = title[:room].rsplit(" ", 1)[0] + "..."
    return "%s - %s" % (head, title)


def preheader(groups, meta):
    """The grey line beside the subject in the inbox. There was none, so
    Gmail was pulling in "PaperFeed Tuesday 22 September"."""
    bits = []
    named = [(name, len(papers)) for name, papers in groups if papers]
    if named:
        bits.append(", ".join("%d in %s" % (count, name) for name, count in named[:3]))
    best = _best(groups)
    if best is not None and getattr(best, "ai_score", None) is not None:
        bits.append("best match %.0f/10" % best.ai_score)
    free = sum(1 for _, papers in groups for paper in papers
               if paper.free_fulltext or (getattr(paper, "metrics", None) or {}).get("is_oa"))
    if free:
        bits.append("%d free to read" % free)
    return " &middot; ".join(bits) or _escape(meta.get("date_label", ""))


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

def _masthead(meta, total):
    matched = meta.get("window_total") or 0
    line = "%d new paper%s" % (total, "" if total == 1 else "s")
    if matched:
        line += ", out of %s that matched in the last %d days" % (
            format(matched, ","), meta.get("lookback_days", 14))
    return _table(
        '<tr><td bgcolor="%s" style="background:%s;padding:0 2px 14px;">'
        '<p style="margin:0;font-size:19px;font-weight:700;color:%s;letter-spacing:'
        '-0.01em;">PaperFeed</p>'
        '<p style="margin:3px 0 0;font-size:12.5px;color:%s;">%s &middot; %s</p>'
        "</td></tr>" % (PAGE, PAGE, INK, DIM,
                        _escape(meta.get("date_label", "")), line),
        "background:%s;" % PAGE,
    )


def _notice(text, colour="#c2703a"):
    return _table(
        '<tr><td bgcolor="%s" style="background:%s;border-left:3px solid %s;'
        'padding:11px 14px;font-size:12.5px;color:#4a525b;line-height:1.5;">'
        "%s</td></tr>" % (CARD, CARD, colour, text),
        "border:1px solid %s;border-radius:8px;margin:0 0 10px;" % LINE,
    )


def _heading(name, count):
    return (
        '<p style="margin:22px 0 9px;font-size:11.5px;font-weight:700;'
        "letter-spacing:.07em;text-transform:uppercase;color:#55606d;"
        'border-bottom:2px solid #dde1e7;padding-bottom:6px;">%s (%d)</p>'
        % (_escape(name), count)
    )


def _footer(meta):
    bits = []
    library = meta.get("library") or {}
    if library.get("total"):
        line = "%d kept in your library, %d unread" % (
            library["total"], library.get("untouched", 0))
        bits.append(line + ".")
    bits.append(
        "The full digest, with every paper and the dashboards, is on your Mac: "
        "run <b>python3 paperfeed.py serve</b> in the paperfeed folder."
    )
    bits.append(
        "Change how often this arrives, or what it searches for, in "
        "config.json."
    )
    return _table(
        '<tr><td bgcolor="%s" style="background:%s;padding:18px 2px 0;'
        'font-size:11.5px;color:%s;line-height:1.6;">%s</td></tr>'
        % (PAGE, PAGE, DIM, "<br>".join(bits)),
        "background:%s;" % PAGE,
    )


def _body(groups, meta, cards_per_topic, tail_limit):
    total = sum(len(papers) for _, papers in groups)
    parts = [_masthead(meta, total)]

    if meta.get("errors"):
        parts.append(_notice(
            "Some sources did not respond, so this may be short: %s"
            % _escape("; ".join(str(error) for error in meta["errors"]))))

    if not total:
        parts.append(_notice(
            "Nothing new this time. Everything matching your topics in the "
            "last %d days has already been shown to you."
            % meta.get("lookback_days", 14), colour="#5b87c4"))
        parts.append(_footer(meta))
        return "".join(parts)

    lead = _best(groups)
    parts.append(_lead_card(lead))

    rising = meta.get("rising") or []
    if rising:
        parts.append(_notice(
            "Moving in your field: %s" % _escape("; ".join(rising)),
            colour="#5b9c62"))

    trimmed = 0
    for name, papers in groups:
        if not papers:
            continue
        parts.append(_heading(name, len(papers)))
        shown = 0
        for paper in papers[:cards_per_topic]:
            if paper is lead:
                continue            # already at the top, in full
            parts.append(_card(paper))
            shown += 1
        rest = [paper for paper in papers[cards_per_topic:] if paper is not lead]
        if tail_limit is not None and len(rest) > tail_limit:
            trimmed += len(rest) - tail_limit
            rest = rest[:tail_limit]

        parts.append(_rows(rest))
        if not shown and not rest:
            parts.append(_notice("Only the paper above.", colour="#5b87c4"))

    if trimmed:
        # The email trimming itself still has to say so, same rule as a filter.
        parts.append(_notice(
            "%d more paper%s would not fit in an email without it being cut "
            "short by your mail client. They are all in the digest on your "
            "Mac." % (trimmed, "" if trimmed == 1 else "s")))

    parts.append(_footer(meta))
    return "".join(parts)


# Progressively smaller shapes, tried in order until one fits. Shortening
# the one-line tails first costs least; dropping full cards costs most, and
# with twenty keyword sets even five cards each would overflow, so that has
# to be reachable too.
SHAPES = ((5, None), (5, 30), (5, 12), (3, 8), (2, 4), (1, 0))


def render(groups, meta):
    """The whole message, trimmed to fit inside Gmail's clipping limit."""
    for cards, tail_limit in SHAPES:
        body = _body(groups, meta, cards, tail_limit)
        page = _page(body, preheader(groups, meta), meta)
        if len(page.encode("utf-8")) <= SIZE_BUDGET:
            return page
    return page        # already as small as this design goes


def _page(body, pre, meta):
    return "".join([
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        # Tells clients the message has been designed for both schemes, which
        # stops some of them inverting it for themselves.
        '<meta name="color-scheme" content="light dark">',
        '<meta name="supported-color-schemes" content="light dark">',
        "<title>PaperFeed &mdash; %s</title>" % _escape(meta.get("date_label", "")),
        "</head>",
        '<body style="margin:0;padding:0;background:%s;color:%s;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,"
        'Arial,sans-serif;">' % (PAGE, INK),
        # The preheader: shown in the inbox list, never in the open message.
        '<div style="display:none;font-size:1px;line-height:1px;max-height:0;'
        'max-width:0;opacity:0;overflow:hidden;">%s</div>' % pre,
        '<table role="presentation" width="100%%" cellpadding="0" '
        'cellspacing="0" border="0" bgcolor="%s" style="background:%s;">'
        '<tr><td align="center" bgcolor="%s" style="background:%s;'
        'padding:18px 10px 34px;">'
        '<table role="presentation" width="100%%" cellpadding="0" '
        'cellspacing="0" border="0" style="width:100%%;max-width:600px;">'
        '<tr><td align="left" bgcolor="%s" style="background:%s;">'
        % (PAGE, PAGE, PAGE, PAGE, PAGE, PAGE),
        body,
        "</td></tr></table></td></tr></table></body></html>",
    ])


# --------------------------------------------------------------------------
# The plain-text alternative
# --------------------------------------------------------------------------

def render_text(groups, meta):
    """Mirrors the HTML: labelled scores, the AI reason, and real links."""
    total = sum(len(papers) for _, papers in groups)
    out = ["PaperFeed - %s" % meta.get("date_label", ""),
           "%d new paper%s, searched the last %d days"
           % (total, "" if total == 1 else "s", meta.get("lookback_days", 14)),
           ""]

    if not total:
        out.append("Nothing new this time.")
        return "\n".join(out) + "\n"

    for name, papers in groups:
        if not papers:
            continue
        out.append(name.upper())
        out.append("-" * len(name))
        for paper in papers:
            marks = []
            if getattr(paper, "ai_score", None) is not None:
                marks.append("AI %.0f/10" % paper.ai_score)
            if paper.score:
                marks.append("score %.1f" % paper.score)
            out.append("[%s] %s" % (", ".join(marks) or "-", paper.title))
            byline = paper.author_line(limit=5)
            if byline:
                out.append("  %s" % byline)
            where = [paper.source]
            if paper.venue:
                where.append(paper.venue)
            if paper.published:
                where.append(paper.published[:10])
            out.append("  %s" % " | ".join(where))
            reason = (getattr(paper, "ai_reason", "") or "").strip()
            if reason:
                out.append("  Why: %s" % reason)
            elif paper.score_reasons:
                out.append("  Why: %s" % "; ".join(paper.score_reasons))
            data = getattr(paper, "metrics", None) or {}
            if data.get("retracted"):
                out.append("  RETRACTED (OpenAlex)")
            if data.get("too_new"):
                out.append("  Too new to have citations")
            elif data.get("citations") is not None:
                out.append("  Cited %d (OpenAlex)" % data["citations"])
            out.append("  %s" % paper.url)
            out.append("")
        out.append("")

    library = meta.get("library") or {}
    if library.get("total"):
        out.append("%d kept in your library, %d unread."
                   % (library["total"], library.get("untouched", 0)))
    out.append("Full digest on your Mac: python3 paperfeed.py serve")
    return "\n".join(out) + "\n"
