"""A small local web page for saving papers out of a digest.

`paperfeed serve` reads the digest that was already written to disk, injects a
Save button onto each card, and serves it at http://127.0.0.1:8765.

It does not re-render the digest or fetch anything. Every paper's data is
already embedded in the file as an invisible JSON blob, so the server just
reads those, adds the buttons, and writes your clicks to library.db. Nothing
leaves your machine and nothing is uploaded.

Bound to 127.0.0.1 on purpose: the socket is not reachable from your network,
so no password or login is needed for it.
"""

import html
import json
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ai
import digest as digest_module
import library

BLOB = re.compile(
    r'<script type="application/json" class="pf-paper">(.*?)</script>', re.S
)

INJECTED_CSS = r"""
.pf-bar {
  position: sticky; top: 0; z-index: 10; margin: -24px -16px 20px;
  padding: 10px 16px; background: #11467f; color: #fff; font-size: 13px;
}
.pf-bar a { color: #cfe0f5; }
.pf-bar + .pf-bar { margin-top: -20px; background: #33507a; font-size: 12px; }
.pf-bar.pf-stale { background: #8a4b1f; }
.pf-bar code { background: rgba(255,255,255,.18); padding: 1px 5px; border-radius: 3px; }
.pf-save {
  margin-top: 10px; font: inherit; font-size: 13px; cursor: pointer;
  padding: 5px 12px; border-radius: 6px; border: 1px solid #c3c8cf;
  background: #f4f5f7; color: #33507a;
}
.pf-save:hover { background: #e8edf4; }
.pf-save.on { background: #d9ead3; border-color: #a9cb99; color: #2c5d1e; }
.pf-save[disabled] { opacity: .5; cursor: default; }
@media (prefers-color-scheme: dark) {
  .pf-save { background: #282c33; border-color: #3c424a; color: #9dbbe4; }
  .pf-save.on { background: #26381f; border-color: #3f5c33; color: #a6cf92; }
}
"""

INJECTED_JS = r"""
(function () {
  var state = JSON.parse(document.getElementById('pf-state').textContent);
  var cards = document.querySelectorAll('.paper');

  function paint(button, saved) {
    button.textContent = saved ? '✓ Saved' : '+ Save';
    button.className = saved ? 'pf-save on' : 'pf-save';
  }

  // One paper can appear twice (the Top 5 block repeats it), so a click has
  // to update every copy, not just the one that was pressed.
  function syncAll(key, saved) {
    cards.forEach(function (card, index) {
      if (state[index] && state[index].key === key) {
        state[index].saved = saved;
        var other = card.querySelector('.pf-save');
        if (other) { paint(other, saved); }
      }
    });
    var counter = document.getElementById('pf-count');
    if (counter) {
      counter.textContent = parseInt(counter.textContent, 10) + (saved ? 1 : -1);
    }
  }

  cards.forEach(function (card, index) {
    var info = state[index];
    var blob = card.querySelector('script.pf-paper');
    if (!info || !blob) { return; }
    var button = document.createElement('button');
    button.type = 'button';
    paint(button, info.saved);
    button.addEventListener('click', function () {
      var wasSaved = info.saved;
      button.disabled = true;
      fetch(wasSaved ? '/api/unsave' : '/api/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: wasSaved ? JSON.stringify({ key: info.key }) : blob.textContent
      }).then(function (response) {
        return response.json();
      }).then(function (data) {
        button.disabled = false;
        if (data.ok) { syncAll(info.key, !wasSaved); }
        else { button.textContent = 'error'; }
      }).catch(function () {
        button.disabled = false;
        button.textContent = 'offline?';
      });
    });
    card.appendChild(button);
  });
})();
"""


def _payloads(page):
    """Every paper in the digest, in document order."""
    found = []
    for raw in BLOB.findall(page):
        try:
            found.append(json.loads(raw))
        except json.JSONDecodeError:
            found.append({})
    return found


def _age_note(digest_path, config_path):
    """Warn when the digest on disk predates the config that produced it.

    `serve` shows the file that was last written, not a live search. Without
    this, editing your keywords and then opening the page looks like the new
    keywords found nothing at all.
    """
    try:
        written = os.path.getmtime(digest_path)
    except OSError:
        return ""

    when = datetime.fromtimestamp(written)
    age_hours = (datetime.now() - when).total_seconds() / 3600.0
    if age_hours < 1:
        age = "just now"
    elif age_hours < 24:
        age = "%d hour%s ago" % (int(age_hours), "" if int(age_hours) == 1 else "s")
    else:
        age = "%d day%s ago" % (int(age_hours / 24), "" if int(age_hours / 24) == 1 else "s")

    note = "Digest written %s." % age
    try:
        if config_path and os.path.getmtime(config_path) > written:
            note += (
                " <b>Your config.json has changed since then</b>, so this page "
                "does not reflect your current keywords &mdash; run "
                "<code>python3 paperfeed.py run --force</code> and reload."
            )
    except OSError:
        pass
    return note


