"""
pdf_extractor.py
Uses pre-built JSON index for instant page lookups.
Uses extract_tables() for 2025 Act pages containing structured tables.
Run build_index.py ONCE first.
"""
import pdfplumber, json, re, os

PDF_1961 = "Income_Tax_Act_1961_-_Full_text_PDF.pdf"
PDF_2025 = "Income_Tax_Act_2025_-_Full_text_PDF.pdf"
INDEX_1961 = "section_index_1961.json"
INDEX_2025 = "section_index_2025.json"

def _load(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"sections": {}, "schedules": {}}

_idx_1961 = _load(INDEX_1961)
_idx_2025 = _load(INDEX_2025)
INDEX_AVAILABLE = bool(_idx_1961["sections"] or _idx_2025["sections"])

SKIP_PREFIXES = ("INCOME-TAX ACT, 1961", "Income-tax Act, 2025",
                 "Direct Taxes Committee", "CH. ", "SCH –", "SCH-")

def _is_footnote(line):
    return bool(re.match(
        r'^\s*\d+[A-Z]{0,2}\.\s+(?:Sub\b|Ins\b|Omit|Renum|Words|Prior|Earlier|See\s)',
        line.strip()
    ))

def _is_table_garbage(line):
    s = line.strip()
    if re.match(r'^[A-Z](\s+[A-Z]){2,}$', s):
        return True
    if len(s) < 4 and re.match(r'^[A-Z0-9]', s):
        return True
    return False

def _clean(text):
    if not text: return ""
    lines = [l for l in text.split('\n')
             if not any(l.strip().startswith(s) for s in SKIP_PREFIXES)
             and not re.match(r'^\d+ of \d+', l.strip())]
    return '\n'.join(lines).strip()


# ── Table formatting ──────────────────────────────────────────────────────────

def _format_table(table):
    if not table:
        return ""
    rows = []
    for row in table:
        cells = []
        for cell in row:
            if cell:
                cleaned = re.sub(r'\s+', ' ', str(cell).replace('\n', ' ')).strip()
                cells.append(cleaned)
            else:
                cells.append('')
        if any(c for c in cells):
            rows.append(' | '.join(cells))
    return '\n'.join(rows)


def _extract_page_smart(page):
    raw_text = page.extract_text() or ""
    tables   = page.extract_tables()

    if not tables:
        return _clean(raw_text)

    try:
        table_bboxes = [tbl.bbox for tbl in page.find_tables()]
    except Exception:
        table_bboxes = []

    words = page.extract_words() or []

    prose_words = []
    for w in words:
        wx = (w['x0'] + w['x1']) / 2
        wy = (w['top'] + w['bottom']) / 2
        in_table = any(
            bx0 <= wx <= bx1 and by0 <= wy <= by1
            for (bx0, by0, bx1, by1) in table_bboxes
        )
        if not in_table:
            prose_words.append(w)

    prose_lines = {}
    for w in prose_words:
        y_key = round(w['top'] / 5) * 5
        if y_key not in prose_lines:
            prose_lines[y_key] = []
        prose_lines[y_key].append(w['text'])

    prose_text = '\n'.join(
        ' '.join(prose_lines[y]) for y in sorted(prose_lines)
    )
    prose_text = _clean(prose_text)

    parts = []
    if prose_text.strip():
        parts.append(prose_text.strip())

    for table in tables:
        formatted = _format_table(table)
        if formatted:
            parts.append("[TABLE]\n" + formatted + "\n[/TABLE]")

    return '\n\n'.join(parts)


# ── Page range fetchers ───────────────────────────────────────────────────────

def _fetch_pages_2025(start_page, num_pages=5):
    collected = []
    try:
        with pdfplumber.open(PDF_2025) as pdf:
            total = len(pdf.pages)
            for i in range(start_page - 1, min(start_page - 1 + num_pages, total)):
                content = _extract_page_smart(pdf.pages[i])
                if content:
                    collected.append(content)
    except Exception as e:
        return f"[Error reading 2025 PDF: {e}]"
    return '\n\n'.join(collected).strip()

