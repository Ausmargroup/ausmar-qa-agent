"""
AUSMAR PSE QA Engine — Production Review Pipeline (v2: Content-Based Classification)

Documents are identified by their CONTENT (PDF text, spreadsheet structure, image analysis),
NOT by filename. This eliminates false positives from inconsistent naming by consultants.

Flow:
1. Extract files from zip, remove junk
2. Extract text/content from each file (pypdf for PDFs, openpyxl for spreadsheets)
3. Classify each file by content using keyword matching, then LLM fallback for ambiguous files
4. Build a file_map (doc_type -> file) used by all downstream checks
5. Rename files to standard names based on classification
6. Run completeness check against the classified map
7. Run vision checks (GeoSite, Red Pen) using the classified file map
8. Generate verdict
"""

import os
import re
import json
import zipfile
import shutil
import base64
import tempfile
import traceback
import gc
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from PIL import Image
import io

import database as db

# PDF text extraction
try:
    from pypdf import PdfReader
    _PYPDF_AVAILABLE = True
except ImportError:
    _PYPDF_AVAILABLE = False

# Spreadsheet inspection
try:
    import openpyxl
    _OPENPYXL_AVAILABLE = True
except ImportError:
    _OPENPYXL_AVAILABLE = False

# PDF-to-image for vision analysis
_PDF2IMAGE_AVAILABLE = True
try:
    from pdf2image import convert_from_path
except ImportError:
    _PDF2IMAGE_AVAILABLE = False


# ---------------------------------------------------------------------------
# OpenAI client (lazy init)
# ---------------------------------------------------------------------------
_client = None

def get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        base_url = os.environ.get("OPENAI_BASE_URL")
        if base_url:
            _client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            _client = OpenAI(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
JUNK_PATTERNS = {"__macosx", ".ds_store", "thumbs.db", ".tmp", "~$"}
LICENCE_BAD_EXTS = {".heic", ".msg", ".webp"}
MAX_IMAGE_DIM = 1024

# NOTE: Gas cooktops are permitted in all estates including those with gas-ban covenants.
# Stockland ruling overrides covenant restrictions. Do NOT flag gas cooktops.

# Standard document types and their canonical filenames
DOC_TYPES = {
    "pse_doc": "PSE Doc (Signed)",
    "pse_excel": "PSE Excel",
    "geosite": "GeoSite Plan (Signed)",
    "itp": "ITP Form (Signed)",
    "deposit_receipt": "Deposit Receipt",
    "drivers_licence": "Drivers Licence",
    "pse_checklist": "PSE Checklist",
    "red_pen": "Red Pen Markup (Signed)",
    "pod_envelope": "POD or Building Envelope",
    "compaction_report": "Compaction Report",
    "covenant_guidelines": "Covenant Design Guidelines",
    "disclosure_plan": "Disclosure Plan",
    "covenant_application": "Covenant Application (Signed)",
    "pool_form": "Pool Form (Signed)",
    "discount_approval": "Discount Approval",
    "owner_supplied": "Owner Supplied Items Approval",
    "modified_plan": "Modified Plan Approval",
    "promo_ack": "Promo Client Acknowledgement (Signed)",
    "fall_ack": "Sites with Fall Acknowledgment (Signed)",
    "acoustic_report": "Acoustic Report",
    "bal_report": "BAL Report",
    "contour_survey": "Contour Survey",
    "sales_accept": "Sales Accept Doc",
    "soil_report": "Soil Report",
    "covenant_doc": "Covenant",
}

# Core required documents (missing = issue)
CORE_REQUIRED = ["pse_doc", "pse_excel", "geosite", "itp", "deposit_receipt", "drivers_licence"]
# Soft required (missing = warning)
SOFT_REQUIRED = ["pse_checklist"]
# Conditionally expected (missing = warning if not explained)
CONDITIONAL_EXPECTED = ["disclosure_plan", "pod_envelope"]


# ---------------------------------------------------------------------------
# Text extraction utilities
# ---------------------------------------------------------------------------
def extract_pdf_text(pdf_path: str, max_pages: int = 5) -> str:
    """Extract text from a PDF using pypdf. Returns empty string on failure."""
    if not _PYPDF_AVAILABLE:
        return ""
    try:
        reader = PdfReader(pdf_path)
        text_parts = []
        for i, page in enumerate(reader.pages[:max_pages]):
            t = page.extract_text() or ""
            text_parts.append(t)
        return "\n".join(text_parts)[:8000]  # Cap at 8K chars to save memory
    except Exception as e:
        print(f"[WARN] extract_pdf_text failed for {pdf_path}: {e}")
        return ""


def extract_spreadsheet_info(file_path: str) -> dict:
    """Extract sheet names and header row from xlsx/xlsm. Returns dict with metadata."""
    if not _OPENPYXL_AVAILABLE:
        return {"type": "spreadsheet", "sheets": [], "headers": [], "sample_text": ""}
    try:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        sheets = wb.sheetnames
        headers = []
        sample_values = []
        for sheet_name in sheets[:3]:
            ws = wb[sheet_name]
            row_count = 0
            for row in ws.iter_rows(max_row=10, values_only=True):
                row_count += 1
                row_text = [str(c).lower() for c in row if c is not None]
                if row_count <= 2:
                    headers.extend(row_text)
                sample_values.extend(row_text)
        wb.close()
        return {
            "type": "spreadsheet",
            "sheets": sheets,
            "headers": headers,
            "sample_text": " ".join(sample_values)[:4000],
        }
    except Exception as e:
        print(f"[WARN] extract_spreadsheet_info failed for {file_path}: {e}")
        return {"type": "spreadsheet", "sheets": [], "headers": [], "sample_text": ""}


# ---------------------------------------------------------------------------
# Image/PDF conversion utilities (for vision analysis)
# ---------------------------------------------------------------------------
def _resize_image(img: Image.Image, max_dim: int = MAX_IMAGE_DIM) -> Image.Image:
    w, h = img.size
    if w <= max_dim and h <= max_dim:
        return img
    if w > h:
        new_w = max_dim
        new_h = int(h * max_dim / w)
    else:
        new_h = max_dim
        new_w = int(w * max_dim / h)
    return img.resize((new_w, new_h), Image.LANCZOS)


def _img_to_b64(img: Image.Image) -> str:
    img = _resize_image(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    buf.close()
    return b64


def pdf_page_to_base64(pdf_path: str, page_num: int = 0, dpi: int = 100) -> str:
    if not _PDF2IMAGE_AVAILABLE:
        return ""
    try:
        images = convert_from_path(pdf_path, first_page=page_num + 1, last_page=page_num + 1, dpi=dpi)
        if not images:
            return ""
        b64 = _img_to_b64(images[0])
        for img in images:
            img.close()
        del images
        gc.collect()
        return b64
    except Exception as e:
        print(f"[WARN] pdf_page_to_base64 failed for {pdf_path} page {page_num}: {e}")
        return ""


def pdf_all_pages_to_base64(pdf_path: str, dpi: int = 100, max_pages: int = 3) -> list[str]:
    if not _PDF2IMAGE_AVAILABLE:
        return []
    try:
        images = convert_from_path(pdf_path, dpi=dpi, last_page=max_pages)
        results = []
        for img in images[:max_pages]:
            results.append(_img_to_b64(img))
            img.close()
        del images
        gc.collect()
        return results
    except Exception as e:
        print(f"[WARN] pdf_all_pages_to_base64 failed for {pdf_path}: {e}")
        return []


def image_to_base64(img_path: str) -> str:
    try:
        with Image.open(img_path) as img:
            return _img_to_b64(img)
    except Exception as e:
        print(f"[WARN] image_to_base64 failed for {img_path}: {e}")
        return ""


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------
def call_text_model(system_prompt: str, user_prompt: str, model: str = "gpt-4.1-nano") -> str:
    resp = get_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=4000,
    )
    return resp.choices[0].message.content.strip()


def call_vision_model(system_prompt: str, user_text: str, image_b64_list: list[str], model: str = "gpt-4.1-mini") -> str:
    content = [{"type": "text", "text": user_text}]
    for b64 in image_b64_list:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
        })
    resp = get_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        temperature=0.1,
        max_tokens=4000,
    )
    return resp.choices[0].message.content.strip()


