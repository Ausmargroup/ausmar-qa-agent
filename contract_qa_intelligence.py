"""
AUSMAR QA Agent — Contract QA Intelligence Engine (V1)

Implements the 18 Tier 1 rules from the Knowledge Capture document.
Each rule executes via OpenAI API by sending relevant document sections
with a structured extraction prompt, returning PASS / FAIL / WARNING
with evidence text.

This is ADDITIVE to the existing Stage 3 engine — it does not replace it.
Stage 3 (Pre-Contract QA) continues to work as before.
This engine powers the new "Contract QA" domain (Stage 4 in the nav).
"""

import json
import traceback

import engine_common as ec
import db_v3_contract_qa as db_v3


# ---------------------------------------------------------------------------
# Rule execution system prompt
# ---------------------------------------------------------------------------
_RULE_EXEC_SYSTEM = """You are the AUSMAR Contract QA Intelligence engine. You execute specific QA rules
against construction documents (specification, NHP pricing, working drawings) to verify consistency
and compliance.

For the given rule, you must:
1. Extract the relevant data points from the provided document text
2. Compare them against the rule's expected outcome
3. Return a structured result

CRITICAL ACCURACY RULES:
- Better to MISS than to FALSE POSITIVE. Only flag genuine issues.
- If data is unclear or missing, return WARNING (not FAIL).
- Be specific in your evidence — cite exact values, sections, and page references.
- Do NOT guess. If you cannot find the relevant data, say so.

Return ONLY valid JSON in this exact format:
{
  "result": "PASS" | "FAIL" | "WARNING",
  "confidence": 0.0 to 1.0,
  "evidence_found": "What was actually found in the documents",
  "evidence_expected": "What should have been found per the rule",
  "documents_affected": "Which documents showed the issue",
  "corrective_action": "Specific action to fix (empty if PASS)",
  "reasoning": "Brief explanation of the determination"
}"""


