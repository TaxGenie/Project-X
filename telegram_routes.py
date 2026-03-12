import os, httpx
from fastapi import APIRouter, Request
from database import get_due_digest_subscriptions, mark_digest_sent

tg_router = APIRouter(prefix="/webhook", tags=["Telegram"])
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_API   = f"https://api.telegram.org/bot{TG_TOKEN}"

@tg_router.post("/telegram")
async def telegram_webhook(request: Request):
    body = await request.json()
    try:
        msg     = body["message"]
        chat_id = msg["chat"]["id"]
        text    = msg.get("text", "").strip()
        await route_message(chat_id, text)
    except (KeyError, TypeError):
        pass
    return {"ok": True}

async def route_message(chat_id: int, text: str):
    t = text.lower()
    if t in ["/start", "/help", "help"]:
        await send_menu(chat_id)
    elif t.startswith("/search "):
        await handle_search(chat_id, text[8:].strip())
    elif t.startswith("/brief "):
        await handle_brief(chat_id, text[7:].strip())
    else:
        # Treat anything else as a search
        await handle_search(chat_id, text)

async def send(chat_id: int, text: str):
    async with httpx.AsyncClient() as c:
        await c.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"  # enables *bold* and _italic_
        })

async def send_menu(chat_id: int):
    await send(chat_id,
        "🍪 *Tax Cookies Bot*\n"
        "_India's AI Tax Research Assistant_\n\n"
        "*Commands:*\n"
        "/search `section 68 itat delhi`\n"
        "/search `CIT vs Lovely Exports`\n"
        "/brief `case name` — AI Case Brief\n\n"
        "🌐 Full platform: taxcookies.in"
    )

async def handle_search(chat_id: int, query: str):
    import re as _re
    await send(chat_id, f"🔍 Searching: *{query}*...")
    try:
        ik_token = os.getenv("INDIANKANOON_API_TOKEN", "")
        ik_query = query if "income tax" in query.lower() else f"{query} income tax"
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            resp = await c.post(
                "https://api.indiankanoon.org/search/",
                data={"formInput": ik_query, "pagenum": "0"},
                headers={"Authorization": f"Token {ik_token}", "Accept": "application/json"}
            )
        if resp.status_code != 200:
            await send(chat_id, f"❌ Search failed (status {resp.status_code}). Try again later.")
            return
        docs = resp.json().get("docs", [])
    except Exception as e:
        await send(chat_id, "❌ Search error. Please try again later.")
        print(f"[Telegram Search Error] {e}")
        return

    if not docs:
        await send(chat_id, f"❌ No results for *{query}*. Try rephrasing.")
        return

    lines = [f"⚖️ *Results for '{query}':*\n"]
    for i, doc in enumerate(docs[:3], 1):
        title = _re.sub(r'<[^>]+>', ' ', doc.get("title", "Untitled")).strip()
        court = doc.get("docsource", "Tribunal")
        tid   = doc.get("tid", "")
        year  = str(doc.get("publishdate", ""))[:4]
        url   = f"https://indiankanoon.org/doc/{tid}/"
        lines.append(
            f"*{i}. {title}*\n"
            f"   {court} · {year}\n"
            f"   [Open Judgment]({url})\n"
        )
    lines.append("_Reply /brief <case name> for AI brief_")
    await send(chat_id, "\n".join(lines))

async def handle_brief(chat_id: int, case_name: str):
    await send(chat_id, f"🍪 Generating brief for *{case_name}*...\n_~20 seconds_")
    # same brief logic as WhatsApp guide — call /case-law/brief/{tid}
    await send(chat_id, "Brief ready → taxcookies.in _(full brief on web)_")


