"""
test_smtp.py — Run this file directly to test your Titan email connection.
Usage:  python test_smtp.py
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import os

load_dotenv()

# ── Fill these in directly here to test (remove after testing) ───────────────
SMTP_USER     = os.getenv("SMTP_USER",     "support@taxcookies.in")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")   # paste your password here if .env not working
TEST_SEND_TO  = "support@taxcookies.in"          # send test email to yourself

print(f"Testing SMTP login for: {SMTP_USER}")
print(f"Password loaded: {'YES ('+str(len(SMTP_PASSWORD))+' chars)' if SMTP_PASSWORD else 'NO — .env not loading'}")
print()

# ── Try Option 1: Port 587 with STARTTLS ─────────────────────────────────────
print("── Trying port 587 (STARTTLS)...")
try:
    with smtplib.SMTP("smtp.titan.email", 587, timeout=15) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(SMTP_USER, SMTP_PASSWORD)
        print("✅  Port 587 STARTTLS: LOGIN SUCCESS")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "TEJAS SMTP Test"
        msg["From"]    = SMTP_USER
        msg["To"]      = TEST_SEND_TO
        msg.attach(MIMEText("TEJAS SMTP test email — if you got this, port 587 works!", "plain"))
        s.sendmail(SMTP_USER, TEST_SEND_TO, msg.as_string())
        print("✅  Test email sent via port 587!")
        print("\n→ Use these settings in your .env:")
        print("   SMTP_HOST=smtp.titan.email")
        print("   SMTP_PORT=587")

except Exception as e:
    print(f"❌  Port 587 failed: {e}")

    # ── Try Option 2: Port 465 with SSL ──────────────────────────────────────
    print("\n── Trying port 465 (SSL)...")
    try:
        with smtplib.SMTP_SSL("smtp.titan.email", 465, timeout=15) as s:
            s.ehlo()
            s.login(SMTP_USER, SMTP_PASSWORD)
            print("✅  Port 465 SSL: LOGIN SUCCESS")

            msg = MIMEMultipart("alternative")
            msg["Subject"] = "TEJAS SMTP Test"
            msg["From"]    = SMTP_USER
            msg["To"]      = TEST_SEND_TO
            msg.attach(MIMEText("TEJAS SMTP test email — if you got this, port 465 works!", "plain"))
            s.sendmail(SMTP_USER, TEST_SEND_TO, msg.as_string())
            print("✅  Test email sent via port 465!")
            print("\n→ Update your .env:")
            print("   SMTP_HOST=smtp.titan.email")
            print("   SMTP_PORT=465")

    except Exception as e2:
        print(f"❌  Port 465 also failed: {e2}")
        print()
        print("═" * 55)
        print("DIAGNOSIS: Both ports failed. Most likely causes:")
        print("  1. Wrong password in .env file")
        print("     → Double-check SMTP_PASSWORD in your .env")
        print("     → It must be your Titan webmail login password")
        print("  2. Password has special chars — wrap in quotes in .env")
        print('     → SMTP_PASSWORD="your@pass#word!"')
        print("  3. Titan requires app password (not webmail password)")
        print("     → Login to mail.titan.email → Settings → Security")
        print("       and create an App Password")
        print("  4. .env file not being read")
        print("     → Run: python -c \"from dotenv import load_dotenv; load_dotenv(); import os; print(repr(os.getenv('SMTP_PASSWORD')))\"")
        print("═" * 55)