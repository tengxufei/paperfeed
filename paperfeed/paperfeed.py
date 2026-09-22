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
import getpass
import json
import logging
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta

import ai
import collection
import config as config_module
import dashboard as dashboard_module
import digest as digest_module
import email_digest
import library
import mailer
import metrics as metrics_module
import phrasing
import query as query_module
import relevance
import retro
import sources
import stats as stats_module
import store as store_module

__version__ = "1.0.0"

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


def collect_papers(cfg, papers):
    """Auto-save the strongest papers from this run.

    The library used to grow only when you clicked Save. With the collector
    on it also accumulates by itself, which is what makes the dashboard's
    history worth analysing. Each row records whether it arrived by a click
    or by the collector, so you can always tell them apart.
    """
    settings = cfg["collect"]
    if not settings["enabled"] or not papers:
        return 0

    threshold = settings["min_score"]
    candidates = [paper for paper in papers if paper.score >= threshold]
    candidates = relevance.rank_with_ai(candidates)[: settings["max_per_run"]]

    added = 0
    for paper in candidates:
        try:
            _, already = library.save(
                cfg["library_path"], digest_module.paper_payload(paper), origin="auto"
            )
            added += not already
        except Exception as error:        # a full disk must not lose the digest
            log.error("Could not auto-save %r: %s", paper.title[:50], error)
            break
    if added:
        log.info(
            "Collector kept %s scoring %.1f or more (library now %s)",
            phrasing.count(added, "paper"),
            threshold,
            phrasing.count(library.count(cfg["library_path"]), "paper"),
        )
    return added


def build_dashboard(cfg, papers, window_papers, keyword_sets, hidden_count, meta):
    """Rebuild the dashboards: one overall, one per keyword set.

    A researcher following several fields needs each one on its own terms -
    a topic heating up is invisible in a combined count.

    Two different sets of papers go in. `papers` is what is NEW to you, which
    is what "new this run" and the score spread are about. `window_papers` is
    everything the queries matched in the lookback window, new or not, and it
    is what the subject panels and the trend history are built from - what a
    field is about does not change according to whether you happened to have
    seen a paper already, and on a short interval almost everything has been
    seen, which is why those panels used to go blank.
    """
    try:
        names = [entry["name"] for entry in keyword_sets]
        by_set = {name: [p for p in papers if p.set_name == name] for name in names}
        window_papers = window_papers or papers
        by_set_window = {name: [p for p in window_papers if p.set_name == name]
                         for name in names}

        overall = stats_module.run_stats(papers, keyword_sets)
        per_set = {name: stats_module.run_stats(by_set[name], keyword_sets)
                   for name in names}
        land = stats_module.run_stats(window_papers, keyword_sets)
        land_per_set = {name: stats_module.run_stats(by_set_window[name], keyword_sets)
                        for name in names}

        history = stats_module.record(
            os.path.join(cfg["base_dir"], "state", "topics.json"),
            dict(
                [(stats_module.ALL, land["subjects"])]
                + [(name, land_per_set[name]["subjects"]) for name in names]
            ),
            dict(
                [(stats_module.ALL, land["total"])]
                + [(name, land_per_set[name]["total"]) for name in names]
            ),
        )
        subject_cache = os.path.join(cfg["base_dir"], "state", "subjects.json")
        collection.remember(
            subject_cache,
            dict([(stats_module.ALL, land)]
                 + [(name, land_per_set[name]) for name in names]),
        )
        index_rows = dashboard_module._history(cfg["digest_dir"])
        summary = library.summary(cfg["library_path"])
        metrics_path = cfg.get("metrics", {}).get("path", "")

        pages = [(None, overall, land, "dashboard.html")]
        for name in names:
            pages.append((name, per_set[name], land_per_set[name],
                          dashboard_module.slug(name)))

        written = 0
        rising = []
        for scope, run, landscape, filename in pages:
            alerts = stats_module.alerts(
                history, landscape["subjects"],
                bucket=scope if scope else stats_module.ALL,
            )
            page = dashboard_module.render(
                run,
                index_rows,
                alerts,
                summary,
                {
                    "date_label": meta["date_label"],
                    "hidden": hidden_count if scope is None else 0,
                    "scope": scope,
                    "sets_order": names,
                    "lookback_days": cfg["lookback_days"],
                },
                coll=collection.snapshot(
                    cfg["library_path"], metrics_path, scope=scope
                ),
                history=history,
                subject_cache=subject_cache,
                landscape=landscape,
            )
            with open(os.path.join(cfg["digest_dir"], filename), "w",
                      encoding="utf-8") as handle:
                handle.write(page)
            written += 1
            if scope is None:
                # One short phrase per notable subject, for the alert email.
                # Only what is picking up: "fading" is not news worth an
                # inbox, and the dashboard covers it either way.
                rising = [
                    "%s is %s (%d this run, %s)" % (name_, verdict, now, detail)
                    for name_, now, verdict, detail in alerts
                    if verdict in ("new", "rising")
                ][:3]
            if scope and alerts:
                for name_, now, verdict, _ in alerts[:2]:
                    log.info("  %-7s %s in %r (%d this run)", verdict, name_, scope, now)

        log.info("Dashboards written: %d (one overall, one per keyword set)", written)
        return written, rising
    except Exception as error:          # a chart must never cost you a digest
        log.error("Dashboards could not be built: %s", error)
        return 0, []


