# ─────────────────────────────────────────────────────────────────────────────
# admin_routes.py  —  TaxGenie Admin Backend
#
# Setup:
#   1. from admin_routes import admin_router
#      app.include_router(admin_router)
#   2. .env:  ADMIN_PASSWORD=your_secret_here
#             DB_PATH=tejas_users.db  (optional)
#
# Feedback write endpoint (PUBLIC — no auth):
#   POST /admin/feedback   { query, section_1961, section_2025, rating, comment }
#   Writes to feedback_log.json via _fb_save().
#   NOTE: If main.py has its own /feedback route, remove it to avoid conflicts.
# ─────────────────────────────────────────────────────────────────────────────

import os, json, csv, io
import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pathlib import Path
from pydantic import BaseModel
from typing import Optional

admin_router = APIRouter(prefix="/admin")

DB_PATH       = os.getenv("DB_PATH", "tejas_users.db")
ADMIN_PASS    = os.getenv("ADMIN_PASSWORD", "changeme123")
FEEDBACK_FILE = Path(__file__).parent / "feedback_log.json"

# ── feedback_store helpers (inline — no import needed) ─────────────────────
def _fb_load() -> list:
    if FEEDBACK_FILE.exists():
        try:
            return json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def _fb_save(log: list):
    # Write atomically: temp file → rename, so a crash never corrupts the log
    tmp = FEEDBACK_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(log, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    tmp.replace(FEEDBACK_FILE)

def _fb_summary(log: list) -> dict:
    if not log:
        return {"total": 0, "up": 0, "down": 0, "sections": {}}
    sections: dict = {}
    for entry in log:
        key = entry.get("section_1961", "unknown").upper()
        if key not in sections:
            sections[key] = {"up": 0, "down": 0, "comments": []}
        sections[key][entry.get("rating", "up")] = sections[key].get(entry.get("rating","up"), 0) + 1
        if entry.get("comment"):
            sections[key]["comments"].append(entry["comment"])
    for sec, data in sections.items():
        data["needs_attention"] = data.get("down", 0) >= 2 or data.get("down", 0) > data.get("up", 0)
    return {
        "total":    len(log),
        "up":       sum(1 for e in log if e.get("rating") == "up"),
        "down":     sum(1 for e in log if e.get("rating") == "down"),
        "sections": sections,
    }


# ── Models ─────────────────────────────────────────────────────────────────
class GrantCreditsRequest(BaseModel):
    user_id: int
    credits: int
    reason:  Optional[str] = "Manual grant by admin"

class ToggleUserRequest(BaseModel):
    user_id:   int
    is_active: bool

class FeedbackRequest(BaseModel):
    query:        str
    section_1961: str
    section_2025: str
    rating:       str          # "up" or "down"
    comment:      str = ""


# ── Helpers ─────────────────────────────────────────────────────────────────
def get_db():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def require_admin(request: Request):
    pw = request.headers.get("X-Admin-Password", "")
    if pw != ADMIN_PASS:
        raise HTTPException(status_code=403, detail="Forbidden")

def ensure_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS credit_grants (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            credits    INTEGER NOT NULL,
            reason     TEXT    DEFAULT '',
            granted_at TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)
    # Safe profile column migration — no-op if columns already exist
    for col, typedef in [
        ("full_name",    "TEXT DEFAULT ''"),
        ("profession",   "TEXT DEFAULT ''"),
        ("organisation", "TEXT DEFAULT ''"),
        ("use_case",     "TEXT DEFAULT ''"),
    ]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
        except Exception:
            pass


# ── Serve admin HTML ─────────────────────────────────────────────────────────
@admin_router.get("", response_class=HTMLResponse)
@admin_router.get("/", response_class=HTMLResponse)
async def admin_page():
    for candidate in [
        Path(__file__).parent / "static" / "admin.html",
        Path(__file__).parent / "admin.html",
    ]:
        if candidate.exists():
            return HTMLResponse(candidate.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="admin.html not found")


# ── Main dashboard data ──────────────────────────────────────────────────────
@admin_router.get("/data")
async def admin_data(request: Request):
    require_admin(request)

    conn = get_db()
    cur  = conn.cursor()
    ensure_tables(cur)

    today     = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    week_ago  = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    # ── User counts ──────────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE DATE(created_at) = ?", (today,))
    new_users_today = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE DATE(created_at) >= ?", (week_ago,))
    new_users_week = cur.fetchone()[0]

    cur.execute("SELECT id, email, created_at, is_active, full_name, profession, organisation, use_case FROM users ORDER BY created_at DESC")
    users = [dict(r) for r in cur.fetchall()]

    # ── Credits today vs yesterday ───────────────────────────────────────────
    cur.execute("SELECT COALESCE(SUM(credits_used),0) FROM daily_credits WHERE date_ist = ?", (today,))
    credits_used_today = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(credits_used),0) FROM daily_credits WHERE date_ist = ?", (yesterday,))
    credits_used_yesterday = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT user_id) FROM daily_credits WHERE date_ist = ?", (today,))
    active_users_today = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT user_id) FROM daily_credits WHERE date_ist = ?", (yesterday,))
    active_users_yesterday = cur.fetchone()[0]

    # ── Sessions ─────────────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM chat_history")
    total_sessions = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM chat_history WHERE DATE(created_at) = ?", (today,))
    sessions_today = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM chat_history WHERE DATE(created_at) = ?", (yesterday,))
    sessions_yesterday = cur.fetchone()[0]

    # ── Credits per user (subqueries to avoid JOIN multiplication) ───────────
    cur.execute("""
        SELECT u.id, u.email, u.is_active, u.full_name, u.profession,
               u.organisation, u.use_case, u.created_at,
               COALESCE(
                   (SELECT SUM(dc2.credits_used) FROM daily_credits dc2 WHERE dc2.user_id = u.id),
               0) AS total_credits,
               (SELECT COUNT(*) FROM chat_history ch2 WHERE ch2.user_id = u.id) AS session_count,
               (SELECT MAX(ch3.updated_at) FROM chat_history ch3 WHERE ch3.user_id = u.id) AS last_active
        FROM   users u
        ORDER  BY total_credits DESC
    """)
    credit_stats = [dict(r) for r in cur.fetchall()]

    # ── Daily activity — last 30 days ────────────────────────────────────────
    cur.execute("""
        SELECT date_ist,
               SUM(credits_used)       AS credits_used,
               COUNT(DISTINCT user_id) AS active_users
        FROM   daily_credits
        GROUP  BY date_ist
        ORDER  BY date_ist ASC
        LIMIT  30
    """)
    daily_activity = [dict(r) for r in cur.fetchall()]

    # ── Recent chats ─────────────────────────────────────────────────────────
    try:
        cur.execute("""
            SELECT u.email, ch.title, ch.session_id,
                   ch.created_at, ch.updated_at,
                   json_array_length(ch.messages) AS message_count
            FROM   chat_history ch
            JOIN   users u ON ch.user_id = u.id
            ORDER  BY ch.updated_at DESC
            LIMIT  50
        """)
        recent_chats = [dict(r) for r in cur.fetchall()]
    except Exception:
        try:
            cur.execute("""
                SELECT u.email, ch.title, ch.session_id,
                       ch.created_at, ch.updated_at,
                       0 AS message_count
                FROM   chat_history ch
                JOIN   users u ON ch.user_id = u.id
                ORDER  BY ch.updated_at DESC LIMIT 50
            """)
            recent_chats = [dict(r) for r in cur.fetchall()]
        except Exception:
            recent_chats = []

    # ── Top searched topics ───────────────────────────────────────────────────
    cur.execute("""
        SELECT title, COUNT(*) AS search_count
        FROM   chat_history
        WHERE  title != 'Untitled Chat' AND title != 'Test Session' AND title != ''
        GROUP  BY title
        ORDER  BY search_count DESC
        LIMIT  10
    """)
    top_topics = [dict(r) for r in cur.fetchall()]

    # ── Grant history ─────────────────────────────────────────────────────────
    cur.execute("""
        SELECT cg.id, u.email, cg.credits, cg.reason, cg.granted_at
        FROM   credit_grants cg
        JOIN   users u ON cg.user_id = u.id
        ORDER  BY cg.granted_at DESC LIMIT 50
    """)
    grants = [dict(r) for r in cur.fetchall()]

    # ── Revenue (from purchases table if it exists) ───────────────────────────
    revenue_total = 0
    purchases     = []
    try:
        cur.execute("""
            SELECT u.email, cp.pack, cp.amount_paid,
                   cp.credits_added, cp.created_at, cp.payment_id
            FROM   credit_purchases cp
            JOIN   users u ON cp.user_id = u.id
            ORDER  BY cp.created_at DESC LIMIT 50
        """)
        purchases = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT COALESCE(SUM(amount_paid),0) FROM credit_purchases")
        revenue_total = cur.fetchone()[0]
    except Exception:
        pass

    # ── Feedbacks (from feedback_log.json via feedback_store) ────────────────
    fb_log            = _fb_load()
    fb_summary        = _fb_summary(fb_log)
    feedbacks         = list(reversed(fb_log))   # most-recent first
    feedback_stats    = {
        "total":      fb_summary["total"],
        "up":         fb_summary["up"],
        "down":       fb_summary["down"],
        "sections":   fb_summary["sections"],
    }

    # ── User profile analytics ────────────────────────────────────────────────
    cur.execute("""
        SELECT profession, COUNT(*) AS count
        FROM users
        WHERE profession IS NOT NULL AND profession != ''
        GROUP BY profession ORDER BY count DESC
    """)
    profession_dist = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT use_case, COUNT(*) AS count
        FROM users
        WHERE use_case IS NOT NULL AND use_case != ''
        GROUP BY use_case ORDER BY count DESC
    """)
    use_case_dist = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT organisation, COUNT(*) AS count
        FROM users
        WHERE organisation IS NOT NULL AND organisation != ''
        GROUP BY organisation ORDER BY count DESC LIMIT 20
    """)
    org_dist = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT COUNT(*) FROM users WHERE profession IS NOT NULL AND profession != ''")
    users_with_profile = cur.fetchone()[0]

    cur.execute("""
        SELECT u.email, u.full_name, u.profession, u.use_case,
               COALESCE(SUM(dc.credits_used),0) AS total_credits,
               COUNT(DISTINCT ch.session_id) AS sessions
        FROM users u
        LEFT JOIN daily_credits dc ON dc.user_id = u.id
        LEFT JOIN chat_history ch ON ch.user_id = u.id
        GROUP BY u.id ORDER BY total_credits DESC LIMIT 10
    """)
    power_users = [dict(r) for r in cur.fetchall()]

    conn.close()

    return {
        "total_users":            total_users,
        "new_users_today":        new_users_today,
        "new_users_week":         new_users_week,
        "credits_used_today":     credits_used_today,
        "credits_used_yesterday": credits_used_yesterday,
        "active_users_today":     active_users_today,
        "active_users_yesterday": active_users_yesterday,
        "total_sessions":         total_sessions,
        "sessions_today":         sessions_today,
        "sessions_yesterday":     sessions_yesterday,
        "revenue_total":          revenue_total,
        "users":                  users,
        "credit_stats":           credit_stats,
        "daily_activity":         daily_activity,
        "recent_chats":           recent_chats,
        "top_topics":             top_topics,
        "grants":                 grants,
        "feedbacks":              feedbacks,
        "feedback_stats":         feedback_stats,
        "purchases":              purchases,
        "profession_dist":        profession_dist,
        "use_case_dist":          use_case_dist,
        "org_dist":               org_dist,
        "users_with_profile":     users_with_profile,
        "power_users":            power_users,
    }


