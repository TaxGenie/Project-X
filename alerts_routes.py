"""
alerts_routes.py — Smart Case Alerts + All-Judgments Digest Subscription API
Mount in main.py with:  app.include_router(alerts_router)

Section Alert endpoints  (keyword-based, per-user):
  GET    /alerts                     — list user's section alerts + digest status
  POST   /alerts                     — create a section alert
  DELETE /alerts/{id}                — delete a section alert

All-Judgments Digest endpoints  (TaxSutra-style broadcast):
  GET    /alerts/digest              — get current subscription status
  POST   /alerts/digest              — subscribe / update subscription
  DELETE /alerts/digest              — unsubscribe
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

from auth_routes import get_current_user
from database import (
    create_alert, list_alerts, delete_alert,
    get_digest_subscription, upsert_digest_subscription, cancel_digest_subscription,
)

alerts_router = APIRouter(prefix="/alerts", tags=["Alerts"])

VALID_COURTS = {
    "all", "itat", "itat-del", "itat-mum", "itat-bang",
    "supremecourt", "bombay", "delhi", "madras",
    "calcutta", "gujarat", "karnataka", "allahabad", "kerala",
}
VALID_ALERT_FREQS  = {"daily", "weekly", "instant"}
VALID_DIGEST_FREQS = {"daily", "weekly"}


class CreateAlertRequest(BaseModel):
    section_query: str
    court:         str = "all"
    frequency:     str = "weekly"

class DigestSubscribeRequest(BaseModel):
    courts:    List[str] = ["all"]
    frequency: str       = "daily"


# ── Section alerts ────────────────────────────────────────────────────────────

@alerts_router.get("")
async def get_alerts(user: dict = Depends(get_current_user)):
    alerts = list_alerts(user["user_id"])
    digest = get_digest_subscription(user["user_id"])
    return {"alerts": alerts, "digest": digest}


@alerts_router.post("")
async def add_alert(req: CreateAlertRequest, user: dict = Depends(get_current_user)):
    if not req.section_query.strip():
        raise HTTPException(status_code=400, detail="section_query cannot be empty")
    if req.court not in VALID_COURTS:
        raise HTTPException(status_code=400, detail=f"Invalid court.")
    if req.frequency not in VALID_ALERT_FREQS:
        raise HTTPException(status_code=400, detail=f"Invalid frequency.")
    existing = list_alerts(user["user_id"])
    if len(existing) >= 10:
        raise HTTPException(status_code=400, detail="Maximum 10 active alerts allowed.")
    alert = create_alert(user_id=user["user_id"], section_query=req.section_query,
                         court=req.court, frequency=req.frequency)
    return {"alert": alert, "message": "Alert created successfully"}


@alerts_router.delete("/{alert_id}")
async def remove_alert(alert_id: int, user: dict = Depends(get_current_user)):
    found = delete_alert(user_id=user["user_id"], alert_id=alert_id)
    if not found:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"deleted": True}


# ── All-judgments digest subscription ────────────────────────────────────────

@alerts_router.get("/digest")
async def get_digest(user: dict = Depends(get_current_user)):
    sub = get_digest_subscription(user["user_id"])
    return {"subscription": sub}


@alerts_router.post("/digest")
async def subscribe_digest(req: DigestSubscribeRequest, user: dict = Depends(get_current_user)):
    invalid = [c for c in req.courts if c not in VALID_COURTS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid court codes: {invalid}")
    if not req.courts:
        raise HTTPException(status_code=400, detail="Select at least one court")
    if req.frequency not in VALID_DIGEST_FREQS:
        raise HTTPException(status_code=400, detail="Digest frequency must be 'daily' or 'weekly'")
    sub = upsert_digest_subscription(user_id=user["user_id"], courts=req.courts, frequency=req.frequency)
    return {"subscription": sub, "message": "Subscription saved"}


@alerts_router.delete("/digest")
async def unsubscribe_digest(user: dict = Depends(get_current_user)):
    found = cancel_digest_subscription(user["user_id"])
    if not found:
        raise HTTPException(status_code=404, detail="No active subscription found")
    return {"unsubscribed": True}
