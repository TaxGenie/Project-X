"""
auth.py — Tax Cookies Authentication
Handles OTP generation, email delivery via Resend API, and JWT session tokens.

Email delivery uses Resend (https://resend.com) — no SMTP, high deliverability,
works with corporate Outlook/Exchange servers that block Gmail SMTP.

Setup:
  1. Sign up at resend.com (free — 3,000 emails/month)
  2. Add & verify your sending domain (e.g. taxcookies.in) in Resend dashboard
  3. Get your API key from Resend dashboard → API Keys
  4. Set in .env:
       RESEND_API_KEY=re_xxxxxxxxxxxx
       FROM_EMAIL=noreply@taxcookies.in    ← must be on your verified domain
       FROM_NAME=Tax Cookies
"""
import random
import string
import smtplib
import requests
import jwt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from database import save_otp, verify_otp, get_or_create_user, create_session, get_session, delete_session

import os
from dotenv import load_dotenv
load_dotenv()

# ── Resend config (primary) ───────────────────────────────────────────────────
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL     = os.getenv("FROM_EMAIL",     "noreply@taxcookies.in")
FROM_NAME      = os.getenv("FROM_NAME",      "Tax Cookies")

# ── Gmail SMTP config (fallback — only used if RESEND_API_KEY is not set) ─────
SMTP_HOST     = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",     "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# ── JWT ───────────────────────────────────────────────────────────────────────
JWT_SECRET    = os.getenv("JWT_SECRET", "change-this-to-a-long-random-string-in-production")
JWT_ALGORITHM = "HS256"


# ══════════════════════════════════════════════════════════════════════════════
# OTP
# ══════════════════════════════════════════════════════════════════════════════

def generate_otp(length: int = 6) -> str:
    """Generate a secure numeric OTP."""
    return ''.join(random.choices(string.digits, k=length))


def send_otp_email(email: str) -> tuple[bool, str]:
    """
    Generate OTP, save to DB, and send via Resend API (primary) or Gmail SMTP (fallback).
    Returns (success: bool, message: str)
    """
    otp = generate_otp()
    subject   = "Your Tax Cookies Login Code"
    html_body = _otp_email_html(otp, email)
    text_body = (
        f"Your Tax Cookies OTP is: {otp}\n\n"
        f"This code expires in 10 minutes.\n\n"
        f"If you did not request this, ignore this email."
    )

    try:
        if RESEND_API_KEY:
            print(f"[Tax Cookies Auth] Sending via Resend to {email}...")
            _send_via_resend(email, subject, html_body, text_body)
            print(f"[Tax Cookies Auth] Resend delivery confirmed for {email}")
        else:
            print("[Tax Cookies Auth] WARNING: RESEND_API_KEY not set in .env — falling back to Gmail SMTP (may not reach corporate inboxes)")
            _send_via_gmail(email, subject, html_body, text_body)
            print(f"[Tax Cookies Auth] Gmail SMTP sent to {email}")

        save_otp(email, otp)
        return True, "OTP sent successfully"

    except Exception as e:
        print(f"[Tax Cookies Auth] FAILED for {email}: {type(e).__name__}: {e}")
        return False, f"Failed to send OTP: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════════
# LOGIN FLOW
# ══════════════════════════════════════════════════════════════════════════════

def login_with_otp(email: str, otp: str) -> tuple[bool, str, dict | None]:
    """
    Verify OTP and return a JWT session token.
    Returns (success, message, {token, user})
    """
    email = email.strip().lower()

    if not verify_otp(email, otp):
        return False, "Invalid or expired OTP. Please request a new one.", None

    user  = get_or_create_user(email)
    token = _create_jwt(user["id"], email)
    create_session(user["id"], token)

    return True, "Login successful", {
        "token": token,
        "user": {
            "id":    user["id"],
            "email": user["email"],
        }
    }


def logout(token: str):
    delete_session(token)


