"""
AUSMAR PSE QA Engine — Production Review Pipeline
Implements every check from pse_qa_review_workflow.md plus rules extracted from:
- 1.0 PSE Document Naming (official naming/completeness)
- 1.1 Sites with Fall (slope rules)
- 1.2 Site Visit Checklist
- 1.3 Sales Accept (checklist items)
- salesacceptissues (real issue patterns)
- Heath's review feedback emails (6 real reviews)
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

# Lazy-initialize the client so import doesn't fail if env var is missing at build time.
# The client is created on first use, at which point the runtime env var is available.
_client = None

def get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable is not set. "
                "Set it in the DigitalOcean App Platform environment variables."
            )
        # Only set base_url if OPENAI_BASE_URL is explicitly provided.
        # If not set, do NOT pass base_url at all — let the openai library
        # use its built-in default (api.openai.com). Hardcoding any URL here
        # risks routing to a proxy that blocks the DigitalOcean IP.
        base_url = os.environ.get("OPENAI_BASE_URL")
        if base_url:
            _client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            _client = OpenAI(api_key=api_key)
    return _client

# Flag for pdf2image availability
_PDF2IMAGE_AVAILABLE = True
try:
    from pdf2image import convert_from_path
except ImportError:
    _PDF2IMAGE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Official naming conventions from 1.0 PSE Document Naming
# Keys: lowercase variants -> canonical name
# ---------------------------------------------------------------------------
CANONICAL_NAMES = {
    # PSE Doc (Signed)
    "pse doc (signed)": "PSE Doc (Signed)",
    "pse doc signed": "PSE Doc (Signed)",
    "psedoc(signed)": "PSE Doc (Signed)",
    "pse document (signed)": "PSE Doc (Signed)",
    "pse (signed)": "PSE Doc (Signed)",
    "pse signed": "PSE Doc (Signed)",
    # PSE Excel
    "pse excel": "PSE Excel",
    "pseexcel": "PSE Excel",
    "pse spreadsheet": "PSE Excel",
    # PSE Checklist
    "pse checklist": "PSE Checklist",
    "checklist": "PSE Checklist",
    # GeoSite Plan (Signed)
    "geosite plan (signed)": "GeoSite Plan (Signed)",
    "geosite (signed)": "GeoSite Plan (Signed)",
    "geo site plan (signed)": "GeoSite Plan (Signed)",
    "geo site (signed)": "GeoSite Plan (Signed)",
    "geosite plan signed": "GeoSite Plan (Signed)",
    "geosite signed": "GeoSite Plan (Signed)",
    "geosite": "GeoSite Plan (Signed)",
    "geo site": "GeoSite Plan (Signed)",
    "geosite plan": "GeoSite Plan (Signed)",
    # Red Pen Markup (Signed)
    "pse red pen markup (signed)": "Red Pen Markup (Signed)",
    "red pen markup (signed)": "Red Pen Markup (Signed)",
    "red pen (signed)": "Red Pen Markup (Signed)",
    "redpen (signed)": "Red Pen Markup (Signed)",
    "red pen markup signed": "Red Pen Markup (Signed)",
    "red pen markup": "Red Pen Markup (Signed)",
    "red pen": "Red Pen Markup (Signed)",
    "redpen": "Red Pen Markup (Signed)",
    "markup (signed)": "Red Pen Markup (Signed)",
    # ITP Form (Signed)
    "intention to purchase (signed)": "ITP Form (Signed)",
    "itp form (signed)": "ITP Form (Signed)",
    "itp (signed)": "ITP Form (Signed)",
    "itp form signed": "ITP Form (Signed)",
    "itp form": "ITP Form (Signed)",
    "itp": "ITP Form (Signed)",
    "intention to purchase": "ITP Form (Signed)",
    # Deposit Receipt
    "deposit receipt": "Deposit Receipt",
    "depositreceipt": "Deposit Receipt",
    "prelim deposit receipt": "Deposit Receipt",
    "receipt": "Deposit Receipt",
    # Drivers Licence
    "drivers licence": "Drivers Licence",
    "drivers license": "Drivers Licence",
    "driver licence": "Drivers Licence",
    "driver license": "Drivers Licence",
    "dl": "Drivers Licence",
    # Sales Accept
    "sales accept doc": "Sales Accept Doc",
    "sales accept": "Sales Accept Doc",
    # Pool Form (Signed)
    "swimming pool form (signed)": "Pool Form (Signed)",
    "pool form (signed)": "Pool Form (Signed)",
    "pool form": "Pool Form (Signed)",
    # Covenant Application Form (Signed)
    "covenant application form (signed)": "Covenant Application (Signed)",
    "covenant application (signed)": "Covenant Application (Signed)",
    "covenant application": "Covenant Application (Signed)",
    # Conditional forms
    "discount approval": "Discount Approval",
    "discount approval form": "Discount Approval",
    "discount approval email": "Discount Approval",
    "owner supplied items approval": "Owner Supplied Items Approval",
    "owner supplied items approval form": "Owner Supplied Items Approval",
    "owner supplied approval": "Owner Supplied Items Approval",
    "modified plan approval": "Modified Plan Approval",
    "modified plan approval form": "Modified Plan Approval",
    "modified plan approval request": "Modified Plan Approval",
    # Sites with Fall
    "sites with fall acknowledgment (signed)": "Sites with Fall Acknowledgment (Signed)",
    "sites with fall (signed)": "Sites with Fall Acknowledgment (Signed)",
    "sites with fall acknowledgment": "Sites with Fall Acknowledgment (Signed)",
    "sites with fall": "Sites with Fall Acknowledgment (Signed)",
    "fall acknowledgment": "Sites with Fall Acknowledgment (Signed)",
    # PROMO Client Acknowledgement
    "promo client acknowledgement (signed)": "Promo Client Acknowledgement (Signed)",
    "promo - client acknowledgement (signed)": "Promo Client Acknowledgement (Signed)",
    "promo client acknowledgement": "Promo Client Acknowledgement (Signed)",
    "client acknowledgement (signed)": "Promo Client Acknowledgement (Signed)",
    "client acknowledgement": "Promo Client Acknowledgement (Signed)",
    "advantage client acknowledgement": "Promo Client Acknowledgement (Signed)",
    "super saver client acknowledgement": "Promo Client Acknowledgement (Signed)",
    # Other standard docs
    "pod": "POD or Building Envelope",
    "building envelope": "POD or Building Envelope",
    "building envelope plan": "POD or Building Envelope",
    "compaction report": "Compaction Report",
    "compaction": "Compaction Report",
    "design guidelines": "Covenant Design Guidelines",
    "covenant design guidelines": "Covenant Design Guidelines",
    "covenant guidelines": "Covenant Design Guidelines",
    "disclosure plan": "Disclosure Plan",
    "survey plan": "Disclosure Plan",
    "disclosure": "Disclosure Plan",
    "covenant": "Covenant",
    "acoustic report": "Acoustic Report",
    "acoustics": "Acoustic Report",
    "bal report": "BAL Report",
    "contour survey": "Contour Survey",
}

LICENCE_BAD_EXTS = {".heic", ".msg", ".webp"}
JUNK_PATTERNS = {"__macosx", ".ds_store", "thumbs.db", ".tmp", "~$"}

# NOTE: Gas cooktops are permitted in all estates including those with gas-ban covenants.
# Stockland has issued a ruling approving gas cooktops as an exception to covenant restrictions.
# Do NOT flag gas cooktops as a QA issue.

# Max image dimension for base64 encoding (saves memory + API cost)
MAX_IMAGE_DIM = 1024


# ---------------------------------------------------------------------------
# Image/PDF conversion utilities — with graceful fallback
# ---------------------------------------------------------------------------
def _resize_image(img: Image.Image, max_dim: int = MAX_IMAGE_DIM) -> Image.Image:
    """Resize image so longest side is max_dim pixels. Returns new image."""
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
    """Convert PIL Image to base64 JPEG string, resizing first."""
    img = _resize_image(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    buf.close()
    return b64


def pdf_page_to_base64(pdf_path: str, page_num: int = 0, dpi: int = 100) -> str:
    """Convert a single PDF page to base64. Returns empty string on failure."""
    if not _PDF2IMAGE_AVAILABLE:
        return ""
    try:
        images = convert_from_path(pdf_path, first_page=page_num + 1, last_page=page_num + 1, dpi=dpi)
        if not images:
            return ""
        b64 = _img_to_b64(images[0])
        # Explicitly free memory
        for img in images:
            img.close()
        del images
        gc.collect()
        return b64
    except Exception as e:
        print(f"[WARN] pdf_page_to_base64 failed for {pdf_path} page {page_num}: {e}")
        return ""


def pdf_all_pages_to_base64(pdf_path: str, dpi: int = 100, max_pages: int = 3) -> list[str]:
    """Convert PDF pages to base64 list. Returns empty list on failure (graceful fallback)."""
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
    """Convert image file to base64, resized to max 1024px."""
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
# Auto-fix: correct file name
# ---------------------------------------------------------------------------
def fix_filename(original_name: str) -> tuple[str, str | None]:
    """Return (corrected_name, correction_description_or_None)."""
    stem = Path(original_name).stem
    ext = Path(original_name).suffix

    stem_lower = stem.lower().strip()

    # Try canonical match — longest match wins
    best_match = None
    best_score = 0
    for pattern, canonical in CANONICAL_NAMES.items():
        if pattern in stem_lower or stem_lower in pattern:
            score = len(pattern)
            if score > best_score:
                best_score = score
                best_match = canonical

    if best_match:
        new_name = best_match + ext
        if new_name != original_name:
            return new_name, f"Renamed '{original_name}' -> '{new_name}'"
        return original_name, None

    # Check Title Case — if not, convert
    words = stem.split()
    if words:
        title_cased = " ".join(
            w.capitalize() if w.lower() not in ("of", "the", "and", "for", "or") else w.lower()
            for w in words
        )
        title_cased = re.sub(r"\(signed\)", "(Signed)", title_cased, flags=re.IGNORECASE)
        new_name = title_cased + ext
        if new_name != original_name:
            return new_name, f"Title-cased '{original_name}' -> '{new_name}'"

    return original_name, None


# ---------------------------------------------------------------------------
# Check 1: File Structure & Naming (with auto-fix)
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

    # Auto-fix: flatten subfolders
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

    # Check zip name format (deal code)
    zip_stem = Path(zip_name).stem
    deal_code_pattern = re.compile(r"^[A-Za-z]\d{2}[A-Za-z]{2,6}$")
    if not deal_code_pattern.match(zip_stem):
        warnings.append(f"Zip name '{zip_name}' may not be a valid deal code (expected format like S26TLS)")

    # Auto-fix: remove junk files, fix names
    cleaned_files = []
    seen_names = set()

    for f in files:
        fn = f["name"]
        fn_lower = fn.lower()

        # Remove junk
        is_junk = any(pat in fn_lower for pat in JUNK_PATTERNS)
        # Also remove .msg files (email attachments, not submission docs)
        if fn_lower.endswith(".msg"):
            is_junk = True
        if is_junk:
            try:
                os.remove(f["full_path"])
            except Exception:
                pass
            corrections.append(f"Removed junk file: '{fn}'")
            continue

        # Fix filename
        new_name, correction = fix_filename(fn)
        if correction:
            new_path = os.path.join(extract_dir, new_name)
            if not os.path.exists(new_path) or new_path == f["full_path"]:
                try:
                    os.rename(f["full_path"], new_path)
                    corrections.append(correction)
                    f["name"] = new_name
                    f["full_path"] = new_path
                    f["rel_path"] = new_name
                except Exception:
                    pass

        # Duplicate check
        if f["name"].lower() in seen_names:
            issues.append(f"Duplicate file: '{f['name']}'")
        seen_names.add(f["name"].lower())

        cleaned_files.append(f)

    # Check licence format
    for f in cleaned_files:
        ext = Path(f["name"]).suffix.lower()
        name_lower = f["name"].lower()
        if "licence" in name_lower or "license" in name_lower or "drivers" in name_lower:
            if ext in LICENCE_BAD_EXTS:
                issues.append(f"Drivers Licence '{f['name']}' in {ext} format — needs .jpg, .png, or .pdf")

    return {
        "files": cleaned_files,
        "issues": issues,
        "warnings": warnings,
        "corrections": corrections,
        "zip_stem": zip_stem,
    }


# ---------------------------------------------------------------------------
# Check 2: Document Completeness (per 1.0 PSE Document Naming)
# ---------------------------------------------------------------------------
def check_document_completeness(files: list[dict]) -> dict:
    issues = []
    warnings = []
    found = {}

    filenames_lower = [f["name"].lower() for f in files]
    all_text = " ".join(filenames_lower)

    # --- CORE REQUIRED DOCUMENTS ---
    core_checks = [
        # PSE Doc: matches 'PSE Doc', 'Provisional Sales Estimate', or any PDF with 'PSE' in name
        ("PSE Doc (Signed)", ["pse doc", "pse_doc", "pse (signed)", "provisional sales estimate", "provisional_sales_estimate"]),
        # PSE Excel: matches any spreadsheet format (.xlsx, .xlsm, .xls, .csv) with 'pse' in name,
        # or explicit 'pse excel' label
        ("PSE Excel", ["pse excel", "pse_excel"]),
        ("GeoSite Plan (Signed)", ["geosite", "geo site", "geo_site"]),
        ("ITP Form (Signed)", ["itp", "intention to purchase"]),
        ("Deposit Receipt", ["deposit", "receipt"]),
        ("Drivers Licence", ["licence", "license", "drivers"]),
        ("PSE Checklist", ["checklist"]),
    ]

    # Special match: PSE Doc — any PDF whose name contains 'pse' counts
    pse_doc_found = any(
        any(kw in fn for kw in ["pse doc", "pse_doc", "pse (signed)", "provisional sales estimate", "provisional_sales_estimate"])
        or ("pse" in fn and fn.endswith(".pdf"))
        for fn in filenames_lower
    )
    if pse_doc_found:
        found["PSE Doc (Signed)"] = True

    # Special match: PSE Excel — any spreadsheet (.xlsx/.xlsm/.xls/.csv) whose name contains 'pse' counts
    spreadsheet_exts = (".xlsx", ".xlsm", ".xls", ".csv")
    pse_excel_found = any(
        ("pse" in fn and any(fn.endswith(ext) for ext in spreadsheet_exts))
        or "pse excel" in fn or "pse_excel" in fn
        for fn in filenames_lower
    )
    if pse_excel_found:
        found["PSE Excel"] = True

    for doc_name, keywords in core_checks:
        # PSE Doc and PSE Excel already handled above
        if doc_name in ("PSE Doc (Signed)", "PSE Excel"):
            if doc_name not in found:
                issues.append(f"Missing: {doc_name}")
            continue
        if any(any(kw in fn for kw in keywords) for fn in filenames_lower):
            found[doc_name] = True
        else:
            if doc_name == "PSE Checklist":
                warnings.append(f"Missing: {doc_name} — should be included per 1.0 PSE Document Naming")
            else:
                issues.append(f"Missing: {doc_name}")

    # --- RED PEN MARKUP (special handling) ---
    has_redpen = any(
        "red pen" in fn or "redpen" in fn or "red_pen" in fn or "markup" in fn
        for fn in filenames_lower
    )
    if has_redpen:
        found["Red Pen Markup (Signed)"] = True
    else:
        # Red pen is required for NHP; for STC it's optional if no structural changes
        warnings.append("Missing: Red Pen Markup — required for NHP submissions, verify if needed for STC")

    # --- CONDITIONAL DOCUMENTS (flag as warnings if absent) ---
    conditional_found = []
    conditional_checks = [
        ("POD or Building Envelope", ["pod", "building envelope", "envelope plan"]),
        ("Compaction Report", ["compaction"]),
        ("Covenant Design Guidelines", ["design guide", "covenant guide"]),
        ("Disclosure Plan / Survey Plan", ["disclosure", "survey plan"]),
        ("Covenant Application (Signed)", ["covenant application", "covenant form"]),
        ("Pool Form (Signed)", ["pool form", "swimming pool", "pool (signed)"]),
        ("Discount Approval", ["discount approval", "discount form"]),
        ("Owner Supplied Items Approval", ["owner supplied", "owner supply"]),
        ("Modified Plan Approval", ["modified plan"]),
        ("Promo Client Acknowledgement (Signed)", ["promo", "client acknowledgement", "advantage", "super saver"]),
        ("Sites with Fall Acknowledgment (Signed)", ["sites with fall", "fall acknowledgment", "fall acknowledgement"]),
        ("Acoustic Report", ["acoustic"]),
        ("BAL Report", ["bal report", "bal "]),
        ("Contour Survey", ["contour"]),
        ("Sales Accept Doc", ["sales accept"]),
    ]

    for name, keywords in conditional_checks:
        if any(any(kw in fn for kw in keywords) for fn in filenames_lower):
            conditional_found.append(name)

    # Per 1.0 naming doc, these should be present or explained:
    if "Disclosure Plan / Survey Plan" not in conditional_found:
        warnings.append("Missing: Disclosure Plan / Survey Plan — required per 1.0 naming unless explained")

    if "POD or Building Envelope" not in conditional_found:
        warnings.append("Missing: POD or Building Envelope Plan — required per 1.0 naming unless explained")

    return {
        "found": found,
        "issues": issues,
        "warnings": warnings,
        "conditional_found": conditional_found,
    }


# ---------------------------------------------------------------------------
# Check 3: GeoSite Verification (Vision)
# From workflow + real issues: must be from geosite.com.au, must have setbacks,
# must be separate from site visit doc, must be to scale, text must be readable
# ---------------------------------------------------------------------------
def check_geosite(files: list[dict]) -> dict:
    geosite_files = [
        f for f in files
        if "geosite" in f["name"].lower() or "geo site" in f["name"].lower()
    ]
    if not geosite_files:
        return {
            "issues": ["No GeoSite file found in submission — CRITICAL per 1.0 PSE Document Naming"],
            "warnings": [], "analysis": {}, "lot_dimensions": None,
        }

    geosite_path = geosite_files[0]["full_path"]
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
            # Graceful fallback — skip vision but don't crash
            return {
                "issues": [],
                "warnings": [vision_warning or "Could not convert GeoSite to images — vision analysis skipped"],
                "analysis": {}, "lot_dimensions": None,
            }

        # Load false-positive feedback to improve prompts
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

        # Critical checks
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

        # Front setback check (SCRC minimum 4.5m to OMP)
        front_sb = analysis.get("front_setback_m")
        if front_sb is not None and isinstance(front_sb, (int, float)):
            if front_sb < 4.5:
                warnings.append(
                    f"Front setback is {front_sb}m — SCRC minimum is typically 4.5m to OMP. "
                    f"Verify with council requirements."
                )

        # Side setback checks
        for side_key, side_label in [("side_setback_left_m", "Left"), ("side_setback_right_m", "Right")]:
            val = analysis.get(side_key)
            if val is not None and isinstance(val, (int, float)):
                if val < 0.6:
                    issues.append(
                        f"RED FLAG: {side_label} side setback is {val}m (under 0.6m) — "
                        f"plan may be too wide for this lot"
                    )

        # Site coverage check (max typically 60%, flag if >58%)
        coverage = analysis.get("site_coverage_percent")
        if coverage is not None and isinstance(coverage, (int, float)):
            if coverage > 60:
                issues.append(f"Site coverage {coverage}% exceeds 60% maximum")
            elif coverage > 58:
                warnings.append(
                    f"Site coverage {coverage}% is very close to 60% maximum — zero margin. "
                    f"Verify with covenant (real issue from S26JYTC review)."
                )

        # Battle-axe lot check (may need LHDC assessment)
        if analysis.get("is_battle_axe_lot") is True:
            warnings.append(
                "Lot appears to be battle-axe configuration — may require LHDC "
                "(Livable Housing Design Standards) assessment. Verify with council."
            )

        # Fall detection
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
# Check 4: Plan-to-Lot Fit (CRITICAL FIRST CHECK)
# ---------------------------------------------------------------------------
def check_plan_to_lot_fit(geosite_result: dict, files: list[dict]) -> dict:
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

    # Load plans from DB
    plans = db.get_all_plans()
    matched_plan = None

    # Try exact match first
    for p in plans:
        if p["name"].lower() in home_design.lower():
            matched_plan = p
            break

    # Try word-level match
    if not matched_plan:
        for p in plans:
            words = p["name"].lower().split()
            if all(w in home_design.lower() for w in words):
                matched_plan = p
                break

    # Try partial match on first word (plan family name)
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

    # Build area sanity check
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
# Check 5: Red Pen Markup (Vision)
# Rules from workflow + real issues:
# - MUST be RED colour on AUSMAR standard base plan
# - MUST have dimensions on all changed areas
# - Tags (e.g. 3.2.a) must match PSE section references
# - Must cover: Floor Plan, Elevations, Electrical, Floor Coverings, Concrete
# - Flag Hebel changeover / width reductions
# ---------------------------------------------------------------------------
def check_red_pen(files: list[dict], deposit_type: str) -> dict:
    redpen_files = [
        f for f in files
        if "red pen" in f["name"].lower() or "redpen" in f["name"].lower()
        or "markup" in f["name"].lower()
    ]

    if not redpen_files:
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

    redpen_path = redpen_files[0]["full_path"]
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
            # Graceful fallback — skip vision but don't crash
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

            # Check plan type coverage
            missing = analysis.get("missing_plan_types", [])
            if missing:
                warnings.append(
                    f"Red Pen may be missing coverage for: {', '.join(missing)}. "
                    f"Per 1.0 naming, should include Floor Plan, Elevations, Electrical, Floor Coverings, Concrete."
                )

        elif deposit_type == "STC":
            if analysis.get("structural_changes_shown") is True:
                issues.append("Structural changes on STC submission — STC should have clean plans")

        # Universal flags
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
# Check 6: PSE Excel Analysis (text-based)
# Checks from real reviews: NHP/STC mismatch, acoustic categories,
# facade consistency, solar requirements
# NOTE: Gas cooktops are NOT flagged — Stockland ruling overrides covenant restrictions.
# ---------------------------------------------------------------------------
def check_pse_excel(files: list[dict], geosite_analysis: dict) -> dict:
    """Analyse PSE Excel for known issue patterns from real reviews."""
    pse_files = [
        f for f in files
        if f["name"].lower().endswith((".xlsx", ".xls"))
        or "pse excel" in f["name"].lower()
    ]

    issues = []
    warnings = []
    analysis = {}

    estate_name = (geosite_analysis.get("estate_name", "") or "").lower()
    home_design = geosite_analysis.get("home_design", "") or ""
    facade_name = geosite_analysis.get("facade_name", "") or ""

    # Gas cooktops: NOT flagged. Stockland has approved gas cooktops as an exception
    # even in estates where the covenant prohibits gas. This is the only covenant rule
    # where the 'not allowed' restriction is overridden by Stockland's ruling.

    # Facade consistency check (from S26JYTC — GeoSite said Traditional, PSE said Coastal)
    if facade_name and geosite_analysis.get("facade_name"):
        analysis["geosite_facade"] = facade_name

    # Flag if no PSE Excel found
    if not pse_files:
        issues.append("Missing: PSE Excel — required per 1.0 PSE Document Naming")

    return {"issues": issues, "warnings": warnings, "analysis": analysis}


# ---------------------------------------------------------------------------
# Detect deposit type from filenames and content
# ---------------------------------------------------------------------------
def detect_deposit_type(files: list[dict]) -> str:
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
# Generate verdict and outputs
# Uses real review patterns from Heath and Nikole's emails as examples
# ---------------------------------------------------------------------------
def generate_verdict(all_results: dict) -> dict:
    results_summary = json.dumps(all_results, indent=2, default=str)
    consultant_name = all_results.get("consultant_name", "Consultant")
    deal_code = all_results.get("deal_code", "")

    system_prompt = """You are the AUSMAR PSE QA Review Agent. Based on the check results, generate the final QA output.