def parse_json_from_llm(raw: str) -> dict:
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    return {"raw_response": raw}


# ---------------------------------------------------------------------------
# CONTENT-BASED DOCUMENT CLASSIFIER
# ---------------------------------------------------------------------------
# Text fingerprints: keywords/phrases that identify each document type from content.
# Checked against extracted PDF text or spreadsheet content.
# Order matters — more specific patterns first to avoid misclassification.
CONTENT_FINGERPRINTS = [
    # PSE Doc — the main Provisional Sales Estimate PDF
    ("pse_doc", {
        "keywords": ["provisional sales estimate", "total contract price", "base price",
                      "inclusions and exclusions", "pse document", "sales estimate"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # ITP — Intention to Purchase agreement
    ("itp", {
        "keywords": ["intention to purchase", "purchaser details", "purchaser name",
                      "intention to proceed", "purchase agreement", "itp form"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # GeoSite Plan
    ("geosite", {
        "keywords": ["geosite", "geo site", "geosite.com.au", "site plan",
                      "setback", "site coverage", "building envelope"],
        "min_matches": 2,  # Need 2+ because "setback" alone is too generic
        "extensions": [".pdf", ".jpg", ".jpeg", ".png"],
    }),
    # Red Pen Markup
    ("red_pen", {
        "keywords": ["red pen", "markup", "floor plan", "elevations", "electrical plan",
                      "floor coverings", "concrete plan"],
        "min_matches": 2,
        "extensions": [".pdf", ".jpg", ".jpeg", ".png"],
    }),
    # PSE Checklist
    ("pse_checklist", {
        "keywords": ["checklist", "pse checklist", "document checklist",
                      "yes/no", "yes / no", "completed"],
        "min_matches": 2,
        "extensions": [".pdf"],
    }),
    # Deposit Receipt
    ("deposit_receipt", {
        "keywords": ["deposit receipt", "receipt", "payment received", "deposit amount",
                      "$2,500", "$4,000", "$2500", "$4000", "preliminary deposit"],
        "min_matches": 1,
        "extensions": [".pdf", ".jpg", ".jpeg", ".png"],
    }),
    # Covenant Application
    ("covenant_application", {
        "keywords": ["covenant application", "design approval application",
                      "design review application", "covenant form"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Pool Form
    ("pool_form", {
        "keywords": ["swimming pool", "pool form", "pool fencing",
                      "pool barrier", "pool safety"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Sites with Fall Acknowledgment
    ("fall_ack", {
        "keywords": ["sites with fall", "fall acknowledgment", "fall acknowledgement",
                      "cut and fill", "retaining wall acknowledgment"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Promo Client Acknowledgement
    ("promo_ack", {
        "keywords": ["client acknowledgement", "promotion acknowledgement",
                      "advantage", "super saver", "promo acknowledgement",
                      "promotional offer"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Acoustic Report
    ("acoustic_report", {
        "keywords": ["acoustic", "noise assessment", "acoustic report",
                      "noise category", "acoustic category", "db(a)"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # BAL Report
    ("bal_report", {
        "keywords": ["bushfire attack level", "bal report", "bal assessment",
                      "bushfire prone", "bal-"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Compaction Report
    ("compaction_report", {
        "keywords": ["compaction", "bearing capacity", "compaction test",
                      "soil compaction", "density ratio"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Soil Report
    ("soil_report", {
        "keywords": ["soil test", "geotechnical", "soil classification",
                      "site classification", "foundation recommendation",
                      "reactive soil"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Contour Survey
    ("contour_survey", {
        "keywords": ["contour survey", "topographic survey", "contour plan",
                      "reduced levels", "spot levels", "topographical"],
        "min_matches": 1,
        "extensions": [".pdf", ".jpg", ".jpeg", ".png"],
    }),
    # Disclosure Plan / Survey Plan
    ("disclosure_plan", {
        "keywords": ["disclosure plan", "plan of subdivision", "survey plan",
                      "plan of survey", "disclosure statement", "community title"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # POD or Building Envelope
    ("pod_envelope", {
        "keywords": ["building envelope", "plan of development", "pod plan",
                      "envelope plan", "development plan"],
        "min_matches": 1,
        "extensions": [".pdf", ".jpg", ".jpeg", ".png"],
    }),
    # Covenant Design Guidelines
    ("covenant_guidelines", {
        "keywords": ["design guidelines", "covenant guidelines", "architectural guidelines",
                      "design requirements", "building covenant"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Discount Approval
    ("discount_approval", {
        "keywords": ["discount approval", "discount form", "discount authorisation",
                      "discount authorization", "approved discount"],
        "min_matches": 1,
        "extensions": [".pdf", ".jpg", ".jpeg", ".png"],
    }),
    # Owner Supplied Items
    ("owner_supplied", {
        "keywords": ["owner supplied", "owner supply", "client supplied",
                      "owner provided items"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Modified Plan Approval
    ("modified_plan", {
        "keywords": ["modified plan", "plan modification", "plan amendment",
                      "modified plan approval"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Sales Accept Doc
    ("sales_accept", {
        "keywords": ["sales accept", "sales acceptance", "acceptance document"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
    # Covenant document (generic)
    ("covenant_doc", {
        "keywords": ["covenant", "building covenant", "restrictive covenant",
                      "community management statement"],
        "min_matches": 1,
        "extensions": [".pdf"],
    }),
]

# Spreadsheet fingerprints (for PSE Excel)
SPREADSHEET_EXTS = {".xlsx", ".xlsm", ".xls", ".csv"}


def classify_by_content(file_info: dict, text_content: str, spreadsheet_info: dict | None) -> tuple[str | None, float]:
    """
    Classify a file by its content. Returns (doc_type_key, confidence).
    confidence: 1.0 = certain, 0.5 = probable, 0.0 = unknown.
    """
    ext = Path(file_info["name"]).suffix.lower()
    text_lower = text_content.lower() if text_content else ""
    fn_lower = file_info["name"].lower()

    # --- Spreadsheet files → PSE Excel ---
    if ext in SPREADSHEET_EXTS:
        # Any spreadsheet with PSE-related content or just being a spreadsheet in this context
        # is almost certainly the PSE Excel
        if spreadsheet_info:
            sample = spreadsheet_info.get("sample_text", "").lower()
            if any(kw in sample for kw in ["pse", "provisional", "price", "estimate", "inclusions",
                                            "base price", "total", "contract"]):
                return "pse_excel", 1.0
        # Even without keyword match, a spreadsheet in a PSE zip is very likely the PSE Excel
        return "pse_excel", 0.8

    # --- Image files: check if it's a drivers licence or other image doc ---
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff"):
        # Drivers licence is typically a photo/scan — we'll use LLM for images later
        # For now, check filename hints as a weak signal (but content takes priority)
        return None, 0.0  # Will be classified by LLM vision

    # --- PDF files: classify by extracted text ---
    if ext == ".pdf":
        if not text_lower:
            # No text extracted (scanned PDF / image-only) — needs vision classification
            return None, 0.0

        # Score each document type
        best_type = None
        best_score = 0

        for doc_type, fingerprint in CONTENT_FINGERPRINTS:
            if ext not in fingerprint["extensions"]:
                continue
            matches = sum(1 for kw in fingerprint["keywords"] if kw in text_lower)
            if matches >= fingerprint["min_matches"] and matches > best_score:
                best_score = matches
                best_type = doc_type

        if best_type:
            confidence = min(1.0, 0.5 + best_score * 0.15)
            return best_type, confidence

    return None, 0.0


def classify_by_vision(file_info: dict) -> tuple[str | None, float]:
    """Use LLM vision to classify an image or scanned PDF. Returns (doc_type_key, confidence)."""
    ext = Path(file_info["name"]).suffix.lower()
    full_path = file_info["full_path"]

    try:
        if ext == ".pdf":
            pages_b64 = pdf_all_pages_to_base64(full_path, dpi=100, max_pages=1)
            if not pages_b64:
                return None, 0.0
            image_b64_list = pages_b64
        elif ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff"):
            b64 = image_to_base64(full_path)
            if not b64:
                return None, 0.0
            image_b64_list = [b64]
        else:
            return None, 0.0

        doc_type_list = ", ".join(f'"{k}": {v}' for k, v in DOC_TYPES.items())

        system_prompt = f"""You are a document classifier for AUSMAR home building submissions.
Classify this document into ONE of these types:
{doc_type_list}

If it's a photo of a person's ID card or driver's licence, classify as "drivers_licence".
If it's a site plan with lot dimensions, setbacks, and house position, classify as "geosite".
If it's floor plans with red/coloured markings showing changes, classify as "red_pen".
If it's a building envelope or POD plan, classify as "pod_envelope".
If it's a deposit/payment receipt, classify as "deposit_receipt".
If it's a contour/topographic survey, classify as "contour_survey".
If it doesn't match any type, respond with "unknown".

Respond with ONLY a JSON object: {{"doc_type": "type_key", "confidence": 0.0-1.0, "reason": "brief reason"}}"""

        raw = call_vision_model(system_prompt, "Classify this document.", image_b64_list)
        result = parse_json_from_llm(raw)
        doc_type = result.get("doc_type", "unknown")
        confidence = float(result.get("confidence", 0.5))

        if doc_type and doc_type != "unknown" and doc_type in DOC_TYPES:
            return doc_type, confidence
        return None, 0.0

    except Exception as e:
        print(f"[WARN] classify_by_vision failed for {file_info['name']}: {e}")
        return None, 0.0


def classify_all_files(files: list[dict], progress_callback=None) -> dict:
    """
    Classify all files by content. Returns:
    {
        "file_map": {doc_type_key: file_info_with_classification},
        "unclassified": [file_info_list],
        "classifications": {filename: {"doc_type": ..., "confidence": ..., "method": ...}},
    }
    """
    file_map = {}  # doc_type -> file_info
    unclassified = []
    classifications = {}

    # Phase 1: Text-based classification (fast, free)
    for f in files:
        ext = Path(f["name"]).suffix.lower()
        text_content = ""
        spreadsheet_info = None

        if ext == ".pdf":
            text_content = extract_pdf_text(f["full_path"])
        elif ext in SPREADSHEET_EXTS:
            spreadsheet_info = extract_spreadsheet_info(f["full_path"])
            text_content = spreadsheet_info.get("sample_text", "") if spreadsheet_info else ""

        doc_type, confidence = classify_by_content(f, text_content, spreadsheet_info)

        if doc_type and confidence >= 0.5:
            # If this doc_type already has a file, keep the higher confidence one
            if doc_type in file_map:
                existing = classifications.get(file_map[doc_type]["name"], {})
                if confidence > existing.get("confidence", 0):
                    # Demote existing to unclassified
                    unclassified.append(file_map[doc_type])
                    file_map[doc_type] = f
                    classifications[f["name"]] = {
                        "doc_type": doc_type, "confidence": confidence, "method": "text",
                        "extracted_text_preview": text_content[:200] if text_content else "",
                    }
                else:
                    unclassified.append(f)
                    classifications[f["name"]] = {
                        "doc_type": doc_type, "confidence": confidence, "method": "text",
                        "note": f"Duplicate — {file_map[doc_type]['name']} already classified as {doc_type}",
                    }
            else:
                file_map[doc_type] = f
                classifications[f["name"]] = {
                    "doc_type": doc_type, "confidence": confidence, "method": "text",
                    "extracted_text_preview": text_content[:200] if text_content else "",
                }
        else:
            unclassified.append(f)
            classifications[f["name"]] = {
                "doc_type": None, "confidence": 0, "method": "text",
                "note": "No text match — queued for vision classification",
            }

    # Phase 2: Vision-based classification for unclassified files (costs API calls)
    still_unclassified = []
    for f in unclassified:
        ext = Path(f["name"]).suffix.lower()
        if ext not in (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff"):
            still_unclassified.append(f)
            continue

        doc_type, confidence = classify_by_vision(f)
        if doc_type and confidence >= 0.4:
            if doc_type in file_map:
                existing = classifications.get(file_map[doc_type]["name"], {})
                if confidence > existing.get("confidence", 0):
                    still_unclassified.append(file_map[doc_type])
                    file_map[doc_type] = f
                    classifications[f["name"]] = {
                        "doc_type": doc_type, "confidence": confidence, "method": "vision",
                    }
                else:
                    still_unclassified.append(f)
            else:
                file_map[doc_type] = f
                classifications[f["name"]] = {
                    "doc_type": doc_type, "confidence": confidence, "method": "vision",
                }
        else:
            still_unclassified.append(f)
            classifications[f["name"]] = classifications.get(f["name"], {})
            classifications[f["name"]]["vision_result"] = "unclassified"

    return {
        "file_map": file_map,
        "unclassified": still_unclassified,
        "classifications": classifications,
    }


# ---------------------------------------------------------------------------
# Check 1: File Structure (extract, flatten, remove junk)
# ---------------------------------------------------------------------------
def check_file_structure(extract_dir: str, zip_name: str) -> dict:
    issues = []
    warnings = []
    corrections = []
    files = []

    for root, dirs, filenames in os.walk(extract_dir):
        rel_root = os.path.relpath(root, extract_dir)
        for fn in filenames:
            if fn.startswith(".") or "__MACOSX" in root:
                continue
            rel_path = os.path.join(rel_root, fn) if rel_root != "." else fn
            full_path = os.path.join(root, fn)
            files.append({"name": fn, "rel_path": rel_path, "full_path": full_path})

    # Flatten subfolders
    has_subfolders = False
    for f in files:
        if os.path.dirname(f["rel_path"]) and os.path.dirname(f["rel_path"]) != ".":
            has_subfolders = True
            new_path = os.path.join(extract_dir, f["name"])
            if not os.path.exists(new_path):
                shutil.move(f["full_path"], new_path)
                corrections.append(f"Flattened: moved '{f['rel_path']}' to zip root")
                f["full_path"] = new_path
                f["rel_path"] = f["name"]

    if has_subfolders:
        corrections.append("Removed subfolder structure — all files now flat in zip root")
        for root, dirs, _ in os.walk(extract_dir, topdown=False):
            for d in dirs:
                dp = os.path.join(root, d)
                try:
                    os.rmdir(dp)
                except Exception:
                    pass

    # Check zip name format
    zip_stem = Path(zip_name).stem
    deal_code_pattern = re.compile(r"^[A-Za-z]\d{2}[A-Za-z]{2,6}$")
    if not deal_code_pattern.match(zip_stem):
        warnings.append(f"Zip name '{zip_name}' may not be a valid deal code (expected format like S26TLS)")

    # Remove junk files
    cleaned_files = []
    seen_names = set()

    for f in files:
        fn = f["name"]
        fn_lower = fn.lower()

        is_junk = any(pat in fn_lower for pat in JUNK_PATTERNS)
        if fn_lower.endswith(".msg"):
            is_junk = True
        if is_junk:
            try:
                os.remove(f["full_path"])
            except Exception:
                pass
            corrections.append(f"Removed junk file: '{fn}'")
            continue

        if f["name"].lower() in seen_names:
            issues.append(f"Duplicate file: '{f['name']}'")
        seen_names.add(f["name"].lower())
        cleaned_files.append(f)

    return {
        "files": cleaned_files,
        "issues": issues,
        "warnings": warnings,
        "corrections": corrections,
        "zip_stem": zip_stem,
    }


# ---------------------------------------------------------------------------
# Check 2: Document Completeness (content-based)
# ---------------------------------------------------------------------------
def check_document_completeness(file_map: dict, unclassified: list) -> dict:
    """Check completeness using the content-classified file_map, NOT filenames."""
    issues = []
    warnings = []
    found = {}
    found_conditional = []

    # Core required
    for doc_key in CORE_REQUIRED:
        canonical = DOC_TYPES[doc_key]
        if doc_key in file_map:
            found[canonical] = file_map[doc_key]["name"]
        else:
            issues.append(f"Missing: {canonical}")

    # Soft required
    for doc_key in SOFT_REQUIRED:
        canonical = DOC_TYPES[doc_key]
        if doc_key in file_map:
            found[canonical] = file_map[doc_key]["name"]
        else:
            warnings.append(f"Missing: {canonical} — should be included per 1.0 PSE Document Naming")

    # Red Pen — special: required for NHP, optional for STC
    if "red_pen" in file_map:
        found["Red Pen Markup (Signed)"] = file_map["red_pen"]["name"]
    else:
        warnings.append("Missing: Red Pen Markup — required for NHP submissions, verify if needed for STC")

    # Conditional documents — note which are present
    conditional_keys = [
        "pod_envelope", "compaction_report", "covenant_guidelines", "disclosure_plan",
        "covenant_application", "pool_form", "discount_approval", "owner_supplied",
        "modified_plan", "promo_ack", "fall_ack", "acoustic_report", "bal_report",
        "contour_survey", "sales_accept", "soil_report", "covenant_doc",
    ]
    for doc_key in conditional_keys:
        if doc_key in file_map:
            found_conditional.append(DOC_TYPES[doc_key])

    # Expected conditionals
    for doc_key in CONDITIONAL_EXPECTED:
        canonical = DOC_TYPES[doc_key]
        if doc_key not in file_map:
            warnings.append(f"Missing: {canonical} — required per 1.0 naming unless explained")

    # Drivers licence format check
    if "drivers_licence" in file_map:
        dl_ext = Path(file_map["drivers_licence"]["name"]).suffix.lower()
        if dl_ext in LICENCE_BAD_EXTS:
            issues.append(f"Drivers Licence in {dl_ext} format — needs .jpg, .png, or .pdf")

    # Note unclassified files
    if unclassified:
        unc_names = [f["name"] for f in unclassified]
        warnings.append(f"Unclassified files (could not determine document type): {', '.join(unc_names)}")

    return {
        "found": found,
        "issues": issues,
        "warnings": warnings,
        "conditional_found": found_conditional,
    }


# ---------------------------------------------------------------------------
# Rename files to standard names based on classification
# ---------------------------------------------------------------------------
def rename_classified_files(extract_dir: str, file_map: dict, classifications: dict) -> list[str]:
    """Rename files to their canonical names. Returns list of correction descriptions."""
    corrections = []

    for doc_key, file_info in file_map.items():
        canonical_name = DOC_TYPES.get(doc_key)
        if not canonical_name:
            continue

        old_name = file_info["name"]
        ext = Path(old_name).suffix
        new_name = canonical_name + ext

        if new_name != old_name:
            old_path = file_info["full_path"]
            new_path = os.path.join(extract_dir, new_name)

            # Avoid overwriting
            if os.path.exists(new_path) and new_path != old_path:
                # Add a suffix to avoid collision
                stem = canonical_name
                counter = 2
                while os.path.exists(new_path):
                    new_name = f"{stem} ({counter}){ext}"
                    new_path = os.path.join(extract_dir, new_name)
                    counter += 1

            try:
                os.rename(old_path, new_path)
                corrections.append(f"Renamed '{old_name}' -> '{new_name}' (identified as {canonical_name} by content)")
                file_info["name"] = new_name
                file_info["full_path"] = new_path
                file_info["rel_path"] = new_name
            except Exception as e:
                print(f"[WARN] rename failed for {old_name}: {e}")

    return corrections


# ---------------------------------------------------------------------------
# Check 3: GeoSite Verification (Vision) — uses file_map
# ---------------------------------------------------------------------------
def check_geosite(file_map: dict) -> dict:
    if "geosite" not in file_map:
        return {
            "issues": ["No GeoSite file found in submission — CRITICAL per 1.0 PSE Document Naming"],
            "warnings": [], "analysis": {}, "lot_dimensions": None,
        }

    geosite_file = file_map["geosite"]
    geosite_path = geosite_file["full_path"]
    ext = Path(geosite_path).suffix.lower()

    try:
        vision_warning = None
        if ext == ".pdf":
            pages_b64 = pdf_all_pages_to_base64(geosite_path, dpi=100, max_pages=3)
            if not pages_b64:
                vision_warning = "PDF vision analysis skipped — poppler unavailable or PDF conversion failed"
        elif ext in (".jpg", ".jpeg", ".png"):
            b64 = image_to_base64(geosite_path)
            pages_b64 = [b64] if b64 else []
            if not pages_b64:
                vision_warning = "Image conversion failed for GeoSite"
        else:
            return {
                "issues": [f"GeoSite in unsupported format: {ext}"],
                "warnings": [], "analysis": {}, "lot_dimensions": None,
            }

        if not pages_b64:
            return {
                "issues": [],
                "warnings": [vision_warning or "Could not convert GeoSite to images — vision analysis skipped"],
                "analysis": {}, "lot_dimensions": None,
            }

        fp_notes = _get_fp_notes("geosite_verification")

        system_prompt = f"""You are an AUSMAR QA reviewer analysing a GeoSite document.

RULES (from real review feedback):
- A valid GeoSite MUST be generated from geosite.com.au (NOT a Site Visit Checklist or hand-drawn plan)
- GeoSite MUST be separate from any site visit documentation or contour plans
- House MUST be sited at scale on the lot
- ALL setback dimensions MUST be shown (front, rear, left side, right side)
- Text must be readable, not overlapping
- Customer signatures required
- If contours are overlaid on the GeoSite making it hard to read, flag this (real issue from S26TLS)
- SCRC front setback to OMP is typically 4.5m minimum

Analyse the image(s) and report in JSON format:
{{
  "is_geosite_tool": true/false,
  "is_combined_with_contours": true/false,
  "house_sited_at_scale": true/false,
  "setback_dimensions_shown": true/false,
  "text_readable": true/false,
  "geo_plan_id_visible": true/false/null,
  "consultant_name": "name or null",
  "customer_signatures_present": true/false,
  "site_coverage_percent": number or null,
  "site_area_m2": number or null,
  "build_area_m2": number or null,
  "home_design": "plan name or null",
  "facade_name": "facade name or null",
  "estate_name": "name or null",
  "lot_number": "number or null",
  "street_address": "address or null",
  "sp_number": "value or null",
  "lot_width_m": number or null,
  "lot_length_m": number or null,
  "lot_area_m2": number or null,
  "side_setback_left_m": number or null,
  "side_setback_right_m": number or null,
  "front_setback_m": number or null,
  "rear_setback_m": number or null,
  "fall_across_site_mm": number or null,
  "is_battle_axe_lot": true/false/null,
  "concerns": ["list of concerns"],
  "notes": "additional observations"
}}

Extract ALL dimensions you can see. Be precise with numbers. If you cannot determine a value, use null.
Do NOT flag issues you are uncertain about — only flag clear problems.{fp_notes}"""

        raw = call_vision_model(
            system_prompt,
            "Analyse this GeoSite document. Extract all dimensions, check all required elements, and identify any concerns.",
            pages_b64,
        )
        analysis = parse_json_from_llm(raw)

        issues = []
        warnings = []

        if analysis.get("is_geosite_tool") is False:
            issues.append(
                "CRITICAL: Document does not appear to be from geosite.com.au — "
                "may be a Site Visit Checklist or hand-drawn plan. Must use geosite.com.au tool."
            )
        if analysis.get("is_combined_with_contours") is True:
            warnings.append(
                "GeoSite appears combined with contour data — Heath requires these to be separate documents. "
                "Contours overlaid on GeoSite make it unreadable (real issue from S26TLS review)."
            )
        if analysis.get("house_sited_at_scale") is False:
            issues.append("House not sited at scale on the lot — cannot verify fit")
        if analysis.get("setback_dimensions_shown") is False:
            issues.append(
                "Setback dimensions not shown on GeoSite — MUST have all setbacks "
                "(front, rear, left side, right side) for drafting team"
            )
        if analysis.get("text_readable") is False:
            warnings.append("Text on GeoSite is overlapping or hard to read — may cause issues for drafting")
        if analysis.get("customer_signatures_present") is False:
            warnings.append("Customer signature(s) may be missing from GeoSite — required per 1.0 naming")

        front_sb = analysis.get("front_setback_m")
        if front_sb is not None and isinstance(front_sb, (int, float)):
            if front_sb < 4.5:
                warnings.append(
                    f"Front setback is {front_sb}m — SCRC minimum is typically 4.5m to OMP. "
                    f"Verify with council requirements."
                )

        for side_key, side_label in [("side_setback_left_m", "Left"), ("side_setback_right_m", "Right")]:
            val = analysis.get(side_key)
            if val is not None and isinstance(val, (int, float)):
                if val < 0.6:
                    issues.append(
                        f"RED FLAG: {side_label} side setback is {val}m (under 0.6m) — "
                        f"plan may be too wide for this lot"
                    )

        coverage = analysis.get("site_coverage_percent")
        if coverage is not None and isinstance(coverage, (int, float)):
            if coverage > 60:
                issues.append(f"Site coverage {coverage}% exceeds 60% maximum")
            elif coverage > 58:
                warnings.append(
                    f"Site coverage {coverage}% is very close to 60% maximum — zero margin. "
                    f"Verify with covenant (real issue from S26JYTC review)."
                )

        if analysis.get("is_battle_axe_lot") is True:
            warnings.append(
                "Lot appears to be battle-axe configuration — may require LHDC "
                "(Livable Housing Design Standards) assessment. Verify with council."
            )

        fall_mm = analysis.get("fall_across_site_mm")
        if fall_mm is not None and isinstance(fall_mm, (int, float)):
            if fall_mm >= 500:
                warnings.append(
                    f"Site fall detected: {fall_mm}mm — Sites with Fall Acknowledgment (Signed) required. "
                    f"Max cut/fill before building manager approval is 1000mm."
                )
            if fall_mm >= 1000:
                issues.append(
                    f"Significant site fall: {fall_mm}mm — may require contour survey, "
                    f"retaining wall engineering, and council application"
                )

        if not analysis.get("home_design"):
            warnings.append("Home Design field not completed on GeoSite")

        lot_dims = {
            "width": analysis.get("lot_width_m"),
            "length": analysis.get("lot_length_m"),
            "area": analysis.get("lot_area_m2") or analysis.get("site_area_m2"),
        }

        return {
            "issues": issues, "warnings": warnings,
            "analysis": analysis, "lot_dimensions": lot_dims,
        }

    except Exception as e:
        traceback.print_exc()
        return {
            "issues": [],
            "warnings": [f"GeoSite vision analysis skipped due to error: {str(e)}"],
            "analysis": {}, "lot_dimensions": None,
        }


# ---------------------------------------------------------------------------
# Check 4: Plan-to-Lot Fit
# ---------------------------------------------------------------------------
def check_plan_to_lot_fit(geosite_result: dict, file_map: dict) -> dict:
    issues = []
    warnings = []
    analysis = geosite_result.get("analysis", {})
    lot_dims = geosite_result.get("lot_dimensions", {})

    if not analysis or isinstance(analysis, str):
        return {
            "issues": ["Cannot verify plan-to-lot fit — GeoSite analysis unavailable"],
            "warnings": [], "plan_identified": None, "fit_result": "UNKNOWN",
        }

    home_design = analysis.get("home_design", "") or ""

    plans = db.get_all_plans()
    matched_plan = None

    for p in plans:
        if p["name"].lower() in home_design.lower():
            matched_plan = p
            break

    if not matched_plan:
        for p in plans:
            words = p["name"].lower().split()
            if all(w in home_design.lower() for w in words):
                matched_plan = p
                break

    if not matched_plan:
        for p in plans:
            family = p["name"].lower().split()[0]
            if len(family) > 3 and family in home_design.lower():
                matched_plan = p
                break

    if not matched_plan:
        note = (
            f"Plan '{home_design}' not in known plans database"
            if home_design
            else "Could not identify plan from GeoSite"
        )
        warnings.append(f"{note} — manual plan-to-lot fit check required by Heath")
        return {
            "issues": issues, "warnings": warnings,
            "plan_identified": home_design, "fit_result": "MANUAL CHECK REQUIRED",
            "plan_specs": None, "lot_dimensions": lot_dims,
        }

    lot_width = lot_dims.get("width") if lot_dims else None
    lot_length = lot_dims.get("length") if lot_dims else None
    fit_result = "PASS"

    if lot_width is not None and isinstance(lot_width, (int, float)):
        if lot_width < matched_plan["min_width"]:
            issues.append(
                f"CRITICAL: {matched_plan['name']} requires minimum {matched_plan['min_width']}m site width "
                f"but lot is only {lot_width}m wide. Plan DOES NOT FIT."
            )
            fit_result = "FAIL — PLAN TOO WIDE"
        elif lot_width < matched_plan["min_width"] + 0.5:
            warnings.append(
                f"Tight fit: {matched_plan['name']} needs {matched_plan['min_width']}m width, "
                f"lot is {lot_width}m. Only {lot_width - matched_plan['min_width']:.1f}m margin."
            )
            fit_result = "TIGHT FIT"
    else:
        warnings.append("Could not extract lot width from GeoSite — manual verification needed")

    if lot_length is not None and isinstance(lot_length, (int, float)):
        if lot_length < matched_plan["min_length"]:
            issues.append(
                f"CRITICAL: {matched_plan['name']} requires minimum {matched_plan['min_length']}m depth "
                f"but lot is only {lot_length}m. Plan DOES NOT FIT."
            )
            if fit_result == "PASS":
                fit_result = "FAIL — LOT TOO SHORT"

    build_area = analysis.get("build_area_m2")
    if build_area and matched_plan.get("total_area"):
        try:
            ba = float(str(build_area).replace("m2", "").replace("m²", "").strip())
            if ba > matched_plan["total_area"] * 1.15:
                warnings.append(
                    f"Build area ({ba}m²) significantly exceeds {matched_plan['name']} "
                    f"standard area ({matched_plan['total_area']}m²) — verify modifications"
                )
        except (ValueError, TypeError):
            pass

    return {
        "issues": issues, "warnings": warnings,
        "plan_identified": matched_plan["name"], "plan_specs": matched_plan,
        "lot_dimensions": lot_dims, "fit_result": fit_result,
    }


# ---------------------------------------------------------------------------
# Check 5: Red Pen Markup (Vision) — uses file_map
# ---------------------------------------------------------------------------
def check_red_pen(file_map: dict, deposit_type: str) -> dict:
    if "red_pen" not in file_map:
        if deposit_type == "NHP":
            return {
                "issues": ["Missing Red Pen Markup — required for NHP ($2,500) submissions per 1.0 naming"],
                "warnings": [], "analysis": {},
            }
        else:
            return {
                "issues": [],
                "warnings": ["No Red Pen Markup found — acceptable for STC ($4,000) if no structural changes"],
                "analysis": {},
            }

    redpen_file = file_map["red_pen"]
    redpen_path = redpen_file["full_path"]
    ext = Path(redpen_path).suffix.lower()

    try:
        vision_warning = None
        if ext == ".pdf":
            pages_b64 = pdf_all_pages_to_base64(redpen_path, dpi=100, max_pages=5)
            if not pages_b64:
                vision_warning = "PDF vision analysis skipped — poppler unavailable or PDF conversion failed"
        elif ext in (".jpg", ".jpeg", ".png"):
            b64 = image_to_base64(redpen_path)
            pages_b64 = [b64] if b64 else []
            if not pages_b64:
                vision_warning = "Image conversion failed for Red Pen"
        else:
            return {
                "issues": [f"Red Pen in unsupported format: {ext}"],
                "warnings": [], "analysis": {},
            }

        if not pages_b64:
            return {
                "issues": [],
                "warnings": [vision_warning or "Could not convert Red Pen to images — vision analysis skipped"],
                "analysis": {},
            }

        fp_notes = _get_fp_notes("red_pen_markup")

        deposit_desc = (
            "NHP ($2,500) — red pen markups ARE required, must be RED on AUSMAR base plan with dimensions"
            if deposit_type == "NHP"
            else "STC ($4,000) — clean plans expected, no structural changes"
        )

        system_prompt = f"""You are an AUSMAR QA reviewer analysing Red Pen Markup documents.
This is a {deposit_desc} submission.

RULES (from real review feedback — these are actual rejection reasons):
- NHP markups MUST be in RED colour on the AUSMAR standard base plan
- Unchanged areas in black, changed areas in RED and dimensioned
- All changed areas MUST have dimensions (real rejection reason from S25MLS)
- Red pen tags (e.g. 3.2.a) should match PSE section references (real issue from S26SDN)
- Must NOT be produced on consultant's own program — must overlay AUSMAR standard plan (S26TLS rejection)
- Per 1.0 naming, Red Pen should cover: Floor Plan, Elevations, Electrical Plan, Floor Coverings Plan, Concrete Plan
- Customer must sign/initial the markup
- Flag if windows are deleted from plan but not noted
- Flag facade changes not captured in markup

Analyse and report in JSON:
{{
  "markup_colour": "red/yellow/green/pink/black/mixed/none",
  "is_red_colour": true/false,
  "is_on_ausmar_base_plan": true/false,
  "has_dimensions_on_changes": true/false,
  "customer_signed": true/false,
  "plan_types_covered": ["list: floor_plan, elevations, electrical, floor_coverings, concrete"],
  "missing_plan_types": ["list of required types not found"],
  "structural_changes_shown": true/false,
  "changes_description": "brief description of what changes are shown",
  "tags_reference_pse_sections": true/false/null,
  "hebel_changeover_noted": true/false,
  "width_reductions_across_plan": true/false,
  "facade_changes_shown": true/false,
  "window_deletions_noted": true/false/null,
  "concerns": [],
  "notes": ""
}}

IMPORTANT: Only flag issues you can clearly see. Do NOT guess or assume problems.{fp_notes}"""

        raw = call_vision_model(system_prompt, "Analyse these Red Pen Markup pages.", pages_b64)
        analysis = parse_json_from_llm(raw)

        issues = []
        warnings = []

        if deposit_type == "NHP":
            if analysis.get("is_red_colour") is False:
                colour = analysis.get("markup_colour", "unknown")
                issues.append(
                    f"Red Pen markups NOT in red (appears {colour}) — "
                    f"must be RED for NHP per AUSMAR standard"
                )
            if analysis.get("is_on_ausmar_base_plan") is False:
                issues.append(
                    "Markups NOT on standard AUSMAR base plan — "
                    "must overlay AUSMAR plan, not consultant's own program "
                    "(real rejection reason from S26TLS)"
                )
            if analysis.get("has_dimensions_on_changes") is False:
                issues.append(
                    "Dimensions missing from changed areas on Red Pen — "
                    "all changes must be dimensioned (real rejection reason from S25MLS)"
                )
            if analysis.get("customer_signed") is False:
                warnings.append("Red Pen may not be signed/initialled by customer")

            missing = analysis.get("missing_plan_types", [])
            if missing:
                warnings.append(
                    f"Red Pen may be missing coverage for: {', '.join(missing)}. "
                    f"Per 1.0 naming, should include Floor Plan, Elevations, Electrical, Floor Coverings, Concrete."
                )

        elif deposit_type == "STC":
            if analysis.get("structural_changes_shown") is True:
                issues.append("Structural changes on STC submission — STC should have clean plans")

        if analysis.get("hebel_changeover_noted"):
            warnings.append(
                "Hebel changeover noted — may be a setback compliance workaround. "
                "Heath should verify this is legitimate."
            )
        if analysis.get("width_reductions_across_plan"):
            warnings.append(
                "Width reductions across entire plan — possible force-fit to lot. "
                "Verify plan-to-lot fit carefully."
            )
        if analysis.get("tags_reference_pse_sections") is False:
            warnings.append(
                "Red pen tags don't appear to match PSE section references "
                "(real issue from S26SDN — tags like 3.2.a should match PSE sections)"
            )

        return {"issues": issues, "warnings": warnings, "analysis": analysis}

    except Exception as e:
        traceback.print_exc()
        return {
            "issues": [],
            "warnings": [f"Red Pen vision analysis skipped due to error: {str(e)}"],
            "analysis": {},
        }


# ---------------------------------------------------------------------------
# Check 6: PSE Excel Analysis — uses file_map
# ---------------------------------------------------------------------------
def check_pse_excel(file_map: dict, geosite_analysis: dict) -> dict:
    if "pse_excel" not in file_map:
        return {
            "issues": ["Missing: PSE Excel — required per 1.0 PSE Document Naming"],
            "warnings": [], "analysis": {},
        }

    issues = []
    warnings = []
    analysis = {}

    facade_name = geosite_analysis.get("facade_name", "") or ""
    if facade_name:
        analysis["geosite_facade"] = facade_name

    # Gas cooktops: NOT flagged. Stockland ruling overrides covenant restrictions.

    return {"issues": issues, "warnings": warnings, "analysis": analysis}


# ---------------------------------------------------------------------------
# Detect deposit type from content (not just filenames)
# ---------------------------------------------------------------------------
def detect_deposit_type(file_map: dict, files: list[dict]) -> str:
    """Detect NHP vs STC from file content and names."""
    # Check ITP content if available
    if "itp" in file_map:
        itp_text = extract_pdf_text(file_map["itp"]["full_path"], max_pages=3)
        itp_lower = itp_text.lower()
        if "nhp" in itp_lower or "$2,500" in itp_lower or "$2500" in itp_lower:
            return "NHP"
        if "stc" in itp_lower or "$4,000" in itp_lower or "$4000" in itp_lower:
            return "STC"

    # Check deposit receipt content
    if "deposit_receipt" in file_map:
        receipt_text = extract_pdf_text(file_map["deposit_receipt"]["full_path"], max_pages=2)
        receipt_lower = receipt_text.lower()
        if "2,500" in receipt_lower or "2500" in receipt_lower:
            return "NHP"
        if "4,000" in receipt_lower or "4000" in receipt_lower:
            return "STC"

    # Fallback: check all filenames
    for f in files:
        fn_lower = f["name"].lower()
        if "nhp" in fn_lower or "2500" in fn_lower or "2,500" in fn_lower:
            return "NHP"
        if "stc" in fn_lower or "4000" in fn_lower or "4,000" in fn_lower:
            return "STC"

    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Build corrected zip
# ---------------------------------------------------------------------------
def build_corrected_zip(extract_dir: str, deal_code: str, output_dir: str) -> str:
    zip_name = f"{deal_code}_corrected.zip" if deal_code else "corrected.zip"
    zip_path = os.path.join(output_dir, zip_name)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in sorted(os.listdir(extract_dir)):
            fp = os.path.join(extract_dir, fn)
            if os.path.isfile(fp) and not fn.startswith("."):
                zf.write(fp, fn)

    return zip_path


# ---------------------------------------------------------------------------
# Helper: get false positive notes for a check
# ---------------------------------------------------------------------------
def _get_fp_notes(check_name: str) -> str:
    try:
        false_positives = db.get_false_positives()
        fps = [fp for fp in false_positives if fp["check_name"] == check_name]
        if fps:
            notes = "\n\nPREVIOUS FALSE POSITIVES TO AVOID (staff-confirmed):\n"
            for fp in fps[:8]:
                notes += f"- Issue '{fp['issue_text']}' was marked incorrect. Note: {fp['notes']}\n"
            return notes
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Generate verdict
# ---------------------------------------------------------------------------
def generate_verdict(all_results: dict) -> dict:
    results_summary = json.dumps(all_results, indent=2, default=str)
    consultant_name = all_results.get("consultant_name", "Consultant")
    deal_code = all_results.get("deal_code", "")

    system_prompt = """You are the AUSMAR PSE QA Review Agent. Based on the check results, generate the final QA output.

DECISION FRAMEWORK (from real AUSMAR reviews):
- ACCEPTED: All checks pass, no content concerns.
- ACCEPTED — Minor admin notes: Admin/naming issues only (already auto-fixed), content is sound
- ACCEPTED WITH CONCERNS: Content borderline but can proceed with caveats.
- NOT ACCEPTED: Critical failures (plan doesn't fit, missing critical docs, covenant breach, etc.)
- PARKED: Missing deal code or awaiting external input

NOTE: Gas cooktops are EXEMPT from covenant gas bans per Stockland ruling. Do NOT flag them.

ACCURACY IS CRITICAL: Do NOT flag false positives. Only flag issues you are confident about.
Admin/naming corrections have been auto-applied. Don't mention those as issues.

Output valid JSON:
{
  "verdict": "one of the above verdicts",
  "verdict_reason": "one-line reason",
  "critical_issues": ["list of rejection-worthy issues only"],
  "warnings": ["list of concerns that don't block acceptance"],
  "heath_review_note": "Technical summary for Heath Nunn (Drafting Manager). Cover GeoSite accuracy, Red Pen quality, plan-to-lot fit, site coverage, acoustic/covenant concerns. Be specific with numbers. 2-4 paragraphs.",
  "consultant_feedback_email": "Professional email to the consultant. Start with 'Hi [consultant name],' and end with 'Regards,\\nNik'. Be specific about what needs fixing. If accepted, acknowledge good work briefly."
}"""

    raw = call_text_model(system_prompt, f"QA results for {deal_code}:\n\n{results_summary}", model="gpt-4.1-mini")
    return parse_json_from_llm(raw)


# ---------------------------------------------------------------------------
# Cross-check against pre-logged job info
# ---------------------------------------------------------------------------
def cross_check_prelog(prelog: dict, review_results: dict) -> list[str]:
    notes = []

    if prelog.get("deposit_amount"):
        amt = prelog["deposit_amount"]
        dep_type = review_results.get("deposit_type", "")
        if amt == 2500 and dep_type == "STC":
            notes.append(
                "Pre-log says $2,500 (NHP) but submission appears to be STC — "
                "known pattern: wholesale groups sometimes take larger deposits"
            )
        elif amt == 4000 and dep_type == "NHP":
            notes.append(
                "Pre-log says $4,000 (STC) but submission appears to be NHP — "
                "verify correct ITP report used"
            )

    if prelog.get("consultant_name"):
        gs_analysis = review_results.get("checks", {}).get("geosite_verification", {}).get("analysis", {})
        gs_consultant = gs_analysis.get("consultant_name", "")
        if gs_consultant and prelog["consultant_name"].lower() not in gs_consultant.lower():
            notes.append(
                f"Pre-log consultant '{prelog['consultant_name']}' doesn't match "
                f"GeoSite consultant '{gs_consultant}'"
            )

    return notes


# ---------------------------------------------------------------------------
# Main QA Review Pipeline (v2: Content-Based)
# ---------------------------------------------------------------------------
def run_qa_review(zip_path: str, zip_name: str, corrected_zip_dir: str,
                  progress_callback=None) -> dict:
    """Run the full QA review with content-based document classification."""

    def _progress(pct, msg):
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                pass

    results = {
        "zip_name": zip_name,
        "timestamp": datetime.now().isoformat(),
        "deposit_type": "UNKNOWN",
        "deal_code": "",
        "checks": {},
        "corrections_applied": [],
        "consultant_name": "",
    }

    extract_dir = tempfile.mkdtemp(prefix="ausmar_qa_")

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile:
        return {"error": "Invalid zip file — could not extract"}

    # Remove __MACOSX
    macosx = os.path.join(extract_dir, "__MACOSX")
    if os.path.exists(macosx):
        shutil.rmtree(macosx)

    try:
        # === Check 1: File Structure (flatten, remove junk) ===
        _progress(5, "Checking file structure...")
        structure = check_file_structure(extract_dir, zip_name)
        results["checks"]["file_structure"] = {
            "issues": structure["issues"],
            "warnings": structure["warnings"],
            "file_count": len(structure["files"]),
            "files": [f["name"] for f in structure["files"]],
        }
        results["corrections_applied"] = structure["corrections"]
        files = structure["files"]
        deal_code = structure["zip_stem"]
        results["deal_code"] = deal_code

        # === NEW: Content-Based Document Classification ===
        _progress(10, "Classifying documents by content (text extraction)...")
        classification_result = classify_all_files(files)
        file_map = classification_result["file_map"]
        unclassified = classification_result["unclassified"]
        classifications = classification_result["classifications"]

        _progress(25, "Document classification complete. Checking completeness...")

        # Store classification info in results
        results["checks"]["document_classification"] = {
            "classifications": {
                fname: {
                    "identified_as": DOC_TYPES.get(info.get("doc_type", ""), "Unknown"),
                    "confidence": info.get("confidence", 0),
                    "method": info.get("method", ""),
                }
                for fname, info in classifications.items()
                if info.get("doc_type")
            },
            "unclassified_files": [f["name"] for f in unclassified],
        }

        # === Rename files to standard names ===
        rename_corrections = rename_classified_files(extract_dir, file_map, classifications)
        results["corrections_applied"].extend(rename_corrections)

        # === Detect deposit type from content ===
        results["deposit_type"] = detect_deposit_type(file_map, files)

        # === Check 2: Document Completeness (content-based) ===
        _progress(30, "Checking document completeness...")
        completeness = check_document_completeness(file_map, unclassified)
        results["checks"]["document_completeness"] = {
            "issues": completeness["issues"],
            "warnings": completeness["warnings"],
            "found_documents": list(completeness["found"].keys()),
            "found_details": completeness["found"],  # maps canonical name -> original filename
            "conditional_documents": completeness["conditional_found"],
        }

        # === Check 3: GeoSite Verification (Vision) ===
        _progress(40, "Analysing GeoSite with vision AI...")
        geosite_result = check_geosite(file_map)
        results["checks"]["geosite_verification"] = {
            "issues": geosite_result["issues"],
            "warnings": geosite_result["warnings"],
            "analysis": geosite_result.get("analysis", {}),
            "lot_dimensions": geosite_result.get("lot_dimensions"),
        }

        gs_analysis = geosite_result.get("analysis", {})
        gs_consultant = gs_analysis.get("consultant_name", "")
        if gs_consultant:
            results["consultant_name"] = gs_consultant

        # === Check 4: Plan-to-Lot Fit ===
        _progress(55, "Verifying plan-to-lot fit...")
        fit_result = check_plan_to_lot_fit(geosite_result, file_map)
        results["checks"]["plan_to_lot_fit"] = {
            "issues": fit_result["issues"],
            "warnings": fit_result["warnings"],
            "plan_identified": fit_result.get("plan_identified"),
            "fit_result": fit_result.get("fit_result"),
            "lot_dimensions": fit_result.get("lot_dimensions"),
            "plan_specs": fit_result.get("plan_specs"),
        }

        # === Check 5: Red Pen Markup (Vision) ===
        _progress(70, "Analysing Red Pen markups...")
        dep_type = results["deposit_type"] if results["deposit_type"] != "UNKNOWN" else "NHP"
        redpen_result = check_red_pen(file_map, dep_type)
        results["checks"]["red_pen_markup"] = {
            "issues": redpen_result["issues"],
            "warnings": redpen_result["warnings"],
            "analysis": redpen_result.get("analysis", {}),
        }

        # === Check 6: PSE Excel ===
        _progress(82, "Checking PSE patterns...")
        pse_result = check_pse_excel(file_map, gs_analysis)
        results["checks"]["pse_analysis"] = {
            "issues": pse_result["issues"],
            "warnings": pse_result["warnings"],
            "analysis": pse_result.get("analysis", {}),
        }

        # === Check 7: Sites with Fall (conditional) ===
        fall_mm = gs_analysis.get("fall_across_site_mm")
        has_fall_ack = "fall_ack" in file_map
        if fall_mm and isinstance(fall_mm, (int, float)) and fall_mm >= 500 and not has_fall_ack:
            if "geosite_verification" in results["checks"]:
                results["checks"]["geosite_verification"]["warnings"].append(
                    f"Site fall {fall_mm}mm >= 500mm but no Sites with Fall Acknowledgment (Signed) found in submission"
                )

        # === Cross-check pre-log ===
        _progress(88, "Cross-checking pre-log data...")
        prelog = db.find_prelog_by_deal_code(deal_code)
        prelog_notes = []
        if prelog:
            prelog_notes = cross_check_prelog(prelog, results)
            results["prelog_id"] = prelog["id"]
            results["prelog_notes"] = prelog_notes
            if prelog_notes:
                results["checks"]["prelog_crosscheck"] = {
                    "issues": [], "warnings": prelog_notes,
                }

        # === Build corrected zip ===
        if results["corrections_applied"]:
            corrected_path = build_corrected_zip(extract_dir, deal_code, corrected_zip_dir)
            results["corrected_zip_path"] = corrected_path
            results["corrected_zip_filename"] = os.path.basename(corrected_path)

        # === Generate verdict ===
        _progress(92, "Generating verdict and outputs...")
        verdict = generate_verdict(results)
        results["verdict_data"] = verdict

        _progress(98, "Finalising...")

    except Exception as e:
        results["error"] = f"Review pipeline error: {str(e)}\n{traceback.format_exc()}"
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
        gc.collect()

    return results
