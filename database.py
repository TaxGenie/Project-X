"""
database.py — TEJAS User Database
PostgreSQL-backed. Tables: users, otp_sessions, user_sessions, daily_credits,
chat_history, user_alerts, digest_subscriptions
Run once at startup via init_db() — called automatically on import.
"""
import os
import json
import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor

# ── Credits config — edit here to change limits ──────────────────────────────
DAILY_CREDIT_LIMIT   = 20   # free credits per user per day
COST_KEY_SUMMARY     = 3    # generate_key_summary
COST_CHAT_MESSAGE    = 1    # each chat follow-up
COST_WORD_EXPORT     = 1    # Word document download

OTP_EXPIRY_MINUTES   = 10
SESSION_EXPIRY_DAYS  = 30


def _conn():
    return psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)


def init_db():
    """Create all tables if they don't exist. Safe to call multiple times."""
    sql = """
        CREATE TABLE IF NOT EXISTS users (
            id          SERIAL PRIMARY KEY,
            email       TEXT    NOT NULL UNIQUE,
            created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
            is_active   INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS otp_sessions (
            id          SERIAL PRIMARY KEY,
            email       TEXT    NOT NULL,
            otp         TEXT    NOT NULL,
            created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMP NOT NULL,
            is_used     INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS user_sessions (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            token       TEXT    NOT NULL UNIQUE,
            created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMP NOT NULL,
            last_seen   TIMESTAMP NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS daily_credits (
            id            SERIAL PRIMARY KEY,
            user_id       INTEGER NOT NULL REFERENCES users(id),
            date_ist      TEXT    NOT NULL,
            credits_used  INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, date_ist)
        );

        CREATE TABLE IF NOT EXISTS chat_history (
            id            SERIAL PRIMARY KEY,
            user_id       INTEGER NOT NULL REFERENCES users(id),
            session_id    TEXT    NOT NULL UNIQUE,
            section_2025  TEXT    NOT NULL DEFAULT '',
            section_1961  TEXT    NOT NULL DEFAULT '',
            title         TEXT    NOT NULL DEFAULT 'Untitled Chat',
            summary       TEXT    NOT NULL DEFAULT '',
            messages      TEXT    NOT NULL DEFAULT '[]',
            created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS user_alerts (
            id            SERIAL PRIMARY KEY,
            user_id       INTEGER NOT NULL REFERENCES users(id),
            section_query TEXT    NOT NULL,
            court         TEXT    NOT NULL DEFAULT 'all',
            frequency     TEXT    NOT NULL DEFAULT 'weekly',
            is_active     INTEGER NOT NULL DEFAULT 1,
            created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
            last_sent_at  TIMESTAMP DEFAULT NULL,
            last_sent_tids TEXT   DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS digest_subscriptions (
            id            SERIAL PRIMARY KEY,
            user_id       INTEGER NOT NULL REFERENCES users(id) UNIQUE,
            courts        TEXT    NOT NULL DEFAULT '["all"]',
            frequency     TEXT    NOT NULL DEFAULT 'daily',
            is_active     INTEGER NOT NULL DEFAULT 1,
            created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
            last_sent_at  TIMESTAMP DEFAULT NULL,
            last_sent_tids TEXT   DEFAULT '[]'
        );

        CREATE INDEX IF NOT EXISTS idx_chat_user     ON chat_history(user_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_otp_email     ON otp_sessions(email, is_used);
        CREATE INDEX IF NOT EXISTS idx_session_token ON user_sessions(token);
        CREATE INDEX IF NOT EXISTS idx_credits_user  ON daily_credits(user_id, date_ist);
        CREATE INDEX IF NOT EXISTS idx_digest_freq   ON digest_subscriptions(frequency, is_active);
        CREATE INDEX IF NOT EXISTS idx_alerts_user   ON user_alerts(user_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_alerts_freq   ON user_alerts(frequency, is_active);
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print("[TEJAS DB] PostgreSQL tables initialised ✅")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# USER OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_or_create_user(email: str) -> dict:
    """Return existing user or create new one. Always returns user dict."""
    email = email.strip().lower()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            if row:
                return dict(row)
            cur.execute("INSERT INTO users (email) VALUES (%s) RETURNING *", (email,))
            row = cur.fetchone()
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# OTP OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def save_otp(email: str, otp: str):
    """Save a new OTP, invalidating all previous OTPs for this email."""
    email = email.strip().lower()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE otp_sessions SET is_used = 1 WHERE email = %s", (email,))
            cur.execute("""
                INSERT INTO otp_sessions (email, otp, expires_at)
                VALUES (%s, %s, NOW() + INTERVAL '%s minutes')
            """ % ('%s', '%s', OTP_EXPIRY_MINUTES), (email, otp))
        conn.commit()
    finally:
        conn.close()


def verify_otp(email: str, otp: str) -> bool:
    """Check OTP validity and mark it used. Returns True if valid."""
    email = email.strip().lower()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM otp_sessions
                WHERE email = %s AND otp = %s AND is_used = 0
                  AND expires_at > NOW()
                ORDER BY id DESC LIMIT 1
            """, (email, otp))
            row = cur.fetchone()
            if not row:
                return False
            cur.execute("UPDATE otp_sessions SET is_used = 1 WHERE id = %s", (row["id"],))
        conn.commit()
        return True
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# SESSION OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def create_session(user_id: int, token: str):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_sessions (user_id, token, expires_at)
                VALUES (%s, %s, NOW() + INTERVAL '%s days')
            """ % ('%s', '%s', SESSION_EXPIRY_DAYS), (user_id, token))
        conn.commit()
    finally:
        conn.close()


def get_session(token: str) -> dict | None:
    """Return session + user if token is valid and not expired."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.user_id, s.expires_at, u.email, u.is_active
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token = %s
                  AND s.expires_at > NOW()
                  AND u.is_active = 1
            """, (token,))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                "UPDATE user_sessions SET last_seen = NOW() WHERE token = %s", (token,)
            )
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def delete_session(token: str):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_sessions WHERE token = %s", (token,))
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# CREDIT OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def _today_ist() -> str:
    """Return today's date in IST as YYYY-MM-DD."""
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%d")


