"""
Real SMTP email sending for citizen account verification.

This sends ACTUAL emails via SMTP - it is not a stub, mock, or a
console-print pretending to be an email. It requires real SMTP
credentials to be set as environment variables (see .env.example
additions below) or it will fail loudly and clearly rather than
silently pretending to have sent something.

Setup (pick ONE real SMTP provider):

  Option A - Gmail (easiest for a class project / demo):
    1. Turn on 2-Step Verification on the Google account you'll send from.
    2. Create an "App Password": Google Account -> Security -> 2-Step
       Verification -> App passwords. Generate one for "Mail".
    3. Set:
         SMTP_HOST=smtp.gmail.com
         SMTP_PORT=587
         SMTP_USER=youraddress@gmail.com
         SMTP_PASSWORD=<the 16-character app password, NOT your real Gmail password>
         FROM_EMAIL=youraddress@gmail.com

  Option B - a free transactional email service (Brevo/Sendinblue,
  Mailtrap, Resend, etc.) - generally more reliable for anything beyond
  a handful of test emails, since Gmail rate-limits and sometimes flags
  automated SMTP sends. Each gives you an SMTP host/port/user/password to
  put in the same four env vars above.

If SMTP_HOST etc. are not set, send_verification_email() raises a clear
RuntimeError instead of silently no-op'ing - so a misconfigured deployment
fails at signup time with an honest error, not a citizen waiting forever
for an email that was never actually attempted.
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)

# The frontend origin the verification link should point back to. Set this
# to wherever the unified app is actually served (see the merged
# frontend1/app/ build) - defaults to the same convention used everywhere
# else in this project (localhost:5173).
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5173")


def is_email_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def send_verification_email(to_email: str, token: str) -> None:
    """
    Sends a real verification email with a link back to the frontend,
    which will call GET /api/auth/verify-email?token=... on load.

    Raises RuntimeError if SMTP isn't configured, or the underlying
    smtplib exception if sending fails (bad credentials, network issue,
    etc.) - callers should catch and translate this into a clear HTTP
    error, not swallow it.
    """
    if not is_email_configured():
        raise RuntimeError(
            "Email is not configured. Set SMTP_HOST, SMTP_USER, and "
            "SMTP_PASSWORD as real environment variables before allowing "
            "signups - see the setup instructions at the top of this file."
        )

    verify_link = f"{APP_BASE_URL}/?verify_token={token}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Verify your CivicResolve account"
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email

    text_body = (
        f"Welcome to CivicResolve!\n\n"
        f"Please verify your email by opening this link:\n{verify_link}\n\n"
        f"This link expires in 24 hours. If you didn't sign up for "
        f"CivicResolve, you can ignore this email."
    )
    html_body = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color:#2563eb;">Welcome to CivicResolve</h2>
      <p>Please verify your email address to activate your account.</p>
      <p style="margin: 24px 0;">
        <a href="{verify_link}"
           style="background:#2563eb;color:white;padding:12px 24px;
                  border-radius:8px;text-decoration:none;font-weight:600;">
          Verify Email
        </a>
      </p>
      <p style="color:#64748b;font-size:13px;">
        This link expires in 24 hours. If you didn't sign up for
        CivicResolve, you can safely ignore this email.
      </p>
    </div>
    """

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(FROM_EMAIL, to_email, msg.as_string())