def build_page(page, db_path, config_path=""):
    """Inject the save UI into a digest that is already on disk."""
    payloads = _payloads(page)
    known = library.saved_keys(db_path)
    state = []
    for payload in payloads:
        if not payload:
            state.append({"key": "", "saved": False})
            continue
        keys = library.keys_for(payload)
        state.append(
            {"key": keys[0], "saved": any(key in known for key in keys)}
        )

    # Count distinct papers, not cards: the Top 5 block repeats papers that
    # also appear lower down, so a naive count reads double.
    saved_now = len({entry["key"] for entry in state if entry["saved"]})
    bar = (
        '<div class="pf-bar">PaperFeed &mdash; click <b>+ Save</b> to keep a paper. '
        '<span id="pf-count">%d</span> on this page are in your library. '
        '<a href="/library">View library</a></div>' % saved_now
    )
    note = _age_note(Handler.digest_path, config_path)
    if note:
        stale = "config.json has changed" in note
        bar += '<div class="pf-bar%s">%s</div>' % (" pf-stale" if stale else "", note)

    injection = "".join(
        [
            "<style>%s</style>" % INJECTED_CSS,
            '<script id="pf-state" type="application/json">%s</script>'
            % json.dumps(state).replace("<", "\\u003c"),
            "<script>%s</script>" % INJECTED_JS,
        ]
    )

    page = page.replace('<div class="wrap">', '<div class="wrap">' + bar, 1)
    if "</body>" in page:
        return page.replace("</body>", injection + "</body>", 1)
    return page + injection


LIBRARY_CSS = r"""
.tools { background:#fff; border:1px solid #e2e4e8; border-radius:8px;
         padding:14px 16px; margin-bottom:14px; }
.tools h3 { margin:0 0 8px; font-size:14px; text-transform:uppercase;
            letter-spacing:.05em; color:#55606d; }
.tools p.hint { font-size:12.5px; color:#77818d; margin:0 0 10px; }
button.act { font:inherit; font-size:13px; cursor:pointer; padding:6px 13px;
             border-radius:6px; border:1px solid #c3c8cf; background:#f4f5f7;
             color:#33507a; }
button.act:hover { background:#e8edf4; }
button.act[disabled] { opacity:.5; cursor:default; }
button.act.primary { background:#11467f; border-color:#11467f; color:#fff; }
button.act.primary:hover { background:#0e3a6a; }
textarea.ask { width:100%; min-height:70px; font:inherit; font-size:13.5px;
               padding:9px 11px; border:1px solid #ccd2da; border-radius:6px;
               resize:vertical; background:#fff; color:inherit; }
.pills { margin:0 0 16px; }
.pill { display:inline-block; font-size:12.5px; padding:4px 11px; margin-right:6px;
        border-radius:12px; background:#eceff3; color:#55606d; cursor:pointer;
        border:1px solid transparent; }
.pill.on { background:#11467f; color:#fff; }
.statusrow { margin-top:9px; display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
.sbtn { font:inherit; font-size:12px; cursor:pointer; padding:3px 10px;
        border-radius:11px; border:1px solid #ccd2da; background:#fff; color:#6a727c; }
.sbtn.on { background:#d9ead3; border-color:#a9cb99; color:#2c5d1e; font-weight:600; }
.sbtn.rm { margin-left:auto; color:#9a5a45; border-color:#e0cec8; }
.out { margin-top:10px; font-size:13.5px; line-height:1.6; white-space:pre-wrap;
       background:#f7f8fa; border-left:3px solid #b9c2cd; padding:10px 12px;
       border-radius:0 6px 6px 0; }
.out.busy { color:#77818d; font-style:italic; white-space:normal; }
.dircard { background:#f7f8fa; border-left:3px solid #6f86a8; padding:11px 13px;
           margin:0 0 10px; border-radius:0 6px 6px 0; }
.dircard h4 { margin:0 0 6px; font-size:14.5px; color:#11467f; }
.dircard .step { margin-top:7px; font-size:13px; color:#3c5a33; }
.dircard .refs { margin-top:7px; font-size:12px; }
@media (prefers-color-scheme: dark) {
  .tools { background:#1f2228; border-color:#33373d; }
  button.act { background:#282c33; border-color:#3c424a; color:#9dbbe4; }
  button.act.primary { background:#2c5b96; border-color:#2c5b96; color:#fff; }
  textarea.ask { background:#16181c; border-color:#3c424a; color:#e6e6e6; }
  .pill { background:#282c33; color:#9aa1aa; }
  .pill.on { background:#2c5b96; color:#fff; }
  .sbtn { background:#1f2228; border-color:#3c424a; color:#9aa1aa; }
  .sbtn.on { background:#26381f; border-color:#3f5c33; color:#a6cf92; }
  .out, .dircard { background:#22262c; }
}
"""

