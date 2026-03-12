"""
alert_scheduler.py — Smart Case Alert Digest Runner
════════════════════════════════════════════════════

Call run_alert_digest(frequency) from a cron endpoint or APScheduler.

Mount the scheduler in main.py:

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from alert_scheduler import run_alert_digest

    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(lambda: asyncio.create_task(run_alert_digest("instant")), "interval", hours=1)
    scheduler.add_job(lambda: asyncio.create_task(run_alert_digest("daily")),   "cron",     hour=7, minute=0)
    scheduler.add_job(lambda: asyncio.create_task(run_alert_digest("weekly")),  "cron",     day_of_week="mon", hour=7, minute=30)
    # All-judgments digest broadcast (TaxSutra-style)
    scheduler.add_job(lambda: asyncio.create_task(run_digest_broadcast("daily")),   "cron",     hour=8,  minute=0)
    scheduler.add_job(lambda: asyncio.create_task(run_digest_broadcast("weekly")),  "cron",     day_of_week="mon", hour=8, minute=0)
    scheduler.start()

Or trigger manually via the admin endpoint in main.py (see bottom of this file).

Dependencies:
    pip install apscheduler httpx
"""

import os
import re
import json
import asyncio
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from database import (
    get_due_alerts, mark_alert_sent,
    get_due_digest_subscriptions, mark_digest_sent,
)

logger = logging.getLogger("alert_scheduler")

# ── Config (from env vars, same as rest of app) ───────────────────────────────
IK_BASE    = "https://api.indiankanoon.org"
IK_TIMEOUT = 15.0

def _ik_token():   return os.getenv("INDIANKANOON_API_TOKEN", "")
def _ik_headers(): return {"Authorization": f"Token {_ik_token()}", "Accept": "application/json"}

# Gmail SMTP
SMTP_HOST    = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER    = os.getenv("SMTP_USER",     "")
SMTP_PASS    = os.getenv("SMTP_PASSWORD", "")   # matches SMTP_PASSWORD in .env
FROM_EMAIL   = os.getenv("FROM_EMAIL",    "support@taxcookies.in")
FROM_NAME    = os.getenv("FROM_NAME",     "Tax Cookies")
APP_URL      = os.getenv("APP_URL",       "https://taxcookies.in")

# Court filter → IK doctype string (mirrors case_law_routes.py)
COURT_DOCTYPES = {
    "all"         : "",
    "itat"        : " doctypes:itat",
    "itat-del"    : " doctypes:itat",   # IK doesn't have bench-level filters; filtered post-search
    "itat-mum"    : " doctypes:itat",
    "itat-bang"   : " doctypes:itat",
    "supremecourt": " doctypes:supremecourt",
    "bombay"      : " doctypes:bombay",
    "delhi"       : " doctypes:delhi",
    "madras"      : " doctypes:chennai",
    "calcutta"    : " doctypes:kolkata",
    "gujarat"     : " doctypes:gujarat",
    "karnataka"   : " doctypes:karnataka",
    "allahabad"   : " doctypes:allahabad",
    "kerala"      : " doctypes:kerala",
}

COURT_LABELS = {
    "all"         : "All Courts",
    "itat"        : "ITAT (All Benches)",
    "itat-del"    : "ITAT Delhi",
    "itat-mum"    : "ITAT Mumbai",
    "itat-bang"   : "ITAT Bangalore",
    "supremecourt": "Supreme Court",
    "bombay"      : "Bombay High Court",
    "delhi"       : "Delhi High Court",
    "madras"      : "Madras High Court",
    "calcutta"    : "Calcutta High Court",
    "gujarat"     : "Gujarat High Court",
    "karnataka"   : "Karnataka High Court",
    "allahabad"   : "Allahabad High Court",
    "kerala"      : "Kerala High Court",
}

FREQ_LABELS = {
    "instant": "Instant",
    "daily":   "Daily Digest",
    "weekly":  "Weekly Digest",
}


# ── IK search ─────────────────────────────────────────────────────────────────

