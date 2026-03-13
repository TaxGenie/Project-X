# ─────────────────────────────────────────────────────────────────────────────
# admin_routes.py  —  TaxGenie Admin Backend (PostgreSQL)
#
# Setup:
#   1. from admin_routes import admin_router
#      app.include_router(admin_router)
#   2. .env:  ADMIN_PASSWORD=your_secret_here
#             DATABASE_URL=postgresql://...
# ─────────────────────────────────────────────────────────────────────────────

import os, json, csv, io
import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pathlib import Path
from pydantic import BaseModel
from typing import Optional

from database import _conn

admin_router = APIRouter(prefix="/admin")

ADMIN_PASS    = os.getenv("ADMIN_PASSWORD", "changeme123")
FEEDBACK_FILE = Path(__file__).parent / "feedback_log.json"

# ── feedback_store helpers ─────────────────────────────────────────────────
def _fb_load() -> list:
    if FEEDBACK_FILE.exists():
        try:
            return json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def _fb_save(log: list):
    tmp = FEEDBACK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
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
    rating:       str
    comment:      str = ""

class BulkGrantRequest(BaseModel):
    user_ids: list
    credits:  int
    reason:   str = "Bulk grant by admin"

class BulkToggleRequest(BaseModel):
    user_ids:  list
    is_active: bool


# ── Helpers ─────────────────────────────────────────────────────────────────
def require_admin(request: Request):
    pw = request.headers.get("X-Admin-Password", "")
    if pw != ADMIN_PASS:
        raise HTTPException(status_code=403, detail="Forbidden")

