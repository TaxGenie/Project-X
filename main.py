from fastapi import FastAPI, HTTPException, Request, Header, Depends, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
import uuid

from section_mapping import get_section_mapping
from llm_engine import generate_key_summary, generate_chat_response
from word_export import export_to_word
from auth_routes import auth_router, chat_router, get_current_user
from auth import get_current_user_from_token
from database import (
    deduct_credits, get_credit_summary, save_chat_session,
    COST_KEY_SUMMARY, COST_CHAT_MESSAGE, COST_WORD_EXPORT
)

from admin_routes import admin_router

try:
    from llm_engine import reload_hints
except ImportError:
    def reload_hints(): return 0

try:
    from feedback_store import record_feedback, get_summary
    _feedback_enabled = True
except ImportError:
    _feedback_enabled = False
    def record_feedback(**kwargs): return {"status": "feedback_store not found"}
    def get_summary(): return {"error": "feedback_store.py missing"}

app = FastAPI(title="TEJAS — CA Income Tax Comparison Tool")

# ── CORS — allows frontend JS to call the API ─────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Register auth + chat history + admin routers ─────────────────────────────
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(admin_router)

_last_context: dict = {}   # keyed by user_id → their last compare context
LAST_GENERATED_FILE = None


# ── Request models ────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):
    query: str
    mode:  Optional[str] = None

class FeedbackRequest(BaseModel):
    rating:  str
    comment: Optional[str] = ""

class ChatRequest(BaseModel):
    message:              str
    history:              Optional[list] = []
    context:              Optional[str]  = ""
    current_section_2025: Optional[str]  = ""
    current_section_1961: Optional[str]  = ""
    session_id:           Optional[str]  = None   # persists chat across reloads


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ══════════════════════════════════════════════════════════════════════════════
# /compare  — Key Summary generation  (costs COST_KEY_SUMMARY credits)
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/compare")
def compare_sections(
    request: QueryRequest,
    user: dict = Depends(get_current_user),
):
    global LAST_GENERATED_FILE, _last_context

    # ── Credit check ──────────────────────────────────────────────────────
    ok, remaining = deduct_credits(user["user_id"], COST_KEY_SUMMARY)
    if not ok:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Not enough credits. You have {remaining} credit(s) left today. "
                f"Credits reset at midnight IST."
            )
        )

    # ── Section lookup ────────────────────────────────────────────────────
    section_map = get_section_mapping(request.query)
    if not section_map:
        raise HTTPException(
            status_code=404,
            detail="Section not found. Try a number like 192, 80C, or a topic like 'capital gain'."
        )

    # ── Generate summary ──────────────────────────────────────────────────
    sections = generate_key_summary(request.query, section_map)
    raw      = sections.get("raw", "")

    # ── Word export ───────────────────────────────────────────────────────
    filename            = export_to_word(raw, query=request.query)
    LAST_GENERATED_FILE = filename

    # ── Store context for feedback + chat ─────────────────────────────────
    _last_context[user["user_id"]] = {
        "query"       : request.query,
        "section_1961": section_map.get("section_number_1961", ""),
        "section_2025": section_map.get("section_number_2025", ""),
    }

    # ── Save search to chat history so it appears in sidebar ─────────────
    new_session_id = str(uuid.uuid4())
    try:
        save_chat_session(
            user_id      = user["user_id"],
            session_id   = new_session_id,
            messages     = [],
            section_2025 = section_map.get("section_number_2025", ""),
            section_1961 = section_map.get("section_number_1961", ""),
            title        = request.query[:60],
            summary      = raw,   # ← save the full key summary text
        )
        print(f"[TEJAS] Search saved to history: {request.query[:40]}")
    except Exception as e:
        print(f"[TEJAS] History save failed: {e}")

    credits = get_credit_summary(user["user_id"])

    return {
        "sec1"               : sections.get("sec1", ""),
        "sec2"               : sections.get("sec2", ""),
        "sec3"               : sections.get("sec3", ""),
        "sec4"               : sections.get("sec4", ""),
        "section_number_2025": section_map.get("section_number_2025", ""),
        "session_id"         : new_session_id,   # frontend uses this for subsequent chat
        "credits"            : credits,
    }


