"""
auth_routes.py — TEJAS Auth + User API
Mount in main.py with:  app.include_router(auth_router)
"""
import re
import uuid
import os
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional

from auth import send_otp_email, login_with_otp, logout, get_current_user_from_token
from database import (
    get_credit_summary, deduct_credits as _db_deduct_credits,
    list_chat_sessions,
    get_chat_session, delete_chat_session, save_chat_session,
    _conn
)

# ── Profession options ────────────────────────────────────────────────────────
PROFESSION_OPTIONS = [
    "Chartered Accountant",
    "Advocate / Tax Lawyer",
    "Company Secretary",
    "Cost Accountant (CMA)",
    "Tax Consultant",
    "Finance Professional",
    "CA Student / Articleship",
    "Law Student",
    "Business Owner / Entrepreneur",
    "Individual / Self-filing",
    "Academic / Researcher",
    "Other",
]

# ── Intended use options ──────────────────────────────────────────────────────
USE_CASE_OPTIONS = [
    "Professional Tax Advisory",
    "Client Advisory & Filing",
    "Legal Research & Drafting",
    "Academic Research",
    "Personal Tax Planning",
    "Comparative Law Study (1961 vs 2025)",
    "Tax Litigation Support",
    "Learning & Study",
    "Business Compliance",
    "Other",
]


def _ensure_profile_columns():
    """Add profile columns to users table if they don't exist yet (safe migration)."""
    try:
        conn = _conn()
        with conn.cursor() as cur:
            for col, typedef in [
                ("full_name",    "TEXT DEFAULT ''"),
                ("profession",   "TEXT DEFAULT ''"),
                ("organisation", "TEXT DEFAULT ''"),
                ("use_case",     "TEXT DEFAULT ''"),
            ]:
                cur.execute(f"""
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {typedef}
                """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Auth] Profile column migration warning: {e}")