def _parse_day(text, label):
    try:
        return date.fromisoformat(text)
    except (ValueError, TypeError):
        sys.stderr.write("%s needs a date like 2024-01-31, not %r\n" % (label, text))
        return None


def command_retro(args):
    """Search a date range of your choosing and analyse what it returns."""
    cfg = load_config_or_exit(args.config)
    setup_logging(cfg["log_path"], verbose=True)

    if args.clear_cache:
        removed = retro.clear_cache(cfg["base_dir"])
        print("Cleared %s from the cache." % phrasing.count(removed, "file"))
        return 0

    start = _parse_day(args.start, "--from")
    end = _parse_day(args.end, "--to")
    if not start or not end:
        return 2
    if end < start:
        sys.stderr.write("--to is before --from.\n")
        return 2

    keyword_sets = [entry for entry in cfg["keyword_sets"] if entry["enabled"]]
    if args.set:
        wanted = args.set.lower()
        keyword_sets = [e for e in keyword_sets if e["name"].lower() == wanted]
        if not keyword_sets:
            sys.stderr.write("No enabled keyword set called %r.\n" % args.set)
            return 2

    def progress(set_name, month, source, count, how):
        log.info("  %-26s %s  %-9s %5d  %s", set_name[:26], month, source, count, how)

    log.info(
        "Searching %s to %s across %s",
        start, end, phrasing.count(len(keyword_sets), "keyword set"),
    )
    papers, report = retro.search(
        cfg, keyword_sets, start, end, refresh=args.refresh, progress=progress
    )
    log.info(
        "%s after deduplication (%d from cache, %d fetched, %s)",
        phrasing.count(len(papers), "paper"),
        report["from_cache"], report["fetched"],
        phrasing.count(report["months"], "month"),
    )
    if not papers:
        sys.stderr.write("Nothing found in that range.\n")
        return 1

    relevance.score_all(papers, keyword_sets, cfg["ranking"])
    papers = relevance.rank(papers)

    comparison = None
    if args.compare:
        try:
            other_start, other_end = [
                _parse_day(part, "--compare") for part in args.compare.split(":")
            ]
        except ValueError:
            sys.stderr.write("--compare needs START:END, e.g. 2023-01-01:2023-12-31\n")
            return 2
        if not other_start or not other_end:
            return 2
        log.info("Fetching the comparison period %s to %s", other_start, other_end)
        other, _ = retro.search(
            cfg, keyword_sets, other_start, other_end,
            refresh=args.refresh, progress=progress,
        )
        comparison = {
            "label": "%s to %s" % (other_start, other_end),
            "changes": stats_module.compare(other, papers),
        }

    run = stats_module.run_stats(papers, keyword_sets)
    page = dashboard_module.render_retro(
        papers,
        run,
        stats_module.by_month(papers, start, end),
        stats_module.top_authors(papers),
        stats_module.top_institutions(papers),
        comparison,
        {
            "range_label": "%s to %s" % (start, end),
            "sets": [entry["name"] for entry in keyword_sets],
            "errors": report["errors"],
            "list_limit": args.limit,
        },
    )
    os.makedirs(cfg["digest_dir"], exist_ok=True)
    name = "retro-%s_%s.html" % (start, end)
    path = os.path.join(cfg["digest_dir"], name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page)

    log.info("Written: %s", path)
    print("\n%s found. Open %s" % (phrasing.count(len(papers), "paper"), path))
    print("Or with the server running: http://127.0.0.1:8931/%s" % name)
    return 0


def command_dashboard(args):
    cfg = load_config_or_exit(args.config)
    setup_logging(cfg["log_path"], verbose=False)
    path = os.path.join(cfg["digest_dir"], "dashboard.html")
    if not os.path.exists(path):
        sys.stderr.write(
            "No dashboard yet. Run 'python3 paperfeed.py run' first.\n"
        )
        return 2
    print(path)
    return 0