# ══════════════════════════════════════════════════════════════════════════════
# /chat  — Follow-up chat  (costs COST_CHAT_MESSAGE credits)
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/chat")
def chat_followup(
    req:  ChatRequest,
    user: dict = Depends(get_current_user),
):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    # ── Credit check ──────────────────────────────────────────────────────
    ok, remaining = deduct_credits(user["user_id"], COST_CHAT_MESSAGE)
    if not ok:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Not enough credits. You have {remaining} credit(s) left today. "
                f"Credits reset at midnight IST."
            )
        )

    # ── Generate reply ────────────────────────────────────────────────────
    reply = generate_chat_response(
        message              = req.message,
        history              = req.history or [],
        context              = req.context or "",
        current_section_2025 = req.current_section_2025 or "",
    )

    # ── Persist chat — append new turn to existing session ───────────────
    session_id = req.session_id or str(uuid.uuid4())
    # Build full message list: all prior history + new turn
    full_messages = list(req.history or []) + [
        {"role": "user",      "content": req.message},
        {"role": "assistant", "content": reply},
    ]
    try:
        save_chat_session(
            user_id      = user["user_id"],
            session_id   = session_id,
            messages     = full_messages,
            section_2025 = req.current_section_2025 or "",
            section_1961 = req.current_section_1961 or "",
        )
        print(f"[TEJAS] Chat saved: session={session_id[:8]} msgs={len(full_messages)}")
    except Exception as e:
        print(f"[TEJAS] ❌ Chat save FAILED: {e}")

    return {
        "reply"             : reply,
        "session_id"        : session_id,
        "credits_remaining" : remaining,
    }


# ══════════════════════════════════════════════════════════════════════════════
# /download-word  — Word export download  (costs COST_WORD_EXPORT credits)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/download-word")
def download_word(token: str = Query(default="")):
    global LAST_GENERATED_FILE

    # Token comes as query param because window.location.href can't set headers
    user = get_current_user_from_token(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated. Please sign in.")

    ok, remaining = deduct_credits(user["user_id"], COST_WORD_EXPORT)
    if not ok:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Not enough credits. You have {remaining} credit(s) left today. "
                f"Credits reset at midnight IST."
            )
        )

    if LAST_GENERATED_FILE and os.path.exists(LAST_GENERATED_FILE):
        return FileResponse(
            LAST_GENERATED_FILE,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="TEJAS_Summary.docx",
            headers={"X-Credits-Remaining": str(remaining)},
        )
    raise HTTPException(status_code=404, detail="No file generated yet.")


# ══════════════════════════════════════════════════════════════════════════════
# FEEDBACK  (no credit cost)
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/feedback")
def submit_feedback(
    req:  FeedbackRequest,
    user: dict = Depends(get_current_user),
):
    ctx = _last_context.get(user["user_id"], {})
    if not ctx.get("query"):
        raise HTTPException(status_code=400, detail="No comparison generated yet. Run a search first.")
    if req.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'.")
    return record_feedback(
        query        = ctx["query"],
        section_1961 = ctx["section_1961"],
        section_2025 = ctx["section_2025"],
        rating       = req.rating,
        comment      = req.comment or "",
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN / DEBUG  (no auth — protect these if deploying publicly)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/feedback-summary")
def feedback_summary():
    return get_summary()

@app.post("/reload-hints")
def reload_hints_endpoint():
    return {"status": "reloaded", "sections_loaded": reload_hints()}

@app.get("/debug-last-output")
def debug_last_output(user: dict = Depends(get_current_user)):
    ctx = _last_context.get(user["user_id"], {})
    if not ctx:
        return {"error": "No output yet for this user. Run a query first."}
    return {
        "query"       : ctx.get("query"),
        "section_1961": ctx.get("section_1961"),
        "section_2025": ctx.get("section_2025"),
    }

@app.get("/credits")
def credits_info(user: dict = Depends(get_current_user)):
    """Quick credit check endpoint for the frontend navbar."""
    return get_credit_summary(user["user_id"])