def get_credits_remaining(user_id: int) -> int:
    today = _today_ist()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT credits_used FROM daily_credits WHERE user_id = %s AND date_ist = %s",
                (user_id, today)
            )
            row = cur.fetchone()
        used = row["credits_used"] if row else 0
        return max(0, DAILY_CREDIT_LIMIT - used)
    finally:
        conn.close()


def deduct_credits(user_id: int, amount: int) -> tuple[bool, int]:
    """
    Deduct `amount` credits. Returns (success, credits_remaining).
    Returns (False, remaining) if insufficient credits.
    """
    today = _today_ist()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT credits_used FROM daily_credits WHERE user_id = %s AND date_ist = %s",
                (user_id, today)
            )
            row = cur.fetchone()
            used = row["credits_used"] if row else 0
            remaining = DAILY_CREDIT_LIMIT - used

            if amount > remaining:
                return False, remaining

            if row:
                cur.execute(
                    "UPDATE daily_credits SET credits_used = credits_used + %s WHERE user_id = %s AND date_ist = %s",
                    (amount, user_id, today)
                )
            else:
                cur.execute(
                    "INSERT INTO daily_credits (user_id, date_ist, credits_used) VALUES (%s, %s, %s)",
                    (user_id, today, amount)
                )
        conn.commit()
        return True, remaining - amount
    finally:
        conn.close()