def command_run(args):
    cfg = load_config_or_exit(args.config)
    setup_logging(cfg["log_path"], verbose=not args.quiet)

    store = store_module.Store(cfg["state_path"])

    # The trend report runs on its own, much slower cadence. Checking it here
    # means the one daily launchd job drives both - no second scheduler.
    if cfg["trends"]["enabled"] and not args.dry_run:
        trends_due, trends_reason = store.due_mark(
            "trends", cfg["trends"]["interval_days"]
        )
        if trends_due:
            log.info("Trend report is due (%s); building it.", trends_reason)
            try:
                build_trends(cfg, store)
            except Exception as error:      # never let it break the digest
                log.error("Trend report failed: %s", error)

    due, reason = store.due(cfg["interval_days"], cfg["interval_hours"])
    # A dry run changes nothing, so the interval gate would only get in the
    # way of the question you are actually asking: what would I get now?
    if not due and not args.force and not args.dry_run:
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
    notices = []
    for keyword_set in keyword_sets:
        papers, set_errors, set_notices = sources.fetch_all(keyword_set, cfg)
        errors.extend(set_errors)
        notices.extend(set_notices)
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
        for message in set_notices:
            log.warning("  ! %s", message)
        fetched.extend(kept)

    unique = store_module.deduplicate(fetched)

    # Two different questions, deliberately separated.
    #
    #   is_new    has PaperFeed ever put this in front of you? The email
    #             carries only these, so nothing repeats in your inbox.
    #   shown     what the digest displays: everything from the last
    #             digest_days, whether or not you have seen it, PLUS
    #             anything older you have never been shown.
    #
    # The second half of that matters: with a strict window, being away for
    # five days would silently cost you the papers from days four and five.
    # The window is a floor, not a ceiling.
    unseen_keys = {
        store_module.fingerprint(paper) for paper in store.filter_new(unique)
    }
    for paper in unique:
        paper.is_new = store_module.fingerprint(paper) in unseen_keys

    cutoff = (date.today() - timedelta(days=cfg["digest_days"])).isoformat()
    shown = [
        paper for paper in unique
        if paper.is_new or (paper.published or "")[:10] >= cutoff
    ]

    ranking = cfg["ranking"]
    show_scores = bool(ranking.get("enabled", True))
    low_scoring = []
    if show_scores:
        relevance.score_all(shown, keyword_sets, ranking)
        # Title coverage first: "is this paper about my topic at all" is a
        # clearer question than "did it accumulate enough points", and
        # answering it first keeps the score threshold doing one job.
        shown, thin = relevance.drop_below_title_concepts(
            shown, keyword_sets
        )
        hidden.extend(thin)
        shown, low_scoring = relevance.drop_below_per_set(
            shown, keyword_sets, ranking.get("min_score", 0)
        )
        hidden.extend(low_scoring)

    # Everything the digest will show, and the subset that is genuinely new.
    # Only the second goes in the email, the collector and the run counts.
    new_papers = [paper for paper in shown if paper.is_new]

    in_window = sum(1 for paper in shown if (paper.published or "")[:10] >= cutoff)
    log.info(
        "%d kept after filters, %d after deduplication, %d in the last %d "
        "days, %d never shown before, %d hidden",
        len(fetched), len(unique), in_window, cfg["digest_days"],
        len(new_papers), len(hidden),
    )

    # Journal and citation figures. Like AI scoring below, this is laid on
    # top of a digest that has to be produced whether or not it works, so
    # enrich() swallows its own failures and reports what it managed.
    metrics_report = {"enabled": False}
    if shown and not args.dry_run:
        metrics_report = metrics_module.enrich(shown, cfg, log)
        if metrics_report.get("enriched"):
            log.info(
                "metrics: %d paper(s) and %d journal(s) from OpenAlex in "
                "%d request(s), %s credits left",
                metrics_report["enriched"], metrics_report["journals"],
                metrics_report["requests"], metrics_report["remaining"],
            )
        for line in metrics_module.describe(metrics_report):
            if "not reachable" in line or "ran low" in line:
                log.warning("  ! %s", line)

    # AI scoring is strictly an enhancement. Anything that goes wrong here -
    # no key, a rejected key, a dead network, a garbled reply - leaves the
    # local ranking in place and the digest still gets written.
    ai_note = None
    used_ai = False
    if cfg["ai"]["enabled"] and shown and not args.dry_run:
        settings = ai.settings_for(cfg)
        if not settings["api_key"]:
            ai_note = (
                "AI scoring is on but $%s is not set in this environment, so "
                "papers were ranked locally instead." % settings["api_key_env"]
            )
        else:
            try:
                scored, usage, problem = ai.score_papers(
                    shown, cfg["ai"], settings, keyword_sets, log
                )
                used_ai = scored > 0
                if scored:
                    spend = ai.cost_of(usage)
                    log.info(
                        "AI scored %d paper(s) in %d call(s), %d in / %d out tokens%s",
                        scored,
                        usage.get("calls", 0),
                        usage.get("input_tokens", 0),
                        usage.get("output_tokens", 0),
                        (" (about $%.3f)" % spend) if spend is not None else "",
                    )
                if problem:
                    ai_note = "Part of the AI scoring did not complete: %s" % problem
                if not scored and not problem:
                    ai_note = "The AI returned no usable scores. Ranked locally."
            except ai.KeyRejected as error:
                ai_note = "%s Ranked locally instead." % error
            except Exception as error:               # never lose the digest
                ai_note = "AI scoring failed (%s). Ranked locally instead." % error
    if ai_note:
        log.warning(ai_note)

    def grouped(papers):
        by_set = {}
        for paper in papers:
            by_set.setdefault(paper.set_name, []).append(paper)
        for name in by_set:
            if used_ai:
                by_set[name] = relevance.rank_with_ai(by_set[name])
            elif show_scores:
                by_set[name] = relevance.rank(by_set[name])
            else:
                by_set[name].sort(key=lambda paper: paper.published or "",
                                  reverse=True)
        return [(entry["name"], by_set.get(entry["name"], []))
                for entry in keyword_sets]

    by_set = {}
    for paper in shown:
        by_set.setdefault(paper.set_name, []).append(paper)
    for name in by_set:
        if used_ai:
            by_set[name] = relevance.rank_with_ai(by_set[name])
        elif show_scores:
            by_set[name] = relevance.rank(by_set[name])
        else:
            by_set[name].sort(key=lambda paper: paper.published or "", reverse=True)
    groups = [(entry["name"], by_set.get(entry["name"], [])) for entry in keyword_sets]
    # The email carries only what you have never been shown, so a paper never
    # arrives in your inbox twice however often the job runs.
    email_groups = grouped(new_papers)

    moment = datetime.now()
    enabled_sources = [
        label for key, (label, _) in sources.SOURCES.items() if cfg["sources"].get(key)
    ]
    source_counts = dict(Counter(paper.source for paper in shown))

    hidden_examples = list(notices)
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
        "total_shown": len(shown),
        "digest_days": cfg["digest_days"],
        "lookback_days": cfg["lookback_days"],
        "errors": errors,
        "sources_label": " and ".join(enabled_sources),
        "show_scores": show_scores,
        "source_counts": source_counts,
        "hidden_count": len(hidden),
        "hidden_examples": hidden_examples,
        "top_papers": (
            (relevance.rank_with_ai(shown)[:5] if used_ai
             else relevance.top_n(shown, 5))
            if len(shown) > 5
            else []
        ),
        "ai_note": ai_note,
        "metrics_lines": metrics_module.describe(metrics_report),
        "ai_label": ai.describe_model(cfg) if used_ai else "",
        "library_total": library.count(cfg["library_path"]),
    }
    html_text = digest_module.render_html(groups, meta)

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

    collect_papers(cfg, new_papers)
    _, rising = build_dashboard(
        cfg, new_papers, unique, keyword_sets, len(hidden), meta
    )

    # The email is rendered here, after the digest is safely on disk and
    # after the dashboard has worked out what is rising, so the alert can
    # carry both. Nothing below may cost the reader their digest.
    meta["window_total"] = len(unique)
    meta["rising"] = rising
    meta["library"] = collection.snapshot(
        cfg["library_path"], cfg.get("metrics", {}).get("path", "")
    )
    text_body = email_digest.render_text(email_groups, meta)
    email_html = email_digest.render(email_groups, meta)

    update_index(
        cfg,
        {
            "file": os.path.basename(stamped),
            "date_label": meta["date_label"],
            "total": len(new_papers),
            "sources": source_counts,
            "sets": {name: len(papers) for name, papers in groups},
            "hidden": len(hidden),
        },
    )

    email_settings = cfg["email"]
    if not email_settings["enabled"]:
        log.info("Email is switched off in config.json (email.enabled).")
    elif not email_settings.get("password_available", True):
        log.warning(
            "Email is on but %s is not set in this environment, so no email "
            "was sent. The digest above is on disk. (Scheduled runs do not "
            "read ~/.zshrc - see the README on launchd and passwords.)",
            email_settings["password_env"],
        )
    elif not new_papers and not email_settings.get("send_when_empty", False):
        log.info("No new papers, so no email sent (email.send_when_empty is false).")
    else:
        subject = email_digest.subject_line(
            email_groups, meta,
            email_settings.get("subject_prefix", "[PaperFeed]"),
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
    due, reason = store.due(cfg["interval_days"], cfg["interval_hours"])

    print("PaperFeed %s" % __version__)
    print("  config      %s" % cfg["config_path"])
    print("  keyword sets")
    for entry in cfg["keyword_sets"]:
        print(
            "    %-34s %s (%d terms)"
            % (entry["name"], "on " if entry["enabled"] else "off", len(entry["terms"]))
        )
    print(
        "  interval    %s"
        % (
            "every %g hours" % cfg["interval_hours"]
            if cfg["interval_hours"]
            else "every %d days" % cfg["interval_days"]
        )
    )
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
        "  remembered  %s (%s)"
        % (
            phrasing.count(store.paper_count, "paper"),
            phrasing.count(len(store.seen), "identity key"),
        )
    )
    print("  library     %s saved" % phrasing.count(library.count(cfg["library_path"]), "paper"))
    latest = os.path.join(cfg["digest_dir"], "latest.html")
    print("  latest      %s" % (latest if os.path.exists(latest) else "none yet"))
    return 0


