"""
llm_engine.py  — TEJAS Comparison Engine
Incorporates:
  Option 2 — Section-specific prompt hints (section_hints.json)
  Option 1 — Feedback loop (feedback_store.py + /feedback endpoint in main.py)
"""
import json, re
from pathlib import Path
from openai import OpenAI
from config import PERPLEXITY_API_KEY, MODEL_NAME
from pdf_extractor import get_both_sections_smart

client = OpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url="https://api.perplexity.ai"
)

# ── Load section hints once at startup ────────────────────────────────────────
_HINTS_FILE = Path(__file__).parent / "section_hints.json"
_HINTS: dict = {}

def _load_hints():
    global _HINTS
    if _HINTS_FILE.exists():
        try:
            raw = json.loads(_HINTS_FILE.read_text(encoding="utf-8"))
            _HINTS = {k: v for k, v in raw.items() if not k.startswith("_")}
        except Exception as e:
            print(f"[TEJAS] Warning: could not load section_hints.json — {e}")
            _HINTS = {}

_load_hints()


def _get_hint(section_1961: str) -> str:
    key = section_1961.lower().replace(" ", "")
    entry = _HINTS.get(key) or _HINTS.get(key.replace("-", ""))
    if entry and isinstance(entry, dict):
        return entry.get("hint", "")
    return ""


def reload_hints():
    _load_hints()
    return len(_HINTS)