def _save_user_profile(user_id: int, full_name: str, profession: str,
                       organisation: str, use_case: str):
    """Persist profile fields for a user."""
    _ensure_profile_columns()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users
                SET full_name=%s, profession=%s, organisation=%s, use_case=%s
                WHERE id=%s
            """, (full_name.strip(), profession.strip(), organisation.strip(),
                  use_case.strip(), user_id))
        conn.commit()
    finally:
        conn.close()

# ── Credit deduction — called by case_law_routes.py ─────────────────────────
def deduct_credits(user_id: int, amount: int = 1) -> int:
    """
    Verify and deduct `amount` credits for `user_id`.
    Returns new credits_remaining on success.
    Raises HTTPException 402 if insufficient, 401 if user not found.

    Wraps database.deduct_credits() which returns (success: bool, remaining: int).
    """
    success, remaining = _db_deduct_credits(user_id=user_id, amount=amount)
    if not success:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. You have {remaining} credit{'s' if remaining != 1 else ''}, this action needs {amount}."
        )
    return remaining


auth_router = APIRouter(prefix="/auth", tags=["auth"])
chat_router = APIRouter(prefix="/chat", tags=["chat"])
_bearer     = HTTPBearer(auto_error=False)


# ── Email validator ───────────────────────────────────────────────────────────
def _valid_email(email: str) -> bool:
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email.strip()))


# ── Auth dependency — use in any protected route ──────────────────────────────
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = get_current_user_from_token(credentials.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    return user


# ══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

class SendOTPRequest(BaseModel):
    email: str

class VerifyOTPRequest(BaseModel):
    email:        str
    otp:          str
    # Optional profile fields — collected at registration in the login modal
    full_name:    Optional[str] = ""
    profession:   Optional[str] = ""
    organisation: Optional[str] = ""
    use_case:     Optional[str] = ""

class UpdateProfileRequest(BaseModel):
    full_name:    str = ""
    profession:   str = ""
    organisation: str = ""
    use_case:     str = ""

# ── Meta endpoint — return dropdown options for the login modal ───────────────
@auth_router.get("/profile-options")
async def profile_options():
    """Return valid dropdown choices for profession and use_case."""
    return {
        "professions": PROFESSION_OPTIONS,
        "use_cases":   USE_CASE_OPTIONS,
    }


@auth_router.post("/send-otp")
async def send_otp(req: SendOTPRequest):
    """Send a 6-digit OTP to the given email address."""
    email = req.email.strip().lower()
    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email address")

    success, message = send_otp_email(email)
    if not success:
        raise HTTPException(status_code=500, detail=message)

    return {"message": "OTP sent. Check your inbox (and spam folder)."}


@auth_router.post("/verify-otp")
async def verify_otp_endpoint(req: VerifyOTPRequest):
    """Verify OTP and return a JWT session token. Saves profile if provided."""
    email = req.email.strip().lower()
    otp   = req.otp.strip()

    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    if not otp.isdigit() or len(otp) != 6:
        raise HTTPException(status_code=400, detail="OTP must be 6 digits")

    success, message, data = login_with_otp(email, otp)
    if not success:
        raise HTTPException(status_code=401, detail=message)

    # Save profile fields if provided (new users fill these during sign-up)
    if any([req.full_name, req.profession, req.organisation, req.use_case]):
        _save_user_profile(
            user_id      = data["user"]["id"],
            full_name    = req.full_name    or "",
            profession   = req.profession   or "",
            organisation = req.organisation or "",
            use_case     = req.use_case     or "",
        )

    return {
        "token": data["token"],
        "user":  data["user"],
    }


@auth_router.post("/update-profile")
async def update_profile(req: UpdateProfileRequest,
                         user: dict = Depends(get_current_user)):
    """Update profile for the currently logged-in user."""
    if req.profession and req.profession not in PROFESSION_OPTIONS:
        raise HTTPException(status_code=400,
            detail=f"Invalid profession. Choose from: {', '.join(PROFESSION_OPTIONS)}")
    if req.use_case and req.use_case not in USE_CASE_OPTIONS:
        raise HTTPException(status_code=400,
            detail=f"Invalid use_case. Choose from: {', '.join(USE_CASE_OPTIONS)}")
    _save_user_profile(
        user_id      = user["user_id"],
        full_name    = req.full_name,
        profession   = req.profession,
        organisation = req.organisation,
        use_case     = req.use_case,
    )
    return {"saved": True, "message": "Profile updated successfully."}


@auth_router.post("/logout")
async def logout_endpoint(user: dict = Depends(get_current_user)):
    logout(user["token"])
    return {"message": "Logged out successfully"}


@auth_router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """Return current user profile + credit summary."""
    _ensure_profile_columns()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT full_name, profession, organisation, use_case
                FROM users WHERE id = %s
            """, (user["user_id"],))
            row = cur.fetchone()
    finally:
        conn.close()
    credits = get_credit_summary(user["user_id"])
    profile = dict(row) if row else {}
    return {
        "user": {
            "id":           user["user_id"],
            "email":        user["email"],
            "full_name":    profile.get("full_name",    ""),
            "profession":   profile.get("profession",   ""),
            "organisation": profile.get("organisation", ""),
            "use_case":     profile.get("use_case",     ""),
        },
        "credits": credits,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CHAT HISTORY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

class SaveChatRequest(BaseModel):
    session_id:   str | None = None   # None = new session
    section_2025: str        = ""
    section_1961: str        = ""
    title:        str        = ""
    messages:     list       = []


@chat_router.get("/history")
async def chat_history(user: dict = Depends(get_current_user)):
    """Return list of all chat sessions for the sidebar."""
    sessions = list_chat_sessions(user["user_id"], limit=50)
    return {"sessions": sessions}


@chat_router.get("/history/{session_id}")
async def get_chat(session_id: str, user: dict = Depends(get_current_user)):
    """Return full message history for a specific chat session."""
    session = get_chat_session(user["user_id"], session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


@chat_router.post("/history/save")
async def save_chat(req: SaveChatRequest, user: dict = Depends(get_current_user)):
    """Create or update a chat session. Returns the session_id."""
    session_id = req.session_id or str(uuid.uuid4())
    save_chat_session(
        user_id      = user["user_id"],
        session_id   = session_id,
        messages     = req.messages,
        section_2025 = req.section_2025,
        section_1961 = req.section_1961,
        title        = req.title,
    )
    return {"session_id": session_id, "saved": True}


@chat_router.delete("/history/{session_id}")
async def delete_chat(session_id: str, user: dict = Depends(get_current_user)):
    """Delete a specific chat session."""
    delete_chat_session(user["user_id"], session_id)
    return {"deleted": True}
