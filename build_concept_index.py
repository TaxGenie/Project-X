"""
build_concept_index.py
Run ONCE to scan the entire 2025 Act PDF and build concept_index.json.
After this, search_pdf_for_concept() uses the cache instead of re-scanning.

Usage:
    python build_concept_index.py
"""
import json, pdfplumber, re, time
from pathlib import Path

PDF_2025 = "Income_Tax_Act_2025_-_Full_text_PDF.pdf"
OUTPUT   = "concept_index.json"

SKIP_PREFIXES = ("INCOME-TAX ACT", "Income-tax Act, 2025",
                 "Direct Taxes Committee", "CH. ", "SCH –", "SCH-")

def _clean(text):
    if not text: return ""
    lines = [l for l in text.split('\n')
             if not any(l.strip().startswith(s) for s in SKIP_PREFIXES)
             and not re.match(r'^\d+ of \d+', l.strip())]
    return '\n'.join(lines).strip()


def build():
    pdf_path = Path(PDF_2025)
    if not pdf_path.exists():
        print(f"ERROR: {PDF_2025} not found")
        return

    index = {}
    t0 = time.time()

    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        print(f"Scanning {total} pages …")
        for i, page in enumerate(pdf.pages):
            if i % 50 == 0:
                elapsed = time.time() - t0
                print(f"  Page {i+1}/{total}  ({elapsed:.0f}s elapsed)")
            text = page.extract_text()
            if text:
                cleaned = _clean(text)
                if cleaned:
                    index[str(i + 1)] = cleaned  # key = 1-based page number

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)

    elapsed = time.time() - t0
    print(f"\nDone. Indexed {len(index)} pages in {elapsed:.1f}s → {OUTPUT}")
    size_mb = Path(OUTPUT).stat().st_size / 1_048_576
    print(f"Index file size: {size_mb:.1f} MB")


if __name__ == "__main__":
    build()