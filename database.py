"""
database.py — TEJAS User Database
SQLite-backed. Tables: users, otp_sessions, user_sessions, daily_credits, chat_history
Run once at startup via init_db() — called automatically on import.
"""
import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent / "tejas_users.db"

# ── Credits config — edit here to change limits ──────────────────────────────
DAILY_CREDIT_LIMIT   = 20   # free credits per user per day
COST_KEY_SUMMARY     = 3    # generate_key_summary
COST_CHAT_MESSAGE    = 1    # each chat follow-up
COST_WORD_EXPORT     = 1    # Word document download

OTP_EXPIRY_MINUTES   = 10
SESSION_EXPIRY_DAYS  = 30


def _conn():
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")   # safe for concurrent reads
    return c


def init_db():
    """Create all tables if they don't exist. Safe to call multiple times."""
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            is_active   INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS otp_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT    NOT NULL COLLATE NOCASE,
            otp         TEXT    NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            expires_at  TEXT    NOT NULL,
            is_used     INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS user_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            token       TEXT    NOT NULL UNIQUE,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            expires_at  TEXT    NOT NULL,
            last_seen   TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS daily_credits (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(id),
            date_ist      TEXT    NOT NULL,   -- YYYY-MM-DD in IST
            credits_used  INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, date_ist)
        );

        CREATE TABLE IF NOT EXISTS chat_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(id),
            session_id    TEXT    NOT NULL UNIQUE,
            section_2025  TEXT    NOT NULL DEFAULT '',
            section_1961  TEXT    NOT NULL DEFAULT '',
            title         TEXT    NOT NULL DEFAULT 'Untitled Chat',
            summary       TEXT    NOT NULL DEFAULT '',   -- key summary (sec3) HTML/markdown
            messages      TEXT    NOT NULL DEFAULT '[]',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );
        -- Add summary column if upgrading from older DB
        CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_history(user_id, updated_at);
        PRAGMA journal_mode=WAL;

        CREATE INDEX IF NOT EXISTS idx_otp_email      ON otp_sessions(email, is_used);
        CREATE INDEX IF NOT EXISTS idx_session_token  ON user_sessions(token);
        CREATE INDEX IF NOT EXISTS idx_chat_user      ON chat_history(user_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_credits_user   ON daily_credits(user_id, date_ist);
        """)
    print(f"[TEJAS DB] Initialised at {DB_PATH}")


# ══════════════════════════════════════════════════════════════════════════════
# USER OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_or_create_user(email: str) -> dict:
    """Return existing user or create new one. Always returns user dict."""
    email = email.strip().lower()
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return dict(row)
        c.execute("INSERT INTO users (email) VALUES (?)", (email,))
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row)


def get_user_by_id(user_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


# ══════════════════════════════════════════════════════════════════════════════
# OTP OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def save_otp(email: str, otp: str):
    """Save a new OTP, invalidating all previous OTPs for this email."""
    email = email.strip().lower()
    with _conn() as c:
        # Invalidate old OTPs
        c.execute("UPDATE otp_sessions SET is_used = 1 WHERE email = ?", (email,))
        c.execute("""
            INSERT INTO otp_sessions (email, otp, expires_at)
            VALUES (?, ?, datetime('now', 'localtime', '+{} minutes'))
        """.format(OTP_EXPIRY_MINUTES), (email, otp))


def verify_otp(email: str, otp: str) -> bool:
    """Check OTP validity and mark it used. Returns True if valid."""
    email = email.strip().lower()
    with _conn() as c:
        row = c.execute("""
            SELECT id FROM otp_sessions
            WHERE email = ? AND otp = ? AND is_used = 0
              AND expires_at > datetime('now','localtime')
            ORDER BY id DESC LIMIT 1
        """, (email, otp)).fetchone()
        if not row:
            return False
        c.execute("UPDATE otp_sessions SET is_used = 1 WHERE id = ?", (row["id"],))
        return True


# ══════════════════════════════════════════════════════════════════════════════
# SESSION OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def create_session(user_id: int, token: str):
    with _conn() as c:
        c.execute("""
            INSERT INTO user_sessions (user_id, token, expires_at)
            VALUES (?, ?, datetime('now','localtime', '+{} days'))
        """.format(SESSION_EXPIRY_DAYS), (user_id, token))


def get_session(token: str) -> dict | None:
    """Return session + user if token is valid and not expired."""
    with _conn() as c:
        row = c.execute("""
            SELECT s.user_id, s.expires_at, u.email, u.is_active
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
              AND s.expires_at > datetime('now','localtime')
              AND u.is_active = 1
        """, (token,)).fetchone()
        if not row:
            return None
        # Touch last_seen
        c.execute("UPDATE user_sessions SET last_seen = datetime('now','localtime') WHERE token = ?", (token,))
        return dict(row)


def delete_session(token: str):
    with _conn() as c:
        c.execute("DELETE FROM user_sessions WHERE token = ?", (token,))


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
    with _conn() as c:
        row = c.execute(
            "SELECT credits_used FROM daily_credits WHERE user_id = ? AND date_ist = ?",
            (user_id, today)
        ).fetchone()
        used = row["credits_used"] if row else 0
        return max(0, DAILY_CREDIT_LIMIT - used)


def deduct_credits(user_id: int, amount: int) -> tuple[bool, int]:
    """
    Deduct `amount` credits. Returns (success, credits_remaining).
    Returns (False, remaining) if insufficient credits.
    """
    today = _today_ist()
    with _conn() as c:
        row = c.execute(
            "SELECT credits_used FROM daily_credits WHERE user_id = ? AND date_ist = ?",
            (user_id, today)
        ).fetchone()
        used = row["credits_used"] if row else 0
        remaining = DAILY_CREDIT_LIMIT - used

        if amount > remaining:
            return False, remaining

        if row:
            c.execute(
                "UPDATE daily_credits SET credits_used = credits_used + ? WHERE user_id = ? AND date_ist = ?",
                (amount, user_id, today)
            )
        else:
            c.execute(
                "INSERT INTO daily_credits (user_id, date_ist, credits_used) VALUES (?, ?, ?)",
                (user_id, today, amount)
            )
        return True, remaining - amount


def get_credit_summary(user_id: int) -> dict:
    today = _today_ist()
    with _conn() as c:
        row = c.execute(
            "SELECT credits_used FROM daily_credits WHERE user_id = ? AND date_ist = ?",
            (user_id, today)
        ).fetchone()
        used = row["credits_used"] if row else 0
        return {
            "used":      used,
            "remaining": max(0, DAILY_CREDIT_LIMIT - used),
            "limit":     DAILY_CREDIT_LIMIT,
            "resets":    "midnight IST",
            "costs": {
                "key_summary":   COST_KEY_SUMMARY,
                "chat_message":  COST_CHAT_MESSAGE,
                "word_export":   COST_WORD_EXPORT,
            }
        }


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
    import json
    if not title:
        first_user = next((m["content"] for m in messages if m.get("role") == "user"), "")
        title = first_user[:60] + ("…" if len(first_user) > 60 else "") or "Untitled Chat"

    with _conn() as c:
        # Ensure summary column exists (safe migration for older DBs)
        cols = [r[1] for r in c.execute("PRAGMA table_info(chat_history)").fetchall()]
        if "summary" not in cols:
            c.execute("ALTER TABLE chat_history ADD COLUMN summary TEXT NOT NULL DEFAULT ''")

        existing = c.execute(
            "SELECT id, messages FROM chat_history WHERE session_id = ? AND user_id = ?",
            (session_id, user_id)
        ).fetchone()

        if existing:
            try:
                existing_msgs = json.loads(existing["messages"])
            except Exception:
                existing_msgs = []
            new_msgs = messages[len(existing_msgs):]
            merged   = existing_msgs + new_msgs

            # Update summary only if a new one is provided
            if summary:
                c.execute("""
                    UPDATE chat_history
                    SET messages = ?, summary = ?, updated_at = datetime('now','localtime')
                    WHERE session_id = ? AND user_id = ?
                """, (json.dumps(merged, ensure_ascii=False), summary, session_id, user_id))
            else:
                c.execute("""
                    UPDATE chat_history
                    SET messages = ?, updated_at = datetime('now','localtime')
                    WHERE session_id = ? AND user_id = ?
                """, (json.dumps(merged, ensure_ascii=False), session_id, user_id))
        else:
            c.execute("""
                INSERT INTO chat_history
                    (user_id, session_id, section_2025, section_1961, title, summary, messages)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, session_id, section_2025, section_1961, title,
                  summary, json.dumps(messages, ensure_ascii=False)))


def get_chat_session(user_id: int, session_id: str) -> dict | None:
    import json
    with _conn() as c:
        # Ensure summary column exists
        cols = [r[1] for r in c.execute("PRAGMA table_info(chat_history)").fetchall()]
        if "summary" not in cols:
            c.execute("ALTER TABLE chat_history ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
        row = c.execute(
            "SELECT * FROM chat_history WHERE session_id = ? AND user_id = ?",
            (session_id, user_id)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["messages"] = json.loads(d["messages"])
        return d


def list_chat_sessions(user_id: int, limit: int = 50) -> list:
    """Return recent chat sessions (no messages — just metadata for sidebar)."""
    with _conn() as c:
        rows = c.execute("""
            SELECT session_id, section_2025, section_1961, title, created_at, updated_at
            FROM chat_history
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()
        return [dict(r) for r in rows]


def delete_chat_session(user_id: int, session_id: str):
    with _conn() as c:
        c.execute(
            "DELETE FROM chat_history WHERE session_id = ? AND user_id = ?",
            (session_id, user_id)
        )


# ── Auto-init on import ───────────────────────────────────────────────────────
init_db()