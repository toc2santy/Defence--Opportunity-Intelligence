"""Pure unit tests for app/email_sender.py — no network (smtplib is faked)."""

import smtplib

import app.email_sender as email_sender


def _configured(monkeypatch):
    monkeypatch.setattr(email_sender, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(email_sender, "SMTP_USER", None)


def test_unconfigured_smtp_logs_and_returns_false(monkeypatch, capsys):
    monkeypatch.setattr(email_sender, "SMTP_HOST", None)
    assert email_sender.send_email("a@b.com", "subj", "body") is False
    assert "SMTP_HOST not configured" in capsys.readouterr().out


def test_a_send_failure_is_contained_not_raised(monkeypatch, capsys):
    """Regression: an unreachable/rejecting SMTP server used to raise out of send_email, turning an already-committed sign-up into a 500 and (on forgot-password) revealing which emails have accounts."""
    _configured(monkeypatch)

    def boom(*a, **k):
        raise OSError("name or service not known")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    assert email_sender.send_email("a@b.com", "subj", "body") is False
    assert "FAILED to send" in capsys.readouterr().out


def test_smtp_is_called_with_a_timeout(monkeypatch):
    _configured(monkeypatch)
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            seen["timeout"] = timeout
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def send_message(self, msg): seen["sent"] = True

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    assert email_sender.send_email("a@b.com", "subj", "body") is True
    assert seen["timeout"] and seen["sent"]