LIBRARY_JS = r"""
function post(url, body) {
  return fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                     body: JSON.stringify(body || {})}).then(function (r) { return r.json(); });
}
function esc(s) {
  var d = document.createElement('div'); d.textContent = s == null ? '' : s;
  return d.innerHTML;
}
function busy(el, message) { el.className = 'out busy'; el.textContent = message; }

document.querySelectorAll('.sbtn[data-status]').forEach(function (b) {
  b.addEventListener('click', function () {
    var card = b.closest('.paper');
    post('/api/status', {key: card.dataset.key, status: b.dataset.status})
      .then(function (d) {
        if (!d.ok) { return; }
        card.querySelectorAll('.sbtn[data-status]').forEach(function (o) {
          o.classList.toggle('on', o.dataset.status === b.dataset.status);
        });
        card.dataset.status = b.dataset.status;
        applyFilter();
      });
  });
});

document.querySelectorAll('.sbtn.rm').forEach(function (b) {
  b.addEventListener('click', function () {
    var card = b.closest('.paper');
    post('/api/unsave', {key: card.dataset.key}).then(function (d) {
      if (d.ok) { card.remove(); }
    });
  });
});

document.querySelectorAll('button.explain').forEach(function (b) {
  b.addEventListener('click', function () {
    var card = b.closest('.paper');
    var out = card.querySelector('.out');
    b.disabled = true;
    busy(out, 'Reading the paper...');
    post('/api/explain', {key: card.dataset.key}).then(function (d) {
      b.disabled = false;
      if (d.ok) { out.className = 'out'; out.textContent = d.summary;
                  b.textContent = 'Explain again'; }
      else { out.className = 'out'; out.textContent = 'Could not explain: ' + d.error; }
    }).catch(function () { b.disabled = false; busy(out, 'The server went away.'); });
  });
});

var dirBtn = document.getElementById('dir-btn');
if (dirBtn) {
  dirBtn.addEventListener('click', function () {
    var out = document.getElementById('dir-out');
    dirBtn.disabled = true;
    busy(out, 'Reading your whole library and looking for openings. This takes a minute.');
    post('/api/directions', {}).then(function (d) {
      dirBtn.disabled = false;
      if (!d.ok) { out.className = 'out'; out.textContent = 'Could not do that: ' + d.error; return; }
      out.className = '';
      out.innerHTML = d.directions.map(function (x) {
        return '<div class="dircard"><h4>' + esc(x.direction) + '</h4>' +
               '<div>' + esc(x.why) + '</div>' +
               (x.first_step ? '<div class="step"><b>Start with:</b> ' + esc(x.first_step) + '</div>' : '') +
               (x.papers.length ? '<div class="refs">from: ' + x.papers.map(function (doi) {
                   return '<a href="https://doi.org/' + esc(doi) + '">' + esc(doi) + '</a>';
                 }).join(' &middot; ') + '</div>' : '') + '</div>';
      }).join('');
    }).catch(function () { dirBtn.disabled = false; busy(out, 'The server went away.'); });
  });
}

var askBtn = document.getElementById('ask-btn');
if (askBtn) {
  askBtn.addEventListener('click', function () {
    var q = document.getElementById('ask-text').value;
    var out = document.getElementById('ask-out');
    if (!q.trim()) { busy(out, 'Describe the problem first.'); return; }
    askBtn.disabled = true;
    busy(out, 'Thinking about your problem against what you have saved...');
    post('/api/troubleshoot', {question: q}).then(function (d) {
      askBtn.disabled = false;
      if (!d.ok) { out.className = 'out'; out.textContent = 'Could not do that: ' + d.error; return; }
      var a = d.result;
      var html = esc(a.answer).replace(/\n/g, '<br>');
      if (a.suggestions && a.suggestions.length) {
        html += '<div style="margin-top:9px"><b>Things to try</b><ul>' +
                a.suggestions.map(function (s) { return '<li>' + esc(s) + '</li>'; }).join('') +
                '</ul></div>';
      }
      if (a.papers && a.papers.length) {
        html += '<div class="refs" style="margin-top:7px">drawing on: ' +
                a.papers.map(function (doi) {
                  return '<a href="https://doi.org/' + esc(doi) + '">' + esc(doi) + '</a>';
                }).join(' &middot; ') + '</div>';
      }
      if (a.gap) { html += '<div style="margin-top:9px;color:#8a6420"><b>Your saved papers do not cover:</b> ' + esc(a.gap) + '</div>'; }
      out.className = 'out'; out.innerHTML = html;
    }).catch(function () { askBtn.disabled = false; busy(out, 'The server went away.'); });
  });
}

var keyBtn = document.getElementById('key-btn');
if (keyBtn) {
  keyBtn.addEventListener('click', function () {
    var input = document.getElementById('key-input');
    var out = document.getElementById('key-out');
    if (!input.value.trim()) { busy(out, 'Paste the key first.'); return; }
    keyBtn.disabled = true;
    busy(out, 'Checking the key with a small test request...');
    post('/api/set-key', {key: input.value}).then(function (d) {
      keyBtn.disabled = false;
      input.value = '';
      if (d.ok) {
        out.className = 'out';
        out.textContent = 'Key works, saved to ' + d.where + '. Reloading...';
        setTimeout(function () { location.reload(); }, 900);
      } else {
        out.className = 'out';
        out.textContent = d.error;
      }
    }).catch(function () { keyBtn.disabled = false; busy(out, 'The server went away.'); });
  });
}

var keyForget = document.getElementById('key-forget');
if (keyForget) {
  keyForget.addEventListener('click', function (e) {
    e.preventDefault();
    post('/api/forget-key', {}).then(function () { location.reload(); });
  });
}

var filter = 'all';
function applyFilter() {
  document.querySelectorAll('.paper').forEach(function (c) {
    c.style.display = (filter === 'all' || c.dataset.status === filter) ? '' : 'none';
  });
}
document.querySelectorAll('.pill').forEach(function (p) {
  p.addEventListener('click', function () {
    filter = p.dataset.filter;
    document.querySelectorAll('.pill').forEach(function (o) { o.classList.toggle('on', o === p); });
    applyFilter();
  });
});
"""


