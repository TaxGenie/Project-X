"""
tds_extractor.py
Extracts specific TDS provisions from Section 393's consolidated table in the 2025 Act.
Each 1961 TDS section (194H, 194J etc.) maps to specific Sl. No. rows in 393.
"""
import pdfplumber, re, os

PDF_2025 = "Income_Tax_Act_2025_-_Full_text_PDF.pdf"

# ── Master map: 1961 section → { sl_nos, pages, keywords, description } ──────
# pages = (start_page, end_page) — 1-based, inclusive
# sl_nos = which Sl. No(s) in the 393 table cover this section
# keywords = used to validate we got the right content

TDS_MAP = {
    "193":   {"sl_nos": ["Sl. No. 5", "interest on securities"],
              "pages": (602, 603), "label": "Interest on Securities (Sl. No. 5)"},
    "194":   {"sl_nos": ["Sl. No. 7", "dividend"],
              "pages": (604, 605), "label": "Dividends (Sl. No. 7)"},
    "194A":  {"sl_nos": ["Sl. No. 5", "interest other than"],
              "pages": (601, 603), "label": "Interest other than Securities (Sl. No. 5)"},
    "194B":  {"sl_nos": ["winnings", "lottery"],
              "pages": (612, 613), "label": "Winnings from Lottery (Sec 393(3) Sl. No. 1)"},
    "194C":  {"sl_nos": ["Sl. No. 6", "contractor", "carrying out any work"],
              "pages": (603, 604), "label": "Payments to Contractors (Sl. No. 6)"},
    "194D":  {"sl_nos": ["insurance commission"],
              "pages": (599, 600), "label": "Insurance Commission (Sl. No. 1(i))"},
    "194G":  {"sl_nos": ["lottery tickets"],
              "pages": (599, 600), "label": "Commission on Lottery (Sl. No. 1)"},
    "194H":  {"sl_nos": ["Sl. No. 1", "commission", "brokerage"],
              "pages": (599, 600), "label": "Commission or Brokerage (Sl. No. 1)"},
    "194I":  {"sl_nos": ["Sl. No. 2", "rent"],
              "pages": (599, 601), "label": "Rent (Sl. No. 2)"},
    "194IA": {"sl_nos": ["Sl. No. 3", "immovable property"],
              "pages": (600, 601), "label": "Transfer of Immovable Property (Sl. No. 3)"},
    "194J":  {"sl_nos": ["Sl. No. 6", "fees for professional", "technical services"],
              "pages": (603, 605), "label": "Professional / Technical Fees (Sl. No. 6(iii))"},
    "194K":  {"sl_nos": ["units", "mutual fund"],
              "pages": (601, 603), "label": "Income from Units (Sl. No. 4)"},
    "194N":  {"sl_nos": ["cash withdrawal", "cash from"],
              "pages": (605, 606), "label": "Cash Withdrawals (Sl. No. 8(iv))"},
    "194O":  {"sl_nos": ["e-commerce"],
              "pages": (604, 605), "label": "E-Commerce Operator Payments"},
    "194Q":  {"sl_nos": ["purchase of goods", "buyer"],
              "pages": (605, 606), "label": "Purchase of Goods (Sl. No. 8(ii))"},
    "194S":  {"sl_nos": ["virtual digital asset", "VDA"],
              "pages": (605, 607), "label": "Virtual Digital Assets (Sl. No. 8(vi))"},
    "195":   {"sl_nos": ["non-resident", "non- resident"],
              "pages": (607, 612), "label": "Payments to Non-Residents (Sec 393(2))"},
}

SKIP = ("Income-tax Act, 2025", "Direct Taxes Committee", "CH. XIX")

def _clean(text):
    if not text: return ""
    return '\n'.join(l for l in text.split('\n')
                     if not any(l.strip().startswith(s) for s in SKIP)
                     and not re.match(r'^\d+ of \d+', l.strip())).strip()

def _format_table_rows(table):
    """Format table rows as clean readable text."""
    lines = []
    for row in table:
        cells = [re.sub(r'\s+', ' ', str(c).replace('\n', ' ')).strip() if c else '' for c in row]
        if any(cells):
            lines.append(' | '.join(cells))
    return '\n'.join(lines)

def _extract_page_smart(page):
    """Extract page combining prose + table."""
    text = page.extract_text() or ""
    tables = page.extract_tables()
    if not tables:
        return _clean(text)

    try:
        bboxes = [t.bbox for t in page.find_tables()]
    except:
        bboxes = []

    words = page.extract_words() or []
    prose_words = []
    for w in words:
        wx = (w['x0'] + w['x1']) / 2
        wy = (w['top'] + w['bottom']) / 2
        in_tbl = any(bx0 <= wx <= bx1 and by0 <= wy <= by1
                     for (bx0, by0, bx1, by1) in bboxes)
        if not in_tbl:
            prose_words.append(w)

    prose_lines = {}
    for w in prose_words:
        y = round(w['top'] / 5) * 5
        prose_lines.setdefault(y, []).append(w['text'])
    prose = _clean('\n'.join(' '.join(prose_lines[y]) for y in sorted(prose_lines)))

    parts = []
    if prose.strip():
        parts.append(prose.strip())
    for t in tables:
        fmt = _format_table_rows(t)
        if fmt:
            parts.append("[TABLE]\n" + fmt + "\n[/TABLE]")
    return '\n\n'.join(parts)


def extract_tds_2025(section_1961: str) -> str:
    """
    Given a 1961 TDS section number (e.g. '194H'), return the exact
    corresponding rows from Section 393 of the 2025 Act.
    """
    key = section_1961.upper().strip()
    info = TDS_MAP.get(key)

    if not info:
        return f"[TDS section {section_1961} not in mapping — returning full Section 393]"

    if not os.path.exists(PDF_2025):
        return f"[2025 Act PDF not found: {PDF_2025}]"

    start_pg, end_pg = info["pages"]
    label = info["label"]
    keywords = info["sl_nos"]

    # Fetch the relevant pages
    collected = []
    try:
        with pdfplumber.open(PDF_2025) as pdf:
            total = len(pdf.pages)
            for i in range(start_pg - 1, min(end_pg, total)):
                content = _extract_page_smart(pdf.pages[i])
                if content:
                    collected.append(content)
    except Exception as e:
        return f"[Error reading PDF: {e}]"

    full = '\n\n'.join(collected)

    # Build the response with clear context
    header = (
        f"[FROM 2025 ACT — Section 393 (Consolidated TDS Table)]\n"
        f"[Relevant provision: {label}]\n"
        f"[This consolidates what was Section {section_1961} in the 1961 Act]\n\n"
    )

    return (header + full)[:7000]


def is_tds_section(section_number: str) -> bool:
    """Returns True if this is a TDS section that needs special 393-table handling."""
    key = section_number.upper().strip()
    # 194x sections and 193, 195 all map to Section 393 table
    if re.match(r'^19[3-5]', key):
        return True
    return key in TDS_MAP