def _fetch_pages_1961(start_page, num_pages=5):
    collected = []
    try:
        with pdfplumber.open(PDF_1961) as pdf:
            total = len(pdf.pages)
            for i in range(start_page - 1, min(start_page - 1 + num_pages, total)):
                text = pdf.pages[i].extract_text()
                if text:
                    collected.append(_clean(text))
    except Exception as e:
        return f"[Error reading 1961 PDF: {e}]"
    return '\n'.join(collected).strip()


# ── Section trimming ──────────────────────────────────────────────────────────

def _trim_1961(full_text, section_num):
    sec = section_num.upper()

    pat_a = re.compile(
        rf'(?:^|\n)([A-Z][^\n.{{}}]{{2,60}})\.\n\s*{re.escape(sec)}\.\s',
        re.MULTILINE
    )
    m = pat_a.search(full_text)
    if m:
        excerpt = full_text[m.start():].strip()
    else:
        pat_b = re.compile(rf'(?:^|\n)\s*{re.escape(sec)}\.\s*\(1\)', re.MULTILINE)
        m = pat_b.search(full_text)
        if m:
            excerpt = full_text[m.start():].strip()
        else:
            pat_c = re.compile(rf'(?:^|\n)\s*{re.escape(sec)}\.\s', re.MULTILINE)
            m = pat_c.search(full_text)
            excerpt = full_text[m.start():].strip() if m else full_text

    lines = excerpt.split('\n')
    result_lines = []
    for i, line in enumerate(lines):
        if i > 5 and re.match(r'^[A-Z][a-z].*\.$', line.strip()):
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ''
            if re.match(r'^\d+[A-Z]{0,4}\.\s', nxt) and not _is_footnote(nxt):
                break
        if i > 5 and not _is_footnote(line):
            m2 = re.match(r'^\s*(\d+[A-Z]{0,4})\.\s+[A-Z(]', line.strip())
            if m2 and m2.group(1).upper() != sec:
                break
        result_lines.append(line)
    return '\n'.join(result_lines).strip()[:6000]


def _trim_2025(full_text, section_num):
    sec = section_num.upper()
    pat = re.compile(rf'(?:^|\n)\s*{re.escape(sec)}\.\s', re.MULTILINE | re.IGNORECASE)
    m = pat.search(full_text)
    if not m:
        return full_text[:7000]
    excerpt = full_text[m.start():].strip()

    lines = excerpt.split('\n')
    result_lines = []
    in_table = False
    for i, line in enumerate(lines):
        if '[TABLE]' in line:
            in_table = True
        if '[/TABLE]' in line:
            in_table = False
            result_lines.append(line)
            continue
        if in_table:
            result_lines.append(line)
            continue
        if i > 10:
            m2 = re.match(r'^\s*(\d+[A-Z]{0,4})\.\s+[A-Z(]', line.strip())
            if m2 and m2.group(1).upper() != sec:
                break
        result_lines.append(line)
    return '\n'.join(result_lines).strip()[:7000]


# ── Public API ────────────────────────────────────────────────────────────────

def extract_section_1961(section_number):
    sec = section_number.upper().strip()
    if not os.path.exists(PDF_1961):
        return f"[1961 Act PDF not found: {PDF_1961}]"
    if INDEX_AVAILABLE and _idx_1961["sections"]:
        page = _idx_1961["sections"].get(sec)
        if not page:
            base = re.match(r'\d+', sec)
            if base: page = _idx_1961["sections"].get(base.group())
        if page:
            text = _fetch_pages_1961(max(1, page - 1), num_pages=5)
            return _trim_1961(text, section_number)
        return f"[Section {section_number} not found in index — please run build_index.py]"
    return _scan_pdf(PDF_1961, section_number, '1961')


