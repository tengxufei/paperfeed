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

import digest as digest_module
import library

BLOB = re.compile(
    r'<script type="application/json" class="pf-paper">(.*?)</script>', re.S
)

INJECTED_CSS = """
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

INJECTED_JS = """
(function () {
  var state = JSON.parse(document.getElementById('pf-state').textContent);
  var cards = document.querySelectorAll('.paper');

  function paint(button, saved) {
    button.textContent = saved ? '\\u2713 Saved' : '+ Save';
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


def library_page(db_path):
    rows = library.all_saved(db_path)
    cards = []
    for record in rows:
        authors = ", ".join(record["authors"][:8])
        tail = [
            bit
            for bit in (record["source"], record["venue"], record["published"])
            if bit
        ]
        cards.append(
            '<div class="paper"><div class="head">'
            '<a class="title" href="%s">%s</a></div>'
            '<p class="byline">%s</p><div class="tags">%s</div>'
            "<div class=\"tags\">saved %s</div></div>"
            % (
                html.escape(record["url"] or ""),
                html.escape(record["title"]),
                html.escape(authors),
                html.escape(" · ".join(tail)),
                html.escape((record["saved_at"] or "")[:10]),
            )
        )
    return "\n".join(
        [
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>PaperFeed &mdash; library</title>",
            "<style>%s</style></head><body><div class=\"wrap\">"
            % digest_module.STYLE,
            "<h1>Your library</h1>",
            '<p class="meta">%d saved paper%s &middot; <a href="/">back to the digest</a></p>'
            % (len(rows), "" if len(rows) == 1 else "s"),
            "\n".join(cards)
            if cards
            else '<div class="empty">Nothing saved yet. Open the digest and '
            "click <b>+ Save</b> on anything worth keeping.</div>",
            "</div></body></html>",
        ]
    )


class Handler(BaseHTTPRequestHandler):
    digest_path = ""
    db_path = ""
    config_path = ""
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

    def do_GET(self):
        if self.path.startswith("/library"):
            self._send(library_page(self.db_path))
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
        else:
            self._json({"ok": False, "error": "unknown endpoint"}, 404)


PORT_IN_USE = (48, 98)    # macOS, Linux


def serve(digest_path, db_path, port=8931, host="127.0.0.1", attempts=12,
          config_path=""):
    """Start the server, stepping past ports another program already holds.

    Other tools squat on tidy round ports (8765 was taken on the machine this
    was written on), and a crash with errno 48 is a poor way to find out.
    """
    Handler.digest_path = digest_path
    Handler.db_path = db_path
    Handler.config_path = config_path
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