async def _search_ik(query: str, court: str, max_results: int = 10) -> list:
    """
    Search IndianKanoon for the given query + court filter.
    Returns list of dicts: {tid, title, date, court, url, headline}
    """
    if not _ik_token():
        logger.warning("INDIANKANOON_API_TOKEN not set — skipping IK search")
        return []

    has_section = bool(re.search(r'section\s*\d+|s\.\s*\d+|\d{2,3}[A-Z]?\b', query, re.IGNORECASE))
    base = query if ("income tax" in query.lower() or has_section) else f"{query} income tax"
    ik_query = base + COURT_DOCTYPES.get(court, "")

    payload = {"formInput": ik_query}
    results = []

    try:
        async with httpx.AsyncClient(timeout=IK_TIMEOUT, follow_redirects=True) as client:
            pages_needed = -(-max_results // 10)
            tasks = [
                client.post(f"{IK_BASE}/search/",
                            data={**payload, "pagenum": str(i)},
                            headers=_ik_headers())
                for i in range(pages_needed)
            ]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

        for resp in responses:
            if isinstance(resp, Exception):
                continue
            if resp.status_code != 200:
                continue
            data = resp.json()
            for doc in data.get("docs", []):
                tid = doc.get("tid")
                if not tid:
                    continue
                results.append({
                    "tid"     : str(tid),
                    "title"   : _strip_html(doc.get("title", "Untitled")),
                    "date"    : doc.get("publishdate", ""),
                    "court"   : _strip_html(doc.get("docsource", "")),
                    "url"     : f"https://indiankanoon.org/doc/{tid}/",
                    "headline": _strip_html(doc.get("headline", ""))[:200],
                })
    except Exception as exc:
        logger.error(f"IK search failed for '{query}': {exc}")

    return results[:max_results]


def _strip_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text or "")
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', text).strip()


# ── Email sending ─────────────────────────────────────────────────────────────

def _send_digest_email(to_email: str, alerts_with_results: list, frequency: str):
    """
    Send a digest email listing all new cases for each alert.

    alerts_with_results: list of {alert: dict, new_cases: list of case dicts}
    """
    if not SMTP_USER or not SMTP_PASS:
        logger.warning("SMTP credentials not set — email not sent")
        return False

    freq_label = FREQ_LABELS.get(frequency, frequency.title())
    ist = timezone(timedelta(hours=5, minutes=30))
    date_str = datetime.now(ist).strftime("%d %B %Y")

    # Build HTML email
    cases_html = ""
    total_cases = 0
    for item in alerts_with_results:
        alert = item["alert"]
        cases = item["new_cases"]
        if not cases:
            continue
        total_cases += len(cases)
        court_label = COURT_LABELS.get(alert["court"], alert["court"])
        cases_html += f"""
        <div style="margin-bottom:24px">
          <div style="font-size:13px;font-weight:700;color:#B8860B;margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid #e5e7eb">
            🔔 {alert['section_query']} &nbsp;·&nbsp; <span style="font-weight:400;color:#6b7280">{court_label}</span>
          </div>
          <div style="display:flex;flex-direction:column;gap:8px">
        """
        for case in cases:
            cases_html += f"""
            <div style="padding:10px 14px;background:#f9fafb;border-radius:8px;border-left:3px solid #B8860B">
              <a href="{APP_URL}/case-law?tid={case['tid']}" style="font-size:13px;font-weight:600;color:#1e3a5f;text-decoration:none">
                {case['title']}
              </a>
              <div style="font-size:11px;color:#6b7280;margin-top:3px">
                {case['court']} &nbsp;·&nbsp; {case['date']}
              </div>
              {f'<div style="font-size:12px;color:#374151;margin-top:5px">{case["headline"]}</div>' if case.get("headline") else ''}
            </div>
            """
        cases_html += "</div></div>"

    if not cases_html:
        logger.info(f"No new cases for {to_email} — skipping email")
        return False

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:600px;margin:32px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)">
    
    <!-- Header -->
    <div style="background:#0f1929;padding:24px 28px;display:flex;align-items:center;gap:12px">
      <span style="font-size:22px">🍪</span>
      <div>
        <div style="font-size:17px;font-weight:700;color:#fff">Tax Cookies</div>
        <div style="font-size:12px;color:#9ca3af">{freq_label} — {date_str}</div>
      </div>
    </div>

    <!-- Body -->
    <div style="padding:24px 28px">
      <p style="font-size:14px;color:#374151;margin:0 0 20px">
        Here are <strong>{total_cases} new judgment{'s' if total_cases != 1 else ''}</strong> matching your case alerts:
      </p>
      {cases_html}
    </div>

    <!-- Footer -->
    <div style="padding:16px 28px;background:#f9fafb;border-top:1px solid #e5e7eb;text-align:center">
      <a href="{APP_URL}" style="font-size:12px;color:#B8860B;font-weight:600;text-decoration:none">Open Tax Cookies →</a>
      <div style="font-size:11px;color:#9ca3af;margin-top:6px">
        You're receiving this because you set up Smart Case Alerts.
        <a href="{APP_URL}" style="color:#9ca3af">Manage alerts</a>
      </div>
    </div>
  </div>
