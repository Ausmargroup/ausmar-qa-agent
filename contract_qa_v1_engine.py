"""
AUSMAR QA Agent — Contract QA V1 Engine.

Rule-based quality checks on contract documents (Specification, NHP, Working Drawings, VOs).
Executes 18 Tier 1 rules via OpenAI structured extraction, producing PASS/FAIL/WARNING
results with evidence for each rule.

This is ADDITIVE — it does NOT replace or modify the existing Stage 3 engine
(contract_qa_engine.py). It is a new domain ('Contract') behind a feature flag.
"""

import json
import time
import os

import engine_common as ec
import db_v1_migrations as db_v1

_IS_PG = bool(os.environ.get("DATABASE_URL", ""))


# ---------------------------------------------------------------------------
# Tier 1 Contract QA Rules (18 rules) — correct IDs from QA Knowledge Capture
# ---------------------------------------------------------------------------
TIER1_RULES = [
    {
        "rule_ref": "QAR-001",
        "category": "Specification",
        "description": "Specification completeness — all sections must be populated",
        "severity": "Critical",
        "trigger": "Any spec section (painting, exclusions, general conditions, external works) is empty or missing content",
        "expected_outcome": "Every numbered section in the spec template contains relevant content or explicit N/A notation",
        "documents_checked": "Specification",
        "automation_type": "text_extraction",
        "parameters": {"min_section_length": 20, "required_sections": 26}
    },
    {
        "rule_ref": "QAR-002",
        "category": "Pricing",
        "description": "Floor covering quantities must match plan measurements",
        "severity": "High",
        "trigger": "Floor covering SQM in spec differs from measured areas on floor plan",
        "expected_outcome": "Spec SQM figures match plan-measured areas exactly (main floors, bedrooms, carpet, tiling per room)",
        "documents_checked": "Specification, Floor Plan, NHP",
        "automation_type": "cross_document_comparison",
        "parameters": {"tolerance_sqm": 2.0}
    },
    {
        "rule_ref": "QAR-003",
        "category": "Pricing",
        "description": "Internal door count must match plan",
        "severity": "High",
        "trigger": "Hinged door or cavity slider quantity in spec/NHP differs from count on floor plan",
        "expected_outcome": "Door quantities in spec match physical doors shown on plan, split by type (hinged, cavity slider) and level",
        "documents_checked": "Specification, NHP, Floor Plan",
        "automation_type": "cross_document_comparison",
        "parameters": {}
    },
    {
        "rule_ref": "QAR-006",
        "category": "Plumbing",
        "description": "Yard gully quantity must match pricing and plan",
        "severity": "High",
        "trigger": "Yard gully count in spec differs from plan or NHP pricing",
        "expected_outcome": "Yard gully quantity is consistent across spec, plan (indicative locations shown), and NHP pricing",
        "documents_checked": "Specification, Site Plan, NHP",
        "automation_type": "text_extraction",
        "parameters": {"minimum_count": 4}
    },
    {
        "rule_ref": "QAR-010",
        "category": "Pricing",
        "description": "VO pricing reconciliation — NHP + Changes must equal contract price",
        "severity": "Critical",
        "trigger": "Sum of NHP base + all VO debits - all VO credits does not equal contract price",
        "expected_outcome": "Mathematical reconciliation is exact; any discrepancy is explained with a documented reason",
        "documents_checked": "NHP, NHP Changes, Contract",
        "automation_type": "numerical_comparison",
        "parameters": {"tolerance_dollars": 1.0}
    },
    {
        "rule_ref": "QAR-013",
        "category": "Compliance",
        "description": "Balustrade height must meet compliance minimum",
        "severity": "Critical",
        "trigger": "Balustrade height dimension on elevation is below 1000mm (typically requires 1020mm minimum)",
        "expected_outcome": "All balustrade heights dimensioned at or above compliance minimum (1000-1020mm depending on jurisdiction)",
        "documents_checked": "Elevations, Floor Plan",
        "automation_type": "numerical_comparison",
        "parameters": {"min_height_mm": 1000}
    },
    {
        "rule_ref": "QAR-014",
        "category": "Drawing",
        "description": "Window schedule must match plan windows",
        "severity": "High",
        "trigger": "Window code, size, or type in schedule differs from what is drawn/tagged on plan",
        "expected_outcome": "Every window on plan has matching entry in schedule with correct code, size, and type",
        "documents_checked": "Floor Plan, Window Schedule",
        "automation_type": "cross_document_comparison",
        "parameters": {}
    },
    {
        "rule_ref": "QAR-015",
        "category": "Cabinetry",
        "description": "Kitchen overhead height must accommodate gas cooktop splashback",
        "severity": "Medium",
        "trigger": "Gas cooktop specified but overhead cupboard height not increased to accommodate higher splashback",
        "expected_outcome": "When gas cooktop is specified, overhead cupboards increased to minimum 2250H to maintain practical size above higher splashback",
        "documents_checked": "Kitchen Plan, Specification, NHP",
        "automation_type": "conditional_check",
        "parameters": {"min_overhead_height_mm": 2250}
    },
    {
        "rule_ref": "QAR-017",
        "category": "Specification",
        "description": "Multi-level homes must split door/robe heights by level",
        "severity": "High",
        "trigger": "Two-storey home has single generic door height statement covering both levels",
        "expected_outcome": "Door heights, robe heights, and joinery heights specified separately for each level (e.g., GF 2400H, UF 2100H)",
        "documents_checked": "Specification",
        "automation_type": "conditional_check",
        "parameters": {}
    },
    {
        "rule_ref": "QAR-018",
        "category": "Drawing",
        "description": "Bi-fold doors must be evenly leafed",
        "severity": "Medium",
        "trigger": "Bi-fold door shown with odd number of leaves",
        "expected_outcome": "All bi-fold doors have even number of leaves (2, 4, 6)",
        "documents_checked": "Floor Plan, Window Schedule",
        "automation_type": "text_extraction",
        "parameters": {}
    },
    {
        "rule_ref": "QAR-019",
        "category": "Compliance",
        "description": "BAL rating requirements must be priced and documented",
        "severity": "Critical",
        "trigger": "Pre-lodgement advice identifies a BAL rating but costs not captured in NHP",
        "expected_outcome": "BAL rating identified in PSE/pre-lodgement flows through to specification (materials), NHP (pricing), and plans (compliant details)",
        "documents_checked": "PSE, Specification, NHP, Elevations",
        "automation_type": "conditional_check",
        "parameters": {"bal_keywords": ["BAL-12.5", "BAL-19", "BAL-29", "BAL-40", "BAL-FZ", "BAL 12.5", "BAL 19", "BAL 29", "BAL 40"]}
    },
    {
        "rule_ref": "QAR-031",
        "category": "Pricing",
        "description": "Promotional discounts must be reconciled when inclusions removed",
        "severity": "Critical",
        "trigger": "A promotional inclusion is removed but the associated discount is not also removed/adjusted",
        "expected_outcome": "When a promotional item is removed, the corresponding promotional discount is also removed or adjusted in NHP pricing",
        "documents_checked": "NHP, NHP Changes",
        "automation_type": "conditional_check",
        "parameters": {}
    },
    {
        "rule_ref": "QAR-043",
        "category": "Specification",
        "description": "Tiling substrate must match floor level",
        "severity": "High",
        "trigger": "Tiling specified on standard substrate but room is on upper level requiring secura sheet flooring",
        "expected_outcome": "Upper level wet area tiling specified on secura sheet flooring; ground level on standard substrate",
        "documents_checked": "Specification",
        "automation_type": "conditional_check",
        "parameters": {}
    },
    {
        "rule_ref": "QAR-045",
        "category": "Pricing",
        "description": "Splashback tiling must be priced when specified",
        "severity": "High",
        "trigger": "Splashback tiles mentioned in spec but not captured in NHP pricing",
        "expected_outcome": "All tiling areas mentioned in specification have corresponding pricing in NHP",
        "documents_checked": "Specification, NHP",
        "automation_type": "cross_document_comparison",
        "parameters": {}
    },
    {
        "rule_ref": "QAR-047",
        "category": "Compliance",
        "description": "Flood level compliance must be verified against engineering",
        "severity": "Critical",
        "trigger": "Site is in flood area but slab level not confirmed against flood level requirements",
        "expected_outcome": "Slab/pad level meets flood level requirement with appropriate tolerance (e.g., +50mm) as confirmed by engineering",
        "documents_checked": "Site Plan, Slab Plan, Engineering",
        "automation_type": "conditional_check",
        "parameters": {"tolerance_mm": 50}
    },
    {
        "rule_ref": "QAR-054",
        "category": "Pricing",
        "description": "Credits must be captured for removed items",
        "severity": "High",
        "trigger": "Items removed from scope but no credit line in NHP Changes",
        "expected_outcome": "Every removed item has a corresponding credit line in NHP Changes (or explicit $0 note if FOC)",
        "documents_checked": "NHP Changes",
        "automation_type": "text_extraction",
        "parameters": {}
    },
    {
        "rule_ref": "QAR-056",
        "category": "Specification",
        "description": "AC system brand/model must be consistent across documents",
        "severity": "High",
        "trigger": "AC brand in spec (e.g., MyAir) differs from what is priced in NHP (e.g., AirTouch)",
        "expected_outcome": "AC system brand and model consistent between specification, NHP pricing, and electrical plan notes",
        "documents_checked": "Specification, NHP, Electrical Plan",
        "automation_type": "consistency_check",
        "parameters": {}
    },
    {
        "rule_ref": "QAR-066",
        "category": "Pricing",
        "description": "Tapware finish changes must apply consistently across all rooms",
        "severity": "High",
        "trigger": "Tapware finish changed in one room but not updated across all rooms",
        "expected_outcome": "When tapware finish is changed (e.g., chrome ILO brushed nickel), all rooms updated consistently",
        "documents_checked": "Specification, NHP",
        "automation_type": "consistency_check",
        "parameters": {}
    },
]


