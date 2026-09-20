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


def send(settings, subject, html_body, text_body):
    password = os.environ.get(settings.get("password_env", ""), "")
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
        raise MailError(
            "The mail server rejected the login for %s. If this is Gmail you need an "
            "app password rather than your normal password. (%s)"
            % (settings["username"], error)
        )
    except Exception as error:
        raise MailError("Could not send mail via %s:%s - %s" % (host, port, error))
