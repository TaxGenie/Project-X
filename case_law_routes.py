"""
case_law_routes.py  —  Tax Cookies Case Law Engine
═══════════════════════════════════════════════════
Endpoints:
  GET  /case-law              — IK structured search  (batched multi-page, page_size aware)
  POST /case-law/ai           — Perplexity broad search (page + page_size aware)
  POST /case-law/doc/{tid}    — Full judgment text (POST, not GET — IK requires this)
  POST /case-law/brief/{tid}  — Tax Cookies AI Case Brief
  POST /case-law/chat         — Chat with a judgment
"""

import os, re, json, asyncio
import httpx
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional, List

from auth_routes import get_current_user

case_law_router = APIRouter(tags=["Case Law"])

IK_BASE    = "https://api.indiankanoon.org"
IK_TIMEOUT  = 15.0    # slightly more generous for batched concurrent calls
DOC_TIMEOUT = 30.0

# IK returns exactly 10 results per pagenum — this is a hard API limit
IK_PAGE_SIZE = 10

# ── helpers ──────────────────────────────────────────────────────────────────
def _ik_token():   return os.getenv("INDIANKANOON_API_TOKEN", "")
def _pkey():       return os.getenv("PERPLEXITY_API_KEY", "")
def _ik_headers(): return {"Authorization": f"Token {_ik_token()}", "Accept": "application/json"}

def _strip_html(text: str) -> str:
    """Remove all HTML tags and decode common entities."""
    text = re.sub(r'<[^>]+>', ' ', text or "")
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', text).strip()

def _strip_html_for_brief(text: str) -> str:
    """Strip HTML and also remove garbled Devanagari transliteration lines (IK artifact).
    These lines look like: 'vk;dj vihyh; vf/kdj.k] t;iqj' — phonetic Hindi encoding.
    """
    text = _strip_html(text)
    # Filter out lines with dense semicolons (Devanagari transliteration artifact)
    clean_lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        semi_count = line.count(';')
        word_count = len(line.split())
        # Skip if: short line, many semicolons relative to words (transliteration pattern)
        if semi_count > 0 and word_count > 0 and semi_count * 3 > word_count and len(line) < 100:
            continue
        clean_lines.append(line)
    return ' '.join(clean_lines).strip()

def _clean_doc_html(html: str) -> str:
    """Clean IK enriched HTML for safe inline rendering."""
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<iframe[^>]*>.*?</iframe>',  '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>',    '', html, flags=re.DOTALL | re.IGNORECASE)
    # Rewrite relative /doc/ links to full IK URLs
    html = re.sub(r'href="(/doc/\d+/)"',
                  r'href="https://indiankanoon.org\1" target="_blank" rel="noopener"', html)
    return html

def _court_label(docsource: str) -> str:
    src = (docsource or "").lower()
    if "income tax appellate" in src or "itat" in src:
        return "ITAT"
    if "supreme" in src:
        return "Supreme Court"
    for hc in ["bombay","delhi","madras","calcutta","kolkata","gujarat",
               "allahabad","kerala","karnataka","gauhati","punjab",
               "andhra","rajasthan","patna","orissa","himachal"]:
        if hc in src:
            return f"{hc.title()} HC"
    if "high court" in src:
        return "High Court"
    return _strip_html(docsource) or "Tribunal"

def _shape_doc(doc: dict) -> dict:
    tid = doc.get("tid")
    return {
        "tid"      : tid,
        "title"    : _strip_html(doc.get("title", "Untitled")),
        "headline" : _strip_html(doc.get("headline", "")),
        "court"    : _court_label(doc.get("docsource", "")),
        "docsource": _strip_html(doc.get("docsource", "")),
        "date"     : doc.get("publishdate", ""),
        "citations": doc.get("numcites", 0),
        "url"      : f"https://indiankanoon.org/doc/{tid}/",
        "source"   : "indiankanoon",
    }

# ── Credit system ────────────────────────────────────────────────────────────
# Try to import deduct_credits from auth_routes (the same function used by /compare and /chat).
# If auth_routes doesn't export it yet, we define a no-op fallback so the endpoints
# still work — just without credit deduction until you add the export.
try:
    from auth_routes import deduct_credits as _auth_deduct_credits
    _HAS_DEDUCT = True
except ImportError:
    _HAS_DEDUCT = False