def describe_set(entry):
    """One line saying what a keyword set actually searches for."""
    if entry.get("query"):
        return entry["query"]
    parts = [" OR ".join(entry["terms"])] if entry["terms"] else []
    parts.extend(entry.get("all_of") or [])
    if entry.get("authors"):
        parts.append("by " + " or ".join(entry["authors"]))
    return " AND ".join(part for part in parts if part) or "(nothing)"


def explain_set(entry, cfg, count=True):
    """Print how one keyword set reaches each search engine.

    The point of this is that the two engines are not equivalent, and where
    they differ the difference should be on the screen rather than buried.
    """
    print("\n  %s" % entry["name"])
    print("    you wrote : %s" % describe_set(entry))

    tree = sources.parsed_query(entry)
    if tree is None:
        print("    (still using the old terms / all_of lists - run "
              "'migrate-queries' to convert it)")

    notes = []
    pubmed_term = sources._pubmed_query(entry, notes)
    print("    PubMed    : %s" % pubmed_term)

    preprint_notes = []
    from datetime import date as _date, timedelta as _timedelta
    end = _date.today()
    start = end - _timedelta(days=cfg["lookback_days"])
    preprint_term = sources._preprint_query(entry, start, end, preprint_notes)
    if preprint_term is None:
        print("    preprints : nothing can match - see the note below")
    else:
        print("    preprints : %s" % preprint_term)

    if tree is not None:
        groups = query_module.concept_groups(tree)
        print("    concepts  : %d (%s)"
              % (len(groups),
                 "; ".join(query_module.unparse(group) for group in groups)))

    if count:
        contact = cfg.get("contact_email", "")
        try:
            total, _, more = sources.count_pubmed(
                entry, cfg["lookback_days"], contact)
            notes.extend(more)
            print("    last %d days: %s on PubMed"
                  % (cfg["lookback_days"], phrasing.count(total, "paper")))
        except Exception as error:
            print("    last %d days: PubMed could not be reached (%s)"
                  % (cfg["lookback_days"], error))
        try:
            total, _, more = sources.count_preprints(
                entry, cfg["lookback_days"], contact)
            preprint_notes.extend(more)
            if total is not None:
                print("                 %s as preprints"
                      % phrasing.count(total, "paper"))
        except Exception as error:
            print("                 Europe PMC could not be reached (%s)" % error)

    # The preprint query is built twice here - once to show it, once to
    # count it - so the same note arrives twice. Say each thing once.
    seen = set()
    for note in notes + preprint_notes:
        if note not in seen:
            seen.add(note)
            print("    note      : %s" % note)


