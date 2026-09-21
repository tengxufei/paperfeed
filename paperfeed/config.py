"""Load and validate config.json, with plain-English errors.

Everything a user might want to change lives in the config file. This module's
job is to read it and, if something is wrong, say what is wrong in a sentence
instead of throwing a traceback.

Keys beginning with "_" are treated as comments and ignored.
"""

import json
import os

DEFAULTS = {
    "interval_days": 3,
    # Overrides interval_days when above zero. Useful for testing, and for
    # anyone who wants to hear about a paper the hour it appears rather than
    # three days later. The scheduler must wake at least this often too.
    "interval_hours": 0,
    "ranking": {
        "enabled": True,
        "title_weight": 4.0,
        "abstract_weight": 1.0,
        "breadth_bonus": 1.0,
        "recency_bonus": 1.0,
        "author_bonus": 3.0,
        "min_score": 0.0,
    },
    "mute": {"terms": [], "journals": []},
    "ai": {
        "enabled": False,
        "provider": "anthropic",
        "base_url": "",
        "model": "claude-haiku-4-5",
        "api_key_env": "PAPERFEED_AI_KEY",
        "interests": "",
        "max_papers_per_run": 40,
        "batch_size": 10,
    },
    "collect": {
        "enabled": False,
        "min_score": 6.0,
        "max_per_run": 25,
    },
    "trends": {
        "enabled": False,
        "months": 3,
        "max_papers": 300,
        "model": "claude-sonnet-5",
        "interval_days": 30,
        "email": False,
    },
    "lookback_days": 14,
    "max_per_set": 30,
    "contact_email": "",
    "sources": {"pubmed": True, "preprints": True},
    "email": {
        "enabled": False,
        "smtp_host": "",
        "smtp_port": 465,
        "username": "",
        "password_env": "PAPERFEED_SMTP_PASSWORD",
        "from_address": "",
        "to_addresses": [],
        "subject_prefix": "[PaperFeed]",
        "send_when_empty": False,
    },
}


class ConfigError(Exception):
    """Raised with a message meant to be read by a human, not a debugger."""


