#!/usr/bin/env python3
"""PaperFeed - keyword-driven literature alerts.

    python3 paperfeed.py run              # the normal run, respects the interval
    python3 paperfeed.py run --force      # ignore the interval, fetch now
    python3 paperfeed.py run --dry-run    # show what would happen, change nothing
    python3 paperfeed.py status           # when it last ran, when it runs next
    python3 paperfeed.py check            # validate config.json
    python3 paperfeed.py test-email       # send one test message

The order of operations matters: the digest file is written to disk *before*
email is attempted, and a failed email never fails the run. The file is the
thing to check.
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta

import config as config_module
import digest as digest_module
import library
import mailer
import relevance
import sources
import store as store_module

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")

log = logging.getLogger("paperfeed")


def setup_logging(log_path, verbose=True):
    log.setLevel(logging.INFO)
    log.handlers = []

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s")
    )
    log.addHandler(file_handler)

    if verbose:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(console)


def load_config_or_exit(path):
    try:
        return config_module.load(path)
    except config_module.ConfigError as error:
        sys.stderr.write("\nConfiguration problem:\n\n%s\n\n" % error)
        sys.exit(2)


def write_digest(cfg, html_text, moment):
    os.makedirs(cfg["digest_dir"], exist_ok=True)
    stamped = os.path.join(
        cfg["digest_dir"], "%s.html" % moment.strftime("%Y-%m-%d-%H%M")
    )
    latest = os.path.join(cfg["digest_dir"], "latest.html")
    with open(stamped, "w", encoding="utf-8") as handle:
        handle.write(html_text)
    with open(latest, "w", encoding="utf-8") as handle:
        handle.write(html_text)
    return stamped, latest


def update_index(cfg, record):
    """Append this run to digests/index.json and rebuild index.html.

    Only run metadata is stored here — a date and some counts — never papers.
    """
    index_path = os.path.join(cfg["digest_dir"], "index.json")
    records = []
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as handle:
                records = json.load(handle) or []
        except (json.JSONDecodeError, OSError):
            records = []

    records = [item for item in records if item.get("file") != record["file"]]
    records.insert(0, record)
    records = records[:400]

    with open(index_path, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2)
    with open(os.path.join(cfg["digest_dir"], "index.html"), "w", encoding="utf-8") as handle:
        handle.write(digest_module.render_index(records))


def command_run(args):
    cfg = load_config_or_exit(args.config)
    setup_logging(cfg["log_path"], verbose=not args.quiet)

    store = store_module.Store(cfg["state_path"])
    due, reason = store.due(cfg["interval_days"])
    if not due and not args.force:
        log.info("Not due yet (%s). Use --force to run anyway.", reason)
        return 0

    keyword_sets = [entry for entry in cfg["keyword_sets"] if entry["enabled"]]
    if args.set:
        wanted = args.set.lower()
        keyword_sets = [
            entry for entry in keyword_sets if entry["name"].lower() == wanted
        ]
        if not keyword_sets:
            sys.stderr.write("No enabled keyword set called %r.\n" % args.set)
            return 2

    log.info(
        "Run started (%s) - %d keyword set(s), last %d days%s",
        reason,
        len(keyword_sets),
        cfg["lookback_days"],
        ", DRY RUN" if args.dry_run else "",
    )

    fetched = []
    errors = []
    hidden = []
    for keyword_set in keyword_sets:
        papers, set_errors = sources.fetch_all(keyword_set, cfg)
        errors.extend(set_errors)
        kept, set_hidden = relevance.filter_papers(papers, keyword_set, cfg)
        hidden.extend(set_hidden)
        log.info(
            "  %-34s %3d found%s",
            keyword_set["name"],
            len(papers),
            (", %d filtered out" % len(set_hidden)) if set_hidden else "",
        )
        for message in set_errors:
            log.warning("  ! %s", message)
        fetched.extend(kept)

    unique = store_module.deduplicate(fetched)
    new_papers = store.filter_new(unique)

    ranking = cfg["ranking"]
    show_scores = bool(ranking.get("enabled", True))
    low_scoring = []
    if show_scores:
        relevance.score_all(new_papers, keyword_sets, ranking)
        new_papers, low_scoring = relevance.drop_below(
            new_papers, ranking.get("min_score", 0)
        )
        hidden.extend(low_scoring)

    log.info(
        "%d kept after filters, %d after deduplication, %d new, %d hidden",
        len(fetched),
        len(unique),
        len(new_papers),
        len(hidden),
    )

    by_set = {}
    for paper in new_papers:
        by_set.setdefault(paper.set_name, []).append(paper)
    for name in by_set:
        if show_scores:
            by_set[name] = relevance.rank(by_set[name])
        else:
            by_set[name].sort(key=lambda paper: paper.published or "", reverse=True)
    groups = [(entry["name"], by_set.get(entry["name"], [])) for entry in keyword_sets]

    moment = datetime.now()
    enabled_sources = [
        label for key, (label, _) in sources.SOURCES.items() if cfg["sources"].get(key)
    ]
    source_counts = dict(Counter(paper.source for paper in new_papers))

    hidden_examples = []
    if hidden:
        reasons = Counter(reason for _, reason in hidden)
        hidden_examples.append(
            "%d paper%s hidden by your filters: %s. Nothing hidden is marked as "
            "seen, so loosening a filter brings it back."
            % (
                len(hidden),
                "" if len(hidden) == 1 else "s",
                "; ".join(
                    "%s (%d)" % (reason, count)
                    for reason, count in reasons.most_common(4)
                ),
            )
        )

    meta = {
        "date_label": digest_module.date_label(moment),
        "total_new": len(new_papers),
        "lookback_days": cfg["lookback_days"],
        "errors": errors,
        "sources_label": " and ".join(enabled_sources),
        "show_scores": show_scores,
        "source_counts": source_counts,
        "hidden_count": len(hidden),
        "hidden_examples": hidden_examples,
        "top_papers": relevance.top_n(new_papers, 5) if len(new_papers) > 5 else [],
    }
    html_text = digest_module.render_html(groups, meta)
    text_body = digest_module.render_text(groups, meta)
    email_html = digest_module.render_email_html(groups, meta)

    if args.dry_run:
        log.info("Dry run: no digest written, nothing marked as seen, no email sent.")
        for name, papers in groups:
            for paper in papers:
                log.info("  %4.1f  [%s] %s", paper.score, name, paper.title)
                if paper.score_reasons:
                    log.info("        why: %s", "; ".join(paper.score_reasons))
        for line in hidden_examples:
            log.info("  %s", line)
        return 1 if errors else 0

    # The file goes to disk first, always, whatever happens next.
    stamped, latest = write_digest(cfg, html_text, moment)
    log.info("Digest written: %s", stamped)
    log.info("Also at:        %s", latest)

    # Everything that passed the filters is marked seen. Papers a filter
    # removed are deliberately left unmarked, so relaxing that filter later
    # lets them show up rather than silently swallowing them forever.
    low_keys = {store_module.fingerprint(paper) for paper, _ in low_scoring}
    store.mark_seen(
        [paper for paper in unique if store_module.fingerprint(paper) not in low_keys]
    )
    store.save(record_run=True)

    update_index(
        cfg,
        {
            "file": os.path.basename(stamped),
            "date_label": meta["date_label"],
            "total": len(new_papers),
            "sources": source_counts,
            "hidden": len(hidden),
        },
    )

    email_settings = cfg["email"]
    if not email_settings["enabled"]:
        log.info("Email is switched off in config.json (email.enabled).")
    elif not new_papers and not email_settings.get("send_when_empty", False):
        log.info("No new papers, so no email sent (email.send_when_empty is false).")
    else:
        subject = "%s %d new paper%s - %s" % (
            email_settings.get("subject_prefix", "[PaperFeed]"),
            len(new_papers),
            "" if len(new_papers) == 1 else "s",
            moment.strftime("%d %b %Y"),
        )
        try:
            mailer.send(email_settings, subject, email_html, text_body)
            log.info("Email sent to %s", ", ".join(email_settings["to_addresses"]))
        except mailer.MailError as error:
            log.error("Email failed: %s", error)
            log.error("The digest above is still on disk - open %s", latest)
            return 1

    return 1 if errors else 0


def command_status(args):
    cfg = load_config_or_exit(args.config)
    setup_logging(cfg["log_path"], verbose=False)
    store = store_module.Store(cfg["state_path"])
    due, reason = store.due(cfg["interval_days"])

    print("PaperFeed status")
    print("  config      %s" % cfg["config_path"])
    print("  keyword sets")
    for entry in cfg["keyword_sets"]:
        print(
            "    %-34s %s (%d terms)"
            % (entry["name"], "on " if entry["enabled"] else "off", len(entry["terms"]))
        )
    print("  interval    every %d days" % cfg["interval_days"])
    print("  lookback    %d days" % cfg["lookback_days"])
    print(
        "  sources     %s"
        % ", ".join(key for key, on in cfg["sources"].items() if on)
    )
    print(
        "  email       %s" % ("on" if cfg["email"]["enabled"] else "off (file only)")
    )
    print(
        "  last run    %s"
        % (store.last_run.astimezone().strftime("%Y-%m-%d %H:%M") if store.last_run else "never")
    )
    print("  due now     %s (%s)" % ("yes" if due else "no", reason))
    print(
        "  remembered  %d papers (%d identity keys)"
        % (store.paper_count, len(store.seen))
    )
    print("  library     %d saved paper(s)" % library.count(cfg["library_path"]))
    latest = os.path.join(cfg["digest_dir"], "latest.html")
    print("  latest      %s" % (latest if os.path.exists(latest) else "none yet"))
    return 0


def command_check(args):
    cfg = load_config_or_exit(args.config)
    print("config.json looks good.")
    print(
        "  %d keyword set(s), %d enabled"
        % (
            len(cfg["keyword_sets"]),
            sum(1 for entry in cfg["keyword_sets"] if entry["enabled"]),
        )
    )
    for entry in cfg["keyword_sets"]:
        if entry["enabled"]:
            print("    %s: %s" % (entry["name"], " OR ".join(entry["terms"])))
    print("  every %d days, looking back %d days" % (cfg["interval_days"], cfg["lookback_days"]))
    print("  email: %s" % ("enabled" if cfg["email"]["enabled"] else "disabled"))
    return 0


def command_test_email(args):
    cfg = load_config_or_exit(args.config)
    setup_logging(cfg["log_path"])
    if not cfg["email"]["enabled"]:
        sys.stderr.write(
            "Email is disabled. Set email.enabled to true in config.json first.\n"
        )
        return 2

    sample = sources.Paper(
        title="PaperFeed test message",
        authors=["PaperFeed"],
        abstract="If you can read this, email delivery is working.",
        url="https://pubmed.ncbi.nlm.nih.gov/",
        published=datetime.now().strftime("%Y-%m-%d"),
        venue="Local test",
        source="PubMed",
        set_name="Test",
    )
    meta = {
        "date_label": digest_module.date_label(),
        "total_new": 1,
        "lookback_days": cfg["lookback_days"],
        "errors": [],
        "sources_label": "a local test",
    }
    groups = [("Test", [sample])]
    try:
        mailer.send(
            cfg["email"],
            "%s test message" % cfg["email"].get("subject_prefix", "[PaperFeed]"),
            digest_module.render_html(groups, meta),
            digest_module.render_text(groups, meta),
        )
    except mailer.MailError as error:
        sys.stderr.write("\nEmail failed:\n\n%s\n\n" % error)
        return 1
    print("Test email sent to %s" % ", ".join(cfg["email"]["to_addresses"]))
    return 0


def _print_papers(papers, numbered=True):
    for index, paper in enumerate(papers, 1):
        prefix = "%3d. " % index if numbered else "     "
        score = ("[%.1f] " % paper.score) if getattr(paper, "score", 0) else ""
        print("%s%s%s" % (prefix, score, paper.title))
        detail = " | ".join(
            bit for bit in (paper.source, paper.venue, paper.published) if bit
        )
        if paper.authors:
            print("      %s" % paper.author_line(limit=5))
        print("      %s" % detail)
        print("      %s" % paper.url)


def command_serve(args):
    import server

    cfg = load_config_or_exit(args.config)
    setup_logging(cfg["log_path"], verbose=False)
    digest_path = os.path.join(cfg["digest_dir"], "latest.html")

    if not os.path.exists(digest_path):
        sys.stderr.write(
            "No digest to serve yet. Run 'python3 paperfeed.py run' first.\n"
        )
        return 2

    try:
        httpd = server.serve(digest_path, cfg["library_path"], args.port)
    except OSError as error:
        sys.stderr.write("Could not start the local server: %s\n" % error)
        return 1

    actual_port = httpd.server_address[1]
    url = "http://127.0.0.1:%d" % actual_port
    if actual_port != args.port:
        print("Port %d was busy, using %d instead." % (args.port, actual_port))
    print("PaperFeed is serving your latest digest at %s" % url)
    print("  %d paper(s) already in your library" % library.count(cfg["library_path"]))
    print("  Click '+ Save' on anything worth keeping. Press Ctrl-C to stop.")
    print("  (Bound to 127.0.0.1 - not reachable from your network.)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. %d paper(s) in your library." % library.count(cfg["library_path"]))
    finally:
        httpd.server_close()
    return 0


def command_saved(args):
    cfg = load_config_or_exit(args.config)
    records = library.all_saved(cfg["library_path"], args.limit)
    if not records:
        print("Nothing saved yet. Run 'paperfeed serve' and click + Save.")
        return 0
    print("%d saved paper(s), newest first:\n" % len(records))
    for index, record in enumerate(records, 1):
        print("%3d. %s" % (index, record["title"]))
        if record["authors"]:
            print("      %s" % ", ".join(record["authors"][:5]))
        detail = " | ".join(
            bit
            for bit in (record["source"], record["venue"], record["published"])
            if bit
        )
        print("      %s" % detail)
        print("      %s" % (record["url"] or ""))
    return 0


def command_search(args):
    cfg = load_config_or_exit(args.config)
    setup_logging(cfg["log_path"], verbose=False)
    text = args.text.strip()
    if not text:
        sys.stderr.write("Give me something to search for.\n")
        return 2

    if not args.online:
        records = library.search(cfg["library_path"], text, args.limit)
        if not records:
            print(
                "Nothing in your library matches %r.\n"
                "Add --online to search PubMed and the preprint servers instead."
                % text
            )
            return 0
        print("%d match(es) in your library:\n" % len(records))
        for index, record in enumerate(records, 1):
            print("%3d. %s" % (index, record["title"]))
            print("      %s | %s" % (record["source"], record["venue"] or "-"))
            print("      %s" % (record["url"] or ""))
        return 0

    # --online: ask the sources directly. Nothing is stored unless you say so.
    since = None
    if args.since:
        try:
            since = date.fromisoformat(args.since)
        except ValueError:
            sys.stderr.write("--since needs a date like 2025-01-31\n")
            return 2
    else:
        since = date.today() - timedelta(days=90)
    lookback = max(1, (date.today() - since).days)

    # "a b c" searched as one exact phrase would almost never match. Treat
    # separate words as "all of these must appear", and honour real quotes
    # when someone genuinely wants a phrase.
    if len(text) > 1 and text[0] == text[-1] == '"':
        terms, all_of = [text[1:-1]], []
    else:
        words = [word for word in text.split() if word]
        terms, all_of = ([], words) if len(words) > 1 else ([text], [])

    keyword_set = {
        "name": "search",
        "terms": terms,
        "all_of": all_of,
        "authors": [],
        "exclude": [],
        "fields": "title_abstract",
        "journals": {"allow": [], "deny": []},
    }

    print("Searching PubMed and preprints since %s ..." % since.isoformat())
    papers = []
    for key, (label, fetcher) in sources.SOURCES.items():
        if not cfg["sources"].get(key):
            continue
        try:
            papers.extend(
                fetcher(keyword_set, lookback, args.limit, cfg.get("contact_email", ""))
            )
        except Exception as error:
            print("  ! %s did not answer: %s" % (label, error))

    papers = store_module.deduplicate(papers)
    relevance.score_all(papers, [keyword_set], cfg["ranking"])
    papers = relevance.rank(papers)[: args.limit]

    if not papers:
        print("No matches.")
        return 0

    print("\n%d result(s), best first:\n" % len(papers))
    _print_papers(papers)

    if not sys.stdin.isatty():
        return 0
    answer = input("\nSave any? Enter numbers (e.g. 1,3) or press Enter to skip: ")
    wanted = [bit.strip() for bit in answer.replace(" ", ",").split(",") if bit.strip()]
    saved = 0
    for bit in wanted:
        if not bit.isdigit() or not (1 <= int(bit) <= len(papers)):
            print("  skipping %r - not one of the numbers above" % bit)
            continue
        paper = papers[int(bit) - 1]
        _, already = library.save(
            cfg["library_path"], digest_module.paper_payload(paper)
        )
        print("  %s %s" % ("already saved:" if already else "saved:", paper.title[:70]))
        saved += not already
    if saved:
        print("\n%d added. Your library now has %d." % (saved, library.count(cfg["library_path"])))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="paperfeed", description="Keyword-driven literature alerts."
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG, help="path to config.json"
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="fetch papers and write a digest")
    run_parser.add_argument(
        "--force", action="store_true", help="run even if the interval has not elapsed"
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and report, but write nothing and remember nothing",
    )
    run_parser.add_argument("--set", help="limit the run to one keyword set, by name")
    run_parser.add_argument(
        "--quiet", action="store_true", help="log to file only (used by the scheduler)"
    )
    run_parser.set_defaults(handler=command_run)

    status_parser = subparsers.add_parser("status", help="show current state")
    status_parser.set_defaults(handler=command_status)

    check_parser = subparsers.add_parser("check", help="validate config.json")
    check_parser.set_defaults(handler=command_check)

    email_parser = subparsers.add_parser("test-email", help="send one test message")
    email_parser.set_defaults(handler=command_test_email)

    serve_parser = subparsers.add_parser(
        "serve", help="open the latest digest in a browser with Save buttons"
    )
    serve_parser.add_argument("--port", type=int, default=8931)
    serve_parser.set_defaults(handler=command_serve)

    saved_parser = subparsers.add_parser("saved", help="list your saved papers")
    saved_parser.add_argument("--limit", type=int, default=100)
    saved_parser.set_defaults(handler=command_saved)

    search_parser = subparsers.add_parser(
        "search", help="search your library, or the sources with --online"
    )
    search_parser.add_argument("text", help="what to look for")
    search_parser.add_argument(
        "--online",
        action="store_true",
        help="query PubMed and the preprint servers instead of your library",
    )
    search_parser.add_argument(
        "--since", help="with --online: earliest date, e.g. 2025-01-31 (default 90 days)"
    )
    search_parser.add_argument("--limit", type=int, default=25)
    search_parser.set_defaults(handler=command_search)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