def ensure_tables():
    """Create credit_grants table and profile columns if not exist. Uses own connection."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS credit_grants (
                    id         SERIAL PRIMARY KEY,
                    user_id    INTEGER NOT NULL,
                    credits    INTEGER NOT NULL,
                    reason     TEXT    DEFAULT '',
                    granted_at TIMESTAMP DEFAULT NOW()
                )
            """)
            # Each ALTER TABLE gets its own savepoint so a failure cannot abort
            # the outer transaction and poison subsequent queries.
            for col, typedef in [
                ("full_name",    "TEXT DEFAULT ''"),
                ("profession",   "TEXT DEFAULT ''"),
                ("organisation", "TEXT DEFAULT ''"),
                ("use_case",     "TEXT DEFAULT ''"),
            ]:
                try:
                    cur.execute("SAVEPOINT ensure_%s" % col)
                    cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {typedef}")
                    cur.execute("RELEASE SAVEPOINT ensure_%s" % col)
                except Exception as col_err:
                    cur.execute("ROLLBACK TO SAVEPOINT ensure_%s" % col)
                    print(f"[Admin] ensure_tables migration skipped ({col}): {col_err}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[Admin] ensure_tables warning: {e}")
    finally:
        conn.close()


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

    # Use fresh connection and rollback on any error to avoid aborted transaction cascade
    conn = _conn()
    try:
        ensure_tables()
    except Exception as e:
        print(f"[Admin] ensure_tables error: {e}")
        try: conn.rollback()
        except: pass

    today     = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    week_ago  = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    try:
        with conn.cursor() as cur:

            cur.execute("SELECT COUNT(*) AS c FROM users")
            total_users = cur.fetchone()["c"]

            cur.execute("SELECT COUNT(*) AS c FROM users WHERE DATE(created_at) = %s", (today,))
            new_users_today = cur.fetchone()["c"]

            cur.execute("SELECT COUNT(*) AS c FROM users WHERE DATE(created_at) >= %s", (week_ago,))
            new_users_week = cur.fetchone()["c"]

            cur.execute("""
                SELECT id, email, created_at, is_active,
                       COALESCE(full_name,'') AS full_name,
                       COALESCE(profession,'') AS profession,
                       COALESCE(organisation,'') AS organisation,
                       COALESCE(use_case,'') AS use_case
                FROM users ORDER BY created_at DESC
            """)
            users = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT COALESCE(SUM(credits_used),0) AS c FROM daily_credits WHERE date_ist = %s", (today,))
            credits_used_today = cur.fetchone()["c"]

            cur.execute("SELECT COALESCE(SUM(credits_used),0) AS c FROM daily_credits WHERE date_ist = %s", (yesterday,))
            credits_used_yesterday = cur.fetchone()["c"]

            cur.execute("SELECT COUNT(DISTINCT user_id) AS c FROM daily_credits WHERE date_ist = %s", (today,))
            active_users_today = cur.fetchone()["c"]

            cur.execute("SELECT COUNT(DISTINCT user_id) AS c FROM daily_credits WHERE date_ist = %s", (yesterday,))
            active_users_yesterday = cur.fetchone()["c"]

            cur.execute("SELECT COUNT(*) AS c FROM chat_history")
            total_sessions = cur.fetchone()["c"]

            cur.execute("SELECT COUNT(*) AS c FROM chat_history WHERE DATE(created_at) = %s", (today,))
            sessions_today = cur.fetchone()["c"]

            cur.execute("SELECT COUNT(*) AS c FROM chat_history WHERE DATE(created_at) = %s", (yesterday,))
            sessions_yesterday = cur.fetchone()["c"]

            cur.execute("""
                SELECT u.id, u.email, u.is_active,
                       COALESCE(u.full_name,'') AS full_name,
                       COALESCE(u.profession,'') AS profession,
                       COALESCE(u.organisation,'') AS organisation,
                       COALESCE(u.use_case,'') AS use_case,
                       u.created_at,
                       COALESCE((SELECT SUM(dc2.credits_used) FROM daily_credits dc2 WHERE dc2.user_id = u.id), 0) AS total_credits,
                       (SELECT COUNT(*) FROM chat_history ch2 WHERE ch2.user_id = u.id) AS session_count,
                       (SELECT MAX(ch3.updated_at) FROM chat_history ch3 WHERE ch3.user_id = u.id) AS last_active
                FROM users u
                ORDER BY total_credits DESC
            """)
            credit_stats = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT date_ist,
                       SUM(credits_used) AS credits_used,
                       COUNT(DISTINCT user_id) AS active_users
                FROM daily_credits
                GROUP BY date_ist ORDER BY date_ist ASC LIMIT 30
            """)
            daily_activity = [dict(r) for r in cur.fetchall()]

            cur.execute("SAVEPOINT sp_recent_chats")
            try:
                cur.execute("""
                    SELECT u.email, ch.title, ch.session_id,
                           ch.created_at, ch.updated_at,
                           COALESCE(json_array_length(ch.messages::json), 0) AS message_count
                    FROM chat_history ch
                    JOIN users u ON ch.user_id = u.id
                    ORDER BY ch.updated_at DESC LIMIT 50
                """)
                recent_chats = [dict(r) for r in cur.fetchall()]
                cur.execute("RELEASE SAVEPOINT sp_recent_chats")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT sp_recent_chats")
                cur.execute("""
                    SELECT u.email, ch.title, ch.session_id,
                           ch.created_at, ch.updated_at, 0 AS message_count
                    FROM chat_history ch
                    JOIN users u ON ch.user_id = u.id
                    ORDER BY ch.updated_at DESC LIMIT 50
                """)
                recent_chats = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT title, COUNT(*) AS search_count
                FROM chat_history
                WHERE title != 'Untitled Chat' AND title != 'Test Session' AND title != ''
                GROUP BY title ORDER BY search_count DESC LIMIT 10
            """)
            top_topics = [dict(r) for r in cur.fetchall()]

            cur.execute("SAVEPOINT sp_grants")
            try:
                cur.execute("""
                    SELECT cg.id, u.email, cg.credits, cg.reason, cg.granted_at
                    FROM credit_grants cg JOIN users u ON cg.user_id = u.id
                    ORDER BY cg.granted_at DESC LIMIT 50
                """)
                grants = [dict(r) for r in cur.fetchall()]
                cur.execute("RELEASE SAVEPOINT sp_grants")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT sp_grants")
                grants = []

            revenue_total = 0
            purchases = []
            cur.execute("SAVEPOINT sp_purchases")
            try:
                cur.execute("""
                    SELECT u.email, cp.pack, cp.amount_paid,
                           cp.credits_added, cp.created_at, cp.payment_id
                    FROM credit_purchases cp JOIN users u ON cp.user_id = u.id
                    ORDER BY cp.created_at DESC LIMIT 50
                """)
                purchases = [dict(r) for r in cur.fetchall()]
                cur.execute("SELECT COALESCE(SUM(amount_paid),0) AS t FROM credit_purchases")
                revenue_total = cur.fetchone()["t"]
                cur.execute("RELEASE SAVEPOINT sp_purchases")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT sp_purchases")

            cur.execute("""
                SELECT COALESCE(profession,'') AS profession, COUNT(*) AS count
                FROM users WHERE profession IS NOT NULL AND profession != ''
                GROUP BY profession ORDER BY count DESC
            """)
            profession_dist = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT COALESCE(use_case,'') AS use_case, COUNT(*) AS count
                FROM users WHERE use_case IS NOT NULL AND use_case != ''
                GROUP BY use_case ORDER BY count DESC
            """)
            use_case_dist = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT COALESCE(organisation,'') AS organisation, COUNT(*) AS count
                FROM users WHERE organisation IS NOT NULL AND organisation != ''
                GROUP BY organisation ORDER BY count DESC LIMIT 20
            """)
            org_dist = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT COUNT(*) AS c FROM users WHERE profession IS NOT NULL AND profession != ''")
            users_with_profile = cur.fetchone()["c"]

            cur.execute("""
                SELECT u.email, COALESCE(u.full_name,'') AS full_name,
                       COALESCE(u.profession,'') AS profession,
                       COALESCE(u.use_case,'') AS use_case,
                       COALESCE(SUM(dc.credits_used),0) AS total_credits,
                       COUNT(DISTINCT ch.session_id) AS sessions
                FROM users u
                LEFT JOIN daily_credits dc ON dc.user_id = u.id
                LEFT JOIN chat_history ch ON ch.user_id = u.id
                GROUP BY u.id ORDER BY total_credits DESC LIMIT 10
            """)
            power_users = [dict(r) for r in cur.fetchall()]

    except Exception as e:
        import traceback
        print(f"[Admin] admin_data ERROR: {e}")
        print(traceback.format_exc())
        try: conn.rollback()
        except: pass
        try: conn.close()
        except: pass
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        try: conn.close()
        except: pass

    fb_log         = _fb_load()
    fb_summary     = _fb_summary(fb_log)

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
        "feedbacks":              list(reversed(fb_log)),
        "feedback_stats":         {"total": fb_summary["total"], "up": fb_summary["up"], "down": fb_summary["down"], "sections": fb_summary["sections"]},
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
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT u.id, u.email,
                       COALESCE(u.full_name,'') AS full_name,
                       COALESCE(u.profession,'') AS profession,
                       COALESCE(u.organisation,'') AS organisation,
                       COALESCE(u.use_case,'') AS use_case,
                       u.created_at, u.is_active,
                       COALESCE((SELECT SUM(dc.credits_used) FROM daily_credits dc WHERE dc.user_id=u.id),0) AS total_credits,
                       (SELECT COUNT(*) FROM chat_history ch WHERE ch.user_id=u.id) AS total_sessions,
                       (SELECT MAX(ch2.updated_at) FROM chat_history ch2 WHERE ch2.user_id=u.id) AS last_active
                FROM users u ORDER BY u.created_at DESC
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID","Email","Name","Profession","Organisation","Intended Use",
                     "Joined","Active","Total Credits Used","Total Sessions","Last Active"])
    for r in rows:
        writer.writerow([r["id"], r["email"], r["full_name"], r["profession"],
                         r["organisation"], r["use_case"], r["created_at"],
                         "Yes" if r["is_active"] else "No",
                         r["total_credits"], r["total_sessions"], r["last_active"] or "Never"])
    output.seek(0)
    fname = f"taxgenie_users_{datetime.date.today().isoformat()}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})