def _strip_comments(value):
    if isinstance(value, dict):
        return {k: _strip_comments(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [_strip_comments(v) for v in value]
    return value


def _positive_int(cfg, key, where=""):
    value = cfg.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(
            "%s'%s' must be a number, but found: %r" % (where, key, value)
        )
    if value < 1:
        raise ConfigError("%s'%s' must be at least 1, but found %s" % (where, key, value))
    return int(value)


def _validate_keyword_sets(raw):
    if not isinstance(raw, list) or not raw:
        raise ConfigError(
            "'keyword_sets' must be a list with at least one entry. "
            "See config.example.json for the shape."
        )

    cleaned = []
    seen_names = set()
    for index, entry in enumerate(raw):
        label = "keyword_sets[%d]" % index
        if not isinstance(entry, dict):
            raise ConfigError("%s must be a block with 'name' and 'terms'." % label)

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("%s needs a non-empty 'name'." % label)
        name = name.strip()
        if name in seen_names:
            raise ConfigError(
                "Two keyword sets are both called %r. Give them different names." % name
            )
        seen_names.add(name)

        def string_list(key, required=False):
            raw_value = entry.get(key)
            if raw_value is None:
                if required:
                    raise ConfigError(
                        "Keyword set %r needs a '%s' list." % (name, key)
                    )
                return []
            if isinstance(raw_value, str):
                raw_value = [raw_value]
            if not isinstance(raw_value, list):
                raise ConfigError(
                    "Keyword set %r: '%s' must be a list of words." % (name, key)
                )
            cleaned_items = []
            for item in raw_value:
                if not isinstance(item, str) or not item.strip():
                    raise ConfigError(
                        "Keyword set %r has an entry in '%s' that is not text: %r"
                        % (name, key, item)
                    )
                cleaned_items.append(item.strip())
            return cleaned_items

        clean_terms = string_list("terms")
        all_of = string_list("all_of")
        authors = string_list("authors")
        exclude = string_list("exclude")

        # A set is valid if it can search for *something*: keywords, a
        # required phrase, or an author to follow.
        if not (clean_terms or all_of or authors):
            raise ConfigError(
                "Keyword set %r has nothing to search for. Give it 'terms', "
                "'all_of', or 'authors'." % name
            )

        fields = entry.get("fields", "title_abstract")
        if fields not in ("title_abstract", "all"):
            raise ConfigError(
                "Keyword set %r has 'fields': %r - it must be \"title_abstract\" "
                "or \"all\"." % (name, fields)
            )

        journals = entry.get("journals") or {}
        if not isinstance(journals, dict):
            raise ConfigError(
                "Keyword set %r: 'journals' must be a block with 'allow' "
                "and/or 'deny' lists." % name
            )
        for key in journals:
            if key not in ("allow", "deny"):
                raise ConfigError(
                    "Keyword set %r: journals.%s is not a setting. Use 'allow' "
                    "or 'deny'." % (name, key)
                )
        allow = journals.get("allow") or []
        deny = journals.get("deny") or []
        for label, values in (("allow", allow), ("deny", deny)):
            if not isinstance(values, list):
                raise ConfigError(
                    "Keyword set %r: journals.%s must be a list." % (name, label)
                )

        set_min_score = entry.get("min_score")
        if set_min_score is not None:
            if isinstance(set_min_score, bool) or not isinstance(
                set_min_score, (int, float)
            ):
                raise ConfigError(
                    "Keyword set %r: 'min_score' must be a number between 0 and 10, "
                    "but found %r." % (name, set_min_score)
                )
            if not 0 <= set_min_score <= 10:
                raise ConfigError(
                    "Keyword set %r: 'min_score' must be between 0 and 10." % name
                )

        set_sources = entry.get("sources")
        if set_sources is not None:
            if not isinstance(set_sources, dict):
                raise ConfigError(
                    "Keyword set %r: 'sources' must be a block like "
                    "{\"preprints\": false}." % name
                )
            for key, value in set_sources.items():
                if not isinstance(value, bool):
                    raise ConfigError(
                        "Keyword set %r: sources.%s must be true or false, "
                        "but found %r." % (name, key, value)
                    )

        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(
                "Keyword set %r has 'enabled': %r — it must be true or false."
                % (name, enabled)
            )

        cleaned.append(
            {
                "name": name,
                "terms": clean_terms,
                "all_of": all_of,
                "authors": authors,
                "exclude": exclude,
                "fields": fields,
                "journals": {"allow": list(allow), "deny": list(deny)},
                "sources": set_sources,
                "min_score": set_min_score,
                "enabled": enabled,
            }
        )

    if not any(entry["enabled"] for entry in cleaned):
        raise ConfigError(
            "Every keyword set has \"enabled\": false, so there is nothing to search."
        )
    return cleaned


def _validate_block(raw, defaults, label):
    """Validate a simple settings block against its defaults.

    Types must match the default's type, so a typo like "enabled": "yes"
    is caught here rather than behaving oddly three steps later.
    """
    block = dict(defaults)
    if raw is None:
        return block
    if not isinstance(raw, dict):
        raise ConfigError("'%s' must be a block of settings." % label)
    for key in raw:
        if key not in defaults:
            raise ConfigError(
                "%s.%s is not a setting. Valid ones: %s"
                % (label, key, ", ".join(sorted(defaults)))
            )
    block.update(raw)

    for key, default in defaults.items():
        value = block[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(
                    "%s.%s must be true or false, but found: %r" % (label, key, value)
                )
        elif isinstance(default, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(
                    "%s.%s must be a number, but found: %r" % (label, key, value)
                )
            if value < 0:
                raise ConfigError("%s.%s cannot be negative." % (label, key))
            block[key] = float(value)
        elif isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(
                    "%s.%s must be a whole number, but found: %r" % (label, key, value)
                )
            if value < 1:
                raise ConfigError("%s.%s must be at least 1." % (label, key))
        elif isinstance(default, str):
            if not isinstance(value, str):
                raise ConfigError(
                    "%s.%s must be text, but found: %r" % (label, key, value)
                )
    return block


def _validate_ranking(raw):
    ranking = dict(DEFAULTS["ranking"])
    if raw is None:
        return ranking
    if not isinstance(raw, dict):
        raise ConfigError("'ranking' must be a block of settings.")
    for key in raw:
        if key not in ranking:
            raise ConfigError(
                "ranking.%s is not a setting. Valid ones: %s"
                % (key, ", ".join(sorted(ranking)))
            )
    ranking.update(raw)

    if not isinstance(ranking["enabled"], bool):
        raise ConfigError(
            "ranking.enabled must be true or false, but found: %r"
            % (ranking["enabled"],)
        )
    for key, value in ranking.items():
        if key == "enabled":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(
                "ranking.%s must be a number, but found: %r" % (key, value)
            )
        if value < 0:
            raise ConfigError("ranking.%s cannot be negative." % key)
    return ranking


def _validate_mute(raw):
    mute = {"terms": [], "journals": []}
    if raw is None:
        return mute
    if not isinstance(raw, dict):
        raise ConfigError(
            "'mute' must be a block with 'terms' and/or 'journals' lists."
        )
    for key in raw:
        if key not in mute:
            raise ConfigError(
                "mute.%s is not a setting. Use 'terms' or 'journals'." % key
            )
    for key in ("terms", "journals"):
        values = raw.get(key, [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            raise ConfigError("mute.%s must be a list of words." % key)
        for item in values:
            if not isinstance(item, str) or not item.strip():
                raise ConfigError(
                    "mute.%s has an entry that is not text: %r" % (key, item)
                )
        mute[key] = [item.strip() for item in values]
    return mute


def _validate_email(raw):
    email = dict(DEFAULTS["email"])
    if raw is None:
        return email
    if not isinstance(raw, dict):
        raise ConfigError("'email' must be a block of settings. See config.example.json.")
    email.update(raw)

    if not isinstance(email["enabled"], bool):
        raise ConfigError(
            "email.enabled must be true or false, but found: %r" % (email["enabled"],)
        )
    if not email["enabled"]:
        return email

    for key in ("smtp_host", "username", "from_address"):
        if not isinstance(email.get(key), str) or not email[key].strip():
            raise ConfigError(
                "Email is enabled, so email.%s must be filled in." % key
            )

    port = email.get("smtp_port")
    if isinstance(port, bool) or not isinstance(port, int):
        raise ConfigError("email.smtp_port must be a number, but found: %r" % (port,))

    recipients = email.get("to_addresses")
    if isinstance(recipients, str):
        recipients = [recipients]
    if not isinstance(recipients, list) or not recipients:
        raise ConfigError(
            "Email is enabled, so email.to_addresses needs at least one address."
        )
    email["to_addresses"] = [str(address).strip() for address in recipients]

    password_env = email.get("password_env") or "PAPERFEED_SMTP_PASSWORD"
    email["password_env"] = password_env
    # A missing password must NOT be a fatal config error. Scheduled runs do
    # not read your shell profile, so treating this as fatal meant a launchd
    # run aborted before writing anything - exactly the failure the local
    # digest file exists to protect you from. Skip the email, keep the file.
    email["password_available"] = bool(os.environ.get(password_env))
    return email


def settings_not_in_file(config_path):
    """Settings that exist but are absent from the user's file.

    A setting that lives only as a built-in default is invisible: there is
    nothing in the file to edit, and no reason to believe it exists. This
    lists them so they can be discovered without reading the source.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = _strip_comments(json.load(handle))
    except (OSError, json.JSONDecodeError):
        return []

    missing = []
    for key, default in sorted(DEFAULTS.items()):
        if not isinstance(default, dict):
            if key not in raw:
                missing.append("%s = %r" % (key, default))
            continue
        present = raw.get(key)
        if not isinstance(present, dict):
            missing.append("%s (whole block)" % key)
            continue
        absent = [name for name in sorted(default) if name not in present]
        if absent:
            missing.append(
                "%s: %s" % (key, ", ".join("%s = %r" % (n, default[n]) for n in absent))
            )
    return missing


def load(config_path):
    """Read config_path and return a validated settings dict.

    Relative output paths are resolved against the config file's own directory,
    so the tool behaves the same whether run by hand or by the scheduler.
    """
    config_path = os.path.abspath(config_path)
    if not os.path.exists(config_path):
        raise ConfigError(
            "No config file at %s\n"
            "Copy config.example.json to config.json and edit it." % config_path
        )

    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as error:
        raise ConfigError(
            "%s is not valid JSON: %s (line %d, column %d).\n"
            "Common causes: a trailing comma after the last item, a missing comma "
            "between items, or a quote that was never closed."
            % (config_path, error.msg, error.lineno, error.colno)
        )

    if not isinstance(raw, dict):
        raise ConfigError("The config file must contain a single JSON object { ... }.")

    raw = _strip_comments(raw)

    cfg = dict(DEFAULTS)
    cfg.update(raw)

    cfg["keyword_sets"] = _validate_keyword_sets(raw.get("keyword_sets"))
    cfg["interval_days"] = _positive_int(cfg, "interval_days")

    hours = cfg.get("interval_hours", 0)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)):
        raise ConfigError(
            "'interval_hours' must be a number (0 to use interval_days "
            "instead), but found: %r" % (hours,)
        )
    if hours < 0:
        raise ConfigError("'interval_hours' cannot be negative.")
    cfg["interval_hours"] = float(hours)
    cfg["lookback_days"] = _positive_int(cfg, "lookback_days")
    cfg["max_per_set"] = _positive_int(cfg, "max_per_set")

    sources = dict(DEFAULTS["sources"])
    if raw.get("sources") is not None:
        if not isinstance(raw["sources"], dict):
            raise ConfigError("'sources' must be a block like {\"pubmed\": true}.")
        sources.update(raw["sources"])
    for key, value in sources.items():
        if not isinstance(value, bool):
            raise ConfigError(
                "sources.%s must be true or false, but found: %r" % (key, value)
            )
    if not any(sources.values()):
        raise ConfigError("Both sources are switched off, so there is nothing to fetch.")
    cfg["sources"] = sources

    if cfg["lookback_days"] < cfg["interval_days"]:
        raise ConfigError(
            "lookback_days (%d) is smaller than interval_days (%d). Papers published "
            "in the gap would be missed — make lookback_days the larger number."
            % (cfg["lookback_days"], cfg["interval_days"])
        )

    cfg["ai"] = _validate_block(raw.get("ai"), DEFAULTS["ai"], "ai")
    if cfg["ai"]["provider"] not in ("anthropic", "openai", "gemini"):
        raise ConfigError(
            "ai.provider is %r. Use one of:\n"
            "  \"anthropic\"  the Claude API\n"
            "  \"gemini\"     Google Gemini\n"
            "  \"openai\"     OpenAI, and anything speaking its chat-completions\n"
            "               API - Groq, DeepSeek, Together, OpenRouter, Ollama.\n"
            "               Point ai.base_url at the service for those."
            % cfg["ai"]["provider"]
        )
    cfg["collect"] = _validate_block(raw.get("collect"), DEFAULTS["collect"], "collect")
    cfg["trends"] = _validate_block(raw.get("trends"), DEFAULTS["trends"], "trends")
    if cfg["ai"]["enabled"] and not (cfg["ai"].get("interests") or "").strip():
        raise ConfigError(
            "ai.enabled is true but ai.interests is empty.\n"
            "Describe what you care about in a sentence or two - that is what "
            "each paper gets scored against."
        )
    cfg["ranking"] = _validate_ranking(raw.get("ranking"))
    cfg["mute"] = _validate_mute(raw.get("mute"))
    cfg["email"] = _validate_email(raw.get("email"))

    base_dir = os.path.dirname(config_path)
    cfg["base_dir"] = base_dir
    cfg["config_path"] = config_path
    cfg["digest_dir"] = os.path.join(base_dir, "digests")
    cfg["state_path"] = os.path.join(base_dir, "state", "seen.json")
    cfg["log_path"] = os.path.join(base_dir, "paperfeed.log")
    cfg["library_path"] = os.path.join(base_dir, "library.db")
    return cfg