def _check_deduct_credits(user: dict, amount: int = 1) -> int:
    """
    Deduct `amount` credits via auth_routes.deduct_credits().
    Returns new credits_remaining on success.
    Raises HTTP 402 if insufficient, HTTP 401 if user not found.
    Falls back gracefully if deduct_credits is not yet exported from auth_routes.
    """
    if not _HAS_DEDUCT:
        # Fallback: credits not deducted — add deduct_credits export to auth_routes.py
        # to enable credit enforcement for case law features.
        return -1   # -1 signals "unknown / not enforced"

    uid = user.get("id") or user.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="User not authenticated")

    try:
        new_remaining = _auth_deduct_credits(user_id=uid, amount=amount)
        return new_remaining
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Credit error: {exc}")


# ── Court filter → IK doctypes: operator (goes inside formInput) ──────────────
# CONFIRMED from IK official docs & live URLs: the doctypes filter is a search
# operator embedded in formInput, e.g. formInput="section 68 income tax doctypes:itat"
# Exact doctype identifiers per IK documentation:
#   Tribunals : itat, aptel, drat, cat, cegat, stt, consumer, cerc, cic, ...
#   SC        : supremecourt
#   High Courts: delhi, bombay, kolkata, chennai, allahabad, andhra, chattisgarh,
#                gauhati, jammu, srinagar, kerala, lucknow, orissa, uttaranchal,
#                gujarat, himachal_pradesh, jharkhand, karnataka, madhyapradesh,
#                patna, punjab, rajasthan, sikkim, meghalaya
#   Aggregators: highcourts (all HCs), tribunals (all tribunals), judgments (SC+HC)
COURT_DOCTYPES: dict[str, str] = {
    "all"         : "",
    "itat"        : " doctypes:itat",
    "supremecourt": " doctypes:supremecourt",
    "highcourts"  : " doctypes:highcourts",
    "bombay"      : " doctypes:bombay",
    "delhi"       : " doctypes:delhi",
    "madras"      : " doctypes:chennai",        # IK uses "chennai" for Madras HC
    "calcutta"    : " doctypes:kolkata",         # IK uses "kolkata" for Calcutta HC
    "gujarat"     : " doctypes:gujarat",
    "karnataka"   : " doctypes:karnataka",
    "allahabad"   : " doctypes:allahabad",
    "kerala"      : " doctypes:kerala",
}


def _passes_court_filter(doc: dict, court_filter: str) -> bool:
    """
    Lightweight post-filter on the normalized court label as a safety net.
    Only active for specific single-court selections. Never drops for 'all'/'highcourts'.
    """
    if court_filter in ("all", "highcourts"):
        return True
    court_label = doc.get("court", "").lower()
    checks = {
        "itat"        : lambda s: "itat" in s,
        "supremecourt": lambda s: "supreme" in s,
        "bombay"      : lambda s: "bombay" in s,
        "delhi"       : lambda s: "delhi" in s,
        "madras"      : lambda s: "madras" in s or "chennai" in s,
        "calcutta"    : lambda s: "calcutta" in s or "kolkata" in s,
        "gujarat"     : lambda s: "gujarat" in s,
        "karnataka"   : lambda s: "karnataka" in s,
        "allahabad"   : lambda s: "allahabad" in s,
        "kerala"      : lambda s: "kerala" in s,
    }
    check = checks.get(court_filter)
    return check(court_label) if check else True

async def _call_perplexity(prompt: str, max_tokens: int = 2500, timeout: float = 35.0) -> str:
    pkey = _pkey()
    if not pkey:
        raise HTTPException(status_code=503, detail="PERPLEXITY_API_KEY not configured")
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {pkey}", "Content-Type": "application/json"},
            json={
                "model"      : "sonar",
                "messages"   : [{"role": "user", "content": prompt}],
                "max_tokens" : max_tokens,
                "temperature": 0.15,
            },
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Perplexity error: {resp.status_code}")
    return resp.json()["choices"][0]["message"]["content"]