def get_credit_summary(user_id: int) -> dict:
    today = _today_ist()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT credits_used FROM daily_credits WHERE user_id = %s AND date_ist = %s",
                (user_id, today)
            )
            row = cur.fetchone()
        used = row["credits_used"] if row else 0
        return {
            "used":      used,
            "remaining": max(0, DAILY_CREDIT_LIMIT - used),
            "limit":     DAILY_CREDIT_LIMIT,
            "resets":    "midnight IST",
            "costs": {
                "key_summary":  COST_KEY_SUMMARY,
                "chat_message": COST_CHAT_MESSAGE,
                "word_export":  COST_WORD_EXPORT,
            }
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# CHAT HISTORY OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def save_chat_session(user_id: int, session_id: str, messages: list,
                      section_2025: str = "", section_1961: str = "",
                      title: str = "", summary: str = ""):
    """
    Create or update a chat session.
    - CREATE: saves title + summary + messages
    - UPDATE: appends new messages only; updates summary if provided; never overwrites title
    """
    if not title:
        first_user = next((m["content"] for m in messages if m.get("role") == "user"), "")
        title = first_user[:60] + ("…" if len(first_user) > 60 else "") or "Untitled Chat"

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, messages FROM chat_history WHERE session_id = %s AND user_id = %s",
                (session_id, user_id)
            )
            existing = cur.fetchone()

            if existing:
                try:
                    existing_msgs = json.loads(existing["messages"])
                except Exception:
                    existing_msgs = []
                new_msgs = messages[len(existing_msgs):]
                merged = existing_msgs + new_msgs

                if summary:
                    cur.execute("""
                        UPDATE chat_history
                        SET messages = %s, summary = %s, updated_at = NOW()
                        WHERE session_id = %s AND user_id = %s
                    """, (json.dumps(merged, ensure_ascii=False), summary, session_id, user_id))
                else:
                    cur.execute("""
                        UPDATE chat_history
                        SET messages = %s, updated_at = NOW()
                        WHERE session_id = %s AND user_id = %s
                    """, (json.dumps(merged, ensure_ascii=False), session_id, user_id))
            else:
                cur.execute("""
                    INSERT INTO chat_history
                        (user_id, session_id, section_2025, section_1961, title, summary, messages)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (user_id, session_id, section_2025, section_1961, title,
                      summary, json.dumps(messages, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def get_chat_session(user_id: int, session_id: str) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM chat_history WHERE session_id = %s AND user_id = %s",
                (session_id, user_id)
            )
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["messages"] = json.loads(d["messages"])
        return d
    finally:
        conn.close()


def list_chat_sessions(user_id: int, limit: int = 50) -> list:
    """Return recent chat sessions (no messages — just metadata for sidebar)."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT session_id, section_2025, section_1961, title, created_at, updated_at
                FROM chat_history
                WHERE user_id = %s
                ORDER BY updated_at DESC
                LIMIT %s
            """, (user_id, limit))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_chat_session(user_id: int, session_id: str):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chat_history WHERE session_id = %s AND user_id = %s",
                (session_id, user_id)
            )
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# ALERT OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def create_alert(user_id: int, section_query: str, court: str, frequency: str) -> dict:
    """Create a new alert. Returns the created alert as a dict."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_alerts (user_id, section_query, court, frequency)
                VALUES (%s, %s, %s, %s) RETURNING *
            """, (user_id, section_query.strip(), court, frequency))
            row = cur.fetchone()
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def list_alerts(user_id: int) -> list:
    """Return all active alerts for a user."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM user_alerts
                WHERE user_id = %s AND is_active = 1
                ORDER BY created_at DESC
            """, (user_id,))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_alert(user_id: int, alert_id: int) -> bool:
    """Soft-delete an alert (mark inactive). Returns True if found."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_alerts SET is_active = 0 WHERE id = %s AND user_id = %s",
                (alert_id, user_id)
            )
            found = cur.rowcount > 0
        conn.commit()
        return found
    finally:
        conn.close()


