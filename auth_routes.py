"""
auth_routes.py — TEJAS Auth + User API
Mount in main.py with:  app.include_router(auth_router)
"""
import re
import uuid
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from auth import send_otp_email, login_with_otp, logout, get_current_user_from_token
from database import (
    get_credit_summary, list_chat_sessions,
    get_chat_session, delete_chat_session, save_chat_session
)

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
    email: str
    otp:   str


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
    """Verify OTP and return a JWT session token."""
    email = req.email.strip().lower()
    otp   = req.otp.strip()

    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    if not otp.isdigit() or len(otp) != 6:
        raise HTTPException(status_code=400, detail="OTP must be 6 digits")

    success, message, data = login_with_otp(email, otp)
    if not success:
        raise HTTPException(status_code=401, detail=message)

    return {
        "token": data["token"],
        "user":  data["user"],
    }


@auth_router.post("/logout")
async def logout_endpoint(user: dict = Depends(get_current_user)):
    logout(user["token"])
    return {"message": "Logged out successfully"}


@auth_router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """Return current user profile + credit summary."""
    credits = get_credit_summary(user["user_id"])
    return {
        "user": {
            "id":    user["user_id"],
            "email": user["email"],
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