def command_query(args):
    """Try a query out before putting it in config.json."""
    if args.cheatsheet or not args.text:
        print(query_module.CHEATSHEET)
        if not args.text:
            return 0

    try:
        tree = query_module.parse(args.text)
    except query_module.QueryError as error:
        print("That query cannot be read:\n\n      %s" % error)
        return 1

    entry = {"name": "your query", "query": args.text, "terms": [],
             "all_of": [], "authors": [], "fields": "title_abstract"}
    cfg = {"lookback_days": args.days, "contact_email": ""}
    try:
        cfg = load_config_or_exit(args.config)
        cfg = dict(cfg)
        cfg["lookback_days"] = args.days
    except SystemExit:
        pass

    print("\nReading it back:\n")
    for line in query_module.describe(tree).splitlines():
        print("    %s" % line)
    explain_set(entry, cfg, count=not args.no_count)
    return 0


def command_migrate_queries(args):
    """Rewrite terms / all_of / authors as a written query, showing the
    before and after counts so that nothing changes silently."""
    cfg = load_config_or_exit(args.config)
    path = cfg["config_path"]
    raw = json.load(open(path, encoding="utf-8"))

    contact = cfg.get("contact_email", "")
    lookback = cfg["lookback_days"]
    changes = []

    for entry in raw.get("keyword_sets", []):
        name = entry.get("name", "?")
        if entry.get("query"):
            print("\n  %s: already has a query, left alone." % name)
            continue
        try:
            written = query_module.from_legacy(entry)
        except query_module.NotMigratable as why:
            print("\n  %s: NOT converted - %s" % (name, why))
            continue

        print("\n  %s" % name)
        print("    before: %s" % describe_set(
            {"terms": entry.get("terms") or [], "all_of": entry.get("all_of") or [],
             "authors": entry.get("authors") or [], "query": ""}))
        print("    after : %s" % written)

        if not args.no_count:
            after = dict(entry)
            after["query"] = written
            for label, counter in (("PubMed", sources.count_pubmed),
                                   ("preprints", sources.count_preprints)):
                try:
                    before_n = counter(entry, lookback, contact)[0]
                    after_n = counter(after, lookback, contact)[0]
                    print("    %-9s %s -> %s in the last %d days"
                          % (label + ":",
                             "n/a" if before_n is None else before_n,
                             "n/a" if after_n is None else after_n, lookback))
                except Exception as error:
                    print("    %-9s could not be counted (%s)" % (label + ":", error))

        entry["query"] = written
        changes.append(name)

    if not changes:
        print("\nNothing to convert.")
        return 0

    if not args.write:
        print("\nThat was a dry run. Nothing has been changed.")
        print("Run it again with --write to save these queries into %s." % path)
        return 0

    backup = path + ".bak"
    with open(backup, "w", encoding="utf-8") as handle:
        json.dump(json.load(open(path, encoding="utf-8")), handle, indent=2)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(raw, handle, indent=2)
        handle.write("\n")
    print("\nConverted %s. Your old config is saved as %s."
          % (phrasing.count(len(changes), "keyword set"), backup))
    print("The old terms / all_of lists were left in place and are now "
          "ignored; delete them when you are happy.")
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
            print("    %s: %s" % (entry["name"], describe_set(entry)))
    print(
        "  showing the last %d days, searching back %d days"
        % (cfg["digest_days"], cfg["lookback_days"])
    )
    print(
        "  %s, looking back %d days"
        % (
            "every %g hours" % cfg["interval_hours"]
            if cfg["interval_hours"]
            else "every %d days" % cfg["interval_days"],
            cfg["lookback_days"],
        )
    )
    if cfg["email"]["enabled"]:
        if cfg["email"].get("password_available"):
            print("  email: enabled, password found in $%s" % cfg["email"]["password_env"])
        else:
            print(
                "  email: enabled, but $%s is NOT set here - digests will still\n"
                "         be written, they just will not be sent."
                % cfg["email"]["password_env"]
            )
    else:
        print("  email: disabled")

    settings = ai.settings_for(cfg)
    print(
        "  AI: %s via %s (%s), key in $%s: %s"
        % (
            "on" if cfg["ai"]["enabled"] else "off",
            cfg["ai"]["provider"],
            cfg["ai"]["base_url"] or "default endpoint",
            settings["api_key_env"],
            "found" if settings["api_key"] else "NOT set",
        )
    )
    table = cfg["metrics"]["journal_table"]
    if cfg["metrics"]["enabled"]:
        print("  metrics: on - citations, open access and journal figures from")
        print("           OpenAlex (free, keyless, CC0). No Impact Factor is")
        print("           fetched or shipped: it is Clarivate's and paywalled.")
        if table["path"]:
            print("           plus your own file: %s" % table["path"])
        else:
            print("           to add licensed figures of your own, set")
            print("           metrics.journal_table.path to a CSV you have.")
    else:
        print("  metrics: off")

    print(
        "  collector: %s"
        % (
            "on, saving papers scoring %.1f or more (max %d a run)"
            % (cfg["collect"]["min_score"], cfg["collect"]["max_per_run"])
            if cfg["collect"]["enabled"]
            else "off - the library only grows when you click Save"
        )
    )

    if getattr(args, "explain", False):
        print("\n  How each keyword set reaches the two search engines:")
        for entry in cfg["keyword_sets"]:
            if entry["enabled"]:
                explain_set(entry, cfg, count=not args.no_count)

    missing = config_module.settings_not_in_file(cfg["config_path"])
    if missing:
        print(
            "\n  Settings you have not written into config.json, so they are\n"
            "  using built-in defaults. Add any you want to change:"
        )
        for line in missing:
            print("    %s" % line)

    if cfg["ai"]["enabled"] or cfg["trends"]["enabled"] or args.costs:
        estimates = ai.estimate_costs(cfg)
        print("\n  Estimated API cost (rough - actual usage is logged each run):")
        scoring = estimates["scoring"]
        if scoring["per_run"] is not None:
            print(
                "    scoring   ~$%.3f per run (%d papers, %s), about $%.2f/year at every %d days"
                % (
                    scoring["per_run"],
                    scoring["papers"],
                    scoring["model"],
                    scoring["per_year"],
                    cfg["interval_days"],
                )
            )
        report = estimates["trends"]
        if report["per_run"] is not None:
            print(
                "    trends    ~$%.3f per report (%d papers, %s), about $%.2f/year at every %d days"
                % (
                    report["per_run"],
                    report["papers"],
                    report["model"],
                    report["per_year"],
                    cfg["trends"]["interval_days"],
                )
            )
        total = sum(
            value["per_year"]
            for key_name, value in estimates.items()
            if value["per_year"] is not None
            and (cfg[key_name if key_name != "scoring" else "ai"]["enabled"] or args.costs)
        )
        print("    -> roughly $%.2f a year in total" % total)
        if not cfg["ai"]["enabled"] and not cfg["trends"]["enabled"]:
            print("    (both are currently off, so you are spending nothing)")
    return 0