def get_due_alerts(frequency: str) -> list:
    """
    Return all active alerts of a given frequency that are due to be sent.
      - 'daily'   : last_sent_at is NULL or was before today IST
      - 'weekly'  : last_sent_at is NULL or was more than 7 days ago
      - 'instant' : last_sent_at is NULL or was more than 1 hour ago
    """
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)

    if frequency == "daily":
        cutoff = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    elif frequency == "weekly":
        cutoff = now_ist - timedelta(days=7)
    else:  # instant
        cutoff = now_ist - timedelta(hours=1)

    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.*, u.email FROM user_alerts a
                JOIN users u ON u.id = a.user_id
                WHERE a.frequency = %s AND a.is_active = 1
                  AND (a.last_sent_at IS NULL OR a.last_sent_at < %s)
            """, (frequency, cutoff_str))
            rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["last_sent_tids"] = json.loads(d.get("last_sent_tids") or "[]")
            except Exception:
                d["last_sent_tids"] = []
            results.append(d)
        return results
    finally:
        conn.close()


def mark_alert_sent(alert_id: int, sent_tids: list):
    """Update last_sent_at and record which tids were included in the digest."""
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now_str = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_sent_tids FROM user_alerts WHERE id = %s", (alert_id,)
            )
            existing = cur.fetchone()
            try:
                prev_tids = json.loads((existing["last_sent_tids"] if existing else None) or "[]")
            except Exception:
                prev_tids = []
            merged = list(dict.fromkeys(prev_tids + sent_tids))[-100:]
            cur.execute("""
                UPDATE user_alerts
                SET last_sent_at = %s, last_sent_tids = %s
                WHERE id = %s
            """, (now_str, json.dumps(merged), alert_id))
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# DIGEST SUBSCRIPTION OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_digest_subscription(user_id: int) -> dict | None:
    """Return the user's digest subscription, or None if not subscribed."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM digest_subscriptions WHERE user_id = %s AND is_active = 1",
                (user_id,)
            )
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["courts"] = json.loads(d["courts"])
        except Exception:
            d["courts"] = ["all"]
        try:
            d["last_sent_tids"] = json.loads(d["last_sent_tids"] or "[]")
        except Exception:
            d["last_sent_tids"] = []
        return d
    finally:
        conn.close()


def upsert_digest_subscription(user_id: int, courts: list, frequency: str) -> dict:
    """Create or update the user's digest subscription."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO digest_subscriptions (user_id, courts, frequency)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    courts    = EXCLUDED.courts,
                    frequency = EXCLUDED.frequency,
                    is_active = 1
                RETURNING *
            """, (user_id, json.dumps(courts), frequency))
            row = cur.fetchone()
        conn.commit()
        d = dict(row)
        try:
            d["courts"] = json.loads(d["courts"])
        except Exception:
            d["courts"] = courts
        return d
    finally:
        conn.close()


def cancel_digest_subscription(user_id: int) -> bool:
    """Unsubscribe (soft-delete). Returns True if a subscription existed."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE digest_subscriptions SET is_active = 0 WHERE user_id = %s AND is_active = 1",
                (user_id,)
            )
            found = cur.rowcount > 0
        conn.commit()
        return found
    finally:
        conn.close()


def get_due_digest_subscriptions(frequency: str) -> list:
    """Return all active digest subscriptions of a given frequency that are due."""
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)

    if frequency == "weekly":
        cutoff = now_ist - timedelta(days=7)
    else:  # daily
        cutoff = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)

    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ds.*, u.email FROM digest_subscriptions ds
                JOIN users u ON u.id = ds.user_id
                WHERE ds.frequency = %s AND ds.is_active = 1
                  AND (ds.last_sent_at IS NULL OR ds.last_sent_at < %s)
            """, (frequency, cutoff_str))
            rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["courts"] = json.loads(d["courts"])
            except Exception:
                d["courts"] = ["all"]
            try:
                d["last_sent_tids"] = json.loads(d["last_sent_tids"] or "[]")
            except Exception:
                d["last_sent_tids"] = []
            results.append(d)
        return results
    finally:
        conn.close()


def mark_digest_sent(subscription_id: int, sent_tids: list):
    """Update last_sent_at and accumulate sent tids (rolling 500)."""
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now_str = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_sent_tids FROM digest_subscriptions WHERE id = %s",
                (subscription_id,)
            )
            existing = cur.fetchone()
            try:
                prev = json.loads((existing["last_sent_tids"] if existing else None) or "[]")
            except Exception:
                prev = []
            merged = list(dict.fromkeys(prev + sent_tids))[-500:]
            cur.execute("""
                UPDATE digest_subscriptions
                SET last_sent_at = %s, last_sent_tids = %s
                WHERE id = %s
            """, (now_str, json.dumps(merged), subscription_id))
        conn.commit()
    finally:
        conn.close()


# ── Auto-init on import ───────────────────────────────────────────────────────
init_db()