# ── CSV exports ──────────────────────────────────────────────────────────────
@admin_router.get("/export/users")
async def export_users_csv(request: Request):
    require_admin(request)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT u.id, u.email, u.full_name, u.profession, u.organisation, u.use_case,
               u.created_at, u.is_active,
               COALESCE((SELECT SUM(dc.credits_used) FROM daily_credits dc WHERE dc.user_id=u.id),0) AS total_credits,
               (SELECT COUNT(*) FROM chat_history ch WHERE ch.user_id=u.id) AS total_sessions,
               (SELECT MAX(ch2.updated_at) FROM chat_history ch2 WHERE ch2.user_id=u.id) AS last_active
        FROM users u ORDER BY u.created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID","Email","Name","Profession","Organisation","Intended Use",
                     "Joined","Active","Total Credits Used","Total Sessions","Last Active"])
    for r in rows:
        writer.writerow([r["id"], r["email"], r["full_name"] or "", r["profession"] or "",
                         r["organisation"] or "", r["use_case"] or "",
                         r["created_at"], "Yes" if r["is_active"] else "No",
                         r["total_credits"], r["total_sessions"],
                         r["last_active"] or "Never"])
    output.seek(0)
    fname = f"taxgenie_users_{datetime.date.today().isoformat()}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})


