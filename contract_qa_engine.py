"""
AUSMAR QA Agent — Stage 3: Pre-Contract QA engine.

Confirms the final contract pack (specification, NHP pricing, working drawings)
correctly reflects the signed source-of-truth (signed PSE/NHP, signed VOs, signed
red pen) before the contract is issued to the client.

Two-pass model (per the AUSMAR Contract QA specification, working example S26JRGB2):
  Pass 1 — automated document/pricing/spec logic (text model). Catches VO carry-through,
           debit/credit matching, credit traceability, deleted-item-still-listed,
           pricing/spec contradictions, base-PSE protection, metadata consistency.
  Pass 2 — drawing/elevation review (vision model) itemised by Lyana's drawing-page
           headings. Catches window/door heights, fixture positioning, deleted items
           still drawn, clearances, electrical fixture type, provision-vs-installed,
           notes/dimensions. EVERY Pass 2 finding is tagged "Needs human drawing
           confirmation" — the tool points the reviewer to the sheet, it does not
           assert construction fact.

Accuracy rules baked in:
  - Better to miss than to false-positive: ambiguity becomes a flagged review item, not a pass.
  - Don't guess: unreadable values are surfaced, never invented.
  - Output matches Lyana's template: itemised by spec section / drawing page with dot points.
"""

import json

import engine_common as ec
import db_v2


# Lyana's QA template drawing-page headings (Pass 2 itemisation)
DRAWING_SECTIONS = [
    "Site Plan", "Floor Plan", "Dimension Plan", "Roof Plan", "Elevations",
    "Slab Plan", "Sections", "Kitchen", "Ensuite", "Bathroom / WC / Laundry",
    "Electrical", "Floor coverings", "External Concrete", "Landscaping", "Details",
]

# Contract specification item sections (Pass 1 itemisation). Derived from the
# AUSMAR contract spec structure; admins can extend rules per section via the UI.
SPEC_SECTIONS = [
    "Item 1 Preliminaries", "Item 2 Site Works", "Item 3 Concrete", "Item 4 Brickwork",
    "Item 5 Framing/Trusses", "Item 6 Roofing", "Item 7 Facade", "Item 8 Windows & Doors",
    "Item 9 Insulation", "Item 10 Plastering", "Item 11 Wet Area / Waterproofing",
    "Item 12 Tiling", "Item 13 Joinery/Cabinetry", "Item 14 Benchtops", "Item 15 Painting",
    "Item 16 Electrical", "Item 17 Plumbing", "Item 18 Appliances", "Item 19 Floor Coverings",
    "Item 20 Fixtures & Fittings", "Item 21 External Works", "Item 22 Landscaping",
    "Item 23 Driveway/Paths", "Item 24 PC/PS Allowances", "Item 25 Energy/BAL/Acoustic",
    "Item 26 Covenant/Developer", "Pricing Total", "Metadata",
]

VALID_SEVERITIES = ["Critical", "High", "Medium", "Low", "Observation"]
VALID_CATEGORIES = ["Pricing", "Specification", "Drawings/Elevations", "Electrical",
                    "Wet Areas", "Joinery", "External"]


# ---------------------------------------------------------------------------
# PASS 1 — automated document comparison
# ---------------------------------------------------------------------------
_PASS1_SYS = """You are the AUSMAR Contract QA Pass 1 engine. You compare the SIGNED SOURCE-OF-TRUTH
(signed PSE/NHP, signed VOs/NHP changes, signed red pen notes if provided as text) against the
CONTRACT OUTPUT (contract specification text and contract NHP pricing text).

Your job: find genuine discrepancies where the contract output does NOT correctly reflect the signed
source. You check:
- VO carry-through: every signed VO appears in pricing/spec or is superseded by a later signed VO.
- Debit/credit matching: contract amounts equal signed VO amounts.
- Credit traceability: signed deletions/credits are traceable in the contract pricing.
- Deleted-item-still-listed: an item deleted by a signed VO must not remain a live inclusion in spec.
- Pricing/spec contradiction: the same item must not be treated differently in pricing vs spec.
- Base PSE protection: base inclusions must not vanish without a signed deletion.
- Later VO precedence: a later signed VO overrides earlier items.
- Metadata: names, lot, address, estate, facade, plan, series, deal code align across documents.

CRITICAL ACCURACY RULES:
1. Better to MISS an issue than to raise a FALSE POSITIVE. Only flag what the documents clearly show.
2. Do NOT guess. If something is ambiguous or a value is unreadable, raise it as a LOW/Observation
   "needs human review" item — do not assert it as a defect.
3. Respect the AUSMAR rules and EXCLUSIONS provided. Never flag an excluded item.
4. Gas cooktops are permitted in all estates (Stockland ruling) — never flag them.

For each issue return these fields exactly:
  issue_ref (leave ""), severity (Critical|High|Medium|Low|Observation),
  category (Pricing|Specification|Drawings/Elevations|Electrical|Wet Areas|Joinery|External),
  section (e.g. "Item 7 Facade" or "Pricing Total" or "Metadata"),
  signed_source (what was signed, with reference),
  contract_output (what the contract says, with reference),
  discrepancy (clear statement of the problem),
  required_action (exact correction required).

Return ONLY valid JSON: { "issues": [ ... ] }
If there are no issues, return { "issues": [] }."""