# ---------------------------------------------------------------------------
# Per-rule prompt templates
# ---------------------------------------------------------------------------
RULE_PROMPTS = {
    "QAR-001": """RULE: Specification Completeness Check
Check that the specification document is complete and all applicable sections have meaningful content.

PARAMETERS: {parameters}

DOCUMENT TEXT (Specification):
{spec_text}

Instructions — follow ALL steps in order:

STEP 1 — EXTRACT THE ACTUAL INDEX:
The specification contains an index/table of contents near the top listing numbered items (e.g. "1.0 PRELIMINARIES", "7.0 FACADE AND ROOF", "11.0 KITCHEN", "23.0 PAINTING", etc.).
You MUST read this actual index to determine what item numbers map to which sections for THIS specific job.
Do NOT assume fixed item numbering — every job's spec may have different item-to-section mappings.
List the actual item numbers and their section names as you find them in the index.

STEP 2 — DETERMINE BUILD TYPE:
Read Item 1 (Preliminaries) or the header area to determine if this is a:
- Lowset / single storey home
- Highset home
- Double storey / two storey home
If the build type is lowset or single storey, a Staircase section is NOT APPLICABLE — do not flag it as missing.
Only flag staircase content as missing for highset or double storey builds.

STEP 3 — CHECK EACH SECTION FOR MEANINGFUL CONTENT:
For each item in the actual index, check if the section body contains meaningful specification text (more than {min_content_length} characters of actual content, not just the heading).
Flag a section as empty ONLY if the section header exists but the body is completely blank or contains only placeholder text like "TBC" or "N/A" with no supporting detail.

STEP 4 — LANDSCAPING EXCLUSION:
Landscaping is NOT included in AUSMAR specifications — it appears only in the exclusions section.
Do NOT flag missing landscaping content as an issue or warning. This is expected and correct.

STEP 5 — TRUNCATION / DOCUMENT COMPLETENESS CHECK:
Read to the end of the document. Check whether the Exclusions section and General Conditions section are present and appear complete.
Flag as an issue if:
- The document ends abruptly mid-sentence or mid-section
- The Exclusions section is missing entirely or ends without the standard closing clauses
- The General Conditions section is missing or appears cut off
- There is evidence the PDF was truncated (e.g. last sentence is incomplete)
If the document ends cleanly with complete sections, do not flag this.

STEP 6 — BAL / ACOUSTIC / ENERGY COMPLIANCE:
Only flag BAL, Acoustic, or Energy compliance content as missing IF Item 1 (Preliminaries) explicitly mentions a BAL rating, acoustic requirement, or energy rating that would require a corresponding detail section.
If Item 1 does not reference BAL or acoustic requirements, these sections are NOT applicable to this job — do not flag them as missing.

STEP 7 — PC/PS ALLOWANCES:
Do NOT flag "no PC/PS allowances" as missing content. PS allowances for slab and piers are valid inclusions.
Only flag a PC/PS section if the section header exists in the index but the section body is completely empty (no dollar amounts, no item descriptions, nothing at all).

Return your findings as JSON:
{{
  "result": "PASS" | "FAIL" | "WARNING",
  "confidence": 0.0-1.0,
  "build_type": "lowset" | "highset" | "double storey" | "unknown",
  "actual_index": {{"1.0": "PRELIMINARIES", "2.0": "SITE WORKS", "...": "..."}},
  "empty_sections": ["list of section names that are empty/missing content"],
  "truncation_detected": true | false,
  "truncation_detail": "description of where/how truncated, or null",
  "bal_acoustic_applicable": true | false,
  "evidence_found": "summary of what was checked and found",
  "evidence_expected": "all sections should have meaningful content",
  "documents_affected": "Contract Specification",
  "corrective_action": "specific actions needed, or 'None — all sections complete'",
  "reasoning": "brief explanation of verdict"
}}

Return PASS if all applicable sections have content and document appears complete.
Return FAIL if any applicable section is empty OR if document appears truncated.
Return WARNING if content is minimal but present, or if build type is ambiguous.""",

    "QAR-002": """RULE: Floor Covering Quantity Reconciliation
Check that floor covering m² quantities in the specification match the plan areas.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS / FLOOR PLAN TEXT:
{plan_text}

NHP PRICING TEXT:
{nhp_text}

Instructions:
- Extract all floor covering quantities (m²) from the specification (carpet, tiles, vinyl, timber)
- Extract any area measurements visible on the floor plan
- Compare the quantities — tolerance is {tolerance_percent}%
- Return PASS if quantities match within tolerance, FAIL if significant discrepancy, WARNING if data unclear""",

    "QAR-003": """RULE: Door Count Reconciliation
Check that the number of doors in the specification matches the floor plan.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS / FLOOR PLAN TEXT:
{plan_text}

NHP PRICING TEXT:
{nhp_text}

Instructions:
- Extract door counts from the specification (hinged doors, cavity sliders, bi-folds, barn doors)
- Count or extract door references from the floor plan text
- Compare counts by type
- Return PASS if counts match, FAIL if there's a discrepancy, WARNING if counts cannot be clearly determined""",

    "QAR-004": """RULE: Yard Gully Quantity Reconciliation
Minimum {minimum_yard_gullies} yard gullies required. Check spec and NHP.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

NHP PRICING TEXT:
{nhp_text}

Instructions:
- Find yard gully quantity in the specification (look in section 4.0 Plumbing)
- Find yard gully quantity in NHP pricing
- Verify minimum of {minimum_yard_gullies} yard gullies
- Return PASS if ≥ minimum and consistent, FAIL if below minimum or inconsistent, WARNING if not found""",

    "QAR-005": """RULE: Contract Price Reconciliation
NHP base price + all VOs (debits - credits) must equal the contract total.

PARAMETERS: {parameters}

NHP PRICING TEXT:
{nhp_text}

NHP CHANGES / VOs TEXT:
{vos_text}

Instructions:
- Extract the NHP base price
- Extract all VO debits and credits
- Calculate: Base + Debits - Credits = Expected Total
- Compare against the stated contract/final total
- Return PASS if they match exactly, FAIL if there's any discrepancy, WARNING if values unclear""",

    "QAR-006": """RULE: Window Schedule Reconciliation
Windows on plan must match window schedule count and types.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS TEXT:
{plan_text}

Instructions:
- Extract window references from the floor plan (codes, sizes, types)
- Extract window schedule entries
- Compare counts and types
- Return PASS if consistent, FAIL if discrepancy found, WARNING if data unclear""",

    "QAR-007": """RULE: BAL Pricing Check
If a BAL (Bushfire Attack Level) rating applies, verify BAL surcharge is in pricing.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

NHP PRICING TEXT:
{nhp_text}

WORKING DRAWINGS TEXT:
{plan_text}

Instructions:
- Search for BAL rating references in all documents (keywords: {bal_keywords})
- If BAL rating is identified, check that:
  a) Specification references BAL-compliant materials
  b) NHP pricing includes a BAL surcharge/allowance line item
- Return PASS if no BAL applies OR if BAL is properly priced, FAIL if BAL identified but not priced, WARNING if unclear""",

    "QAR-008": """RULE: Promotional Pricing Reconciliation
If a promotion was applied and items removed, verify discount also adjusted.

PARAMETERS: {parameters}

NHP PRICING TEXT:
{nhp_text}

NHP CHANGES / VOs TEXT:
{vos_text}

Instructions:
- Look for promotional packages or discounts in the NHP
- Check if any promotional items were subsequently removed via VOs
- If promotional items removed, verify the promotional discount was also removed/adjusted
- Return PASS if no promo issues, FAIL if promo item removed but discount kept, WARNING if unclear""",

    "QAR-009": """RULE: Flood Level Compliance
If flood overlay applies, verify floor levels comply.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS TEXT:
{plan_text}

NHP PRICING TEXT:
{nhp_text}

Instructions:
- Search for flood-related references (keywords: {flood_keywords})
- If flood overlay applies, check that:
  a) A flood level (DFL/Q100) is stated
  b) The slab/floor level is set above the flood level (minimum +{tolerance_mm}mm)
- Return PASS if no flood applies OR if levels comply, FAIL if flood identified but levels non-compliant, WARNING if data unclear""",

    "QAR-010": """RULE: AC Brand Consistency
AC system brand must be consistent across all document references.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

NHP PRICING TEXT:
{nhp_text}

WORKING DRAWINGS TEXT:
{plan_text}

Instructions:
- Extract AC system brand/model from specification
- Extract AC system brand/model from NHP pricing
- Extract any AC references from working drawings/electrical plan
- Compare all references for consistency
- Return PASS if all consistent, FAIL if brands differ, WARNING if only found in one document""",

    "QAR-011": """RULE: Tapware Consistency
Tapware brand/range/finish must be consistent across all rooms.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

NHP PRICING TEXT:
{nhp_text}

Instructions:
- Extract tapware specifications for each room: {rooms_to_check}
- Check brand, range, and finish are consistent across all rooms
- If a finish change was made (e.g., chrome to brushed nickel), verify ALL rooms updated
- Return PASS if consistent, FAIL if inconsistency found, WARNING if data unclear""",

    "QAR-012": """RULE: Ceiling Height Consistency
Ceiling heights in spec must match plan notations.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS TEXT:
{plan_text}

Instructions:
- Extract ceiling height from specification (per level if multi-storey)
- Extract any ceiling height notations from working drawings
- Standard heights: Ground {standard_heights_ground}mm, Upper {standard_heights_upper}mm
- Compare for consistency
- Return PASS if consistent, FAIL if discrepancy, WARNING if heights not clearly stated""",

    "QAR-013": """RULE: Slab Class Verification
Slab class on plans must match engineering/soil report.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS TEXT:
{plan_text}

Instructions:
- Extract slab class from working drawings (slab plan)
- Extract slab class from specification
- Valid classes: {slab_classes}
- Check consistency between documents
- Return PASS if consistent, FAIL if discrepancy, WARNING if slab class not clearly stated""",

    "QAR-014": """RULE: Roof Material Consistency
Roof material in spec must match elevation drawings.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS TEXT:
{plan_text}

Instructions:
- Extract roof material/type from specification (look in section 7.0 Facade and Roof — roofing is combined with facade in the current index)
- Extract roof material references from elevation drawings
- Check consistency (type and profile)
- Return PASS if consistent, FAIL if discrepancy, WARNING if not clearly stated""",

    "QAR-015": """RULE: External Cladding Consistency
Cladding type in spec must match elevations.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS TEXT:
{plan_text}

Instructions:
- Extract cladding material from specification (look in section 7.0 Facade and Roof)
- Extract cladding tags/notes from elevation drawings
- Check consistency
- Return PASS if consistent, FAIL if discrepancy, WARNING if not clearly stated""",

    "QAR-016": """RULE: Garage Door Size Verification
Garage door dimensions must match plan opening.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS TEXT:
{plan_text}

NHP PRICING TEXT:
{nhp_text}

Instructions:
- Extract garage door size from specification
- Extract garage door opening dimensions from floor plan
- Standard sizes: {standard_sizes}
- Compare for consistency (tolerance: {tolerance_mm}mm)
- Return PASS if consistent, FAIL if discrepancy, WARNING if dimensions unclear""",

    "QAR-017": """RULE: Hot Water System Consistency
HWS type/size in spec must match plumbing plan.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS TEXT:
{plan_text}

NHP PRICING TEXT:
{nhp_text}

Instructions:
- Extract hot water system type and capacity from specification
- Extract HWS references from working drawings
- Extract HWS from NHP pricing
- Check consistency across all documents
- Return PASS if consistent, FAIL if discrepancy, WARNING if not clearly stated""",

    "QAR-018": """RULE: Smoke Alarm Compliance
Smoke alarm count must meet NCC requirements for the plan layout.

PARAMETERS: {parameters}

SPECIFICATION TEXT:
{spec_text}

WORKING DRAWINGS TEXT:
{plan_text}

Instructions:
- Determine the dwelling layout (number of bedrooms, levels, hallway configuration)
- NCC requirements: {ncc_requirements}
- Minimum per level: {minimum_per_level}
- Extract smoke alarm count/placement from electrical plan
- Verify compliance with NCC requirements
- Return PASS if compliant, FAIL if non-compliant, WARNING if cannot determine""",
}