@admin_router.get("/export/activity")
async def export_activity_csv(request: Request):
    require_admin(request)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT dc.date_ist, u.email, dc.credits_used
                FROM daily_credits dc JOIN users u ON dc.user_id=u.id
                ORDER BY dc.date_ist DESC, dc.credits_used DESC
            """)
            rows = cur.fetchall()
    finally:
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
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email FROM users WHERE id = %s", (req.user_id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found.")
            cur.execute("UPDATE users SET is_active = %s WHERE id = %s",
                        (1 if req.is_active else 0, req.user_id))
        conn.commit()
    finally:
        conn.close()
    verb = "activated" if req.is_active else "deactivated"
    return {"status": "ok", "message": f"{user['email']} {verb} successfully."}


# ── Grant credits ────────────────────────────────────────────────────────────
@admin_router.post("/grant-credits")
async def grant_credits(req: GrantCreditsRequest, request: Request):
    require_admin(request)
    if req.credits <= 0:
        raise HTTPException(status_code=400, detail="Credits must be positive.")
    conn = _conn()
    try:
        ensure_tables()
        today = datetime.date.today().isoformat()
        with conn.cursor() as cur:
            cur.execute("SELECT id, email FROM users WHERE id = %s", (req.user_id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found.")
            cur.execute("SELECT id, credits_used FROM daily_credits WHERE user_id=%s AND date_ist=%s",
                        (req.user_id, today))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE daily_credits SET credits_used=%s WHERE id=%s",
                            (max(0, row["credits_used"] - req.credits), row["id"]))
            else:
                cur.execute("INSERT INTO daily_credits (user_id, date_ist, credits_used) VALUES (%s,%s,%s)",
                            (req.user_id, today, -req.credits))
            cur.execute("INSERT INTO credit_grants (user_id, credits, reason) VALUES (%s,%s,%s)",
                        (req.user_id, req.credits, req.reason or "Manual grant by admin"))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "message": f"{req.credits} credits added to {user['email']}",
            "email": user["email"], "credits": req.credits}


# ── Grant history ────────────────────────────────────────────────────────────
@admin_router.get("/grant-history")
async def grant_history(request: Request):
    require_admin(request)
    conn = _conn()
    try:
        ensure_tables()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cg.id, u.email, cg.credits, cg.reason, cg.granted_at
                FROM credit_grants cg JOIN users u ON cg.user_id=u.id
                ORDER BY cg.granted_at DESC LIMIT 50
            """)
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return {"grants": rows}