def email_preflight(cfg):
    """Report what we can tell before attempting a send. Never prints the
    password itself - only its shape."""
    settings = cfg["email"]
    env_name = settings.get("password_env", "PAPERFEED_SMTP_PASSWORD")
    shape = mailer.describe_password(os.environ.get(env_name, ""))
    problems = []

    print("Email settings")
    print("  server        %s:%s" % (settings["smtp_host"], settings["smtp_port"]))
    print("  login as      %s" % settings["username"])
    print("  from          %s" % settings["from_address"])
    print("  to            %s" % ", ".join(settings["to_addresses"]))

    if not shape["set"]:
        print("  password      $%s is NOT set in this shell" % env_name)
        problems.append(
            "Set it with:  export %s='your-app-password'" % env_name
        )
    else:
        verdict = "looks like a Google app password" if shape["looks_like_app_password"] else (
            "does NOT look like an app password"
        )
        print(
            "  password      $%s is set, %d characters, %s"
            % (env_name, shape["length"], verdict)
        )
        if shape["had_spaces"]:
            print("                (it contains spaces; these are stripped automatically)")
        if shape["length"] != 16:
            problems.append(
                "A Google app password is exactly 16 letters. Yours is %d "
                "characters, which suggests it is your normal account password "
                "- Gmail always rejects those for SMTP. Generate one at "
                "https://myaccount.google.com/apppasswords" % shape["length"]
            )

    # Gmail will not let you send as an address the logged-in account does
    # not own, so a mismatch here fails after authentication succeeds.
    if (
        "gmail" in settings["smtp_host"]
        and settings["from_address"].strip().lower()
        != settings["username"].strip().lower()
    ):
        problems.append(
            "from_address (%s) is not the account you log in as (%s). Gmail "
            "only lets you send as yourself, or as a verified alias. Make "
            "them the same unless you have set up an alias."
            % (settings["from_address"], settings["username"])
        )

    if problems:
        print("\nLikely problems:")
        for problem in problems:
            print("  - %s" % problem)
    return problems