# ---------------------------------------------------------------------------
# Rule execution
# ---------------------------------------------------------------------------
def execute_rule(rule, spec_text="", plan_text="", nhp_text="", vos_text=""):
    """Execute a single rule against the provided document texts.

    Returns a dict with: result, confidence, evidence_found, evidence_expected,
    documents_affected, corrective_action
    """
    rule_ref = rule.get("rule_ref", "")
    params = rule.get("parameters_parsed", {})

    prompt_template = RULE_PROMPTS.get(rule_ref)
    if not prompt_template:
        return {
            "result": "WARNING",
            "confidence": 0.0,
            "evidence_found": "No prompt template defined for this rule",
            "evidence_expected": rule.get("expected_outcome", ""),
            "documents_affected": rule.get("documents_checked", ""),
            "corrective_action": "",
            "reasoning": "Rule not yet implemented"
        }

    # Build the prompt with parameters
    format_vars = {
        "parameters": json.dumps(params, indent=2),
        "spec_text": (spec_text or "")[:25000],
        "plan_text": (plan_text or "")[:20000],
        "nhp_text": (nhp_text or "")[:20000],
        "vos_text": (vos_text or "")[:15000],
        "min_content_length": params.get("min_content_length", 20),
        "tolerance_percent": params.get("tolerance_percent", 5),
        "minimum_yard_gullies": params.get("minimum_yard_gullies", 4),
        "bal_keywords": ", ".join(params.get("bal_keywords", [])),
        "flood_keywords": ", ".join(params.get("flood_keywords", [])),
        "tolerance_mm": params.get("tolerance_mm", 50),
        "rooms_to_check": ", ".join(params.get("rooms_to_check", [])),
        "standard_heights_ground": params.get("standard_heights", {}).get("ground", 2700),
        "standard_heights_upper": params.get("standard_heights", {}).get("upper", 2550),
        "slab_classes": ", ".join(params.get("slab_classes", [])),
        "standard_sizes": json.dumps(params.get("standard_sizes", {})),
        "ncc_requirements": json.dumps(params.get("ncc_requirements", {})),
        "minimum_per_level": params.get("minimum_per_level", 2),
    }

    try:
        user_prompt = prompt_template.format(**format_vars)
    except KeyError as e:
        user_prompt = prompt_template  # Use raw if formatting fails

    # Call the AI model
    try:
        raw = ec.call_text_model(_RULE_EXEC_SYSTEM, user_prompt, model="gpt-4.1-mini")
        result = ec.parse_json_from_llm(raw)

        # Validate and sanitize
        valid_results = ["PASS", "FAIL", "WARNING"]
        res = result.get("result", "WARNING").upper()
        if res not in valid_results:
            res = "WARNING"

        return {
            "result": res,
            "confidence": min(1.0, max(0.0, float(result.get("confidence", 0.5)))),
            "evidence_found": (result.get("evidence_found") or "").strip(),
            "evidence_expected": (result.get("evidence_expected") or rule.get("expected_outcome", "")).strip(),
            "documents_affected": (result.get("documents_affected") or rule.get("documents_checked", "")).strip(),
            "corrective_action": (result.get("corrective_action") or "").strip(),
            "reasoning": (result.get("reasoning") or "").strip(),
        }
    except Exception as e:
        return {
            "result": "WARNING",
            "confidence": 0.0,
            "evidence_found": f"Rule execution error: {str(e)}",
            "evidence_expected": rule.get("expected_outcome", ""),
            "documents_affected": rule.get("documents_checked", ""),
            "corrective_action": "Manual review required",
            "reasoning": f"Error during AI analysis: {str(e)}"
        }