# ---------------------------------------------------------------------------
# Rule execution prompts
# ---------------------------------------------------------------------------
_RULE_EXEC_SYSTEM = """You are the AUSMAR Contract QA Rule Executor. You evaluate a SINGLE quality rule
against the provided contract documents. You must determine whether the rule PASSES, FAILS, or
produces a WARNING based on the evidence in the documents.

CRITICAL ACCURACY RULES:
1. Better to return WARNING (needs human review) than to assert a FALSE FAIL.
2. If you cannot find enough evidence to evaluate the rule, return WARNING with explanation.
3. Only return FAIL when the documents CLEARLY show a violation.
4. Only return PASS when you have POSITIVE evidence the requirement is met.
5. Be specific about what you found (evidence_found) and what was expected (evidence_expected).
6. For conditional rules (e.g. "if gas cooktop specified..."), if the condition does NOT apply
   (e.g. no gas cooktop), return PASS with evidence explaining the condition is not triggered.

Return ONLY valid JSON in this exact format:
{
  "result": "PASS" | "FAIL" | "WARNING",
  "confidence": 0.0 to 1.0,
  "evidence_found": "What the documents actually show",
  "evidence_expected": "What the rule requires",
  "recommendation": "Specific corrective action if FAIL/WARNING, or confirmation note if PASS"
}"""