@admin_router.get("/export/activity")
async def export_activity_csv(request: Request):
    require_admin(request)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT dc.date_ist, u.email, dc.credits_used
        FROM daily_credits dc JOIN users u ON dc.user_id=u.id
        ORDER BY dc.date_ist DESC, dc.credits_used DESC
    """)
    rows = cur.fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date","Email","Credits Used"])
    for r in rows:
        writer.writerow([r["date_ist"], r["email"], r["credits_used"]])
    output.seek(0)
    fname = f"taxgenie_activity_{datetime.date.today().isoformat()}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})


# ── Toggle user active/inactive ──────────────────────────────────────────────
@admin_router.post("/toggle-user")
async def toggle_user(req: ToggleUserRequest, request: Request):
    require_admin(request)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT id, email FROM users WHERE id = ?", (req.user_id,))
    user = cur.fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found.")
    cur.execute("UPDATE users SET is_active = ? WHERE id = ?",
                (1 if req.is_active else 0, req.user_id))
    conn.commit()
    conn.close()
    verb = "activated" if req.is_active else "deactivated"
    return {"status": "ok", "message": f"{user['email']} {verb} successfully."}


# ── Grant credits ────────────────────────────────────────────────────────────
@admin_router.post("/grant-credits")
async def grant_credits(req: GrantCreditsRequest, request: Request):
    require_admin(request)
    if req.credits <= 0:
        raise HTTPException(status_code=400, detail="Credits must be positive.")

    conn = get_db()
    cur  = conn.cursor()
    ensure_tables(cur)
    today = datetime.date.today().isoformat()

    cur.execute("SELECT id, email FROM users WHERE id = ?", (req.user_id,))
    user = cur.fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found.")

    cur.execute("SELECT id, credits_used FROM daily_credits WHERE user_id=? AND date_ist=?",
                (req.user_id, today))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE daily_credits SET credits_used=? WHERE id=?",
                    (max(0, row["credits_used"] - req.credits), row["id"]))
    else:
        cur.execute("INSERT INTO daily_credits (user_id, date_ist, credits_used) VALUES (?,?,?)",
                    (req.user_id, today, -req.credits))

    cur.execute("INSERT INTO credit_grants (user_id, credits, reason) VALUES (?,?,?)",
                (req.user_id, req.credits, req.reason or "Manual grant by admin"))
    conn.commit()
    conn.close()

    return {"status": "ok",
            "message": f"{req.credits} credits added to {user['email']}",
            "email": user["email"], "credits": req.credits}


# ── Grant history ────────────────────────────────────────────────────────────
@admin_router.get("/grant-history")
async def grant_history(request: Request):
    require_admin(request)
    conn = get_db()
    cur  = conn.cursor()
    ensure_tables(cur)
    cur.execute("""
        SELECT cg.id, u.email, cg.credits, cg.reason, cg.granted_at
        FROM   credit_grants cg JOIN users u ON cg.user_id=u.id
        ORDER  BY cg.granted_at DESC LIMIT 50
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"grants": rows}