</body>
</html>"""

    subject = f"🍪 Tax Cookies {freq_label}: {total_cases} new case{'s' if total_cases != 1 else ''} — {date_str}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        logger.info(f"Digest sent to {to_email} ({total_cases} cases)")
        return True
    except Exception as exc:
        logger.error(f"Failed to send digest to {to_email}: {exc}")
        return False


# ── Main scheduler entry point ────────────────────────────────────────────────

# ── Broadcast queries — one per court, used for all-judgments digest ──────────
# We query IK with a broad "income tax" + doctype search to get the latest rulings
DIGEST_COURT_QUERIES: dict[str, str] = {
    "all"         : "income tax",
    "itat"        : "income tax doctypes:itat",
    "itat-del"    : "income tax doctypes:itat Delhi",
    "itat-mum"    : "income tax doctypes:itat Mumbai",
    "itat-bang"   : "income tax doctypes:itat Bangalore",
    "supremecourt": "income tax doctypes:supremecourt",
    "bombay"      : "income tax doctypes:bombay",
    "delhi"       : "income tax doctypes:delhi",
    "madras"      : "income tax doctypes:chennai",
    "calcutta"    : "income tax doctypes:kolkata",
    "gujarat"     : "income tax doctypes:gujarat",
    "karnataka"   : "income tax doctypes:karnataka",
    "allahabad"   : "income tax doctypes:allahabad",
    "kerala"      : "income tax doctypes:kerala",
}


async def _fetch_latest_judgments(courts: list, max_per_court: int = 10) -> list:
    """
    Fetch the latest income tax judgments from IK for the given list of courts.
    Returns deduplicated list of case dicts, sorted newest first.
    """
    if not _ik_token():
        logger.warning("INDIANKANOON_API_TOKEN not set — skipping digest fetch")
        return []

    # Deduplicate: if "all" is in the list, just do one broad query
    if "all" in courts:
        courts_to_fetch = ["all"]
    else:
        courts_to_fetch = list(dict.fromkeys(courts))   # preserve order, dedupe

    tasks = []
    labels = []
    for court in courts_to_fetch:
        query = DIGEST_COURT_QUERIES.get(court, f"income tax {court}")
        tasks.append(_search_ik(query, court if court != "all" else "all", max_results=max_per_court))
        labels.append(court)

    results_per_court = await asyncio.gather(*tasks, return_exceptions=True)

    seen_tids: set = set()
    merged: list   = []
    for court, results in zip(labels, results_per_court):
        if isinstance(results, Exception):
            logger.error(f"Digest IK fetch failed for court {court}: {results}")
            continue
        for case in results:
            if case["tid"] not in seen_tids:
                seen_tids.add(case["tid"])
                merged.append(case)

    return merged


def _build_digest_email_html(
    to_email: str,
    cases: list,
    courts: list,
    frequency: str,
    date_str: str,
) -> str:
    """Build the full HTML for an all-judgments digest email (TaxSutra-style)."""
    freq_label = FREQ_LABELS.get(frequency, frequency.title())
    court_label = "All Income Tax Courts" if "all" in courts else ", ".join(
        COURT_LABELS.get(c, c) for c in courts
    )

    cases_html = ""
    for i, case in enumerate(cases):
        cases_html += f"""
        <tr>
          <td style="padding:10px 0;border-bottom:1px solid #f0f0f0;vertical-align:top">
            <a href="{APP_URL}/?q={case['tid']}" style="font-size:13px;font-weight:600;color:#1e3a5f;text-decoration:none;line-height:1.5">
              {case['title']}
            </a>
            <div style="margin-top:3px;font-size:11px;color:#6b7280">
              {case['court']}
              {f"&nbsp;·&nbsp; {case['date']}" if case.get('date') else ""}
            </div>
            {f'<div style="margin-top:5px;font-size:12px;color:#374151;line-height:1.5">{case["headline"]}</div>' if case.get('headline') else ''}
            <div style="margin-top:6px">
              <a href="https://indiankanoon.org/doc/{case['tid']}/" style="font-size:11px;color:#B8860B;text-decoration:none;font-weight:600">
                Read on IndianKanoon →
              </a>
              &nbsp;&nbsp;
              <a href="{APP_URL}" style="font-size:11px;color:#6b7280;text-decoration:none">
                Open in Tax Cookies →
              </a>
            </div>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:640px;margin:32px auto;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.08)">

    <!-- Header -->
    <div style="background:#0f1929;padding:24px 32px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <span style="font-size:24px">🍪</span>
        <div>
          <div style="font-size:18px;font-weight:700;color:#ffffff">Tax Cookies</div>
          <div style="font-size:12px;color:#9ca3af">{freq_label} Judgment Digest — {date_str}</div>
        </div>
      </div>
      <div style="background:rgba(184,134,11,0.15);border:1px solid rgba(184,134,11,0.3);border-radius:8px;padding:10px 14px;margin-top:4px">
        <div style="font-size:12px;color:#D4A843;font-weight:600">
          📚 {len(cases)} new judgment{'s' if len(cases) != 1 else ''} &nbsp;·&nbsp; {court_label}
        </div>
      </div>
    </div>

    <!-- Cases list -->
    <div style="padding:24px 32px">
      <table style="width:100%;border-collapse:collapse">
        <tbody>
          {cases_html}
        </tbody>
      </table>
    </div>

    <!-- Footer -->
    <div style="padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb">
      <table style="width:100%">
        <tr>
          <td style="font-size:12px;color:#6b7280">
            <a href="{APP_URL}" style="color:#B8860B;font-weight:600;text-decoration:none">Open Tax Cookies</a>
            &nbsp;·&nbsp;
            <a href="{APP_URL}" style="color:#9ca3af;text-decoration:none">Manage subscription</a>
          </td>
          <td style="text-align:right;font-size:11px;color:#9ca3af">
            You subscribed to the {freq_label} digest
          </td>
        </tr>
      </table>
    </div>
  </div>
