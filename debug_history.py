"""
debug_history.py — run this to check if chat history is saving correctly
Usage: python debug_history.py
"""
from database import init_db, save_chat_session, list_chat_sessions, get_credit_summary
import sqlite3, os
from pathlib import Path

DB_PATH = Path(__file__).parent / "tejas_users.db"

print(f"DB exists: {DB_PATH.exists()}")
print(f"DB path: {DB_PATH}")
print()

# ── Check what's in the DB ────────────────────────────────────────────────────
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

print("── USERS ────────────────────────────────")
users = conn.execute("SELECT * FROM users").fetchall()
if not users:
    print("  No users found — login hasn't created a user yet")
else:
    for u in users:
        print(f"  id={u['id']}  email={u['email']}  created={u['created_at']}")

print()
print("── SESSIONS ─────────────────────────────")
sessions = conn.execute("SELECT * FROM user_sessions").fetchall()
if not sessions:
    print("  No active sessions — user may not be logged in")
else:
    for s in sessions:
        print(f"  user_id={s['user_id']}  expires={s['expires_at']}  last_seen={s['last_seen']}")

print()
print("── CHAT HISTORY ─────────────────────────")
chats = conn.execute("SELECT * FROM chat_history ORDER BY updated_at DESC LIMIT 10").fetchall()
if not chats:
    print("  No chat history found")
    print("  → This means save_chat_session() is either not being called")
    print("    or is failing silently")
else:
    for c in chats:
        import json
        msgs = json.loads(c['messages'])
        print(f"  session={c['session_id'][:8]}...  user_id={c['user_id']}  "
              f"title={c['title'][:40]}  msgs={len(msgs)}  updated={c['updated_at']}")

print()
print("── DAILY CREDITS ────────────────────────")
credits = conn.execute("SELECT * FROM daily_credits ORDER BY id DESC LIMIT 5").fetchall()
if not credits:
    print("  No credit records — /compare hasn't been called with auth yet")
else:
    for cr in credits:
        print(f"  user_id={cr['user_id']}  date={cr['date_ist']}  used={cr['credits_used']}")

conn.close()

print()
print("── MANUAL SAVE TEST ─────────────────────")
if users:
    uid = users[0]['id']
    import uuid
    test_id = str(uuid.uuid4())
    try:
        save_chat_session(
            user_id=uid,
            session_id=test_id,
            messages=[{"role":"user","content":"test"},{"role":"assistant","content":"reply"}],
            section_2025="67",
            title="Test Session"
        )
        result = list_chat_sessions(uid)
        print(f"  ✅ Manual save worked — {len(result)} session(s) in DB for user {uid}")
    except Exception as e:
        print(f"  ❌ Manual save FAILED: {e}")
else:
    print("  Skipped — no users in DB yet (login first, then re-run)")