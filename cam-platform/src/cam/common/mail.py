"""Minimal SMTP mailer shared by services that send notification email.

Design goals:
  * Zero-config dev: when ``smtp_host`` is unset the message is LOGGED rather than
    sent, so the feature is fully exercisable (and testable) without a mail server.
  * Secrets stay out of the Settings object (NFR-06): the SMTP password is read
    from the env var *named* by ``smtp_password_env`` at send time, never stored
    on Settings and never logged.
  * Both transports: STARTTLS on a plain connection (port 587, the default) and
    implicit TLS / SMTPS (port 465, ``smtp_ssl=True``).

Callers treat delivery as best-effort — a raised exception should never break the
business flow that triggered the email.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate

from .config import Settings

log = logging.getLogger("cam.mail")


def send_email(settings: Settings, to: str, subject: str, text: str,
               html: str | None = None) -> str:
    """Send (or log) a single email. Returns a short status string:
    ``sent`` | ``logged`` (no SMTP host configured) | ``skipped_no_recipient``.
    Raises on genuine SMTP failures so callers can log/count them."""
    if not to:
        return "skipped_no_recipient"

    if not settings.smtp_host:
        # dev / unconfigured: make the intent visible without sending anything
        log.info("EMAIL (not sent — no SMTP host) to=%s subject=%r", to, subject)
        return "logged"

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    timeout = settings.smtp_timeout_seconds
    if settings.smtp_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                              timeout=timeout, context=context) as server:
            _authenticate_and_send(server, settings, msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout) as server:
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            _authenticate_and_send(server, settings, msg)

    log.info("email sent to=%s subject=%r", to, subject)
    return "sent"


def _authenticate_and_send(server: smtplib.SMTP, settings: Settings, msg: EmailMessage) -> None:
    if settings.smtp_username:
        password = os.environ.get(settings.smtp_password_env, "")
        server.login(settings.smtp_username, password)
    server.send_message(msg)