def extract_section_2025(section_number):
    sec = section_number.upper().strip()
    if not os.path.exists(PDF_2025):
        return f"[2025 Act PDF not found: {PDF_2025}]"
    if INDEX_AVAILABLE and _idx_2025["sections"]:
        page = _idx_2025["sections"].get(sec)
        if not page:
            base = re.match(r'\d+', sec)
            if base: page = _idx_2025["sections"].get(base.group())
        if page:
            text = _fetch_pages_2025(page, num_pages=5)
            return _trim_2025(text, section_number)
        return f"[Section {section_number} not found in index — please run build_index.py]"
    return _scan_pdf(PDF_2025, section_number, '2025')


def extract_schedule(schedule_ref, act='2025'):
    ref = schedule_ref.upper().strip()
    pdf_path = PDF_2025 if act == '2025' else PDF_1961
    idx = _idx_2025 if act == '2025' else _idx_1961
    if not os.path.exists(pdf_path): return f"[PDF not found]"
    page = idx["schedules"].get(ref)
    if not page: return f"[Schedule {schedule_ref} not in index]"
    stop_re = re.compile(r'^SCHEDULE\s+[IVXLC0-9]+', re.IGNORECASE)
    collected = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i in range(page - 1, min(page + 11, len(pdf.pages))):
                pg = pdf.pages[i]
                raw = pg.extract_text() or ""
                first = raw.strip().split('\n')[0].strip()
                if i > page - 1 and stop_re.match(first): break
                collected.append(_extract_page_smart(pg) if act == '2025' else _clean(raw))
    except Exception as e:
        return f"[Error: {e}]"
    return '\n\n'.join(collected).strip()[:6000]


def get_both_sections(section_1961_num, section_2025_num):
    return extract_section_1961(section_1961_num), extract_section_2025(section_2025_num)


def _scan_pdf(pdf_path, section_number, act):
    """Slow fallback — only used when index is not built."""
    if act == '1961':
        pat = re.compile(
            rf'[A-Z][^\n.{{}}]{{2,60}}\.\n\s*{re.escape(section_number)}\.\s|'
            rf'(?:^|\n)\s*{re.escape(section_number)}\.\s*\(1\)',
            re.MULTILINE | re.IGNORECASE
        )
    else:
        pat = re.compile(rf'(?:^|\n)\s*{re.escape(section_number)}\.\s', re.MULTILINE | re.IGNORECASE)

    collected = []
    found = False
    found_page = -1
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text: continue
                if not found:
                    if pat.search(text):
                        found = True
                        found_page = i
                        collected.append(_extract_page_smart(page) if act == '2025' else _clean(text))
                else:
                    if i > found_page + 5: break
                    collected.append(_extract_page_smart(page) if act == '2025' else _clean(text))
                    if len(collected) >= 5: break
    except Exception as e:
        return f"[Error: {e}]"
    if not collected:
        return f"[Section {section_number} not found in {act} Act]"
    full = '\n\n'.join(collected)
    return _trim_1961(full, section_number) if act == '1961' else _trim_2025(full, section_number)


# ── TDS smart routing ─────────────────────────────────────────────────────────

def extract_section_2025_smart(section_number, section_number_1961=None):
    try:
        from tds_extractor import is_tds_section, extract_tds_2025
        if section_number_1961 and is_tds_section(section_number_1961):
            return extract_tds_2025(section_number_1961)
        if str(section_number) == "393":
            return "[Section 393 is the full TDS table — use a specific 1961 section like 194H for targeted extraction]"
    except ImportError:
        pass
    return extract_section_2025(section_number)


def get_both_sections_smart(section_1961_num, section_2025_num):
    t1 = extract_section_1961(section_1961_num)
    t2 = extract_section_2025_smart(section_2025_num, section_number_1961=section_1961_num)
    return t1, t2


# ══════════════════════════════════════════════════════════════════════════════
# CHAT CONTEXT SEARCH — finds related 2025 Act sections for a chat question
# ══════════════════════════════════════════════════════════════════════════════