def _run_pass1(signed_text, contract_spec_text, contract_pricing_text, rules_block):
    user = (
        "SIGNED SOURCE-OF-TRUTH (PSE/NHP + signed VOs + red pen notes):\n"
        + signed_text[:30000]
        + "\n\n=== CONTRACT SPECIFICATION (output under test) ===\n"
        + contract_spec_text[:25000]
        + "\n\n=== CONTRACT NHP PRICING (output under test) ===\n"
        + contract_pricing_text[:20000]
        + rules_block
    )
    raw = ec.call_text_model(_PASS1_SYS, user, model="gpt-4.1-mini")
    issues = ec.parse_json_list(raw, "issues")
    return _sanitise_issues(issues)


# ---------------------------------------------------------------------------
# PASS 2 — drawing / elevation review (vision)
# ---------------------------------------------------------------------------
_PASS2_SYS = """You are the AUSMAR Contract QA Pass 2 drawing reviewer. You are shown CONTRACT WORKING
DRAWING pages as images, plus text describing the SIGNED CHANGES (VOs and red pen intent). Your job is
to point a human reviewer (Lyana) to likely drawing/elevation discrepancies on the correct sheet.

You check, for the visible sheets:
- Elevation alignment: window/door heads/heights consistent and matching signed intent.
- Fixture positioning: mixers, lights, powerpoints, niches, shelves, hooks where signed.
- Deleted items: items deleted by signed VO/red pen are removed from all views and notes.
- Appliance clearances: laundry/kitchen/wet-area appliances have practical clearance.
- Electrical type: pendant vs wall light vs LED vs provision vs installed; single GPO vs DGPO.
- Notes/dimensions: required notes present; dimensions reflect signed changes.

ABSOLUTE RULES:
1. You are an ASSISTANT, not the decision-maker. EVERY item you raise is a POINTER for a human to
   confirm on the drawing. Phrase required_action as "Confirm ..." and set "needs_human_confirmation": true.
2. Better to MISS than to FALSE-POSITIVE. Only raise items where the drawing genuinely suggests a problem
   or where a signed change clearly needs verifying on this sheet. If a sheet looks correct, raise nothing for it.
3. Do NOT guess hidden detail you cannot see. If a value is illegible, say so as an Observation.
4. Respect the AUSMAR rules and EXCLUSIONS provided.

For each item return:
  issue_ref (""), severity (Critical|High|Medium|Low|Observation),
  category (Drawings/Elevations|Electrical|Wet Areas|Joinery|External),
  section (one of: Site Plan, Floor Plan, Dimension Plan, Roof Plan, Elevations, Slab Plan, Sections,
           Kitchen, Ensuite, Bathroom / WC / Laundry, Electrical, Floor coverings, External Concrete,
           Landscaping, Details),
  signed_source, contract_output, discrepancy, required_action, needs_human_confirmation (true).

Return ONLY valid JSON: { "issues": [ ... ] }."""


def _run_pass2(drawing_images_b64, signed_changes_text, rules_block):
    if not drawing_images_b64:
        return []
    user = (
        "SIGNED CHANGES (VOs + red pen intent) to verify on these drawing sheets:\n"
        + signed_changes_text[:12000]
        + rules_block
        + "\n\nReview the attached working-drawing pages and raise pointers for human confirmation."
    )
    raw = ec.call_vision_model(_PASS2_SYS, user, drawing_images_b64, model="gpt-4.1-mini")
    issues = ec.parse_json_list(raw, "issues")
    issues = _sanitise_issues(issues)
    for it in issues:
        it["needs_human_confirmation"] = True
        if "Confirm" not in it.get("required_action", ""):
            it["required_action"] = "Confirm on drawing: " + it.get("required_action", "")
    return issues


# ---------------------------------------------------------------------------
# Sanitising / safety
# ---------------------------------------------------------------------------
def _sanitise_issues(issues):
    clean = []
    if not isinstance(issues, list):
        return clean
    for it in issues:
        if not isinstance(it, dict):
            continue
        sev = it.get("severity", "Medium")
        if sev not in VALID_SEVERITIES:
            sev = "Medium"
        cat = it.get("category", "Specification")
        clean.append({
            "issue_ref": "",
            "severity": sev,
            "category": cat,
            "section": (it.get("section") or "").strip() or "General",
            "signed_source": (it.get("signed_source") or "").strip(),
            "contract_output": (it.get("contract_output") or "").strip(),
            "discrepancy": (it.get("discrepancy") or "").strip(),
            "required_action": (it.get("required_action") or "").strip(),
            "status": "Open",
            "needs_human_confirmation": bool(it.get("needs_human_confirmation", False)),
        })
    return clean