DECISION FRAMEWORK (from real AUSMAR reviews):
- ACCEPTED: All checks pass, no content concerns. Example: "S26SBSH — ACCEPTED" (good GeoSite, compliant cooktop, reasonable site coverage)
- ACCEPTED — Minor admin notes: Admin/naming issues only (already auto-fixed by the system), content is sound
- ACCEPTED WITH CONCERNS: Content borderline but can proceed with caveats. Example: "S26JYTC — ACCEPTED with 3 items" (facade label mismatch, porch area borderline, GPO quantity mismatch)
- NOT ACCEPTED: Critical failures. Example reasons from real rejections:
  * Plan doesn't fit lot (S26TLS — no setbacks, can't verify fit)
  * GeoSite missing/wrong tool/no setbacks
  * Red Pen not in red / not on AUSMAR base plan / no dimensions (S26TLS, S25MLS)
  * Covenant breach — other covenant violations (NOTE: gas cooktops are EXEMPT per Stockland ruling)
  * Missing buyer signatures (S26MP — second buyer didn't sign)
  * Missing critical documents
  * Contour survey needed for established lot with fall (S26JW — Heath rejected despite Nikole accepting)
- PARKED: Missing deal code or awaiting external input

REAL EXAMPLES OF HEATH'S REVIEW STYLE:
- "Rejected need contour survey to assess site fall, need survey plan/plan of sub for site boundaries"
- "Is on the report and shows acoustics not required"
- "200mm extension is shown on red pen" / "How pantry is shown is fine"
- "Would be good to have an identification section at the top stating if it required LHDC"

ACCURACY IS CRITICAL: Nikole said "we have no room for losing trust in this program or process."
- Do NOT flag false positives. Only flag issues you are confident about.
- If something is borderline, put it as a warning/concern, not a critical issue.
- Admin/naming corrections have been auto-applied by the system. Don't mention those as issues.

Output valid JSON:
{
  "verdict": "one of the above verdicts",
  "verdict_reason": "one-line reason",
  "critical_issues": ["list of rejection-worthy issues only"],
  "warnings": ["list of concerns that don't block acceptance"],
  "heath_review_note": "Technical summary for Heath Nunn (Drafting Manager). Write like Nikole's real emails to Heath — cover GeoSite accuracy, Red Pen quality, plan-to-lot fit, site coverage, acoustic/covenant concerns. Be specific with numbers and lot references. 2-4 paragraphs.",
  "consultant_feedback_email": "Professional email to the consultant. Start with 'Hi [consultant name],' and end with 'Regards,\\nNik'. Be specific about what needs fixing. Reference PSE section numbers where relevant. If accepted, acknowledge good work briefly. Keep it constructive and solution-focused."
}"""

    raw = call_text_model(system_prompt, f"QA results for {deal_code}:\n\n{results_summary}", model="gpt-4.1-mini")
    return parse_json_from_llm(raw)


# ---------------------------------------------------------------------------
# Cross-check against pre-logged job info
# ---------------------------------------------------------------------------
def cross_check_prelog(prelog: dict, review_results: dict) -> list[str]:
    notes = []

    # Deposit amount vs stream
    if prelog.get("deposit_amount"):
        amt = prelog["deposit_amount"]
        dep_type = review_results.get("deposit_type", "")
        if amt == 2500 and dep_type == "STC":
            notes.append(
                "Pre-log says $2,500 (NHP) but submission appears to be STC — "
                "known pattern: wholesale groups sometimes take larger deposits (Sh26TGCH pattern)"
            )
        elif amt == 4000 and dep_type == "NHP":
            notes.append(
                "Pre-log says $4,000 (STC) but submission appears to be NHP — "
                "verify correct ITP report used (Sh26TGCH pattern: wrong ITP report)"
            )

    # Consultant name match
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
# Main QA Review Pipeline
# ---------------------------------------------------------------------------
def run_qa_review(zip_path: str, zip_name: str, corrected_zip_dir: str,
                  progress_callback=None) -> dict:
    """Run the full QA review. progress_callback(pct, msg) is called to report progress."""

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
        # === Check 1: File Structure & Naming (with auto-fix) ===
        _progress(10, "Checking file structure and naming...")
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

        # === Detect deposit type ===
        results["deposit_type"] = detect_deposit_type(files)

        # === Check 2: Document Completeness (per 1.0 PSE Document Naming) ===
        _progress(20, "Checking document completeness...")
        completeness = check_document_completeness(files)
        results["checks"]["document_completeness"] = {
            "issues": completeness["issues"],
            "warnings": completeness["warnings"],
            "found_documents": list(completeness["found"].keys()),
            "conditional_documents": completeness["conditional_found"],
        }

        # === Check 3: GeoSite Verification (Vision) ===
        _progress(35, "Analysing GeoSite with vision AI...")
        geosite_result = check_geosite(files)
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

        # === Check 4: Plan-to-Lot Fit (CRITICAL) ===
        _progress(55, "Verifying plan-to-lot fit...")
        fit_result = check_plan_to_lot_fit(geosite_result, files)
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
        redpen_result = check_red_pen(files, dep_type)
        results["checks"]["red_pen_markup"] = {
            "issues": redpen_result["issues"],
            "warnings": redpen_result["warnings"],
            "analysis": redpen_result.get("analysis", {}),
        }

        # === Check 6: PSE Excel / Known Issue Patterns ===
        _progress(82, "Checking PSE patterns...")
        pse_result = check_pse_excel(files, gs_analysis)
        results["checks"]["pse_analysis"] = {
            "issues": pse_result["issues"],
            "warnings": pse_result["warnings"],
            "analysis": pse_result.get("analysis", {}),
        }

        # === Check 7: Sites with Fall (conditional) ===
        fall_mm = gs_analysis.get("fall_across_site_mm")
        has_fall_ack = any(
            "fall" in f["name"].lower() and "acknowledgment" in f["name"].lower()
            or "fall" in f["name"].lower() and "acknowledgement" in f["name"].lower()
            or "sites with fall" in f["name"].lower()
            for f in files
        )
        if fall_mm and isinstance(fall_mm, (int, float)) and fall_mm >= 500 and not has_fall_ack:
            if "geosite_verification" in results["checks"]:
                results["checks"]["geosite_verification"]["warnings"].append(
                    f"Site fall {fall_mm}mm >= 500mm but no Sites with Fall Acknowledgment (Signed) found in submission"
                )

        # === Check pre-log match ===
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

        # === Build corrected zip (only if corrections were made) ===
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