# ── Feedbacks (feedback_log.json) ────────────────────────────────────────────

# PUBLIC — called by the frontend when user clicks 👍 / 👎
@admin_router.post("/feedback")
async def post_feedback(req: FeedbackRequest):
    """Record a thumbs-up / thumbs-down from any user. No auth required."""
    if req.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'.")
    log = _fb_load()
    log.append({
        "ts"          : datetime.datetime.now().isoformat(timespec="seconds"),
        "query"       : req.query.strip(),
        "section_1961": req.section_1961.strip(),
        "section_2025": req.section_2025.strip(),
        "rating"      : req.rating,
        "comment"     : req.comment.strip(),
    })
    _fb_save(log)
    return {"status": "saved", "total_entries": len(log)}
@admin_router.get("/feedbacks")
async def get_feedbacks(request: Request):
    require_admin(request)
    log     = _fb_load()
    summary = _fb_summary(log)
    return {
        "feedbacks": list(reversed(log)),   # most-recent first
        "stats":     summary,
    }


@admin_router.delete("/feedbacks/{index}")
async def delete_feedback(index: int, request: Request):
    """Delete feedback by its 0-based index in the JSON array (oldest=0)."""
    require_admin(request)
    log = _fb_load()
    if index < 0 or index >= len(log):
        raise HTTPException(status_code=404, detail="Feedback entry not found.")
    removed = log.pop(index)
    _fb_save(log)
    return {"status": "ok", "message": f"Feedback from {removed.get('ts','')} deleted.",
            "remaining": len(log)}


