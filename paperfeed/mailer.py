"""Send the digest by email.

Deliberately thin. The password is read from an environment variable, never
from the config file. Failures raise MailError, which the caller logs and
shrugs off -- the digest file has already been written by then.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage


class MailError(Exception):
    """Sending failed. The local digest file is unaffected."""


def describe_password(raw):
    """What we can say about a password without ever revealing it."""
    if raw is None:
        raw = ""
    stripped = "".join(raw.split())
    return {
        "set": bool(raw),
        "length": len(stripped),
        "had_spaces": raw != stripped,
        "looks_like_app_password": len(stripped) == 16 and stripped.isalnum(),
    }


def send(settings, subject, html_body, text_body):
    raw_password = os.environ.get(settings.get("password_env", ""), "")
    # Google shows app passwords as four groups of four ("abcd efgh ijkl
    # mnop"). Pasted verbatim that fails authentication, which is the single
    # most common cause of a rejected Gmail login. Strip the spaces.
    password = "".join(raw_password.split())
    if not password:
        raise MailError(
            "Environment variable %s is empty, so there is no password to log in with."
            % settings.get("password_env")
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings["from_address"]
    message["To"] = ", ".join(settings["to_addresses"])
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    host = settings["smtp_host"]
    port = int(settings["smtp_port"])
    context = ssl.create_default_context()

    try:
        if port == 465:
            # Implicit TLS: the connection is encrypted from the first byte.
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
                server.login(settings["username"], password)
                server.send_message(message)
        else:
            # Ports 587/25: connect in the clear, then upgrade with STARTTLS.
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(settings["username"], password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        shape = describe_password(raw_password)
        hints = []
        if shape["had_spaces"]:
            hints.append(
                "the value had spaces in it (now stripped automatically, so "
                "that was not the cause this time)"
            )
        if shape["length"] != 16:
            hints.append(
                "it is %d characters long - a Google app password is exactly "
                "16 letters, so this looks like your normal account password, "
                "which Gmail always rejects for SMTP"
                % shape["length"]
            )
        elif not shape["looks_like_app_password"]:
            hints.append(
                "it is 16 characters but contains punctuation - app passwords "
                "are letters only"
            )
        else:
            hints.append(
                "it is shaped like a valid app password, so the likely causes "
                "are that it was generated for a different Google account "
                "than %s, or that it has been revoked" % settings["username"]
            )
        raise MailError(
            "The mail server rejected the login for %s.\n"
            "About the password in $%s: %s.\n"
            "Gmail also requires 2-Step Verification to be ON before app "
            "passwords exist at all: "
            "https://myaccount.google.com/apppasswords\n"
            "(server said: %s)"
            % (
                settings["username"],
                settings.get("password_env", "?"),
                "; ".join(hints),
                str(error)[:120],
            )
        )
    except Exception as error:
        raise MailError("Could not send mail via %s:%s - %s" % (host, port, error))
