"""
build_index.py  — Run ONCE from ca_tax_tool folder:
    python build_index.py
Creates section_index_1961.json and section_index_2025.json
"""
import pdfplumber, json, re, os, time

PDF_1961 = "Income_Tax_Act_1961_-_Full_text_PDF.pdf"
PDF_2025 = "Income_Tax_Act_2025_-_Full_text_PDF.pdf"

# 1961 Act: two patterns
# Pattern A: "Heading.\nNUM. " — section name appears BEFORE the number
PATTERN_1961_A = re.compile(r'[A-Z][^\n.]{2,60}\.\n(\d+[A-Z]{0,4})\.\s', re.MULTILINE)
# Pattern B: "NUM.(1)" — number directly followed by (1)
PATTERN_1961_B = re.compile(r'(?:^|\n)\s*(\d+[A-Z]{0,4})\.\s*\(1\)', re.MULTILINE)
# Footnote pattern to SKIP — lines like "32. Sub. for..." or "32. Ins. by"
FOOTNOTE = re.compile(r'(?:^|\n)\s*\d+[A-Z]{0,2}\.\s+(?:Sub\.|Ins\.|Omit|Renum|Words|Proviso|Clause|Prior|Earlier|See\s)')

# 2025 Act: cleaner — "NUM. Heading" pattern
PATTERN_2025 = re.compile(r'(?:^|\n)\s*(\d+[A-Z]{0,4})\.\s+[A-Z(]', re.MULTILINE)

SCHED_2025 = re.compile(r'^SCHEDULE\s+([IVXLC]+|\d+)', re.IGNORECASE)
SCHED_1961 = re.compile(r'^THE\s+(\w+)\s+SCHEDULE', re.IGNORECASE)

ROMAN_MAP = {'I':1,'II':2,'III':3,'IV':4,'V':5,'VI':6,'VII':7,'VIII':8,
             'IX':9,'X':10,'XI':11,'XII':12,'XIII':13,'XIV':14,'XV':15,'XVI':16}
WORD_MAP  = {'FIRST':1,'SECOND':2,'THIRD':3,'FOURTH':4,'FIFTH':5,'SIXTH':6,
             'SEVENTH':7,'EIGHTH':8,'NINTH':9,'TENTH':10,'ELEVENTH':11,'TWELFTH':12}
ROMAN_LIST = ['I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII','XIII','XIV','XV','XVI']

def is_footnote_line(line):
    """Return True if this line looks like a footnote (e.g. '32. Sub. for...')"""
    return bool(re.match(r'^\s*\d+[A-Z]{0,2}\.\s+(?:Sub\b|Ins\b|Omit|Renum|Words|Prior|Earlier|See\s)', line.strip()))

def build_1961(pdf_path):
    sections = {}
    schedules = {}
    print(f"\nIndexing 1961 Act...")
    t = time.time()

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            if i % 150 == 0:
                print(f"  Page {i+1}/{total}...")
            text = page.extract_text()
            if not text: continue
            pg = i + 1

            # Schedule detection
            for line in text.split('\n')[:3]:
                m = SCHED_1961.match(line.strip())
                if m:
                    word = m.group(1).upper()
                    if word in WORD_MAP:
                        n = WORD_MAP[word]
                        roman = ROMAN_LIST[n-1]
                        if roman not in schedules:
                            schedules[roman] = pg
                            schedules[str(n)] = pg

            # Pattern A: Heading.\nNUM.  (most reliable for early sections)
            for m in PATTERN_1961_A.finditer(text):
                sec = m.group(1).upper()
                if sec not in sections:
                    try:
                        num = int(re.match(r'\d+', sec).group())
                        if 1 <= num <= 800:
                            sections[sec] = pg
                    except: pass

            # Pattern B: NUM.(1) — catches sections not matched by A
            lines = text.split('\n')
            for j, line in enumerate(lines):
                # Skip footnote lines
                if is_footnote_line(line):
                    continue
                m = re.match(r'^\s*(\d+[A-Z]{0,4})\.\s*\(1\)', line.strip())
                if m:
                    sec = m.group(1).upper()
                    if sec not in sections:
                        try:
                            num = int(re.match(r'\d+', sec).group())
                            if 1 <= num <= 800:
                                sections[sec] = pg
                        except: pass

    print(f"  Done in {time.time()-t:.0f}s — {len(sections)} sections, {len(schedules)} schedules")
    return sections, schedules

def build_2025(pdf_path):
    sections = {}
    schedules = {}
    print(f"\nIndexing 2025 Act...")
    t = time.time()

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            if i % 150 == 0:
                print(f"  Page {i+1}/{total}...")
            text = page.extract_text()
            if not text: continue
            pg = i + 1

            first = text.strip().split('\n')[0].strip()
            m = SCHED_2025.match(first)
            if m:
                ref = m.group(1).upper()
                if ref not in schedules:
                    schedules[ref] = pg
                    if ref in ROMAN_MAP:
                        schedules[str(ROMAN_MAP[ref])] = pg
                    else:
                        try:
                            n = int(ref)
                            if 1 <= n <= len(ROMAN_LIST):
                                schedules[ROMAN_LIST[n-1]] = pg
                        except: pass

            for m in PATTERN_2025.finditer(text):
                sec = m.group(1).upper()
                if sec not in sections:
                    try:
                        num = int(re.match(r'\d+', sec).group())
                        if 1 <= num <= 800:
                            sections[sec] = pg
                    except: pass

    print(f"  Done in {time.time()-t:.0f}s — {len(sections)} sections, {len(schedules)} schedules")
    return sections, schedules

def main():
    for pdf in [PDF_1961, PDF_2025]:
        if not os.path.exists(pdf):
            print(f"ERROR: {pdf} not found. Run from ca_tax_tool folder.")
            return

    s1, sch1 = build_1961(PDF_1961)
    with open("section_index_1961.json","w") as f:
        json.dump({"sections": s1, "schedules": sch1}, f, indent=2)
    print("Saved: section_index_1961.json")

    s2, sch2 = build_2025(PDF_2025)
    with open("section_index_2025.json","w") as f:
        json.dump({"sections": s2, "schedules": sch2}, f, indent=2)
    print("Saved: section_index_2025.json")

    print("\n✅ Index built successfully!")
    # Test key sections
    tests = [('1961', s1, ['32','80C','192','44AD','115BAC']),
             ('2025', s2, ['33','123','392','58','202'])]
    for act, idx, secs in tests:
        for sec in secs:
            pg = idx.get(sec, 'NOT FOUND')
            status = '✅' if pg != 'NOT FOUND' else '❌'
            print(f"  {status} {act} Act Section {sec} → page {pg}")

if __name__ == "__main__":
    main()