# ══════════════════════════════════════════════════════════════════════════════
# DIGEST BROADCAST
# Called by APScheduler cron jobs in main.py:
#   run_digest_broadcast("daily")  — every day at 08:00 IST
#   run_digest_broadcast("weekly") — every Monday at 08:00 IST
#
# Flow:
#   1. Fetch due subscriptions from digest_subscriptions table
#   2. For each subscriber, filter out already-sent tids
#   3. Send Telegram message
#   4. Mark subscription as sent via mark_digest_sent()
# ══════════════════════════════════════════════════════════════════════════════
async def run_digest_broadcast(mode: str):
    """
    Broadcast a tax digest to all subscribers due for this frequency.

    Args:
        mode: "daily" or "weekly"
    """
    print(f"[TEJAS] 📢 Starting {mode} digest broadcast...")

    # ── 1. Fetch due subscriptions from DB ───────────────────────────────
    try:
        subscriptions = get_due_digest_subscriptions(mode)
    except Exception as e:
        print(f"[TEJAS] ❌ Could not fetch digest subscriptions: {e}")
        return

    if not subscriptions:
        print(f"[TEJAS] ℹ️  No subscribers due for {mode} digest — skipping.")
        return

    print(f"[TEJAS] 📋 {len(subscriptions)} subscriber(s) due for {mode} digest.")

    # ── 2. Fetch latest judgments once (shared across all subscribers) ───
    page_size = 3 if mode == "daily" else 7
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.get(
                "http://localhost:8000/case-law",
                params={"q": "income tax", "page_size": page_size, "sort": "date"},
                headers={"Authorization": f"Bearer {os.getenv('INTERNAL_API_KEY')}"},
            )
        all_results = resp.json().get("results", [])
    except Exception as e:
        print(f"[TEJAS] ❌ Case-law fetch failed during digest: {e}")
        all_results = []

    # ── 3. Broadcast to each subscriber ──────────────────────────────────
    sent_count, skipped_count, failed_count = 0, 0, 0

    for sub in subscriptions:
        user_id           = sub["user_id"]
        sub_id            = sub["id"]
        email             = sub.get("email", "")
        already_sent_tids = sub.get("last_sent_tids", [])

        # Filter out judgments already sent to this subscriber
        new_results = [
            r for r in all_results
            if str(r.get("tid", r.get("id", ""))) not in already_sent_tids
        ]

        if not new_results:
            print(f"[TEJAS] ℹ️  No new judgments for user {user_id} ({email}) — skipping.")
            skipped_count += 1
            mark_digest_sent(sub_id, [])   # reset the cutoff timer anyway
            continue

        message   = _build_digest_message(mode, new_results)
        sent_tids = [str(r.get("tid", r.get("id", ""))) for r in new_results]

        # telegram_chat_id must be stored on the subscription or users table.
        # Add a `telegram_chat_id` column to digest_subscriptions (or users) and
        # populate it when the user connects their Telegram account.
        tg_chat_id = sub.get("telegram_chat_id") or user_id

        try:
            await send(tg_chat_id, message)
            mark_digest_sent(sub_id, sent_tids)
            print(f"[TEJAS] ✅ Digest → user {user_id} ({email}), {len(new_results)} judgment(s).")
            sent_count += 1
        except Exception as e:
            print(f"[TEJAS] ❌ Failed → user {user_id} ({email}): {e}")
            failed_count += 1

    print(
        f"[TEJAS] 📢 {mode.capitalize()} digest complete — "
        f"sent: {sent_count}, skipped: {skipped_count}, failed: {failed_count}"
    )


def _build_digest_message(mode: str, results: list) -> str:
    """Compose the Telegram Markdown digest from a list of judgment dicts."""
    label = "📅 *Daily Tax Digest*" if mode == "daily" else "📆 *Weekly Tax Digest*"
    lines = [f"🍪 {label}", "_Latest Indian Tax Judgments_\n"]
    for i, r in enumerate(results, 1):
        lines.append(
            f"*{i}. {r['title']}*\n"
            f"   {r['court']} · {str(r.get('date', ''))[:10]}\n"
            f"   [Read Judgment]({r['url']})\n"
        )
    lines.append("🌐 Full research → taxcookies.in")
    lines.append("_Reply /brief <case name> for AI summary_")
    return "\n".join(lines)
