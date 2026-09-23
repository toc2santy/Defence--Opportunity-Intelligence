"""
Outbound email — currently just the password-reset link, written so
any future transactional email (a notification, an invite) can reuse
send_email() rather than growing its own SMTP handling.

No email provider account exists for this project. Real delivery
needs real SMTP credentials in .env (SMTP_HOST/PORT/USER/PASSWORD/
FROM) — provisioning those (a Gmail app password, a SendGrid/Postmark
account, whatever the operator already has) is a business decision
outside what this code can do on its own, the same "I can build the
mechanism, you provide the account" split as every other external
integration in this project (SAM_GOV_API_KEY, WhatsApp Business API).

DEV FALLBACK: when SMTP_HOST is unset, send_email() does not fail or
silently drop the message — it logs the full email (recipient,
subject, body — so the reset link itself is visible) to stdout. This
is what makes the reset flow testable end-to-end before any real SMTP
account is configured, and is deliberately loud (not a quiet no-op)
so nobody mistakes dev-mode logging for a real email having gone out.
"""

import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM = os.environ.get("SMTP_FROM", "no-reply@defence-oi.local")


def send_email(to_email: str, subject: str, body: str) -> bool:
    """
    Returns True if actually handed to an SMTP server, False if it
    fell back to console logging (no SMTP configured). Callers should
    treat both as "the request was processed" — the caller-facing API
    response must not reveal which happened, since that would leak
    whether a given email address has an account (see the forgot-
    password route's constant-shape response).
    """
    if not SMTP_HOST:
        print(
            f"\n[email_sender] SMTP_HOST not configured — logging instead of sending.\n"
            f"  To: {to_email}\n  Subject: {subject}\n  Body:\n{body}\n"
        )
        return False

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    return True