@admin_router.get("/export/feedbacks")
async def export_feedbacks_csv(request: Request):
    require_admin(request)
    log = _fb_load()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "Query", "Section 1961", "Section 2025", "Rating", "Comment"])
    for e in log:
        writer.writerow([
            e.get("ts",""), e.get("query",""),
            e.get("section_1961",""), e.get("section_2025",""),
            e.get("rating",""), e.get("comment",""),
        ])
    output.seek(0)
    fname = f"taxcookies_feedbacks_{datetime.date.today().isoformat()}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})


# ── Bulk credit grant ────────────────────────────────────────────────────────
class BulkGrantRequest(BaseModel):
    user_ids: list
    credits:  int
    reason:   str = "Bulk grant by admin"

@admin_router.post("/bulk-grant")
async def bulk_grant(req: BulkGrantRequest, request: Request):
    require_admin(request)
    if req.credits <= 0:
        raise HTTPException(status_code=400, detail="Credits must be positive.")
    conn = get_db()
    cur  = conn.cursor()
    ensure_tables(cur)
    today = datetime.date.today().isoformat()
    success, failed = [], []
    for uid in req.user_ids:
        try:
            cur.execute("SELECT id, email FROM users WHERE id = ?", (uid,))
            user = cur.fetchone()
            if not user:
                failed.append(uid); continue
            cur.execute("SELECT id, credits_used FROM daily_credits WHERE user_id=? AND date_ist=?", (uid, today))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE daily_credits SET credits_used=? WHERE id=?",
                            (max(0, row["credits_used"] - req.credits), row["id"]))
            else:
                cur.execute("INSERT INTO daily_credits (user_id, date_ist, credits_used) VALUES (?,?,?)",
                            (uid, today, -req.credits))
            cur.execute("INSERT INTO credit_grants (user_id, credits, reason) VALUES (?,?,?)",
                        (uid, req.credits, req.reason))
            success.append(user["email"])
        except Exception:
            failed.append(uid)
    conn.commit()
    conn.close()
    return {"status": "ok", "granted_to": len(success), "failed": len(failed),
            "message": f"{req.credits} credits granted to {len(success)} users."}


# ── Bulk toggle active ───────────────────────────────────────────────────────
class BulkToggleRequest(BaseModel):
    user_ids:  list
    is_active: bool

@admin_router.post("/bulk-toggle")
async def bulk_toggle(req: BulkToggleRequest, request: Request):
    require_admin(request)
    conn = get_db()
    cur  = conn.cursor()
    for uid in req.user_ids:
        try:
            cur.execute("UPDATE users SET is_active = ? WHERE id = ?",
                        (1 if req.is_active else 0, uid))
        except Exception:
            pass
    conn.commit()
    conn.close()
    verb = "enabled" if req.is_active else "disabled"
    return {"status": "ok", "message": f"{len(req.user_ids)} users {verb}."}