# ---------------------------------------------------------------------------
# Main Contract QA Intelligence runner
# ---------------------------------------------------------------------------
def run_contract_qa_intelligence(inputs, deal_code="", consultant_name="",
                                 progress_cb=None):
    """
    Run all active Tier 1 Contract QA rules against uploaded documents.

    inputs: dict of file paths. Keys:
        working_drawings, specification, nhp, vos (variation orders)

    Returns a dict with submission results including per-rule findings.
    """
    def _p(pct, msg):
        if progress_cb:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    _p(5, "Creating QA submission...")

    # Create submission record
    sub_id = db_v3.create_submission(deal_code, consultant_name)

    try:
        # Extract document texts
        _p(10, "Reading specification...")
        spec_text = ""
        if inputs.get("specification"):
            pages = ec.extract_pdf_pages(inputs["specification"])
            spec_text = ec.pages_to_text(pages)

        _p(20, "Reading working drawings...")
        plan_text = ""
        if inputs.get("working_drawings"):
            pages = ec.extract_pdf_pages(inputs["working_drawings"])
            plan_text = ec.pages_to_text(pages)

        _p(30, "Reading NHP pricing...")
        nhp_text = ""
        if inputs.get("nhp"):
            pages = ec.extract_pdf_pages(inputs["nhp"])
            nhp_text = ec.pages_to_text(pages)

        _p(35, "Reading variation orders...")
        vos_text = ""
        if inputs.get("vos"):
            pages = ec.extract_pdf_pages(inputs["vos"])
            vos_text = ec.pages_to_text(pages)

        # Get active Tier 1 rules
        _p(40, "Loading Contract QA rules...")
        rules = db_v3.get_tier1_rules()

        if not rules:
            db_v3.fail_submission(sub_id, "No active Contract QA rules found")
            return {
                "submission_id": sub_id,
                "status": "failed",
                "error": "No active Contract QA rules found"
            }

        # Check we have at least a specification
        if not spec_text:
            db_v3.fail_submission(sub_id, "No specification document provided or readable")
            return {
                "submission_id": sub_id,
                "status": "failed",
                "error": "Specification document is required for Contract QA"
            }

        # Execute each rule
        total_rules = len(rules)
        findings = []
        passed = 0
        failed = 0
        warnings = 0
        critical_failures = 0

        for i, rule in enumerate(rules):
            progress = 45 + int((i / total_rules) * 45)
            _p(progress, f"Executing rule {rule['rule_ref']}: {rule['description'][:50]}...")

            result = execute_rule(rule, spec_text, plan_text, nhp_text, vos_text)

            # Count results
            if result["result"] == "PASS":
                passed += 1
            elif result["result"] == "FAIL":
                failed += 1
                if rule.get("severity") == "Critical":
                    critical_failures += 1
            else:  # WARNING
                warnings += 1

            # Save finding to database
            finding_id = db_v3.save_finding(
                submission_id=sub_id,
                rule_id=rule["id"],
                rule_ref=rule["rule_ref"],
                result=result["result"],
                severity=rule.get("severity", "Medium"),
                category=rule.get("category", ""),
                description=rule.get("description", ""),
                evidence_found=result["evidence_found"],
                evidence_expected=result["evidence_expected"],
                documents_affected=result["documents_affected"],
                corrective_action=result["corrective_action"],
                confidence=result["confidence"],
            )

            findings.append({
                "id": finding_id,
                "rule_ref": rule["rule_ref"],
                "rule_id": rule["id"],
                "result": result["result"],
                "severity": rule.get("severity", "Medium"),
                "category": rule.get("category", ""),
                "description": rule.get("description", ""),
                "evidence_found": result["evidence_found"],
                "evidence_expected": result["evidence_expected"],
                "documents_affected": result["documents_affected"],
                "corrective_action": result["corrective_action"],
                "confidence": result["confidence"],
            })

        # Calculate QA score
        qa_score = round((passed / total_rules) * 100, 1) if total_rules > 0 else 0.0

        # Determine verdict
        if critical_failures > 0:
            verdict = "DO NOT ISSUE"
            verdict_reason = f"{critical_failures} critical rule(s) failed. Contract must not be issued until resolved."
        elif failed > 0:
            verdict = "ISSUE AFTER CORRECTIONS"
            verdict_reason = f"{failed} rule(s) failed. Resolve before client issue."
        elif warnings > 0:
            verdict = "ISSUE WITH NOTED ITEMS"
            verdict_reason = f"All rules passed but {warnings} warning(s) require review."
        else:
            verdict = "READY TO ISSUE"
            verdict_reason = "All rules passed. Contract is ready for issue."

        # Update submission record
        _p(92, "Saving results...")
        db_v3.update_submission_results(
            sub_id, total_rules, passed, failed, warnings,
            critical_failures, qa_score, verdict, verdict_reason
        )

        _p(95, "Consolidating findings...")

        return {
            "submission_id": sub_id,
            "status": "completed",
            "deal_code": deal_code,
            "consultant_name": consultant_name,
            "total_rules": total_rules,
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
            "critical_failures": critical_failures,
            "qa_score": qa_score,
            "verdict": verdict,
            "verdict_reason": verdict_reason,
            "findings": findings,
        }

    except Exception as e:
        traceback.print_exc()
        db_v3.fail_submission(sub_id, f"Contract QA failed: {str(e)}")
        return {
            "submission_id": sub_id,
            "status": "failed",
            "error": f"Contract QA failed: {str(e)}"
        }
