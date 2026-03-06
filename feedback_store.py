"""
feedback_store.py
Stores thumbs-up / thumbs-down feedback and surfaces patterns
to help improve section_hints.json over time.

Storage: feedback_log.json  (auto-created, human-readable)
"""
import json, os, datetime
from pathlib import Path

FEEDBACK_FILE = Path(__file__).parent / "feedback_log.json"


def _load() -> list:
    if FEEDBACK_FILE.exists():
        try:
            return json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(log: list):
    FEEDBACK_FILE.write_text(
        json.dumps(log, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def record_feedback(
    query: str,
    section_1961: str,
    section_2025: str,
    rating: str,          # "up" or "down"
    comment: str = ""
):
    """Append one feedback record. Called from main.py."""
    log = _load()
    log.append({
        "ts"          : datetime.datetime.now().isoformat(timespec="seconds"),
        "query"       : query,
        "section_1961": section_1961,
        "section_2025": section_2025,
        "rating"      : rating,       # "up" or "down"
        "comment"     : comment,
    })
    _save(log)
    return {"status": "saved", "total_entries": len(log)}


def get_summary() -> dict:
    """
    Returns a summary useful for the /feedback-summary admin endpoint.
    Groups by section, counts up/down, flags sections with >1 thumbs-down.
    """
    log = _load()
    if not log:
        return {"total": 0, "sections": {}}

    sections: dict = {}
    for entry in log:
        key = entry.get("section_1961", "unknown").upper()
        if key not in sections:
            sections[key] = {"up": 0, "down": 0, "comments": []}
        sections[key][entry["rating"]] += 1
        if entry.get("comment"):
            sections[key]["comments"].append(entry["comment"])

    # Flag sections needing attention (more downs than ups, or 2+ downs)
    for sec, data in sections.items():
        data["needs_attention"] = data["down"] >= 2 or data["down"] > data["up"]

    return {
        "total"   : len(log),
        "up"      : sum(e["rating"] == "up"   for e in log),
        "down"    : sum(e["rating"] == "down" for e in log),
        "sections": sections,
    }