_TOPIC_CLUSTERS = {
    "capital_gains": {
        "sections_2025": {"2","67","68","69","70","71","72","73","74","75","76","77",
                          "78","79","80","81","82","83","84","85","86","87","88","89",
                          "90","91","196","197"},
        "keywords":      {"capital","gain","transfer","asset","acquisition","cost",
                          "holding","period","short","long","term","stcg","ltcg",
                          "indexation","exemption","54","45","48","49","50",
                          "immovable","property","bond","reinvest","residential",
                          "slump","sale","fmv","fair","market","stamp","duty",
                          "depreciable","liquidation","agricultural","land","urban",
                          "42a","definition","month","year","listed","unlisted",
                          "equity","share","mutual","fund","debenture"},
    },
    "salary": {
        "sections_2025": {"2","15","16","17","18","19","20"},
        "keywords":      {"salary","perquisite","allowance","standard","deduction",
                          "employer","employee","epf","provident","gratuity","leave",
                          "encashment","arrear","pension","tds","192"},
    },
    "business": {
        "sections_2025": {"2","26","27","28","29","30","31","32","33","34","35","36",
                          "37","38","39","40","41","42","43","44","45","46","47",
                          "48","49","50","51","52","53","54","55","56","57","58",
                          "59","60","61","62","63","64","65"},
        "keywords":      {"business","profession","depreciation","deduction","audit",
                          "presumptive","turnover","revenue","expense","repair",
                          "insurance","bad","debt","wcv","actual","cost"},
    },
    "house_property": {
        "sections_2025": {"2","20","21","22","23","24","25"},
        "keywords":      {"house","property","rent","annual","value","interest",
                          "loan","municipal","tax","self","occupied","let","out"},
    },
    "deductions": {
        "sections_2025": {"2","119","120","121","122","123","124","125","126","127",
                          "128","129","130","131","132","133","134","135","136",
                          "137","138","139","140","141","142","143","144","145",
                          "146","147","148","149","150","151","152","153","154",
                          "155","156","157"},
        "keywords":      {"deduction","80c","nps","insurance","medical","donation",
                          "charity","80d","80e","80g","80gg","investment","ppf",
                          "elss","home","loan","education","disability"},
    },
}


def _score_section(question_words, description, name_1961,
                   name_2025, current_sec_2025, cluster_sections):
    text = (description + " " + name_1961 + " " + name_2025).lower()
    text_words = set(re.findall(r'[a-z0-9]+', text))
    overlap = len(question_words & text_words)
    score = overlap * 1.0
    sec_num = re.search(r'\d+[A-Za-z]*$', name_2025.strip())
    if sec_num and sec_num.group().upper() in cluster_sections:
        score += 1.5
    if overlap > 0 and len(description) > 30:
        score += 0.3
    q = " ".join(question_words)
    desc_l = description.lower()
    if any(w in q for w in ["holding", "period", "stcg", "ltcg", "short", "long", "term"]):
        if any(w in desc_l for w in ["definition", "computation", "capital", "gain", "acquisition", "cost"]):
            score += 2.0
    if any(w in q for w in ["exempt", "54", "relief", "rollover", "reinvest"]):
        if any(w in desc_l for w in ["exempt", "54", "relief", "gain"]):
            score += 2.0
    if any(w in q for w in ["indexation", "inflation", "cost", "index", "cii"]):
        if any(w in desc_l for w in ["computation", "cost", "capital", "acquisition"]):
            score += 2.0
    if any(w in q for w in ["deduct", "80c", "80d", "investment", "save", "saving"]):
        if any(w in desc_l for w in ["deduction", "saving", "investment"]):
            score += 2.0
    if any(w in q for w in ["tds", "deduct", "withhold", "payer", "rate"]):
        if any(w in desc_l for w in ["tds", "deduct", "withhold", "source"]):
            score += 2.0
    return score