def command_test_email(args):
    cfg = load_config_or_exit(args.config)
    setup_logging(cfg["log_path"], verbose=False)
    if not cfg["email"]["enabled"]:
        sys.stderr.write(
            "Email is disabled. Set email.enabled to true in config.json first.\n"
        )
        return 2

    problems = email_preflight(cfg)
    if problems and not args.anyway:
        print("\nNot sending. Fix the above, or re-run with --anyway to try regardless.")
        return 1
    print("\nSending ...")

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
            email_digest.render_text(groups, meta),
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
        httpd = server.serve(
            digest_path, cfg["library_path"], args.port,
            config_path=cfg["config_path"], cfg=cfg,
        )
    except OSError as error:
        sys.stderr.write("Could not start the local server: %s\n" % error)
        return 1

    actual_port = httpd.server_address[1]
    url = "http://127.0.0.1:%d" % actual_port
    if actual_port != args.port:
        print("Port %d was busy, using %d instead." % (args.port, actual_port))
    print("PaperFeed is serving your latest digest at %s" % url)
    print("  %s already in your library" % phrasing.count(library.count(cfg["library_path"]), "paper"))
    print("  Click '+ Save' on anything worth keeping. Press Ctrl-C to stop.")
    print("  (Bound to 127.0.0.1 - not reachable from your network.)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. %s in your library." % phrasing.count(library.count(cfg["library_path"]), "paper"))
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
        print("%s in your library:\n" % phrasing.count(len(records), "match", "matches"))
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
                fetcher(
                    keyword_set, lookback, args.limit, cfg.get("contact_email", "")
                )
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