def _build_rule_prompt(rule, doc_texts):
    """Build the user prompt for evaluating a single rule against documents."""
    parts = [
        f"RULE: {rule['rule_ref']} — {rule['description']}",
        f"SEVERITY: {rule['severity']}",
        f"TRIGGER: {rule.get('trigger', '')}",
        f"EXPECTED OUTCOME: {rule.get('expected_outcome', '')}",
        f"DOCUMENTS TO CHECK: {rule.get('documents_checked', '')}",
    ]
    if rule.get("parameters"):
        parts.append(f"PARAMETERS: {json.dumps(rule['parameters'])}")

    parts.append("\n--- DOCUMENT CONTENT ---")
    for doc_name, text in doc_texts.items():
        if text:
            # Cap each document to avoid token overflow
            capped = text[:15000]
            parts.append(f"\n=== {doc_name.upper()} ===\n{capped}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------
def run_contract_qa_v1(inputs, deal_code="", consultant_name="", progress_cb=None):
    """
    Run Contract QA V1 — execute Tier 1 rules against uploaded documents.

    inputs: dict of file paths. Recognised keys:
        working_drawings, specification, nhp, vos
    Returns: dict with results, dashboard data, findings.
    """
    def _p(pct, msg):
        if progress_cb:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    _p(5, "Reading contract documents...")

    # Extract text from uploaded documents
    doc_texts = {}
    for key in ["specification", "nhp", "vos", "working_drawings"]:
        path = inputs.get(key)
        if path:
            pages = ec.extract_pdf_pages(path)
            doc_texts[key] = ec.pages_to_text(pages) if pages else ""
        else:
            doc_texts[key] = ""

    # Check we have minimum required documents
    if not doc_texts.get("specification") and not doc_texts.get("nhp"):
        return {
            "stage": "contract_qa_v1",
            "deal_code": deal_code,
            "consultant_name": consultant_name,
            "verdict": "PARKED",
            "verdict_reason": "Cannot run Contract QA without at least a Specification or NHP document.",
            "results": [],
            "score": {"score": 0, "total": 0, "passed": 0, "failed": 0, "warnings": 0},
            "findings": [],
        }

    _p(15, "Loading active Contract QA rules...")

    # Get active Contract domain rules from DB
    active_rules = _get_active_contract_rules()
    if not active_rules:
        # Fall back to TIER1_RULES if DB hasn't been seeded yet
        active_rules = TIER1_RULES

    total_rules = len(active_rules)
    results = []
    findings = []

    _p(20, f"Executing {total_rules} Contract QA rules...")

    for idx, rule in enumerate(active_rules):
        pct = 20 + int((idx / total_rules) * 65)
        _p(pct, f"Rule {idx+1}/{total_rules}: {rule['rule_ref']} — {rule['description'][:50]}...")

        start_time = time.time()
        try:
            result = _execute_single_rule(rule, doc_texts)
        except Exception as e:
            result = {
                "result": "WARNING",
                "confidence": 0.0,
                "evidence_found": f"Rule execution error: {str(e)}",
                "evidence_expected": rule.get("expected_outcome", ""),
                "recommendation": "Manual review required — rule execution encountered an error.",
            }
        elapsed_ms = int((time.time() - start_time) * 1000)

        rule_result = {
            "rule_ref": rule["rule_ref"],
            "rule_id": rule.get("id", 0),
            "category": rule["category"],
            "description": rule["description"],
            "severity": rule["severity"],
            "result": result.get("result", "WARNING"),
            "confidence": result.get("confidence", 0.0),
            "evidence_found": result.get("evidence_found", ""),
            "evidence_expected": result.get("evidence_expected", ""),
            "recommendation": result.get("recommendation", ""),
            "execution_time_ms": elapsed_ms,
            "documents_checked": rule.get("documents_checked", ""),
        }
        results.append(rule_result)

        # Build finding for non-PASS results
        if result.get("result") in ("FAIL", "WARNING"):
            findings.append(rule_result)

    _p(90, "Calculating QA score and building dashboard...")

    # Calculate score
    passed = sum(1 for r in results if r["result"] == "PASS")
    failed = sum(1 for r in results if r["result"] == "FAIL")
    warnings = sum(1 for r in results if r["result"] == "WARNING")
    score_pct = round((passed / total_rules) * 100, 1) if total_rules > 0 else 0

    score = {
        "score": score_pct,
        "total": total_rules,
        "passed": passed,
        "failed": failed,
        "warnings": warnings,
    }

    # Determine verdict
    critical_fails = [r for r in results if r["result"] == "FAIL" and r["severity"] == "Critical"]
    high_fails = [r for r in results if r["result"] == "FAIL" and r["severity"] == "High"]

    if critical_fails:
        verdict = "DO NOT ISSUE"
        verdict_reason = f"{len(critical_fails)} critical rule(s) failed. Contract must not be issued until resolved."
    elif high_fails:
        verdict = "ISSUE AFTER CORRECTIONS"
        verdict_reason = f"{len(high_fails)} high-priority rule(s) failed. Resolve before client issue."
    elif failed > 0:
        verdict = "ISSUE WITH NOTED ITEMS"
        verdict_reason = f"{failed} rule(s) failed (medium/low severity). Review and resolve where practical."
    elif warnings > 0:
        verdict = "CONDITIONAL PASS"
        verdict_reason = f"All rules passed but {warnings} warning(s) need human confirmation."
    else:
        verdict = "READY TO ISSUE"
        verdict_reason = "All Contract QA rules passed. Contract is ready for issue."

    # Group findings by category
    findings_by_category = {}
    for f in findings:
        findings_by_category.setdefault(f["category"], []).append(f)

    _p(95, "Contract QA V1 complete.")

    return {
        "stage": "contract_qa_v1",
        "deal_code": deal_code,
        "consultant_name": consultant_name,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "results": results,
        "score": score,
        "findings": findings,
        "findings_by_category": findings_by_category,
        "critical_failures": critical_fails,
        "total_execution_time_ms": sum(r["execution_time_ms"] for r in results),
    }


def _execute_single_rule(rule, doc_texts):
    """Execute a single rule against the document texts using OpenAI."""
    user_prompt = _build_rule_prompt(rule, doc_texts)

    raw = ec.call_text_model(
        _RULE_EXEC_SYSTEM,
        user_prompt,
        model="gpt-4.1-mini"
    )

    # Parse the JSON response
    parsed = ec.parse_json_from_llm(raw)

    # Validate result field
    valid_results = ("PASS", "FAIL", "WARNING")
    result_val = (parsed.get("result") or "WARNING").upper()
    if result_val not in valid_results:
        result_val = "WARNING"

    # Validate confidence
    confidence = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "result": result_val,
        "confidence": confidence,
        "evidence_found": (parsed.get("evidence_found") or "").strip(),
        "evidence_expected": (parsed.get("evidence_expected") or "").strip(),
        "recommendation": (parsed.get("recommendation") or "").strip(),
    }


