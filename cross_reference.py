import pdfplumber
import re
import os

PDF_1961 = "Income_Tax_Act_1961_-_Full_text_PDF.pdf"
PDF_2025 = "Income_Tax_Act_2025_-_Full_text_PDF.pdf"

# ─────────────────────────────────────────
# Schedule page maps (pre-scanned)
# ─────────────────────────────────────────
SCHEDULE_PAGES_2025 = {
    'I': 718, 'II': 721, 'III': 729, 'IV': 747, 'V': 753,
    'VI': 762, 'VII': 769, 'VIII': 779, 'IX': 781, 'X': 785,
    'XI': 789, 'XII': 799, 'XIII': 801, 'XIV': 802,
    'XV': 804, 'XVI': 814,
    # Arabic aliases
    '1': 718, '2': 721, '3': 729, '4': 747, '5': 753,
    '6': 762, '7': 769, '8': 779, '9': 781, '10': 785,
    '11': 789, '12': 799, '13': 801, '14': 802,
    '15': 804, '16': 814,
}

SCHEDULE_PAGES_1961 = {
    'FIRST': 873, 'ELEVENTH': 906,
    'I': 873, 'XI': 906,
    '1': 873, '11': 906,
}

# ─────────────────────────────────────────
# Cross-reference detection
# ─────────────────────────────────────────

def detect_cross_references(text):
    """
    Scans AI output text and finds all cross-references:
    - Schedules  (e.g. Schedule XV, Schedule XI)
    - Sections   (e.g. section 289(3), section 140)
    - Tables     (e.g. Table below, Table: S.No. 5)
    Returns a deduplicated list of dicts.
    """
    refs = []
    seen = set()

    # Schedules: "Schedule XV", "Schedule 15", "Schedule XI Part A"
    for m in re.finditer(
        r'\bSchedule[s]?\s+([IVXLC]+|[0-9]+[A-Z]?)(?:\s+Part\s+([A-Z]+))?',
        text, re.IGNORECASE
    ):
        sched = m.group(1).upper()
        part  = m.group(2).upper() if m.group(2) else None
        key   = f"SCHEDULE_{sched}_{part or ''}"
        if key not in seen:
            seen.add(key)
            refs.append({
                'type': 'schedule',
                'ref': sched,
                'part': part,
                'label': f"Schedule {m.group(1)}" + (f" Part {m.group(2)}" if part else ""),
                'original': m.group(0)
            })

    # Sections: "section 289(3)", "section 140", "section 202(1)"
    for m in re.finditer(
        r'\bsection\s+(\d+[A-Z]{0,3})(?:\((\d+)\))?(?:\(([a-z])\))?',
        text, re.IGNORECASE
    ):
        sec = m.group(1)
        sub = m.group(2) or ''
        key = f"SECTION_{sec}_{sub}"
        if key not in seen:
            seen.add(key)
            refs.append({
                'type': 'section',
                'ref': sec,
                'subsection': sub,
                'label': f"Section {sec}" + (f"({sub})" if sub else ""),
                'original': m.group(0)
            })

    # Tables mentioned inline: "Table: S.No. 5", "Table below"
    for m in re.finditer(
        r'\b(Table\s*(?:below|:?\s*S\.?\s*No\.?\s*\d+[A-Za-z\(\)]*|of\s+\w+))',
        text, re.IGNORECASE
    ):
        key = f"TABLE_{m.group(1).upper()[:30]}"
        if key not in seen:
            seen.add(key)
            refs.append({
                'type': 'table',
                'ref': m.group(1),
                'label': m.group(1).strip(),
                'original': m.group(0)
            })

    return refs


# ─────────────────────────────────────────
# Fetchers
# ─────────────────────────────────────────

def fetch_schedule(sched_ref, part=None, act='2025'):
    """Fetch full text of a Schedule from the specified Act PDF."""
    ref_upper = sched_ref.strip().upper()

    if act == '2025':
        if not os.path.exists(PDF_2025):
            return f"[2025 Act PDF not found]"
        pages_map = SCHEDULE_PAGES_2025
        pdf_path  = PDF_2025
        stop_pattern = re.compile(r'^SCHEDULE\s+[IVXLC0-9]+', re.IGNORECASE)
    else:
        if not os.path.exists(PDF_1961):
            return f"[1961 Act PDF not found]"
        pages_map = SCHEDULE_PAGES_1961
        pdf_path  = PDF_1961
        stop_pattern = re.compile(r'^THE\s+\w+\s+SCHEDULE', re.IGNORECASE)

    start_page = pages_map.get(ref_upper)
    if not start_page:
        return f"[Schedule {sched_ref} not found in index — may need manual lookup]"

    collected = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i in range(start_page - 1, min(start_page + 12, len(pdf.pages))):
                text = pdf.pages[i].extract_text()
                if not text:
                    continue
                # Stop at the next schedule heading
                first_line = text.strip().split('\n')[0].strip()
                if i > start_page and stop_pattern.match(first_line):
                    break
                # Filter part if requested
                if part:
                    if f"PART {part}" in text.upper() or f"Part {part}" in text:
                        collected.append(text)
                    elif collected:  # already collecting this part
                        collected.append(text)
                else:
                    collected.append(text)
    except Exception as e:
        return f"[Error reading PDF: {e}]"

    if not collected:
        return f"[Schedule {sched_ref} content could not be extracted]"

    # Clean headers/footers
    clean = []
    for page_text in collected:
        lines = page_text.split('\n')
        filtered = [l for l in lines if not re.match(
            r'^(Income-tax Act, 2025|INCOME-TAX ACT, 1961|Direct Taxes Committee|CH\.|SCH\s*[–-])', l.strip()
        )]
        clean.append('\n'.join(filtered))

    return '\n'.join(clean).strip()[:5000]