# ── Parser for key_summary — full text goes into sec3 ─────────────────────────
def _parse_key_summary(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(t.split("\n")[1:])
    if t.endswith("```"):
        t = "\n".join(t.split("\n")[:-1])
    pat = re.compile(r'===\s*SECTION\s*3[^=]*===\s*', re.IGNORECASE)
    t = pat.sub("", t).strip()
    return {"sec1": "", "sec2": "", "sec3": t, "sec4": ""}


# ── Shared context builder ─────────────────────────────────────────────────────
def _build_context(section_map: dict):
    sec_1961      = section_map.get("section_number_1961", "")
    sec_2025      = section_map.get("section_number_2025", "")
    sec_name_1961 = section_map.get("section_1961", f"Section {sec_1961}")
    sec_name_2025 = section_map.get("section_2025", f"Section {sec_2025}")
    text_1961, text_2025 = get_both_sections_smart(sec_1961, sec_2025)
    is_tds = "[FROM 2025 ACT — Section 393" in text_2025
    section_hint = _get_hint(sec_1961)
    return sec_1961, sec_2025, sec_name_1961, sec_name_2025, text_1961, text_2025, is_tds, section_hint


def _hint_block(sec_name_1961, section_hint):
    if not section_hint:
        return ""
    return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION-SPECIFIC GUIDANCE FOR {sec_name_1961.upper()}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{section_hint}
"""


def _tds_block(sec_name_1961, is_tds):
    if not is_tds:
        return ""
    return f"""
CRITICAL STRUCTURAL NOTE:
The 2025 Act abolished all individual TDS sections (193, 194, 194A, 194B, 194C, 194H,
194I, 194J, 194N, 194S, 195 etc.) and merged them into ONE consolidated table under
Section 393. The text below for the 2025 Act is the EXACT rows from that table that
correspond to {sec_name_1961}. When comparing, always cite the specific Sl. No. from
Section 393 (e.g. "Section 393, Table, Sl. No. 1(ii)") rather than just "Section 393".
"""


# ══════════════════════════════════════════════════════════════════════════════
# KEY SUMMARY — Plain-English explanation with worked example
# ══════════════════════════════════════════════════════════════════════════════
def generate_key_summary(query: str, section_map: dict) -> dict:
    """
    Detailed plain-English summary with worked example.
    Returns {sec1:'', sec2:'', sec3: <full summary>, sec4:'', raw}
    """
    sec_1961, sec_2025, sec_name_1961, sec_name_2025, \
        text_1961, text_2025, is_tds, section_hint = _build_context(section_map)

    hint_block = _hint_block(sec_name_1961, section_hint)
    tds_note   = _tds_block(sec_name_1961, is_tds)

    prompt = f"""You are a senior Chartered Accountant with 25 years of experience in Indian direct tax law. \
A client — a business owner or salaried professional with no legal background — has asked you to explain \
{sec_name_2025} of the Income Tax Act 2025 in plain language. Write as if you are sitting across the table \
from them. Be thorough, friendly, and concrete. Do not mention the section number anywhere in your explanation. \
Focus purely on helping the client understand what it means, when it applies, who it affects, and what they \
need to do. Avoid legal jargon. If a technical term must be used, explain it immediately in simple words. \
Do not quote the law verbatim and do not refer to clauses or sub-sections. Every number, rate, threshold, \
and condition must be taken verbatim from the source text below — never guess or use general knowledge. \
End with a short practical takeaway summarizing what the client should remember.

CRITICAL RULE — DO NOT ASK QUESTIONS: You have been given the exact source text of the section below. \
Use it. Do not ask the user to clarify which section they mean. Do not ask for confirmation. \
Do not say you need more information. Simply produce the explanation based on the source text provided. \
If the source text covers multiple sub-topics, cover all of them in your response.

{tds_note}{hint_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SOURCE TEXT — INCOME TAX ACT 2025
{sec_name_2025}
(Note: [TABLE]...[/TABLE] blocks contain pipe-separated structured data — read every row carefully)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{text_2025}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CORRESPONDING 1961 ACT TEXT (for context only — do not focus on differences)
{sec_name_1961}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{text_1961}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSTRUCTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Write in plain English. No legal jargon without explanation.
- Cover EVERY sub-section, proviso, explanation, definition, formula, exception, and condition from the source text.
- If the section contains 10 sub-sections, there must be at least 10 clearly separated explanatory bullets.
- Do not summarise multiple sub-sections into one bullet.
- Include every rate (%) and threshold (Rs. amount) exactly as stated in the source.
- The worked example must use realistic Indian figures and walk through the maths step by step.
- Use the EXACT output format below. Do not add extra headers or change the marker text.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — FOLLOW EXACTLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

=== SECTION: KEY SUMMARY ===
[1-2 sentences. Provide the corresponding Section number under the Income-tax Act, 2025 and clearly state which Section of the Income-tax Act, 1961 it has been transformed or renumbered from.]

**What this provision does**
[3–4 sentences. Explain the purpose and who it affects in everyday language.]

**Who it applies to**
[Be specific — individual / HUF / company / partnership / deductor / resident / non-resident. State who is included and who is excluded.]

**The rules explained simply**
[Cover every sub-section and proviso as a separate bullet. For each rule: state what it means, who it affects, and the exact Rs. amount or % from the text. Do not merge rules — if there are 8 sub-sections, there are 8+ bullets.]

**Key thresholds and rates at a glance**
[A clean bullet list: each threshold or rate on its own line with the exact figure from the source text.]

**What happens if you don't comply**
[Consequences: interest, penalty, prosecution — with the specific section references and amounts if mentioned in the source text. If not mentioned, say so.]

The following worked examples are purely illustrative and are intended only to explain the operation of this specific section in a simplified manner. The calculations and assumptions used are limited to the scope of this provision and may not consider the interaction with other sections, exemptions, deductions, or overriding provisions of the Income-tax Act, 2025 and related rules.

Accordingly, in real-life situations, the actual tax treatment may differ when the provision is read in conjunction with other applicable sections of the Act.

**Worked example**
[A detailed, realistic numerical example using Indian names and figures (e.g. Rajesh runs a trading business, Priya is a salaried employee, Neha is a House property owner, Rahul is a investor earning capital gains, Arjun is a Investor earning passive income like interest, dividend, etc, Arun is earning income from providing professional services). Walk through EVERY calculation step. Show the maths. Make it long enough to cover edge cases if they exist in the source text — aim for at least 3–4 sub-scenarios if the provision has multiple rates or thresholds.]
{"**Note on 2025 Act structure**" + chr(10) + f"This provision was formerly {sec_name_1961} — a standalone section in the Income Tax Act 1961. In the 2025 Act it is now part of Section 393's consolidated TDS table. Practically, this means all TDS compliance (certificates, returns, challan codes) now references Section 393 rather than the old section number." if is_tds else ""}
"""

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior Chartered Accountant explaining Indian tax law to a non-expert client. "
                    "Be thorough, friendly, and precise. Follow all formatting instructions exactly. "
                    "Every number must come from the source text — never guess. "
                    "IMPORTANT: Never ask clarifying questions. Never ask the user to confirm which section "
                    "they mean. The source text has been provided — use it and produce the full explanation immediately."
                )
            },
            {"role": "user", "content": prompt}
        ],
        temperature=0.01,
        max_tokens=6000
    )
    raw = response.choices[0].message.content
    sections = _parse_key_summary(raw)
    sections["raw"] = raw
    return sections


# ── Legacy alias — for word_export.py and any other callers ───────────────────
def generate_comparison(query: str, section_map: dict) -> dict:
    return generate_key_summary(query, section_map)


# ══════════════════════════════════════════════════════════════════════════════
# CHAT — Follow-up questions about a Key Summary
# ══════════════════════════════════════════════════════════════════════════════
def generate_chat_response(message: str, history: list, context: str,
                           current_section_2025: str = "") -> str:
    """
    Answers a follow-up question grounded entirely in the 2025 Act PDF.

    Context injection order (highest → lowest priority):
      1. Full-text concept search across entire PDF   ← NEW primary engine
      2. Definitions chapter (pages 109-115) targeted by question keywords
      3. Raw text of the current section
      4. Plain-English key summary (secondary / already rendered to user)
      5. Related sections from section mapping (existing scorer)
    """
    from pdf_extractor import extract_section_2025, search_definitions_2025, search_pdf_for_concept
    try:
        from pdf_extractor import search_related_2025
    except ImportError:
        search_related_2025 = lambda **kw: ""

    # ── 1. Full-text concept search over entire 2025 Act PDF ──────────────
    # This is the primary engine. It finds any concept anywhere in the PDF —
    # holding period in Section 2, exemptions in Section 67-91, TDS in 393 etc.
    concept_text = ""
    try:
        concept_text = search_pdf_for_concept(
            question=message,
            max_results=4,
            context_chars_per_hit=1500,
        )
        if concept_text:
            print(f"[TEJAS Chat] Concept search: {len(concept_text)} chars "
                  f"across top-4 matching pages")
        else:
            print(f"[TEJAS Chat] Concept search: no hits for '{message[:60]}'")
    except Exception as e:
        print(f"[TEJAS Chat] search_pdf_for_concept error: {e}")

    # ── 2. Definitions chapter — always inject full pages 109-115 ────────
    # No keyword filtering: statutes say "held for 24 months" not "holding period",
    # "not more than" not "limit" — filtering always misses the exact clause.
    definitions_text = ""
    try:
        definitions_text = search_definitions_2025(context_chars=8000)
        if definitions_text:
            print(f"[TEJAS Chat] Definitions fetch: {len(definitions_text)} chars")
    except Exception as e:
        print(f"[TEJAS Chat] search_definitions_2025 error: {e}")

    # ── 3. Raw PDF text for the current section ────────────────────────────
    current_raw_text = ""
    if current_section_2025:
        try:
            raw = extract_section_2025(current_section_2025)
            if raw and not raw.startswith("["):
                current_raw_text = raw[:4000]
                print(f"[TEJAS Chat] Current section {current_section_2025}: "
                      f"{len(current_raw_text)} chars")
        except Exception as e:
            print(f"[TEJAS Chat] extract_section_2025 error: {e}")

    # ── 4. Related sections from section mapping ───────────────────────────
    related_pdf_text = ""
    try:
        related_pdf_text = search_related_2025(
            question=message,
            current_section_2025=current_section_2025,
            max_sections=2,
            max_chars_per_section=1500,
        )
        if related_pdf_text:
            print(f"[TEJAS Chat] Related sections: {len(related_pdf_text)} chars")
    except Exception as e:
        print(f"[TEJAS Chat] search_related_2025 error: {e}")

    # ── 5. System prompt ───────────────────────────────────────────────────
    system_prompt = (
        "You are a senior Chartered Accountant with 25 years of experience in Indian direct tax law. "
        "A user is asking follow-up questions about the Income Tax Act 2025.\n\n"
        "=== ABSOLUTE RULES — NO EXCEPTIONS ===\n"
        "1. USE ONLY the PDF text provided below. NEVER use training data for specific figures, "
        "   rates, thresholds, month counts, or conditions. Your training data contains the OLD "
        "   1961 Act rules — they are WRONG for 2025. Ignore all prior knowledge.\n"
        "   Examples of what NOT to use from training: 36-month holding period (abolished), "
        "   old indexation rules, old TDS thresholds — all may be changed.\n"
        "2. The 2025 Act text IS provided below across multiple sections. Read every block "
        "   carefully. NEVER say 'you haven't shared the text' or ask the user to paste anything.\n"
        "3. If a specific figure is genuinely absent from ALL provided text blocks, say: "
        "   'I don't see this specific detail in the sections loaded — try searching for it "
        "   directly in TEJAS.' Do NOT substitute training data as a guess.\n"
        "4. Always cite which section/page/clause the answer came from.\n\n"
        "=== FORMAT ===\n"
        "Plain English, **bold** key terms, bullets for lists, numbered steps for calculations. "
        "Show full calculation workings with Indian names and rupee figures."
    )

    # ── 6. Assemble context block ──────────────────────────────────────────
    context_block = ""

    # Concept search result — PRIMARY, goes first
    if concept_text.strip():
        context_block += (
            f"\n\n{'═' * 60}\n"
            f"FULL-TEXT CONCEPT SEARCH — 2025 ACT PDF (most relevant pages for this question)\n"
            f"{'═' * 60}\n"
            f"{concept_text.strip()}\n"
            f"{'═' * 60}\n"
        )

    # Definitions chapter — targeted paragraphs
    if definitions_text.strip():
        context_block += (
            f"\n\n{'═' * 60}\n"
            f"DEFINITIONS CHAPTER — 2025 Act (pages 109–115, paragraphs matching question)\n"
            f"{'═' * 60}\n"
            f"{definitions_text.strip()}\n"
            f"{'═' * 60}\n"
        )

    # Current section raw text
    if current_raw_text.strip():
        context_block += (
            f"\n\n{'═' * 60}\n"
            f"CURRENT SECTION — Section {current_section_2025} (raw PDF text)\n"
            f"{'═' * 60}\n"
            f"{current_raw_text.strip()}\n"
            f"{'═' * 60}\n"
        )

    # Plain-English summary (already shown to user, lowest priority)
    if context.strip():
        context_block += (
            f"\n\n{'─' * 60}\n"
            f"PLAIN-ENGLISH SUMMARY (secondary — PDF text above takes precedence)\n"
            f"{'─' * 60}\n"
            f"{context.strip()}\n"
            f"{'─' * 60}\n"
        )

    # Related sections from section mapping
    if related_pdf_text.strip():
        context_block += (
            f"\n\n{'─' * 60}\n"
            f"RELATED SECTIONS FROM 2025 ACT PDF\n"
            f"{'─' * 60}\n"
            f"{related_pdf_text.strip()}\n"
            f"{'─' * 60}\n"
        )

    if not context_block.strip():
        context_block = (
            "\n\n[NOTE: PDF text could not be loaded. Answer based on your knowledge "
            "of the 2025 Act but clearly flag every figure as unverified.]\n"
        )

    # ── 7. Build and send messages ─────────────────────────────────────────
    messages = [{"role": "system", "content": system_prompt + context_block}]

    for turn in history[-10:]:
        role = turn.get("role", "user")
        content_turn = turn.get("content", "")
        if role in ("user", "assistant") and content_turn:
            messages.append({"role": role, "content": content_turn})

    messages.append({"role": "user", "content": message})

    # CRITICAL: Use a non-online model for chat so Perplexity does NOT do web
    # search. Online/sonar models will find US IRS rules and present them as
    # Indian law. Any model name containing "online" or "sonar" must be replaced.
    chat_model = MODEL_NAME
    if "online" in chat_model.lower() or "sonar" in chat_model.lower():
        # Fall back to a pure-instruct model with no web search
        chat_model = "sonar"

    response = client.chat.completions.create(
        model=chat_model,
        messages=messages,
        temperature=0.1,
        max_tokens=2000
    )
    return response.choices[0].message.content
