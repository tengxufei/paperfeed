"""The public website: the digests and dashboards, plus the full library.

`build-site` writes a folder that GitHub Pages serves as it is. Three things
turn the digest folder into a site with Save, a library and the AI buttons,
none of which a plain file host can answer:

- every digest page gets web/pf-web.js, the browser's stand-in for
  `paperfeed serve`. It answers the same /api/... calls the Mac's server
  answers, from a private GitHub repository and the AI service directly, so
  the Save and library scripts on the page run unchanged;
- every paper embedded in a page gets its identity keys, computed here by
  the same rules the library uses, so the browser never has to re-implement
  them - and archived digests written before this existed get them too;
- library.html, the library page with everything except the papers, which
  are private and arrive in the browser only once it is connected.

pf-web-data.json carries everything the browser needs from the Python side
- the card template, the Save and library scripts, the AI prompts - so each
exists once, here, and the page cannot drift from the Mac's.
"""

import json
import os
import re
import shutil

import ai
import library
import server

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_SCRIPT = os.path.join(HERE, "web", "pf-web.js")
LIBRARY_COUNT = re.compile(r'(<a [^>]*href="library\.html"[^>]*>(?:(?!</a>).)*?Library)<span class="n">[^<]*</span>', re.S)


def _with_keys(match):
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return match.group(0)
    payload["keys"] = library.keys_for(payload)
    return ('<script type="application/json" class="pf-paper">%s</script>'
            % json.dumps(payload).replace("<", "\\u003c"))


def page_for_web(page):
    """One digest or dashboard page, readied for the website."""
    page = server.BLOB.sub(_with_keys, page)
    # The library is library.html on the website, not /library. Marked links
    # first; the dashboard's in-text link carried no marker in older pages.
    page = page.replace('href="/library" data-needs-server', 'href="library.html"')
    page = page.replace('href="/library"', 'href="library.html"')
    # The Library link carries a count. The library is private, so a public
    # page shows none - not even the "0" a run with no library would print.
    page = LIBRARY_COUNT.sub(r"\1", page)
    tag = '<script src="pf-web.js"></script>'
    if tag not in page:
        page = page.replace("</body>", tag + "</body>", 1) if "</body>" in page else page + tag
    return page


def web_data():
    """Everything pf-web.js takes from the Python side, as one JSON file."""
    return {
        "card": {
            "template": server.CARD_TEMPLATE,
            "status_button": server.STATUS_BUTTON,
            "explain_button": server.EXPLAIN_BUTTON,
            "out_hidden": server.OUT_HIDDEN,
            "statuses": [list(pair) for pair in server.STATUS_LABELS],
        },
        "save_css": server.INJECTED_CSS,
        "save_js": server.INJECTED_JS,
        "library_js": server.LIBRARY_JS,
        "prompts": {
            "explain_system": ai.EXPLAIN_SYSTEM,
            "explain_template": ai.EXPLAIN_TEMPLATE,
            "directions_system": ai.DIRECTIONS_SYSTEM,
            "directions_template": ai.DIRECTIONS_TEMPLATE,
            "troubleshoot_system": ai.TROUBLESHOOT_SYSTEM,
            "troubleshoot_template": ai.TROUBLESHOOT_TEMPLATE,
            "library_limit": ai.LIBRARY_LIMIT,
        },
    }


def build(cfg, out_dir):
    """Write the site into out_dir. Returns how many files were written."""
    source = cfg["digest_dir"]
    os.makedirs(out_dir, exist_ok=True)
    written = 0
    for name in sorted(os.listdir(source)):
        path = os.path.join(source, name)
        if not os.path.isfile(path):
            continue
        target = os.path.join(out_dir, name)
        if name.endswith(".html"):
            with open(path, "r", encoding="utf-8") as handle:
                page = handle.read()
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(page_for_web(page))
        else:
            shutil.copyfile(path, target)
        written += 1

    with open(os.path.join(out_dir, "library.html"), "w", encoding="utf-8") as handle:
        handle.write(server.library_shell(cfg))
    with open(os.path.join(out_dir, "pf-web-data.json"), "w", encoding="utf-8") as handle:
        json.dump(web_data(), handle, ensure_ascii=False)
    shutil.copyfile(WEB_SCRIPT, os.path.join(out_dir, "pf-web.js"))
    return written + 3