def fetch_section_for_annexure(section_num, act='2025'):
    """
    Fetch the text of a cross-referenced section from the PDF.
    Only fetches if not already the primary section being compared.
    """
    if act == '2025':
        if not os.path.exists(PDF_2025):
            return f"[2025 Act PDF not found]"
        pdf_path = PDF_2025
    else:
        if not os.path.exists(PDF_1961):
            return f"[1961 Act PDF not found]"
        pdf_path = PDF_1961

    sec_pattern = re.compile(
        rf'(?:^|\n)\s*{re.escape(section_num)}\.\s+[A-Z]', re.MULTILINE
    )
    next_sec_pattern = re.compile(
        r'\n\s*\d+[A-Z]{0,3}\.\s+[A-Z]'
    )

    collected = []
    found = False
    found_page = -1

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            for i in range(total):
                text = pdf.pages[i].extract_text()
                if not text:
                    continue
                if not found:
                    if sec_pattern.search(text):
                        found = True
                        found_page = i
                        collected.append(text)
                else:
                    if i > found_page + 4:
                        break
                    collected.append(text)
                    if len(collected) >= 3:
                        break
    except Exception as e:
        return f"[Error: {e}]"

    if not collected:
        return f"[Section {section_num} not found in {act} Act PDF]"

    full = '\n'.join(collected)

    # Extract just this section
    m = sec_pattern.search(full)
    if m:
        excerpt = full[m.start():]
        # Find where next section starts
        next_m = next_sec_pattern.search(excerpt[200:])
        if next_m:
            excerpt = excerpt[:next_m.start() + 200]
        return excerpt.strip()[:3000]

    return full[:3000]


def fetch_table_from_section(table_ref, section_context, act='2025'):
    """
    Fetch a table mentioned inline in a section.
    We re-fetch the parent section and extract the table portion.
    """
    # Table is part of the section already fetched — extract table lines
    lines = section_context.split('\n')
    table_lines = []
    in_table = False
    for line in lines:
        if 'Table' in line or line.strip().startswith('Sl.') or line.strip().startswith('A B'):
            in_table = True
        if in_table:
            table_lines.append(line)
            if len(table_lines) > 60:  # cap at 60 lines
                break

    if table_lines:
        return '\n'.join(table_lines)
    return f"[Table from {table_ref} — see parent section text]"


# ─────────────────────────────────────────
# Main function: process all cross-refs
# ─────────────────────────────────────────

def build_annexures(ai_output_text, primary_section_1961, primary_section_2025):
    """
    Given the full AI comparison output, detect all cross-references,
    fetch their content, and return a list of annexures.

    Returns: list of dicts with keys: title, content, ref_type
    """
    refs = detect_cross_references(ai_output_text)
    annexures = []
    annexure_num = 1

    # Sections to SKIP (these are the primary sections already shown)
    skip_sections = {primary_section_1961.upper(), primary_section_2025.upper()}

    for ref in refs:
        if ref['type'] == 'schedule':
            # Fetch from 2025 Act primarily
            content = fetch_schedule(ref['ref'], ref.get('part'), act='2025')
            # Also try 1961 if it's a schedule that exists there
            content_1961 = fetch_schedule(ref['ref'], ref.get('part'), act='1961')

            annexures.append({
                'number': annexure_num,
                'title': f"Annexure {annexure_num}: {ref['label']} — Income Tax Act 2025",
                'content': content,
                'ref_type': 'schedule',
                'act': '2025'
            })
            annexure_num += 1

            if content_1961 and 'not found' not in content_1961.lower() and 'not found' not in content_1961[:50].lower():
                annexures.append({
                    'number': annexure_num,
                    'title': f"Annexure {annexure_num}: {ref['label']} — Income Tax Act 1961",
                    'content': content_1961,
                    'ref_type': 'schedule',
                    'act': '1961'
                })
                annexure_num += 1

        elif ref['type'] == 'section':
            sec = ref['ref'].upper()
            if sec in skip_sections:
                continue  # Don't re-fetch the main section

            content = fetch_section_for_annexure(ref['ref'], act='2025')
            if content and 'not found' not in content[:50].lower():
                annexures.append({
                    'number': annexure_num,
                    'title': f"Annexure {annexure_num}: Section {ref['ref']} — Income Tax Act 2025 (Cross-Reference)",
                    'content': content,
                    'ref_type': 'section',
                    'act': '2025'
                })
                annexure_num += 1

        elif ref['type'] == 'table':
            # Tables are embedded in sections; note them as references
            annexures.append({
                'number': annexure_num,
                'title': f"Annexure {annexure_num}: {ref['label']} (Referenced Table)",
                'content': f"This table is referenced within the sections above. "
                           f"Please refer to the relevant section in the Act for the complete table:\n\n"
                           f"Reference: \"{ref['original']}\"",
                'ref_type': 'table',
                'act': 'both'
            })
            annexure_num += 1

    return annexures