# ── Feedbacks ────────────────────────────────────────────────────────────────
@admin_router.post("/feedback")
async def post_feedback(req: FeedbackRequest):
    if req.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'.")
    log = _fb_load()
    log.append({
        "ts":           datetime.datetime.now().isoformat(timespec="seconds"),
        "query":        req.query.strip(),
        "section_1961": req.section_1961.strip(),
        "section_2025": req.section_2025.strip(),
        "rating":       req.rating,
        "comment":      req.comment.strip(),
    })
    _fb_save(log)
    return {"status": "saved", "total_entries": len(log)}

@admin_router.get("/feedbacks")
async def get_feedbacks(request: Request):
    require_admin(request)
    log     = _fb_load()
    summary = _fb_summary(log)
    return {"feedbacks": list(reversed(log)), "stats": summary}

@admin_router.delete("/feedbacks/{index}")
async def delete_feedback(index: int, request: Request):
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
    writer.writerow(["Timestamp","Query","Section 1961","Section 2025","Rating","Comment"])
    for e in log:
        writer.writerow([e.get("ts",""), e.get("query",""), e.get("section_1961",""),
                         e.get("section_2025",""), e.get("rating",""), e.get("comment","")])
    output.seek(0)
    fname = f"taxcookies_feedbacks_{datetime.date.today().isoformat()}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})


# ── Bulk grant ───────────────────────────────────────────────────────────────
@admin_router.post("/bulk-grant")
async def bulk_grant(req: BulkGrantRequest, request: Request):
    require_admin(request)
    if req.credits <= 0:
        raise HTTPException(status_code=400, detail="Credits must be positive.")
    conn = _conn()
    try:
        ensure_tables()
        today = datetime.date.today().isoformat()
        success, failed = [], []
        with conn.cursor() as cur:
            for uid in req.user_ids:
                try:
                    cur.execute("SELECT id, email FROM users WHERE id = %s", (uid,))
                    user = cur.fetchone()
                    if not user:
                        failed.append(uid); continue
                    cur.execute("SELECT id, credits_used FROM daily_credits WHERE user_id=%s AND date_ist=%s", (uid, today))
                    row = cur.fetchone()
                    if row:
                        cur.execute("UPDATE daily_credits SET credits_used=%s WHERE id=%s",
                                    (max(0, row["credits_used"] - req.credits), row["id"]))
                    else:
                        cur.execute("INSERT INTO daily_credits (user_id, date_ist, credits_used) VALUES (%s,%s,%s)",
                                    (uid, today, -req.credits))
                    cur.execute("INSERT INTO credit_grants (user_id, credits, reason) VALUES (%s,%s,%s)",
                                (uid, req.credits, req.reason))
                    success.append(user["email"])
                except Exception:
                    failed.append(uid)
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "granted_to": len(success), "failed": len(failed),
            "message": f"{req.credits} credits granted to {len(success)} users."}


# ── Bulk toggle ──────────────────────────────────────────────────────────────
@admin_router.post("/bulk-toggle")
async def bulk_toggle(req: BulkToggleRequest, request: Request):
    require_admin(request)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            for uid in req.user_ids:
                try:
                    cur.execute("UPDATE users SET is_active = %s WHERE id = %s",
                                (1 if req.is_active else 0, uid))
                except Exception:
                    pass
        conn.commit()
    finally:
        conn.close()
    verb = "enabled" if req.is_active else "disabled"
    return {"status": "ok", "message": f"{len(req.user_ids)} users {verb}."}