def search_related_2025(question, current_section_2025="",
                        max_sections=2, max_chars_per_section=2500):
    try:
        from section_mapping import SECTION_MAPPING
    except ImportError:
        return ""
    if not os.path.exists(PDF_2025):
        return ""

    stop_words = {"the","a","an","is","are","was","were","be","been","being",
                  "have","has","had","do","does","did","will","would","shall",
                  "should","may","might","can","could","of","in","on","at","to",
                  "for","with","by","from","as","what","how","when","where",
                  "which","who","this","that","these","those","it","its","i",
                  "me","my","we","our","you","your","he","she","they","their",
                  "and","or","but","not","no","if","then","also","about","under",
                  "per","act","tax","income","section","2025","1961"}

    raw_words = set(re.findall(r'[a-z0-9]+', question.lower()))
    question_words = raw_words - stop_words
    if not question_words:
        return ""

    cluster_sections = set()
    for cluster in _TOPIC_CLUSTERS.values():
        if current_section_2025 in cluster["sections_2025"]:
            cluster_sections = cluster["sections_2025"]
            question_words = question_words | (cluster["keywords"] & raw_words | {current_section_2025})
            break

    scored = []
    seen_2025 = set()
    for key, entry in SECTION_MAPPING.items():
        if key.startswith("_"):
            continue
        sec2025 = entry.get("section_number_2025", "")
        if sec2025 == current_section_2025 or sec2025 in seen_2025:
            continue
        seen_2025.add(sec2025)
        score = _score_section(
            question_words,
            entry.get("description", ""),
            entry.get("section_1961", ""),
            entry.get("section_2025", ""),
            current_section_2025,
            cluster_sections,
        )
        if score > 0:
            scored.append((score, sec2025, entry.get("section_1961", f"Section {sec2025}"),
                           entry.get("description", "")))

    if not scored:
        return ""

    scored.sort(key=lambda x: -x[0])
    top = scored[:max_sections]

    parts = []
    for score, sec2025_num, sec1961_name, description in top:
        try:
            text = extract_section_2025(sec2025_num)
            if text and not text.startswith("[") and len(text) > 100:
                text = text[:max_chars_per_section]
                parts.append(f"--- RELATED: {sec1961_name} ({description}) ---\n{text}")
        except Exception:
            continue

    return "\n\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# DIRECT DEFINITIONS FETCH — bypasses broken Section 2 index entry
# Pages 109–115 confirmed by full PDF scan to contain the definitions chapter
# ══════════════════════════════════════════════════════════════════════════════

_DEFINITIONS_PAGES = (108, 120)   # wider range — clause 42A (months) confirmed on p.112


def search_definitions_2025(keywords=None, context_chars=8000):
    """
    Return the full text of the definitions chapter (pages 109-115).
    Keyword filtering is intentionally NOT used — statutory definitions
    use different wording than questions (e.g. "held for" vs "holding period")
    so paragraph filtering always misses clauses. Always returns the complete
    definitions pages so the model can find any clause.
    """
    if not os.path.exists(PDF_2025):
        return ""

    start_p, end_p = _DEFINITIONS_PAGES
    collected = []

    try:
        with pdfplumber.open(PDF_2025) as pdf:
            total = len(pdf.pages)
            for i in range(start_p - 1, min(end_p, total)):
                page_text = _extract_page_smart(pdf.pages[i])
                if page_text:
                    collected.append(page_text)
    except Exception as e:
        return f"[search_definitions_2025 error: {e}]"

    return ("\n\n".join(collected))[:context_chars]


# ══════════════════════════════════════════════════════════════════════════════
# FULL-TEXT CONCEPT SEARCH — searches the entire 2025 Act PDF for any concept
# Uses concept_index.json if available (built by build_concept_index.py),
# otherwise falls back to a live page-by-page scan (slower but always works).
# ══════════════════════════════════════════════════════════════════════════════

_CONCEPT_INDEX_PATH = "concept_index.json"
_concept_index = None   # { "109": "page text...", ... }