def _assign_refs(issues, deal_code, prefix):
    code = deal_code or "JOB"
    cat_short = {"Pricing": "PRICE", "Specification": "SPEC", "Drawings/Elevations": "ELEV",
                 "Electrical": "ELEC", "Wet Areas": "WET", "Joinery": "JOIN", "External": "EXT"}
    seq = 0
    for it in issues:
        seq += 1
        tag = cat_short.get(it["category"], "GEN")
        it["issue_ref"] = f"{code}-{prefix}-{tag}-{seq:03d}"
    return issues


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def run_contract_qa(inputs, deal_code="", consultant_name="", job_category="",
                    progress_cb=None):
    """
    inputs: dict of file paths. Recognised keys:
        signed_nhp, signed_vos (or nhp_changes), red_pen,
        contract_spec, contract_pricing, working_drawings
    Missing mandatory docs => parked (REVIEW REQUIRED) rather than a misleading pass.
    """
    def _p(pct, msg):
        if progress_cb:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    mandatory = ["contract_spec", "contract_pricing", "working_drawings"]
    source_keys = ["signed_nhp", "signed_vos", "nhp_changes"]
    have_source = any(inputs.get(k) for k in source_keys)
    missing = [k for k in mandatory if not inputs.get(k)]

    if missing or not have_source:
        return {
            "stage": 3, "deal_code": deal_code, "consultant_name": consultant_name,
            "verdict": "PARKED",
            "verdict_reason": "Cannot run a reliable contract QA without the full set. "
                              f"Missing: {', '.join(missing) if missing else ''}"
                              f"{' and a signed source-of-truth document' if not have_source else ''}.",
            "issues": [], "pass1_count": 0, "pass2_count": 0,
            "needs_human_review": True, "by_section": {},
        }

    _p(5, "Reading signed source-of-truth documents...")
    signed_parts = []
    for k in ["signed_nhp", "signed_vos", "nhp_changes", "red_pen"]:
        if inputs.get(k):
            pages = ec.extract_pdf_pages(inputs[k])
            if pages:
                signed_parts.append(f"--- {k.upper()} ---\n" + ec.pages_to_text(pages))
    signed_text = "\n\n".join(signed_parts)

    _p(20, "Reading contract specification...")
    spec_pages = ec.extract_pdf_pages(inputs["contract_spec"])
    spec_text = ec.pages_to_text(spec_pages)

    _p(30, "Reading contract pricing...")
    pricing_pages = ec.extract_pdf_pages(inputs["contract_pricing"])
    pricing_text = ec.pages_to_text(pricing_pages)

    _p(40, "Loading AUSMAR rules...")
    rules_block = db_v2.get_rules_prompt_block("Stage 3")

    _p(50, "Pass 1 — document, pricing & specification comparison...")
    pass1 = _run_pass1(signed_text, spec_text, pricing_text, rules_block)
    pass1 = _assign_refs(pass1, deal_code, "P1")

    _p(70, "Pass 2 — drawing & elevation review (AI-assisted)...")
    drawing_imgs = ec.pdf_all_pages_to_base64(inputs["working_drawings"], dpi=110, max_pages=8)
    pass2 = _run_pass2(drawing_imgs, signed_text, rules_block)
    pass2 = _assign_refs(pass2, deal_code, "P2")

    all_issues = pass1 + pass2

    # Group by section for Lyana's itemised template output
    by_section = {}
    for it in all_issues:
        by_section.setdefault(it["section"], []).append(it)

    # Verdict
    has_critical = any(i["severity"] == "Critical" for i in all_issues)
    has_high = any(i["severity"] == "High" for i in all_issues)
    if has_critical:
        verdict = "DO NOT ISSUE"
        reason = "Critical contract discrepancies found. Contract must not be issued until resolved or formally accepted by manager."
    elif has_high:
        verdict = "ISSUE AFTER CORRECTIONS"
        reason = "High-priority discrepancies found. Resolve before client issue unless an approved exception is recorded."
    elif all_issues:
        verdict = "ISSUE WITH NOTED ITEMS"
        reason = "Only medium/low/observation items found. Review and resolve where practical before issue."
    else:
        verdict = "READY TO ISSUE"
        reason = "No discrepancies found in Pass 1. Pass 2 drawing items (if any) still require human confirmation."

    _p(90, "Consolidating findings...")
    summary = _build_consultant_summary(all_issues, verdict)

    return {
        "stage": 3,
        "deal_code": deal_code,
        "consultant_name": consultant_name,
        "job_category": job_category,
        "verdict": verdict,
        "verdict_reason": reason,
        "issues": all_issues,
        "by_section": by_section,
        "pass1_count": len(pass1),
        "pass2_count": len(pass2),
        "consultant_summary": summary,
        "needs_human_review": True,  # Pass 2 always needs human drawing confirmation
    }


def _build_consultant_summary(issues, verdict):
    from collections import Counter
    sev = Counter(i["severity"] for i in issues)
    cat = Counter(i["category"] for i in issues)
    pricing_exposure = sum(
        1 for i in issues if i["category"] == "Pricing"
    )
    return {
        "recommendation": verdict,
        "critical": sev.get("Critical", 0),
        "high": sev.get("High", 0),
        "medium": sev.get("Medium", 0),
        "low": sev.get("Low", 0),
        "observation": sev.get("Observation", 0),
        "by_category": dict(cat),
        "pricing_items": pricing_exposure,
    }