def command_trends(args):
    cfg = load_config_or_exit(args.config)
    setup_logging(cfg["log_path"], verbose=not args.quiet)

    store = store_module.Store(cfg["state_path"])
    due, reason = store.due_mark("trends", cfg["trends"]["interval_days"])
    if not due and not args.force:
        log.info("Trend report not due yet (%s). Use --force to run anyway.", reason)
        return 0

    themes, path = build_trends(cfg, store)
    if not path:
        return 1
    print("%s. Open %s" % (phrasing.count(len(themes), "theme"), path))
    return 0 if themes else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="paperfeed",
        description="PaperFeed %s - keyword-driven literature alerts." % __version__,
    )
    parser.add_argument(
        "--version", action="version", version="PaperFeed %s" % __version__
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

    query_parser = subparsers.add_parser(
        "query", help="try a search query out, or print the syntax cheatsheet"
    )
    query_parser.add_argument(
        "text", nargs="?", default="",
        help='the query, e.g. \'(IDH1 OR "isocitrate dehydrogenase 1") AND glioma\''
    )
    query_parser.add_argument(
        "--cheatsheet", action="store_true", help="print the syntax guide"
    )
    query_parser.add_argument(
        "--days", type=int, default=14, help="window to count over (default 14)"
    )
    query_parser.add_argument(
        "--no-count", action="store_true", help="do not contact the search engines"
    )
    query_parser.set_defaults(handler=command_query)

    migrate_parser = subparsers.add_parser(
        "migrate-queries",
        help="convert old terms/all_of lists into written queries",
    )
    migrate_parser.add_argument(
        "--write", action="store_true",
        help="actually save the changes (otherwise it is a dry run)"
    )
    migrate_parser.add_argument(
        "--no-count", action="store_true", help="skip the before/after counts"
    )
    migrate_parser.set_defaults(handler=command_migrate_queries)

    check_parser = subparsers.add_parser("check", help="validate config.json")
    check_parser.add_argument(
        "--explain", action="store_true",
        help="show how each keyword set reaches PubMed and Europe PMC"
    )
    check_parser.add_argument(
        "--no-count", action="store_true",
        help="with --explain, do not contact the search engines"
    )
    check_parser.add_argument(
        "--costs",
        action="store_true",
        help="show the AI cost estimate even when the AI features are off",
    )
    check_parser.set_defaults(handler=command_check)

    email_parser = subparsers.add_parser("test-email", help="send one test message")
    email_parser.add_argument(
        "--anyway", action="store_true",
        help="attempt the send even if the pre-flight check finds problems",
    )
    email_parser.set_defaults(handler=command_test_email)

    serve_parser = subparsers.add_parser(
        "serve", help="open the latest digest in a browser with Save buttons"
    )
    serve_parser.add_argument("--port", type=int, default=8931)
    serve_parser.set_defaults(handler=command_serve)

    dash_parser = subparsers.add_parser(
        "dashboard", help="print the path to the dashboard page"
    )
    dash_parser.set_defaults(handler=command_dashboard)

    retro_parser = subparsers.add_parser(
        "retro", help="search a date range of your choosing and analyse it"
    )
    retro_parser.add_argument("--from", dest="start", help="first date, e.g. 2024-01-01")
    retro_parser.add_argument("--to", dest="end", help="last date, e.g. 2024-12-31")
    retro_parser.add_argument("--set", help="limit to one keyword set")
    retro_parser.add_argument(
        "--compare", help="another range to compare against, START:END"
    )
    retro_parser.add_argument(
        "--refresh", action="store_true", help="ignore the cache and refetch"
    )
    retro_parser.add_argument(
        "--clear-cache", action="store_true", help="delete cached results and stop"
    )
    retro_parser.add_argument("--limit", type=int, default=60,
                              help="how many papers to list on the page")
    retro_parser.set_defaults(handler=command_retro)

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

    trends_parser = subparsers.add_parser(
        "trends", help="summarise the themes of the last few months"
    )
    trends_parser.add_argument("--force", action="store_true")
    trends_parser.add_argument("--quiet", action="store_true")
    trends_parser.set_defaults(handler=command_trends)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