def library_page(db_path, has_key, ai_on):
    rows = library.all_saved(db_path, limit=500)
    counts = library.status_counts(db_path)
    total = len(rows)

    cards = []
    for record in rows:
        status = record.get("status") or "unread"
        authors = ", ".join(record["authors"][:6])
        tail = [
            bit for bit in (record["source"], record["venue"], record["published"]) if bit
        ]
        buttons = "".join(
            '<button class="sbtn%s" data-status="%s">%s</button>'
            % (" on" if status == value else "", value, label)
            for value, label in (
                ("unread", "unread"), ("reading", "reading"), ("read", "read")
            )
        )
        explain = (
            '<button class="act explain">%s</button>'
            % ("Explain again" if record.get("ai_summary") else "Explain this")
            if has_key
            else ""
        )
        cards.append(
            '<div class="paper" data-key="%s" data-status="%s">'
            '<div class="head"><a class="title" href="%s">%s</a></div>'
            '<p class="byline">%s</p><div class="tags">%s</div>'
            '<div class="statusrow">%s%s<button class="sbtn rm">remove</button></div>'
            '<div class="out"%s>%s</div>'
            "</div>"
            % (
                html.escape(record["key"]),
                html.escape(status),
                html.escape(record["url"] or ""),
                html.escape(record["title"]),
                html.escape(authors),
                html.escape(" \u00b7 ".join(tail)),
                buttons,
                explain,
                "" if record.get("ai_summary") else ' style="display:none"',
                html.escape(record.get("ai_summary") or ""),
            )
        )

    pills = "".join(
        '<span class="pill%s" data-filter="%s">%s%s</span>'
        % (
            " on" if value == "all" else "",
            value,
            label,
            "" if value == "all" else " (%d)" % counts.get(value, 0),
        )
        for value, label in (
            ("all", "all %d" % total), ("unread", "unread"),
            ("reading", "reading"), ("read", "read"),
        )
    )

    if has_key and ai_on is not None:
        tools = (
            '<div class="tools"><h3>Where this collection points</h3>'
            '<p class="hint">Reads everything you have saved and suggests '
            "directions these papers open up but do not close.</p>"
            '<button class="act primary" id="dir-btn">Suggest research directions</button>'
            '<div id="dir-out"></div></div>'
            '<div class="tools"><h3>Stuck on something?</h3>'
            '<p class="hint">Describe a problem in your own work. Answered from '
            "your saved papers, with what they do and do not cover.</p>"
            '<textarea class="ask" id="ask-text" placeholder="e.g. My designed '
            'binders express well but show no binding by BLI. What should I check '
            'first?"></textarea>'
            '<div style="margin-top:9px"><button class="act primary" id="ask-btn">Ask</button></div>'
            '<div id="ask-out"></div></div>'
            '<p class="meta" style="margin-top:-6px">An API key is stored. '
            '<a href="#" id="key-forget">Remove it</a> to switch the AI tools '
            "off.</p>"
        )
    else:
        tools = (
            '<div class="tools"><h3>Turn on the AI tools</h3>'
            '<p class="hint">Explaining papers, suggesting research directions '
            "and answering questions about your own work need an Anthropic API "
            'key. Get one at <a href="https://console.anthropic.com/settings/keys">'
            "console.anthropic.com</a>, then paste it here.</p>"
            '<input type="password" class="ask" id="key-input" '
            'style="min-height:0;height:38px" placeholder="sk-ant-..." '
            'autocomplete="off">'
            '<div style="margin-top:9px">'
            '<button class="act primary" id="key-btn">Check and save key</button>'
            "</div>"
            '<p class="hint" style="margin-top:9px">It is checked against the '
            "API before being saved, and stored in your macOS Keychain - never "
            "in a file in this project. This page is served only to this Mac.</p>"
            '<div id="key-out"></div></div>'
        )

    body = (
        cards
        and "\n".join(cards)
        or '<div class="empty">Nothing saved yet. Open the digest and click '
        "<b>+ Save</b> on anything worth keeping.</div>"
    )

    return "\n".join(
        [
            '<!doctype html><html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>PaperFeed &mdash; library</title>",
            "<style>%s%s</style></head><body><div class=\"wrap\">"
            % (digest_module.STYLE, LIBRARY_CSS),
            "<h1>Your library</h1>",
            '<p class="meta">%d saved paper%s &middot; '
            '<a href="/">back to the digest</a></p>' % (total, "" if total == 1 else "s"),
            tools,
            '<div class="pills">%s</div>' % pills,
            body,
            "<script>%s</script>" % LIBRARY_JS,
            "</div></body></html>",
        ]
    )


