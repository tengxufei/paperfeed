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

        terms = entry.get("terms")
        if not isinstance(terms, list) or not terms:
            raise ConfigError(
                "Keyword set %r needs a 'terms' list with at least one search term." % name
            )
        clean_terms = []
        for term in terms:
            if not isinstance(term, str) or not term.strip():
                raise ConfigError(
                    "Keyword set %r has a term that is not text: %r" % (name, term)
                )
            clean_terms.append(term.strip())

        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(
                "Keyword set %r has 'enabled': %r — it must be true or false."
                % (name, enabled)
            )

        cleaned.append({"name": name, "terms": clean_terms, "enabled": enabled})

    if not any(entry["enabled"] for entry in cleaned):
        raise ConfigError(
            "Every keyword set has \"enabled\": false, so there is nothing to search."
        )
    return cleaned


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
    if not os.environ.get(password_env):
        raise ConfigError(
            "Email is enabled but the environment variable %s is not set.\n"
            "PaperFeed never stores your password in a file. Set it with:\n"
            "    export %s='your-app-password'" % (password_env, password_env)
        )
    return email


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

    cfg["email"] = _validate_email(raw.get("email"))

    base_dir = os.path.dirname(config_path)
    cfg["base_dir"] = base_dir
    cfg["config_path"] = config_path
    cfg["digest_dir"] = os.path.join(base_dir, "digests")
    cfg["state_path"] = os.path.join(base_dir, "state", "seen.json")
    cfg["log_path"] = os.path.join(base_dir, "paperfeed.log")
    return cfg
