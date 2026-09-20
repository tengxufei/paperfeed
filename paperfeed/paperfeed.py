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
from datetime import datetime

import config as config_module
import digest as digest_module
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

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