</body>
</html>"""


async def run_digest_broadcast(frequency: str) -> dict:
    """
    TaxSutra-style broadcast: send ALL latest income tax judgments to every subscriber.
    Called by the scheduler on the same cron as run_alert_digest but separately.
    """
    logger.info(f"[Digest] Running {frequency} broadcast...")

    due_subs = get_due_digest_subscriptions(frequency)
    if not due_subs:
        logger.info(f"[Digest] No due {frequency} subscriptions")
        return {"frequency": frequency, "subs_checked": 0, "emails_sent": 0}

    logger.info(f"[Digest] {len(due_subs)} subscribers due")

    # Gather all unique court sets across subscribers to batch IK calls
    # Group subscribers by their courts tuple to avoid redundant IK queries
    court_key_to_cases: dict[str, list] = {}

    async def _fetch_for_key(court_key: str, courts: list):
        if court_key not in court_key_to_cases:
            cases = await _fetch_latest_judgments(courts, max_per_court=10)
            court_key_to_cases[court_key] = cases
        return court_key_to_cases[court_key]

    emails_sent   = 0
    total_new     = 0
    ist = timezone(timedelta(hours=5, minutes=30))
    date_str = datetime.now(ist).strftime("%d %B %Y")

    for sub in due_subs:
        courts    = sub["courts"]
        court_key = json.dumps(sorted(courts))

        # Fetch (or reuse cached) cases for this court set
        all_cases = await _fetch_for_key(court_key, courts)

        # Filter out already-sent tids
        already_sent = set(sub.get("last_sent_tids") or [])
        new_cases    = [c for c in all_cases if c["tid"] not in already_sent]

        if not new_cases:
            logger.info(f"[Digest] No new cases for user {sub['user_id']} — skipping")
            mark_digest_sent(sub["id"], [])
            continue

        # Build and send email
        html    = _build_digest_email_html(sub["email"], new_cases, courts, frequency, date_str)
        freq_label = FREQ_LABELS.get(frequency, frequency.title())
        subject = f"🍪 Tax Cookies {freq_label} Digest: {len(new_cases)} new judgment{'s' if len(new_cases) != 1 else ''} — {date_str}"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"]      = sub["email"]
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(FROM_EMAIL, sub["email"], msg.as_string())
            emails_sent += 1
            total_new   += len(new_cases)
            mark_digest_sent(sub["id"], [c["tid"] for c in new_cases])
            logger.info(f"[Digest] Sent to {sub['email']}: {len(new_cases)} cases")
        except Exception as exc:
            logger.error(f"[Digest] Email failed for {sub['email']}: {exc}")

    logger.info(f"[Digest] Broadcast done: {emails_sent} emails, {total_new} new cases")
    return {
        "frequency"   : frequency,
        "subs_checked": len(due_subs),
        "emails_sent" : emails_sent,
        "new_cases"   : total_new,
    }



async def run_alert_digest(frequency: str) -> dict:
    """
    Run the digest for all alerts of the given frequency that are due.
    Returns a summary dict for logging / admin endpoint response.

    frequency: "instant" | "daily" | "weekly"
    """
    logger.info(f"[Alerts] Running {frequency} digest...")

    due_alerts = get_due_alerts(frequency)
    if not due_alerts:
        logger.info(f"[Alerts] No due {frequency} alerts found")
        return {"frequency": frequency, "alerts_checked": 0, "emails_sent": 0}

    logger.info(f"[Alerts] Found {len(due_alerts)} due alerts")

    # Group by user_id to send one combined email per user
    by_user: dict[int, list] = {}
    for alert in due_alerts:
        uid = alert["user_id"]
        by_user.setdefault(uid, []).append(alert)

    emails_sent = 0
    total_new_cases = 0

    for user_id, user_alerts in by_user.items():
        email = user_alerts[0]["email"]
        alerts_with_results = []

        # Search IK for each alert concurrently
        search_tasks = [
            _search_ik(a["section_query"], a["court"], max_results=5)
            for a in user_alerts
        ]
        all_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        for alert, results in zip(user_alerts, all_results):
            if isinstance(results, Exception):
                results = []

            # Filter out tids already sent for this alert
            already_sent = set(alert.get("last_sent_tids") or [])
            new_cases = [c for c in results if c["tid"] not in already_sent]

            alerts_with_results.append({
                "alert"    : alert,
                "new_cases": new_cases,
            })

        # Only send if there's at least one new result across all alerts for this user
        any_new = any(len(item["new_cases"]) > 0 for item in alerts_with_results)

        if any_new:
            sent = _send_digest_email(email, alerts_with_results, frequency)
            if sent:
                emails_sent += 1
                # Mark each alert as sent + record which tids were included
                for item in alerts_with_results:
                    new_tids = [c["tid"] for c in item["new_cases"]]
                    total_new_cases += len(new_tids)
                    if new_tids:
                        mark_alert_sent(item["alert"]["id"], new_tids)
        else:
            # No new results — still update last_sent_at so we don't re-check too soon
            for item in alerts_with_results:
                mark_alert_sent(item["alert"]["id"], [])

    logger.info(f"[Alerts] {frequency} digest done: {emails_sent} emails, {total_new_cases} new cases")
    return {
        "frequency"      : frequency,
        "alerts_checked" : len(due_alerts),
        "users_checked"  : len(by_user),
        "emails_sent"    : emails_sent,
        "new_cases_found": total_new_cases,
    }