def _get_active_contract_rules():
    """Get active rules with domain='Contract' from the database."""
    try:
        import database as db
        conn = db.get_db()
        if _IS_PG:
            cur = conn.cursor()
            cur.execute("SELECT * FROM qa_rules WHERE domain='Contract' AND active=1 ORDER BY rule_ref")
            rows = cur.fetchall()
        else:
            rows = conn.execute("SELECT * FROM qa_rules WHERE domain='Contract' AND active=1 ORDER BY rule_ref").fetchall()
        conn.close()

        rules = []
        for r in rows:
            d = dict(r)
            # Parse parameters JSON
            try:
                if d.get("parameters"):
                    if isinstance(d["parameters"], str):
                        d["parameters"] = json.loads(d["parameters"])
                else:
                    d["parameters"] = {}
            except (json.JSONDecodeError, TypeError):
                d["parameters"] = {}
            rules.append(d)
        return rules if rules else None
    except Exception as e:
        print(f"[WARN] _get_active_contract_rules: {e}")
        return None


# ---------------------------------------------------------------------------
# Seed Tier 1 rules into database
# ---------------------------------------------------------------------------
def seed_tier1_rules():
    """Insert the 18 Tier 1 Contract QA rules into qa_rules table.
    Idempotent — skips rules that already exist (keyed on rule_ref + domain)."""
    import database as db

    conn = db.get_db()
    try:
        # Get existing rule refs for Contract domain
        if _IS_PG:
            cur = conn.cursor()
            cur.execute("SELECT rule_ref FROM qa_rules WHERE domain='Contract'")
            existing = {r["rule_ref"] for r in cur.fetchall()}
        else:
            existing = {r["rule_ref"] for r in conn.execute("SELECT rule_ref FROM qa_rules WHERE domain='Contract'").fetchall()}

        for rule in TIER1_RULES:
            if rule["rule_ref"] in existing:
                continue

            params_json = json.dumps(rule.get("parameters", {}))

            if _IS_PG:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO qa_rules (rule_ref, category, description, severity, active,
                       stage_applicability, domain, parameters, version, source)
                       VALUES (%s,%s,%s,%s,1,%s,%s,%s,1,'seed')""",
                    (rule["rule_ref"], rule["category"], rule["description"],
                     rule["severity"], "Contract", "Contract", params_json))
            else:
                conn.execute(
                    """INSERT INTO qa_rules (rule_ref, category, description, severity, active,
                       stage_applicability, domain, parameters, version, source)
                       VALUES (?,?,?,?,1,?,?,?,1,'seed')""",
                    (rule["rule_ref"], rule["category"], rule["description"],
                     rule["severity"], "Contract", "Contract", params_json))

        conn.commit()
    except Exception as e:
        print(f"[WARN] seed_tier1_rules: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
