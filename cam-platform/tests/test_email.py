"""Email notifications: the SMTP mailer (log vs send, STARTTLS vs SSL, secret
handling) and the run-completion email dispatch from the worker."""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

import cam.services.orchestration.main as orch
from cam.common import mail
from cam.common.config import Settings
from cam.services.orchestration import worker
from tests.test_orchestration import _create_run, wired  # noqa: F401


# ---------- the mailer -------------------------------------------------------

def _settings(**over) -> Settings:
    base = dict(service_name="test", smtp_from="CAM <cam@bank.example>",
                smtp_timeout_seconds=5)
    base.update(over)
    return Settings(**base)


def test_no_smtp_host_logs_instead_of_sending():
    assert mail.send_email(_settings(smtp_host=""), "a@b.com", "Hi", "body") == "logged"


def test_no_recipient_is_skipped():
    assert mail.send_email(_settings(smtp_host="smtp.x"), "", "Hi", "body") == "skipped_no_recipient"


class _FakeSMTP:
    last: "_FakeSMTP | None" = None

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged = None
        self.sent = None
        _FakeSMTP.last = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.logged = (user, password)

    def send_message(self, msg):
        self.sent = msg


def test_starttls_send_logs_in_and_never_stores_the_password(monkeypatch):
    monkeypatch.setattr(mail.smtplib, "SMTP", _FakeSMTP)
    os.environ["CAM_TEST_SMTP_PW"] = "s3cret"
    s = _settings(smtp_host="smtp.bank", smtp_port=587, smtp_username="mailer",
                  smtp_password_env="CAM_TEST_SMTP_PW", smtp_starttls=True)

    assert mail.send_email(s, "analyst@bank.example", "Subj", "text", "<b>html</b>") == "sent"
    sent = _FakeSMTP.last
    assert sent.started_tls is True
    assert sent.logged == ("mailer", "s3cret")
    assert sent.sent["To"] == "analyst@bank.example"
    assert sent.sent["Subject"] == "Subj"
    # NFR-06: the password is only ever read from the env var, never on Settings
    assert not hasattr(s, "smtp_password")


def test_ssl_transport_used_when_configured(monkeypatch):
    captured = {}

    class _FakeSSL(_FakeSMTP):
        def __init__(self, host, port, timeout=None, context=None):
            super().__init__(host, port, timeout, context)
            captured["ssl"] = True

    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", _FakeSSL)
    s = _settings(smtp_host="smtp.bank", smtp_port=465, smtp_ssl=True)
    assert mail.send_email(s, "x@bank.example", "S", "t") == "sent"
    assert captured.get("ssl") is True


# ---------- run-completion dispatch -----------------------------------------

def test_completion_emails_the_creator_when_enabled(wired, analyst_headers, monkeypatch):
    monkeypatch.setattr(worker, "_email_enabled", lambda: True)
    monkeypatch.setattr(worker.resolver, "fetch_user_contact",
                        lambda u: {"email": "kunal@bank.example", "display_name": "Kunal"})
    sent: list[dict] = []
    monkeypatch.setattr(worker.mail, "send_email",
                        lambda settings, to, subject, text, html=None:
                        sent.append({"to": to, "subject": subject, "text": text, "html": html})
                        or "sent")
    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
    assert len(sent) == 1
    msg = sent[0]
    assert msg["to"] == "kunal@bank.example"
    assert "CAM ready" in msg["subject"]
    assert f"/runs/{run['id']}" in msg["text"] and f"/runs/{run['id']}" in msg["html"]


def test_no_email_when_disabled(wired, analyst_headers, monkeypatch):
    monkeypatch.setattr(worker, "_email_enabled", lambda: False)
    sent: list = []
    monkeypatch.setattr(worker.mail, "send_email",
                        lambda *a, **k: sent.append(1) or "sent")
    with TestClient(orch.app) as c:
        _create_run(c, analyst_headers)
        worker.drain()
    assert sent == []


def test_no_email_when_creator_has_no_address(wired, analyst_headers, monkeypatch):
    monkeypatch.setattr(worker, "_email_enabled", lambda: True)
    monkeypatch.setattr(worker.resolver, "fetch_user_contact", lambda u: {"email": ""})
    sent: list = []
    monkeypatch.setattr(worker.mail, "send_email",
                        lambda *a, **k: sent.append(1) or "sent")
    with TestClient(orch.app) as c:
        _create_run(c, analyst_headers)
        worker.drain()
    assert sent == []