class Handler(BaseHTTPRequestHandler):
    digest_path = ""
    db_path = ""
    config_path = ""
    cfg = None
    server_version = "PaperFeed"

    def log_message(self, fmt, *args):      # quieter than the default
        pass

    def _send(self, body, status=200, content_type="text/html; charset=utf-8"):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, payload, status=200):
        self._send(json.dumps(payload), status, "application/json")

    def _ai_key(self):
        cfg = self.cfg or {}
        return ai.read_key(cfg.get("base_dir", ""))

    def do_GET(self):
        if self.path.startswith("/library"):
            self._send(
                library_page(
                    self.db_path,
                    bool(self._ai_key()),
                    (self.cfg or {}).get("ai"),
                )
            )
            return
        if self.path not in ("/", "/index.html"):
            self._send("<h1>Not found</h1>", 404)
            return
        if not os.path.exists(self.digest_path):
            self._send(
                "<h1>No digest yet</h1><p>Run <code>python3 paperfeed.py run"
                "</code> first, then reload.</p>",
                404,
            )
            return
        with open(self.digest_path, "r", encoding="utf-8") as handle:
            self._send(build_page(handle.read(), self.db_path, self.config_path))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"ok": False, "error": "bad json"}, 400)
            return

        if self.path == "/api/save":
            if not body.get("title"):
                self._json({"ok": False, "error": "no title"}, 400)
                return
            key, already = library.save(self.db_path, body)
            self._json({"ok": True, "key": key, "already": already})
        elif self.path == "/api/unsave":
            removed = library.remove(self.db_path, body.get("key", ""))
            self._json({"ok": True, "removed": removed})
        elif self.path == "/api/status":
            done = library.set_status(
                self.db_path, body.get("key", ""), body.get("status", "")
            )
            self._json({"ok": done})
        elif self.path == "/api/set-key":
            self._set_key(body)
        elif self.path == "/api/forget-key":
            ai.forget_key((self.cfg or {}).get("base_dir", ""))
            self._json({"ok": True})
        elif self.path == "/api/explain":
            self._explain(body)
        elif self.path == "/api/directions":
            self._directions()
        elif self.path == "/api/troubleshoot":
            self._troubleshoot(body)
        else:
            self._json({"ok": False, "error": "unknown endpoint"}, 404)

    # -- the AI endpoints. Each fails soft: an error becomes a message on the
    # -- page, never a broken library.
    def _interests(self):
        return ((self.cfg or {}).get("ai") or {}).get("interests", "")

    def _set_key(self, body):
        """Validate a pasted key, then store it. The key is never logged or
        echoed back; only whether it worked."""
        candidate = (body.get("key") or "").strip()
        if not candidate:
            self._json({"ok": False, "error": "nothing was pasted"})
            return
        cfg = self.cfg or {}
        model = (cfg.get("ai") or {}).get("model") or ai.DEFAULT_SCORING_MODEL
        try:
            ai.call(candidate, model, "Reply with the single word: ok", "ok",
                    max_tokens=10)
        except ai.KeyRejected:
            self._json({
                "ok": False,
                "error": "The API rejected that key, so it was not saved. "
                         "Check you copied all of it - they start with sk-ant-.",
            })
            return
        except ai.AIError as error:
            self._json({
                "ok": False,
                "error": "Could not check the key (%s). Nothing was saved - this "
                         "usually means a network problem rather than a bad key."
                         % error,
            })
            return
        where = ai.store_key(candidate, cfg.get("base_dir", ""))
        self._json({"ok": True, "where": where})

    def _explain(self, body):
        record = library.get(self.db_path, body.get("key", ""))
        if not record:
            self._json({"ok": False, "error": "that paper is not in your library"})
            return
        key = self._ai_key()
        if not key:
            self._json({"ok": False, "error": "no API key stored - run set-key"})
            return
        summary, usage, error = ai.explain_paper(
            record, self._interests(), key,
            ((self.cfg or {}).get("ai") or {}).get("model"),
        )
        if error:
            self._json({"ok": False, "error": error})
            return
        library.set_summary(self.db_path, record["key"], summary)
        self._json({"ok": True, "summary": summary, "cost": ai.cost_of(usage)})

    def _directions(self):
        key = self._ai_key()
        if not key:
            self._json({"ok": False, "error": "no API key stored - run set-key"})
            return
        papers = library.all_saved(self.db_path, limit=200)
        directions, usage, error = ai.research_directions(
            papers, self._interests(), key,
            ((self.cfg or {}).get("trends") or {}).get("model"),
        )
        if error:
            self._json({"ok": False, "error": error})
            return
        self._json(
            {"ok": True, "directions": directions, "cost": ai.cost_of(usage)}
        )

    def _troubleshoot(self, body):
        key = self._ai_key()
        if not key:
            self._json({"ok": False, "error": "no API key stored - run set-key"})
            return
        papers = library.all_saved(self.db_path, limit=200)
        result, usage, error = ai.troubleshoot(
            body.get("question", ""), papers, self._interests(), key,
            ((self.cfg or {}).get("trends") or {}).get("model"),
        )
        if error:
            self._json({"ok": False, "error": error})
            return
        self._json({"ok": True, "result": result, "cost": ai.cost_of(usage)})


PORT_IN_USE = (48, 98)    # macOS, Linux


def serve(digest_path, db_path, port=8931, host="127.0.0.1", attempts=12,
          config_path="", cfg=None):
    """Start the server, stepping past ports another program already holds.

    Other tools squat on tidy round ports (8765 was taken on the machine this
    was written on), and a crash with errno 48 is a poor way to find out.
    """
    Handler.digest_path = digest_path
    Handler.db_path = db_path
    Handler.config_path = config_path
    Handler.cfg = cfg
    for candidate in range(port, port + attempts):
        try:
            return ThreadingHTTPServer((host, candidate), Handler)
        except OSError as error:
            if error.errno not in PORT_IN_USE:
                raise
    raise OSError(
        "ports %d-%d are all in use; pass --port to pick another"
        % (port, port + attempts - 1)
    )
