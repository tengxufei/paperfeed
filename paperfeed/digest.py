"""Turn a list of papers into something worth reading, on the web.

  render_html     the local digest, the thing we always write first
  render_index    the list of past digests
  render_trends   the periodic themed briefing

This module also owns the shell (sidebar, filter, score key) that the library
and dashboard pages sit in, and the per-paper pieces they share.

The email is NOT here - see email_digest.py. Mail clients strip <style>,
ignore flex and grid, drop SVG and refuse <details>, so it is a separate
render rather than this one with different colours.

The local file embeds a small JSON blob per paper. It is invisible in the
browser, and it is what lets `paperfeed serve` offer a Save button later
without PaperFeed having to store papers you never asked it to keep.
"""

import html
import json
import re

import metrics
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
#compact:checked ~ .wrap .paper,
#compact:checked ~ .app .paper {
  padding: 6px 12px; margin-bottom: 5px; border-radius: 5px;
}
#compact:checked ~ .wrap .paper .byline,
#compact:checked ~ .wrap .paper .why,
#compact:checked ~ .wrap .paper .chips,
#compact:checked ~ .wrap .paper details,
#compact:checked ~ .wrap .paper .ai-why,
#compact:checked ~ .app .paper .byline,
#compact:checked ~ .app .paper .why,
#compact:checked ~ .app .paper .chips,
#compact:checked ~ .app .paper .metrics,
#compact:checked ~ .app .paper details,
#compact:checked ~ .app .paper .ai-why { display: none; }
#compact:checked ~ .wrap .paper .head,
#compact:checked ~ .app .paper .head { align-items: center; }
#compact:checked ~ .wrap .paper a.title,
#compact:checked ~ .app .paper a.title { font-size: 14px; font-weight: 500; }
#compact:checked ~ .wrap .paper .tags,
#compact:checked ~ .app .paper .tags {
  margin: 2px 0 0 48px; font-size: 11px; opacity: .8;
}
#compact:checked ~ .wrap .showing,
#compact:checked ~ .app .showing { margin-bottom: 6px; }
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
.badge.kind { background: #efe7f4; color: #5c3f73; }
.badge.new { background: #2c5d1e; color: #ffffff; letter-spacing: .04em; }
.metrics { margin-top: 7px; display: flex; flex-wrap: wrap; gap: 5px; }
.metric {
  display: inline-block; font-size: 11.5px; padding: 2px 8px;
  border-radius: 4px; background: #f2f5f9; color: #46505e;
  border: 1px solid #e2e8f0; cursor: help;
}
.metric .src {
  display: inline-block; margin-left: 6px; font-size: 10px;
  color: #8a929c; text-transform: uppercase; letter-spacing: 0.02em;
}
.metric.quiet { background: #fafafa; color: #8a929c; font-style: italic; }
.metric.bad { background: #fbe4e4; color: #8f2020; border-color: #f0c4c4;
              font-weight: 600; }
.metric.own { background: #fff6e5; border-color: #f0e0c0; color: #6b5320; }
.sourced {
  font-size: 12px; color: #6b727b; margin: 10px 0 0; line-height: 1.55;
  border-left: 3px solid #dfe3e8; padding-left: 10px;
}
.score.ai .tag {
  font-size: 8.5px; font-weight: 700; letter-spacing: .06em;
  opacity: .8; margin-right: 3px; vertical-align: 1px;
}
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
  .badge.kind { background: #322a3d; color: #c0a6dd; }
  .badge.new { background: #3f7a2c; color: #ffffff; }
  .metric { background: #23272e; color: #a8b0ba; border-color: #32373f; }
  .metric .src { color: #767d87; }
  .metric.quiet { background: #1e2126; color: #767d87; }
  .metric.bad { background: #3a2222; color: #e8a0a0; border-color: #543030; }
  .metric.own { background: #332c1e; border-color: #4a4130; color: #d7b377; }
  .sourced { color: #8d949c; border-left-color: #353a42; }
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



# --------------------------------------------------------------------------
# The shell every page sits in: a left rail, and the page beside it
# --------------------------------------------------------------------------
#
# The digest is opened three ways - straight off disk, through `serve`, and
# (in its own separate render) as an email. So the rail is pure CSS and the
# drawer on a narrow screen is a checkbox, not a script: the file works with
# JavaScript switched off, exactly as the compact-mode toggle already does.

SHELL_CSS = r"""
:root {
  --rail: 248px; --ink: #1a1a1a; --dim: #6a727c; --line: #e2e5ea;
  --panel: #ffffff; --page: #f6f7f9; --accent: #11467f; --accent-soft: #eaf0f8;
}
body.shell { padding: 0; }
.app { display: flex; align-items: flex-start; min-height: 100vh; }
.side {
  width: var(--rail); flex: 0 0 var(--rail); align-self: stretch;
  background: var(--panel); border-right: 1px solid var(--line);
  padding: 20px 16px 40px; position: sticky; top: 0; height: 100vh;
  overflow-y: auto; box-sizing: border-box;
}
.main { flex: 1 1 auto; min-width: 0; padding: 22px 18px 64px; }
.main > .wrap { max-width: 860px; margin: 0 auto; }

.brand { display: flex; align-items: baseline; gap: 8px; margin: 0 0 2px; }
.brand b { font-size: 17px; letter-spacing: -0.01em; color: var(--ink); }
.brand span { font-size: 11px; color: var(--dim); }
.side .when { font-size: 11.5px; color: var(--dim); margin: 0 0 18px; }

.rail-h {
  font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em;
  color: #9aa1aa; margin: 20px 0 7px; font-weight: 600;
}
.rail-nav { display: flex; flex-direction: column; gap: 1px; }
.rail-nav a {
  display: flex; align-items: center; gap: 9px; padding: 7px 9px;
  border-radius: 7px; text-decoration: none; font-size: 13.5px;
  color: #3d4550;
}
.rail-nav a:hover { background: #f1f3f6; }
.rail-nav a.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
.rail-nav a .ico { width: 15px; text-align: center; opacity: .75; font-size: 13px; }
.rail-nav a .n {
  margin-left: auto; font-size: 11px; color: var(--dim);
  background: #f0f2f5; border-radius: 9px; padding: 0 6px; font-weight: 600;
}
.rail-nav a.on .n { background: #ffffff; color: var(--accent); }
.rail-nav a.off { opacity: .45; cursor: help; }

.rail-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.rail-stat {
  background: #f7f8fa; border: 1px solid var(--line); border-radius: 7px;
  padding: 7px 8px;
}
.rail-stat b { display: block; font-size: 16px; line-height: 1.2; color: var(--ink); }
.rail-stat span { font-size: 10.5px; color: var(--dim); }

.key { font-size: 11.5px; color: var(--dim); line-height: 1.5; }
.key .row { display: flex; gap: 8px; align-items: flex-start; margin-bottom: 8px; }
.key .row > :first-child { flex: 0 0 auto; margin-top: 1px; }

.filterbox {
  width: 100%; box-sizing: border-box; font: inherit; font-size: 13px;
  padding: 7px 10px; border: 1px solid var(--line); border-radius: 7px;
  background: #fbfbfc; color: var(--ink);
}
.filterbox:focus { outline: none; border-color: #b9c8dd; background: #fff; }
.viewopts { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; }
.viewopts label {
  display: flex; align-items: center; gap: 7px; cursor: pointer;
  color: #46505e; user-select: none;
}
.switch .dot {
  width: 26px; height: 15px; border-radius: 8px; background: #d8dde4;
  position: relative; transition: background .15s; flex: 0 0 auto;
}
.switch .dot::after {
  content: ""; position: absolute; top: 2px; left: 2px; width: 11px;
  height: 11px; border-radius: 50%; background: #fff; transition: left .15s;
}
#compact:checked ~ .app .switch .dot { background: #5b87c4; }
#compact:checked ~ .app .switch .dot::after { left: 13px; }
.hitcount { font-size: 11.5px; color: var(--dim); margin: 8px 0 0; }

/* The drawer button and scrim exist only on narrow screens. */
#navtoggle { position: absolute; opacity: 0; pointer-events: none; }
.topbar { display: none; }
.scrim { display: none; }

@media (max-width: 899px) {
  .side {
    position: fixed; z-index: 60; top: 0; left: 0; bottom: 0; height: 100%;
    transform: translateX(-100%); transition: transform .18s ease-out;
    box-shadow: 0 0 24px rgba(0,0,0,.14);
  }
  #navtoggle:checked ~ .app .side { transform: none; }
  #navtoggle:checked ~ .app .scrim {
    display: block; position: fixed; inset: 0; z-index: 50;
    background: rgba(20,24,30,.34);
  }
  .main { padding-top: 8px; }
  .topbar {
    display: flex; align-items: center; gap: 10px; position: sticky; top: 0;
    z-index: 30; margin: -8px -18px 12px; padding: 9px 14px;
    background: rgba(246,247,249,.95); backdrop-filter: blur(6px);
    border-bottom: 1px solid var(--line);
  }
  .topbar label.burger {
    cursor: pointer; font-size: 17px; line-height: 1; padding: 3px 9px;
    border: 1px solid var(--line); border-radius: 7px; background: #fff;
    color: #3d4550;
  }
  .topbar .here { font-size: 13.5px; font-weight: 600; color: var(--ink); }
}

@media (prefers-color-scheme: dark) {
  :root {
    --ink: #e7e9ec; --dim: #8d949c; --line: #2c3037; --panel: #1a1d22;
    --page: #14161a; --accent: #9dbbe4; --accent-soft: #23303f;
  }
  .rail-nav a { color: #b9c0c8; }
  .rail-nav a:hover { background: #22262c; }
  .rail-nav a .n { background: #23272e; color: #8d949c; }
  .rail-nav a.on .n { background: #14161a; color: var(--accent); }
  .rail-stat { background: #1e2126; border-color: #2c3037; }
  .filterbox { background: #1e2126; border-color: #2c3037; color: #e7e9ec; }
  .filterbox:focus { background: #23272e; border-color: #3d4550; }
  .viewopts label { color: #b9c0c8; }
  .switch .dot { background: #363b43; }
  .topbar { background: rgba(20,22,26,.95); }
  .topbar label.burger { background: #1a1d22; color: #b9c0c8; }
}
"""

# Filtering and the view switches. Everything here degrades to "no filtering"
# if it never runs, which is why the page is readable without it.
SHELL_JS = r"""
(function () {
  // Opened straight off disk, /library is a dead link - there is no server
  // to answer it. Say so instead of offering a link that goes nowhere.
  if (location.protocol === 'file:') {
    document.querySelectorAll('a[data-needs-server]').forEach(function (link) {
      link.removeAttribute('href');
      link.classList.add('off');
      link.title = 'Run: python3 paperfeed.py serve';
    });
  }

  var box = document.querySelector('.filterbox');
  var opts = document.querySelectorAll('.viewopts input');
  var count = document.querySelector('.hitcount');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.paper'));
  if (!cards.length) { return; }

  var text = cards.map(function (card) {
    return (card.textContent || '').toLowerCase();
  });

  function wanted(card, index, needle) {
    if (needle && text[index].indexOf(needle) === -1) { return false; }
    // Pages with their own filter (the library's status pills) hand it in
    // here rather than setting display themselves. Two scripts both hiding
    // and showing the same cards would take it in turns to undo each other.
    if (window.pfExtraFilter && !window.pfExtraFilter(card)) { return false; }
    for (var i = 0; i < opts.length; i++) {
      if (!opts[i].checked) { continue; }
      var rule = opts[i].getAttribute('data-rule');
      if (rule === 'oa' && !card.querySelector('.badge.oa')) { return false; }
      if (rule === 'primary' && card.dataset.secondary) { return false; }
      if (rule === 'nonew' && card.querySelector('.metric.quiet')) { return false; }
      if (rule === 'new' && !card.querySelector('.badge.new')) { return false; }
    }
    return true;
  }

  function apply() {
    var needle = box ? box.value.trim().toLowerCase() : '';
    var shown = 0;
    cards.forEach(function (card, index) {
      var keep = wanted(card, index, needle);
      card.style.display = keep ? '' : 'none';
      if (keep) { shown++; }
    });
    // A section whose papers are all hidden is just a confusing empty
    // heading, so it goes too.
    document.querySelectorAll('details.set').forEach(function (block) {
      var any = block.querySelector('.paper:not([style*="none"])');
      block.style.display = any ? '' : 'none';
    });
    if (count) {
      count.textContent = (shown === cards.length)
        ? (cards.length + ' papers')
        : ('showing ' + shown + ' of ' + cards.length);
    }
  }

  if (box) { box.addEventListener('input', apply); }
  opts.forEach(function (option) { option.addEventListener('change', apply); });
  window.pfApplyFilters = apply;
  apply();
})();
"""


def _rail_link(href, icon, label, count=None, active=False, needs_server=False):
    number = '<span class="n">%s</span>' % _escape(str(count)) if count is not None else ""
    return (
        '<a class="%s" href="%s"%s><span class="ico">%s</span>%s%s</a>'
        % ("on" if active else "", _escape(href),
           " data-needs-server" if needs_server else "",
           icon, _escape(label), number)
    )


def _score_key(meta):
    """What the numbers in front of a title measure.

    Two bare digits with a tooltip was not an explanation: a tooltip cannot
    be reached on a phone, and an unexplained number is one you stop
    trusting. The distinction that matters most is stated first - these
    rank a paper's RELEVANCE TO YOU, and say nothing about whether it is any
    good. The quality signals are separate, and live on each card.
    """
    rows = [
        '<div class="row"><span></span><span>Both numbers estimate how '
        "closely a paper matches <b>what you asked for</b>. Neither is a "
        "judgement of the paper itself &mdash; for that, look at the "
        "citation and journal figures on each card.</span></div>"
    ]
    if meta.get("ai_label"):
        rows.append(
            '<div class="row"><span class="score ai"><span class="tag">AI</span>9'
            "</span><span><b>Relevance judged by %s</b>, 0&ndash;10, against "
            "the <b>interests</b> you wrote for this topic. It reads the "
            "title and abstract, so it can tell a paper that is about your "
            "question from one that merely mentions it.</span></div>"
            % _escape(meta["ai_label"])
        )
    rows.append(
        '<div class="row"><span class="score strong" style="--fill:80%">8.0</span>'
        "<span><b>Relevance measured by PaperFeed</b>, 0&ndash;10, by where "
        "your query's terms appear: a concept in the <b>title</b> scores 4, "
        "a <b>MeSH heading</b> 2, a mention in the <b>abstract</b> 1, an "
        "<b>author you follow</b> 3. No judgement, no model &mdash; just "
        "arithmetic you can check, and the reasons are printed under every "
        "title.</span></div>"
    )
    if meta.get("ai_label"):
        rows.append(
            "<div class=\"row\"><span></span><span>Papers are ordered by the "
            "AI score where there is one, and by PaperFeed's own score "
            "otherwise.</span></div>"
        )
    return '<div class="key">%s</div>' % "".join(rows)


def sidebar(meta, active, topics=(), extras="", stats=()):
    """The left rail, shared by the digest, the library and the dashboards."""
    here = {"digest": "Digest", "library": "Library",
            "dashboard": "Dashboard", "index": "All digests",
            "retro": "Date-range search"}.get(active, "PaperFeed")

    parts = [
        '<input type="checkbox" id="navtoggle">',
        '<div class="app">',
        '<label class="scrim" for="navtoggle"></label>',
        '<aside class="side">',
        '<p class="brand"><b>PaperFeed</b><span>%s</span></p>'
        % _escape(meta.get("sources_label", "") or "literature alerts"),
        '<p class="when">%s</p>' % _escape(meta.get("date_label", "")),
        '<div class="rail-nav">',
        _rail_link(meta.get("digest_href", "latest.html"), "&#9635;", "Digest",
                   meta.get("total_new"), active == "digest"),
        _rail_link(meta.get("library_href", "/library"), "&#9733;", "Library",
                   meta.get("library_total"), active == "library",
                   needs_server=True),
        _rail_link(meta.get("dashboard_href", "dashboard.html"), "&#9680;",
                   "Dashboard", None, active == "dashboard"),
        _rail_link("index.html", "&#9776;", "All digests", None, active == "index"),
        "</div>",
    ]

    if stats:
        parts.append('<p class="rail-h">At a glance</p><div class="rail-stats">')
        for value, label in stats:
            parts.append('<div class="rail-stat"><b>%s</b><span>%s</span></div>'
                         % (_escape(str(value)), _escape(label)))
        parts.append("</div>")

    if topics:
        parts.append('<p class="rail-h">Topics</p><div class="rail-nav">')
        for topic in topics:
            parts.append(_rail_link(topic["href"], "&#8226;", topic["label"],
                                    topic.get("count"), topic.get("active")))
        parts.append("</div>")

    if extras:
        parts.append(extras)

    if meta.get("show_scores", True):
        parts.append('<p class="rail-h">Relevance scoring</p>')
        parts.append(_score_key(meta))

    parts.extend([
        "</aside>",
        '<main class="main">',
        '<div class="topbar"><label class="burger" for="navtoggle">&#9776;</label>'
        '<span class="here">%s</span></div>' % _escape(here),
    ])
    return "\n".join(parts)


def shell_close():
    return "</main></div>"


def search_panel(placeholder="Filter these papers", options=()):
    """The filter box and view switches that sit in the rail."""
    rows = "".join(
        '<label><input type="checkbox" data-rule="%s">%s</label>'
        % (_escape(rule), _escape(label)) for rule, label in options
    )
    return (
        '<p class="rail-h">Filter</p>'
        '<input class="filterbox" type="search" placeholder="%s" '
        'aria-label="%s">'
        '<div class="viewopts" style="margin-top:8px">%s</div>'
        '<p class="hitcount"></p>' % (_escape(placeholder), _escape(placeholder), rows)
    )


def _shorten(text, limit):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _escape(value):
    # Not `value or ""`: that silently turns a real 0 into a blank, which is
    # how a dashboard tile ended up with a caption and no number.
    return html.escape("" if value is None else str(value))


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


# Article kinds worth showing on the card. "Journal Article" is on almost
# every record and says nothing, so it is not one of them.
# What "hide reviews and comments" should actually hide. A randomised
# controlled trial and a retraction notice are neither, and hiding them
# behind that label meant a user asking to see primary research saw FEWER
# trials, and lost retraction notices entirely.
SECONDARY_TYPES = frozenset({
    "Review", "Systematic Review", "Comment", "Editorial", "Letter",
})

NOTABLE_TYPES = (
    "Review", "Systematic Review", "Meta-Analysis", "Case Reports",
    "Comment", "Editorial", "Letter", "Retracted Publication",
    "Published Erratum", "Clinical Trial", "Randomized Controlled Trial",
    "Preprint",
)


def _article_kind(paper):
    """What kind of thing this is, from PubMed's own labelling."""
    kinds = [kind for kind in (paper.publication_types or [])
             if kind in NOTABLE_TYPES]
    return kinds[0] if kinds else ""


def _metrics_row(paper):
    """Triage figures, each labelled with where it came from.

    Every number here names its source in the same breath, because a bare
    number on a card gets read as a verdict from the tool itself.
    """
    data = getattr(paper, "metrics", None) or {}
    if not data:
        return ""

    bits = []
    if data.get("retracted"):
        bits.append(
            '<span class="metric bad" title="OpenAlex records this work as '
            'retracted.">retracted</span>'
        )

    citations = data.get("citations")
    if data.get("too_new"):
        bits.append(
            '<span class="metric quiet" title="Published too recently for '
            'anyone to have cited it. A zero here would be a verdict, not a '
            'fact.">too new to have citations</span>'
        )
    elif citations is not None:
        bits.append(
            '<span class="metric">cited %d<span class="src">OpenAlex</span></span>'
            % citations
        )
        fwci = data.get("fwci")
        if fwci:
            bits.append(
                '<span class="metric" title="Field-Weighted Citation Impact: '
                '1.0 is the average for papers of the same field, type and '
                'year, so this is not a penalty for being recent.">'
                '%.1f&times; field average<span class="src">OpenAlex FWCI</span>'
                "</span>" % fwci
            )
    elif paper.citations is not None:
        bits.append(
            '<span class="metric">cited %d<span class="src">Europe PMC</span>'
            "</span>" % paper.citations
        )

    journal = data.get("journal") or {}
    citedness = journal.get("two_year_mean_citedness")
    if citedness is not None:
        works = journal.get("works_count")
        bits.append(
            '<span class="metric" title="OpenAlex 2-year mean citedness for '
            "%s. It counts EVERY item the journal publishes, meeting "
            "abstracts and errata included, so journals with large abstract "
            'supplements read far below their reputation. Not an Impact '
            'Factor.">journal citedness %.1f%s<span class="src">OpenAlex'
            "</span></span>"
            % (_escape(journal.get("name") or "this journal"), citedness,
               (" over %s works" % format(works, ",")) if works else "")
        )
    if journal.get("in_doaj"):
        bits.append(
            '<span class="metric" title="Listed in the Directory of Open '
            'Access Journals.">in DOAJ<span class="src">OpenAlex</span></span>'
        )

    table = data.get("table") or {}
    for label, value in (table.get("values") or {}).items():
        bits.append(
            '<span class="metric own" title="Read from your own licensed '
            'file. PaperFeed never fetched this number.">%s %s'
            '<span class="src">%s</span></span>'
            % (_escape(label), _escape(value), _escape(table.get("label", "")))
        )

    if not bits:
        return ""
    return '<div class="metrics">%s</div>' % "".join(bits)


def _badges(paper):
    badge_class = "badge preprint" if paper.source == "Preprint" else "badge"
    bits = []
    # The digest shows a whole recent window, not only what changed, so this
    # is what separates "you have not seen this" from "this is merely
    # recent". Without it the two are indistinguishable.
    if getattr(paper, "is_new", True):
        bits.append('<span class="badge new">new</span>')
    bits.append('<span class="%s">%s</span>' % (badge_class, _escape(paper.source)))
    kind = _article_kind(paper)
    if kind and kind != "Preprint":
        bits.append('<span class="badge kind">%s</span>' % _escape(kind))
    if paper.free_fulltext:
        if paper.fulltext_url:
            bits.append(
                '<a class="badge oa" href="%s">free full text</a>'
                % _escape(paper.fulltext_url)
            )
        else:
            bits.append('<span class="badge oa">free full text</span>')
    else:
        oa_url = (getattr(paper, "metrics", None) or {}).get("oa_url")
        if oa_url:
            bits.append(
                '<a class="badge oa" href="%s">free full text</a>'
                % _escape(oa_url)
            )

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


def _paper_html(paper, show_scores, ai_label=""):
    kind = _article_kind(paper)
    parts = ['<div class="paper" data-doi="%s"%s>'
             % (_escape(paper.doi),
                ' data-secondary="1"' if kind in SECONDARY_TYPES else "")]

    head = ['<div class="head">']
    if getattr(paper, "ai_score", None) is not None:
        # Labelled "AI", not left as a bare number. Two unexplained numbers
        # in front of a title is a puzzle, and a tooltip is no answer on a
        # phone. The sidebar carries the full key.
        head.append(
            '<span class="score ai" title="%s">'
            '<span class="tag">AI</span>%.0f</span>'
            % (_escape(ai_label or "relevance scored by the AI, 0-10"),
               paper.ai_score)
        )
    if show_scores:
        head.append(
            '<span class="%s" style="--fill:%d%%" title="%s">%.1f</span>'
            % (
                _score_class(paper.score),
                int(min(100, paper.score * 10)),
                _escape("PaperFeed's own score out of 10 - " +
                        ("; ".join(paper.score_reasons) or "no matching terms")),
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
    parts.append(_metrics_row(paper))
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


def _headline_counts(meta):
    """Two numbers, because they answer different questions: how much is
    recent, and how much of it you have not seen."""
    shown = meta.get("total_shown", meta.get("total_new", 0))
    fresh = meta.get("total_new", 0)
    days = meta.get("digest_days", 3)
    line = "%s from the last %s" % (
        phrasing.count(shown, "paper"),
        "day" if days == 1 else "%d days" % days,
    )
    if fresh == shown:
        return line + ", all new to you"
    if fresh:
        return line + ", %d new to you" % fresh
    return line + ", none of them new to you"


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
    shown_total = meta.get("total_shown", total)
    show_scores = meta.get("show_scores", True)
    topics = [
        {"label": name, "href": "#" + _slug(name), "count": len(papers)}
        for name, papers in groups
    ]
    counts = meta.get("source_counts") or {}
    glance = [(shown_total, "in the last %d days" % meta.get("digest_days", 3))]
    if total:
        glance.append((total, "new to you"))
    for label, number in sorted(counts.items()):
        glance.append((number, "from %s" % label))
    if meta.get("hidden_count"):
        glance.append((meta["hidden_count"], "hidden by filters"))

    options = [("oa", "free full text only"),
               ("primary", "hide reviews and comments"),
               ("nonew", "hide papers too new to be cited")]
    if total and total < shown_total:
        # Only worth offering when the page actually holds both kinds.
        options.insert(0, ("new", "only papers new to me"))
    panel = search_panel("Filter these papers", tuple(options)) + (
        # A plain <label for> drives the real checkbox, which lives outside
        # .app because the compact-mode CSS reaches it as a sibling. No
        # script, and no second checkbox to get out of step with the first.
        '<p class="rail-h">View</p><div class="viewopts">'
        '<label class="switch" for="compact"><span class="dot"></span>'
        "compact list</label></div>"
    )

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>PaperFeed &mdash; %s</title>" % _escape(meta.get("date_label", "")),
        "<style>%s%s</style></head><body class=\"shell\">" % (STYLE, SHELL_CSS),
        '<input type="checkbox" id="compact">',
        sidebar(meta, "digest", topics=topics, extras=panel, stats=glance),
        '<div class="wrap">',
        "<h1>PaperFeed</h1>",
        '<p class="meta">%s &middot; %s</p>'
        % (_escape(meta.get("date_label", "")), _escape(_headline_counts(meta))),
        _counts_line(meta),
    ]

    sourcing = list(meta.get("metrics_lines") or [])
    if sourcing:
        parts.append(
            '<p class="sourced">%s<br><em>%s</em></p>'
            % ("<br>".join(_escape(line) for line in sourcing),
               _escape(metrics.DISCLAIMER))
        )

    if meta.get("errors"):
        parts.append('<div class="errors"><strong>Some sources did not respond.</strong><ul>')
        for message in meta["errors"]:
            parts.append("<li>%s</li>" % _escape(message))
        parts.append("</ul><p>The list below may be incomplete for those sources.</p></div>")

    if meta.get("ai_note"):
        parts.append('<div class="errors">%s</div>' % _escape(meta["ai_note"]))

    # The page is gated on how many papers there are to SHOW, not on how
    # many are new. A quiet week still has a window worth looking at, and
    # gating on the new count blanked a page holding twelve papers.
    if shown_total == 0 and meta.get("errors"):
        parts.append(
            '<div class="empty">Nothing to show &mdash; but at least one '
            "source did not answer, so this may not be the full picture. "
            "The next run will pick up anything that was missed.</div>"
        )
    elif shown_total == 0:
        parts.append(
            '<div class="empty">Nothing matched your keywords in the last %d '
            "days, and there is nothing older you have not already seen.</div>"
            % meta.get("digest_days", 3)
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
            fresh = sum(1 for paper in papers if getattr(paper, "is_new", True))
            parts.append(
                '<span class="setcount">%s</span></div></summary>'
                '<div class="setbody">'
                % ("%d &middot; %d new" % (len(papers), fresh) if fresh
                   else "%d" % len(papers))
            )
            if rest:
                parts.append(
                    '<p class="showing">Top %d of %d, best first</p>'
                    % (len(best), len(papers))
                )
            parts.extend(_paper_html(paper, show_scores, meta.get("ai_label", ""))
                         for paper in best)
            if rest:
                parts.append(
                    '<details class="rest"><summary>Show the other %d</summary>'
                    % len(rest)
                )
                parts.extend(
                    _paper_html(paper, show_scores, meta.get("ai_label", ""))
                    for paper in rest)
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
    parts.append("</div>")
    parts.append(shell_close())
    parts.append("<script>%s</script>" % SHELL_JS)
    parts.append("</body></html>")
    return "\n".join(part for part in parts if part)


# The email is rendered by email_digest.py, not here. Mail clients strip
# <style>, ignore flex and grid, drop SVG and refuse <details>, so it is a
# genuinely separate render rather than this one with different colours.

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

    total = sum(record.get("total", 0) for record in records)
    meta = {
        "date_label": "%s recorded" % phrasing.count(len(records), "run"),
        "sources_label": "all digests",
        "show_scores": False,
        "digest_href": "latest.html",
    }
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>PaperFeed &mdash; all digests</title>",
            "<style>%s%s</style></head><body class=\"shell\">" % (STYLE, SHELL_CSS),
            sidebar(meta, "index", extras=search_panel("Find a run"),
                    stats=[(len(records), "runs recorded"),
                           (total, "papers alerted")]),
            '<div class="wrap">',
            "<h1>All digests</h1>",
            '<p class="meta">%s, newest first</p>'
            % phrasing.count(len(records), "digest"),
            "\n".join(rows) if rows else '<div class="empty">No digests yet.</div>',
            '<p class="footer"><a href="latest.html">Latest digest</a></p>',
            "</div>",
            shell_close(),
            "<script>%s</script>" % SHELL_JS,
            "</body></html>",
        ]
    )


def render_trends(themes, meta):
    """The quarterly trend briefing."""
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>PaperFeed trends &mdash; %s</title>" % _escape(meta.get("period", "")),
        "<style>%s%s</style></head><body class=\"shell\">" % (STYLE, SHELL_CSS),
        sidebar({"date_label": _escape(meta.get("period", "")),
                 "sources_label": "trend briefing", "show_scores": False},
                "index"),
        '<div class="wrap">',
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
    parts.append("</div>")
    parts.append(shell_close())
    parts.append("<script>%s</script>" % SHELL_JS)
    parts.append("</body></html>")
    return "\n".join(parts)


def date_label(moment=None):
    return (moment or datetime.now()).strftime("%A %d %B %Y, %H:%M")