def _load_concept_index():
    global _concept_index
    if _concept_index is not None:
        return _concept_index
    if os.path.exists(_CONCEPT_INDEX_PATH):
        try:
            with open(_CONCEPT_INDEX_PATH, encoding="utf-8") as f:
                _concept_index = json.load(f)
            print(f"[TEJAS] Concept index loaded: {len(_concept_index)} pages")
            return _concept_index
        except Exception as e:
            print(f"[TEJAS] concept_index.json load failed: {e}")
    _concept_index = {}
    return _concept_index


def search_pdf_for_concept(question, max_results=4,
                           context_chars_per_hit=1500,
                           live_scan_fallback=True):
    """
    Search the entire 2025 Act PDF for pages relevant to `question`.
    Returns a formatted string ready to inject into a prompt, or "" if nothing found.

    Strategy:
      1. Score every page in concept_index.json by keyword overlap (with synonyms).
      2. Return the top max_results pages' text, trimmed to context_chars_per_hit.
      3. If concept index is empty AND live_scan_fallback=True, do a live scan.
    """
    stop = {"the","a","an","is","are","was","were","be","been","have","has","had",
            "do","does","did","will","would","shall","should","may","might","can",
            "could","of","in","on","at","to","for","with","by","from","as","what",
            "how","when","where","which","who","this","that","it","its","i","me",
            "my","we","our","you","your","and","or","but","not","no","if","then",
            "also","about","under","per","act","tax","income","section","2025","1961"}

    q_words = set(re.findall(r'[a-z0-9]+', question.lower())) - stop

    # Synonym expansion — statutory language differs from question language
    _SYNONYMS = {
        "holding":   {"held", "hold"},
        "period":    {"months", "month", "years", "year", "twenty", "twelve", "thirty"},
        "shortterm": {"short", "stcg", "42a"},
        "longterm":  {"long", "ltcg", "42b"},
        "transfer":  {"sold", "sell", "convey", "exchange"},
        "exemption": {"exempt", "relief", "deduction", "54"},
        "indexation":{"indexed", "inflation", "cii", "cost"},
        "fmv":       {"fair", "market", "value", "stamp"},
    }
    expanded = set(q_words)
    for w in list(q_words):
        if w in _SYNONYMS:
            expanded |= _SYNONYMS[w]
    q_words = expanded

    if not q_words:
        return ""

    index = _load_concept_index()

    # ── path A: use pre-built index ────────────────────────────────────────
    if index:
        scored = []
        for page_str, page_text in index.items():
            pt_lower = page_text.lower()
            hits = sum(1 for w in q_words if w in pt_lower)
            if hits:
                scored.append((hits, int(page_str), page_text))
        scored.sort(key=lambda x: -x[0])
        top = scored[:max_results]
        if top:
            parts = []
            for hits, page_num, text in top:
                snippet = text[:context_chars_per_hit].strip()
                parts.append(f"[Page {page_num} — {hits} keyword matches]\n{snippet}")
            return "\n\n".join(parts)

    # ── path B: live scan fallback when index not yet built ────────────────
    if not live_scan_fallback or not os.path.exists(PDF_2025):
        return ""

    print("[TEJAS] concept_index.json not available — running live PDF scan (slow)")
    scored = []
    try:
        with pdfplumber.open(PDF_2025) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                tl = text.lower()
                hits = sum(1 for w in q_words if w in tl)
                if hits:
                    scored.append((hits, i + 1, text))
    except Exception as e:
        return f"[search_pdf_for_concept live scan error: {e}]"

    scored.sort(key=lambda x: -x[0])
    top = scored[:max_results]
    if not top:
        return ""

    parts = []
    for hits, page_num, text in top:
        snippet = _clean(text)[:context_chars_per_hit].strip()
        parts.append(f"[Page {page_num} — {hits} keyword matches]\n{snippet}")
    return "\n\n".join(parts)