def get_current_user_from_token(token: str) -> dict | None:
    """
    Validate JWT signature + expiry, then check session in DB.
    Returns user dict or None.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

    session = get_session(token)
    if not session:
        return None

    return {
        "user_id": session["user_id"],
        "email":   session["email"],
        "token":   token,
    }


# ══════════════════════════════════════════════════════════════════════════════
# JWT INTERNALS
# ══════════════════════════════════════════════════════════════════════════════

def _create_jwt(user_id: int, email: str) -> str:
    payload = {
        "sub":   str(user_id),
        "email": email,
        "iat":   datetime.now(timezone.utc),
        "exp":   datetime.now(timezone.utc) + timedelta(days=30),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ══════════════════════════════════════════════════════════════════════════════
# RESEND API  (primary sender)
# ══════════════════════════════════════════════════════════════════════════════

def _send_via_resend(to_email: str, subject: str, html_body: str, text_body: str):
    """Send email via Resend HTTP API. Raises on failure."""
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type":  "application/json",
        },
        json={
            "from":    f"{FROM_NAME} <{FROM_EMAIL}>",
            "to":      [to_email],
            "subject": subject,
            "html":    html_body,
            "text":    text_body,
        },
        timeout=10,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Resend API error {response.status_code}: {response.text}"
        )
    print(f"[Tax Cookies Auth] Resend accepted: {response.json().get('id','—')}")


# ══════════════════════════════════════════════════════════════════════════════
# GMAIL SMTP  (fallback — may not reach corporate inboxes)
# ══════════════════════════════════════════════════════════════════════════════

def _send_via_gmail(to_email: str, subject: str, html_body: str, text_body: str):
    """Send via Gmail SMTP (STARTTLS port 587). Requires App Password in .env."""
    msg = MIMEMultipart("alternative")
    msg["Subject"]  = subject
    msg["From"]     = f"{FROM_NAME} <{SMTP_USER}>"
    msg["To"]       = to_email
    msg["Reply-To"] = SMTP_USER

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html",  "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, to_email, msg.as_string())


# ══════════════════════════════════════════════════════════════════════════════
# EMAIL TEMPLATE
# ══════════════════════════════════════════════════════════════════════════════

def _otp_email_html(otp: str, email: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#0F1117;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0F1117;padding:40px 0;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#1A1D2E;border-radius:12px;overflow:hidden;
                    border:1px solid #2D3154;">
        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#1A237E,#283593);
                     padding:32px 40px;text-align:center;">
            <div style="font-size:32px;margin-bottom:8px;">⚖</div>
            <div style="font-size:26px;font-weight:700;color:#C9A84C;
                        font-family:Georgia,serif;letter-spacing:2px;">Tax Cookies</div>
            <div style="font-size:12px;color:#90A4AE;margin-top:4px;
                        letter-spacing:1px;">BY TAXCOOKIES.IN</div>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:40px;">
            <p style="color:#B0BEC5;font-size:15px;margin:0 0 24px;">
              Hello,<br><br>
              Here is your one-time login code for Tax Cookies.
            </p>
            <!-- OTP Box -->
            <div style="background:#0F1117;border:2px solid #1A237E;
                        border-radius:10px;padding:28px;text-align:center;
                        margin:0 0 28px;">
              <div style="font-size:42px;font-weight:700;letter-spacing:16px;
                          color:#C9A84C;font-family:'Courier New',monospace;">
                {otp}
              </div>
              <div style="color:#546E7A;font-size:13px;margin-top:12px;">
                Expires in <strong style="color:#90A4AE;">10 minutes</strong>
              </div>
            </div>
            <p style="color:#546E7A;font-size:13px;margin:0 0 8px;">
              This code was requested for: <strong style="color:#78909C;">{email}</strong>
            </p>
            <p style="color:#546E7A;font-size:13px;margin:0;">
              If you did not request this, you can safely ignore this email.
            </p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="border-top:1px solid #2D3154;padding:20px 40px;
                     text-align:center;">
            <p style="color:#37474F;font-size:12px;margin:0;">
              Tax Cookies · Where Every Tax Query Finds an Answer<br>
              <a href="https://taxcookies.in"
                 style="color:#1565C0;text-decoration:none;">taxcookies.in</a>
              &nbsp;·&nbsp;
              <a href="mailto:support@taxcookies.in"
                 style="color:#1565C0;text-decoration:none;">support@taxcookies.in</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
