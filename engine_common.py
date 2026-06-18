"""
Shared helpers for the AUSMAR QA Agent V2 engines (Stage 2 NHP review,
Stage 3 contract QA). Built on top of the Stage 1 qa_engine helpers so the
two new engines stay consistent with the existing tool and reuse the same
OpenAI client, model defaults, and PDF utilities.

Key difference from Stage 1: Stage 2/3 documents are large (61+ VOs, multi-page
spec/pricing/drawings). We do NOT cap extraction at 5 pages / 8K chars, because
silently dropping a VO or spec line would create a missing-item false negative —
the worst outcome for this tool. Instead we extract every page and keep a
per-page map so issues can cite a page reference.
"""

import json
import re

import qa_engine as qe

# Re-export the proven Stage 1 helpers so engines import from one place
call_text_model = qe.call_text_model
call_vision_model = qe.call_vision_model
parse_json_from_llm = qe.parse_json_from_llm
pdf_page_to_base64 = qe.pdf_page_to_base64
pdf_all_pages_to_base64 = qe.pdf_all_pages_to_base64
extract_pdf_text = qe.extract_pdf_text


# ---------------------------------------------------------------------------
# Full-document text extraction (no page cap) with per-page references
# ---------------------------------------------------------------------------
def extract_pdf_pages(pdf_path: str, max_pages: int = 60):
    """Return a list of {page, text} dicts, one per page (1-indexed).

    max_pages is a generous safety ceiling, not a 5-page cap. Returns [] if
    pypdf is unavailable or the file cannot be read.
    """
    if not qe._PYPDF_AVAILABLE:
        return []
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        pages = []
        for i, page in enumerate(reader.pages[:max_pages]):
            txt = (page.extract_text() or "").strip()
            pages.append({"page": i + 1, "text": txt})
        return pages
    except Exception as e:
        print(f"[WARN] extract_pdf_pages failed for {pdf_path}: {e}")
        return []


def pages_to_text(pages, per_page_char_cap: int = 6000) -> str:
    """Flatten a page list into a single annotated string with page markers."""
    parts = []
    for p in pages:
        body = p["text"][:per_page_char_cap]
        parts.append(f"=== PAGE {p['page']} ===\n{body}")
    return "\n\n".join(parts)


def page_count_text(pages) -> str:
    return f"{len(pages)} page(s)"


# ---------------------------------------------------------------------------
# Money parsing helpers (deterministic; used to double-check the AI's maths)
# ---------------------------------------------------------------------------
_MONEY_RE = re.compile(r"-?\$?\s?-?\d[\d,]*\.?\d{0,2}")


def parse_money(text):
    """Best-effort parse of a dollar string to float. Returns None if unclear."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().replace(",", "").replace("$", "").replace(" ", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        val = float(s)
        return -val if neg else val
    except ValueError:
        return None


GROSS_UP = 1.45475  # AUSMAR sell = cost x 1.45475 (GST-inclusive)


# ---------------------------------------------------------------------------
# Robust JSON list extraction from an LLM response
# ---------------------------------------------------------------------------
def parse_json_list(raw: str, key: str):
    """Extract a list under `key` from a possibly-noisy LLM JSON response."""
    obj = parse_json_from_llm(raw)
    if isinstance(obj, dict) and key in obj and isinstance(obj[key], list):
        return obj[key]
    # Fallback: try to find a bare JSON array
    m = re.search(r"\[[\s\S]*\]", raw)
    if m:
        try:
            arr = json.loads(m.group())
            if isinstance(arr, list):
                return arr
        except Exception:
            pass
    return []