async def _fetch_ik_page(client: httpx.AsyncClient, payload: dict, pagenum: int) -> dict:
    """
    Fetch a single IK search page.
    Returns the parsed JSON dict, or an empty dict on any error
    (so one bad page doesn't kill the whole batched request).
    """
    p = {**payload, "pagenum": str(pagenum)}
    try:
        resp = await client.post(f"{IK_BASE}/search/", data=p, headers=_ik_headers())
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# GET /case-law  — Indian Kanoon search  (batched multi-page, page_size aware)
# ══════════════════════════════════════════════════════════════════════════════
@case_law_router.get("/case-law")
async def search_case_law(
    query        : str = Query(..., min_length=1),
    court_filter : str = Query(default="all"),
    year_from    : str = Query(default=""),
    year_to      : str = Query(default=""),
    title_only   : str = Query(default=""),
    page         : int = Query(default=0, ge=0),
    page_size    : int = Query(default=20, ge=1, le=50),
    user         : dict = Depends(get_current_user),
):
    """
    IK returns exactly 10 results per pagenum (hard API limit).
    To honour page_size > 10 we batch multiple IK pagenums concurrently.

    Examples:
      Frontend page=0, page_size=20  →  fetch IK pagenums 0 + 1   (results  1–20)
      Frontend page=1, page_size=20  →  fetch IK pagenums 2 + 3   (results 21–40)
      Frontend page=2, page_size=20  →  fetch IK pagenums 4 + 5   (results 41–60)
    """
    if not _ik_token():
        raise HTTPException(status_code=503, detail="INDIANKANOON_API_NOT_CONFIGURED")

    # Safety cap
    page_size = min(page_size, 50)

    # How many IK pagenums needed to fill one frontend page? (ceiling division)
    ik_pages_needed = -(-page_size // IK_PAGE_SIZE)          # e.g. 20 → 2, 30 → 3

    # Which IK pagenums to fetch?
    ik_start    = page * ik_pages_needed
    ik_pagenums = list(range(ik_start, ik_start + ik_pages_needed))

    # Build the shared IK search payload
    has_section = bool(re.search(
        r'section\s*\d+|s\.\s*\d+|\d{2,3}[A-Z]?\b', query, re.IGNORECASE
    ))
    base     = query if ("income tax" in query.lower() or has_section) else f"{query} income tax"
    # doctypes: operator is embedded inside formInput — confirmed correct IK API approach
    ik_query = base + COURT_DOCTYPES.get(court_filter, "")

    payload: dict = {"formInput": ik_query}
    if year_from:         payload["fromdate"] = f"1-1-{year_from}"
    if year_to:           payload["todate"]   = f"31-12-{year_to}"
    if title_only == "1": payload["title"]    = base

    try:
        async with httpx.AsyncClient(timeout=IK_TIMEOUT, follow_redirects=True) as client:
            # Fetch all needed IK pages concurrently
            tasks      = [_fetch_ik_page(client, payload, pn) for pn in ik_pagenums]
            pages_data = await asyncio.gather(*tasks)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Indian Kanoon timed out.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Search error: {exc}")

    # Require at least the first page to have succeeded
    if not pages_data or not pages_data[0]:
        raise HTTPException(status_code=502, detail="Indian Kanoon returned no data.")

    # Merge results — deduplicate by tid AND apply client-side court filter as safety net
    seen_tids: set  = set()
    merged_docs: list = []
    total = int(pages_data[0].get("total", 0))   # IK total is consistent across pages

    for page_data in pages_data:
        for doc in page_data.get("docs", []):
            tid = doc.get("tid")
            if tid and tid not in seen_tids:
                shaped = _shape_doc(doc)
                # Drop any result that doesn't pass the court filter
                # (safety net in case IK doctypes filter leaks cross-court results)
                if _passes_court_filter(shaped, court_filter):
                    seen_tids.add(tid)
                    merged_docs.append(shaped)

    # Trim to exactly page_size (last batch may return slightly fewer anyway)
    merged_docs = merged_docs[:page_size]

    return {
        "results"     : merged_docs,
        "total"       : total,
        "page"        : page,
        "page_size"   : page_size,
        "query"       : ik_query,
        "court_filter": court_filter,
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /case-law/ai  — Perplexity broad search (page + page_size aware)
# ══════════════════════════════════════════════════════════════════════════════
class AICaseLawRequest(BaseModel):
    query        : str
    court_filter : str  = "all"
    year_from    : str  = ""
    year_to      : str  = ""
    page         : int  = 0     # which batch of results (0-indexed)
    page_size    : int  = 20    # how many results per batch
    scenario     : bool = False  # True when called from Scenario mode


@case_law_router.post("/case-law/ai")
async def ai_case_law_search(req: AICaseLawRequest, user: dict = Depends(get_current_user)):

    court_hint = {
        "all"         : "Supreme Court, High Courts, and ITAT",
        "itat"        : "Income Tax Appellate Tribunal (ITAT) only",
        "supremecourt": "Supreme Court of India only",
        "highcourts"  : "Indian High Courts only",
        "bombay"      : "Bombay High Court only",
        "delhi"       : "Delhi High Court only",
        "madras"      : "Madras High Court only",
        "calcutta"    : "Calcutta High Court only",
    }.get(req.court_filter, "Indian courts")

    year_hint = ""
    if req.year_from and req.year_to:
        year_hint = f" decided between {req.year_from}–{req.year_to}"
    elif req.year_from:
        year_hint = f" decided after {req.year_from}"
    elif req.year_to:
        year_hint = f" decided before {req.year_to}"

    # Cap page_size; Perplexity context limits how many cases can be properly detailed
    n = min(max(req.page_size, 5), 30)

    # For page > 0, instruct Perplexity to provide a different batch of cases
    page_instruction = ""
    if req.page > 0:
        skip = req.page * n
        page_instruction = (
            f"\nIMPORTANT: This is page {req.page + 1} of results. "
            f"Skip the {skip} most prominent/well-known cases and provide the NEXT {n} "
            f"less-commonly cited but equally valid judgments on this topic."
        )

    # ── JSON schema block reused by both prompts ──
    json_schema = """[
  {
    "title": "Full party names e.g. CIT v. Lovely Exports (P) Ltd",
    "court": "e.g. Supreme Court / ITAT Mumbai / Bombay HC",
    "date": "Year or DD-MM-YYYY",
    "citation": "e.g. [2008] 216 CTR 195 (SC) — leave blank if unsure",
    "summary": "<<SUMMARY_INSTRUCTION>>",
    "section": "e.g. Section 68, 148, 54EC",
    "url": "Direct indiankanoon URL if found, else empty",
    "source": "indiankanoon / taxpundit / itatonline / taxmann / court"
  }
]"""

    if req.scenario:
        summary_instr = (
            "4–5 sentences: what was the dispute, what was held, the ratio, "
            "AND specifically how this helps in the client's situation described above"
        )
        prompt = f"""You are a senior Indian income tax advocate doing case law research for a CA.

CLIENT SITUATION:
{req.query}

Identify the key legal issues in this situation and find the {n} most relevant Indian income tax
judgments that would SUPPORT the taxpayer's position.
Court restriction: {court_hint}{year_hint}{page_instruction}

Search indiankanoon.org, taxpundit.org, itatonline.org, taxmann.com.

CRITICAL RULES:
- Only include cases you are CERTAIN exist. Never invent citations.
- For each case explain SPECIFICALLY how it helps in this client's situation.
- If unsure of a citation string, leave the citation field empty.
- Prioritise cases directly on point over tangentially related ones.

Return ONLY a valid JSON array, no markdown fences, no explanation:
{json_schema.replace("<<SUMMARY_INSTRUCTION>>", summary_instr)}"""

    else:
        summary_instr = (
            "3–4 sentences: what was the dispute, what was held, what is the ratio, "
            "why it matters to practitioners"
        )
        prompt = f"""You are a senior Indian income tax advocate doing case law research for a CA.

Find the {n} most authoritative Indian income tax judgments on: "{req.query}"
Court restriction: {court_hint}{year_hint}{page_instruction}

Search indiankanoon.org, taxpundit.org, itatonline.org, taxmann.com, litigationhub.in.

CRITICAL RULES:
- Only include cases you are CERTAIN exist. Never invent citations.
- If unsure of a citation string, leave the citation field empty.
- Prioritise landmark/frequently-cited cases over obscure ones.
- For ITAT cases include the bench city (e.g. "ITAT Mumbai").

Return ONLY a valid JSON array, no markdown fences, no explanation:
{json_schema.replace("<<SUMMARY_INSTRUCTION>>", summary_instr)}"""

    # Scale max_tokens with number of cases requested
    max_tok = min(500 + n * 200, 4000)

    # ── Deduct 2 credits before calling Perplexity ──
    credits_remaining = _check_deduct_credits(user, amount=2)

    try:
        raw = await _call_perplexity(prompt, max_tokens=max_tok, timeout=50.0)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI search error: {exc}")

    try:
        clean = re.sub(r'```(?:json)?|```', '', raw).strip()
        s = clean.find('[')
        e = clean.rfind(']') + 1
        if s == -1:
            raise ValueError("No JSON array found in Perplexity response")
        cases = json.loads(clean[s:e])
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Parse error: {exc}. Raw response: {raw[:300]}"
        )

    results = [{
        "tid"      : None,
        "title"    : _strip_html(c.get("title", "Untitled")),
        "headline" : _strip_html(c.get("summary", "")),
        "court"    : _strip_html(c.get("court", "")),
        "docsource": _strip_html(c.get("court", "")),
        "date"     : c.get("date", ""),
        "citations": c.get("citation", ""),
        "section"  : c.get("section", ""),
        "url"      : c.get("url", ""),
        "source"   : c.get("source", "ai"),
    } for c in cases]

    # Signal that more pages are always available from AI search
    # estimated_total is always at least one page ahead so the frontend shows Load More
    estimated_total = (req.page + 2) * n

    return {
        "results"           : results,
        "total"             : estimated_total,
        "page"              : req.page,
        "page_size"         : n,
        "query"             : req.query,
        "mode"              : "scenario" if req.scenario else "ai",
        "credits_remaining" : credits_remaining,
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /case-law/doc/{tid}  — Full judgment text (IK requires POST, not GET)
# ══════════════════════════════════════════════════════════════════════════════
@case_law_router.post("/case-law/doc/{tid}")
async def get_case_law_doc(tid: int, user: dict = Depends(get_current_user)):
    if not _ik_token():
        raise HTTPException(status_code=503, detail="INDIANKANOON_API_NOT_CONFIGURED")
    try:
        async with httpx.AsyncClient(timeout=DOC_TIMEOUT, follow_redirects=True) as client:
            resp = await client.post(f"{IK_BASE}/doc/{tid}/", headers=_ik_headers())
        if resp.status_code == 401:
            raise HTTPException(status_code=503, detail="INDIANKANOON_TOKEN_INVALID")
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Judgment not found.")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Indian Kanoon returned HTTP {resp.status_code}")
        data = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Indian Kanoon timed out.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Doc fetch error: {exc}")

    return {
        "tid"      : tid,
        "title"    : _strip_html(data.get("title", "Untitled")),
        "doc"      : _clean_doc_html(data.get("doc", "")),
        "docsource": _strip_html(data.get("docsource", "")),
        "date"     : data.get("publishdate", ""),
        "citations": data.get("numcites", 0),
        "bench"    : _strip_html(data.get("bench", "")),
        "url"      : f"https://indiankanoon.org/doc/{tid}/",
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /case-law/brief/{tid}  — Tax Cookies AI Case Brief
# Fetches full judgment from IK then generates structured brief via Perplexity
# ══════════════════════════════════════════════════════════════════════════════
class BriefRequest(BaseModel):
    title     : str = ""
    docsource : str = ""
    date      : str = ""

@case_law_router.post("/case-law/brief/{tid}")
async def generate_case_brief(tid: int, req: BriefRequest, user: dict = Depends(get_current_user)):
    # Step 1: fetch full judgment text
    if not _ik_token():
        raise HTTPException(status_code=503, detail="INDIANKANOON_API_NOT_CONFIGURED")
    try:
        async with httpx.AsyncClient(timeout=DOC_TIMEOUT, follow_redirects=True) as client:
            resp = await client.post(f"{IK_BASE}/doc/{tid}/", headers=_ik_headers())
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"IK returned HTTP {resp.status_code}")
        doc_data = resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Brief fetch error: {exc}")

    # Extract plain text from judgment HTML (truncate to ~6000 chars for LLM)
    raw_doc = _strip_html_for_brief(doc_data.get("doc", ""))
    title   = _strip_html(doc_data.get("title", req.title or "Unknown"))
    court   = _strip_html(doc_data.get("docsource", req.docsource or ""))
    date    = doc_data.get("publishdate", req.date or "")
    numcite = doc_data.get("numcites", 0)
    bench   = _strip_html(doc_data.get("bench", ""))
    judgment_text = raw_doc[:6000] if len(raw_doc) > 6000 else raw_doc

    if not judgment_text.strip():
        raise HTTPException(status_code=422, detail="No judgment text available to brief.")

    prompt = f"""You are a senior Indian tax counsel generating a professional case brief for Chartered Accountants.

CASE DETAILS:
Title: {title}
Court: {court}
Date: {date}
Bench: {bench}

JUDGMENT TEXT (may be truncated — focus on the substantive legal analysis, ignore procedural headers):
{judgment_text}

Generate a structured Tax Cookies Case Brief in strict JSON format. Return ONLY the JSON object — no markdown fences, no explanation, no preamble:
{{
  "title": "Clean, readable case name (e.g. 'ABC Pvt Ltd v. ITO [ITAT Mumbai 2022]') — remove HTML artifacts",
  "court": "{court}",
  "date": "{date}",
  "bench": "{bench}",
  "sections_1961": ["List Income Tax Act 1961 sections actually decided on, e.g. 'Section 68', 'Section 147'"],
  "sections_2025": ["Corresponding 2025 Act sections if identifiable, else empty list"],
  "act_bridge": "1–2 sentences: whether this case law survives under the new Income Tax Act 2025 and why (e.g. provision retained / restructured / omitted)",
  "issue": "Single crisp sentence: the precise legal question the court/tribunal decided",
  "facts": ["3–5 key facts as short bullet strings — be specific about amounts, AY, nature of transaction"],
  "held": "One sentence: who won (assessee/revenue) and what was ordered",
  "ratio": "2–3 sentences: the legal principle/test established — write as a citable proposition starting with 'The court held that...' or 'The ratio is...'",
  "practitioner_note": "2–3 actionable sentences: when to cite this case, what arguments it supports, any limitations or cautions",
  "good_law_signal": "positive",
  "good_law_reason": "One sentence: whether this judgment is widely followed, distinguished in later cases, or overruled",
  "keywords": ["5–8 searchable keywords e.g. unexplained cash credit, burden of proof, section 68, creditworthiness, ITAT Mumbai"]
}}

IMPORTANT: 
- "good_law_signal" must be exactly one of: "positive", "caution", or "negative"
- All text values must be clean English — no HTML tags, no Devanagari transliteration
- If judgment text is unclear, make your best inference from the case title and court"""

    # ── Deduct 2 credits before calling Perplexity ──
    brief_credits_remaining = _check_deduct_credits(user, amount=2)

    try:
        raw = await _call_perplexity(prompt, max_tokens=2000, timeout=40.0)
        clean = re.sub(r'```(?:json)?|```', '', raw).strip()
        s = clean.find('{'); e = clean.rfind('}') + 1
        brief = json.loads(clean[s:e])
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Brief parse error: {exc}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Brief generation error: {exc}")

    brief["tid"]               = tid
    brief["numcites"]          = numcite
    brief["url"]               = f"https://indiankanoon.org/doc/{tid}/"
    brief["credits_remaining"] = brief_credits_remaining
    return brief


# ══════════════════════════════════════════════════════════════════════════════
# POST /case-law/chat  — Chat with a judgment
# ══════════════════════════════════════════════════════════════════════════════
class ChatWithCaseRequest(BaseModel):
    tid      : Optional[int]  = None
    title    : str            = ""
    doc_text : str            = ""   # frontend sends already-fetched plain text
    message  : str            = ""
    history  : List[dict]     = []

@case_law_router.post("/case-law/chat")
async def chat_with_case(req: ChatWithCaseRequest, user: dict = Depends(get_current_user)):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    judgment_context = (req.doc_text[:5000] if req.doc_text else
                        f"Judgment: {req.title}. (Full text not available in context.)")

    system = f"""You are an expert Indian income tax counsel assisting a Chartered Accountant.

The CA is asking questions about this specific court judgment:
TITLE: {req.title}
TID: {req.tid}

JUDGMENT TEXT (may be truncated):
{judgment_context}

Rules:
- Answer ONLY based on this judgment and your legal knowledge
- Always cite the specific paragraph or observation from the judgment when possible
- If the judgment text is truncated and you cannot answer precisely, say so
- Use plain language a CA can use directly in client advice or submissions
- Format key propositions as "The court held that..." or "The ratio is..."
- When asked about 2025 Act applicability, reason from first principles about whether the provision has changed"""

    messages = [{"role": "system", "content": system}]
    for h in req.history[-6:]:
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": req.message})

    pkey = _pkey()
    if not pkey:
        raise HTTPException(status_code=503, detail="PERPLEXITY_API_KEY not configured")

    # ── Deduct 1 credit before calling Perplexity ──
    chat_credits_remaining = _check_deduct_credits(user, amount=1)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {pkey}", "Content-Type": "application/json"},
                json={
                    "model"      : "sonar",
                    "messages"   : messages,
                    "max_tokens" : 1200,
                    "temperature": 0.2,
                },
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Perplexity error: {resp.status_code}")
        reply = resp.json()["choices"][0]["message"]["content"]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chat error: {exc}")

    return {"reply": reply, "credits_remaining": chat_credits_remaining}
