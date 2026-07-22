"""
AUSMAR QA Agent V3 — Contract QA Intelligence database extensions.

Adds:
- Domain field (PSE / NHP / Contract) to qa_rules
- Rule parameters/thresholds as JSON config
- Rule version tracking
- Per-rule pass/fail/warning result recording
- QA score calculation
- Contract QA submission tracking (Stage 4)

BACKWARDS COMPATIBLE: All existing tables, columns, and data remain untouched.
New columns use ALTER TABLE ... ADD COLUMN IF NOT EXISTS (Postgres) or safe
try/except for SQLite. Existing rules get domain='Contract' tag where appropriate.
"""

import json
import os

import database as db
import db_v2

_IS_PG = bool(os.environ.get("DATABASE_URL", ""))


def _q(sql):
    return sql.replace("?", "%s") if _IS_PG else sql


def _exec(conn, sql, params=()):
    if _IS_PG:
        cur = conn.cursor()
        _SENTINEL = "\x00PARAM\x00"
        pg_sql = sql.replace("?", "%s")
        pg_sql = pg_sql.replace("%s", _SENTINEL).replace("%", "%%").replace(_SENTINEL, "%s")
        cur.execute(pg_sql, params)
        return cur
    else:
        return conn.execute(sql, params)


def _now_default():
    return "TIMESTAMPTZ DEFAULT NOW()" if _IS_PG else "TEXT DEFAULT (datetime('now'))"


def _serial_pk():
    return "SERIAL PRIMARY KEY" if _IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


# ---------------------------------------------------------------------------
# Schema migrations (idempotent)
# ---------------------------------------------------------------------------
def init_v3():
    """Apply V3 schema additions. Safe to call on every boot."""
    conn = db.get_db()
    pk = _serial_pk()
    ts = _now_default()

    # 1. Add domain column to qa_rules (if not exists)
    _safe_add_column(conn, "qa_rules", "domain", "TEXT DEFAULT 'Contract'")
    # 2. Add parameters JSON column to qa_rules
    _safe_add_column(conn, "qa_rules", "parameters", "TEXT DEFAULT '{}'")
    # 3. Add version column to qa_rules
    _safe_add_column(conn, "qa_rules", "version", "INTEGER DEFAULT 1")
    # 4. Add trigger_condition column
    _safe_add_column(conn, "qa_rules", "trigger_condition", "TEXT DEFAULT ''")
    # 5. Add expected_outcome column
    _safe_add_column(conn, "qa_rules", "expected_outcome", "TEXT DEFAULT ''")
    # 6. Add documents_checked column
    _safe_add_column(conn, "qa_rules", "documents_checked", "TEXT DEFAULT ''")
    # 7. Add automation_type column
    _safe_add_column(conn, "qa_rules", "automation_type", "TEXT DEFAULT 'AI-assisted'")

    # Create contract_qa_submissions table (Stage 4 — Contract QA Intelligence)
    create_submissions = f"""CREATE TABLE IF NOT EXISTS contract_qa_submissions (
        id {pk},
        deal_code TEXT NOT NULL DEFAULT '',
        consultant_name TEXT DEFAULT '',
        status TEXT DEFAULT 'processing',
        total_rules INTEGER DEFAULT 0,
        passed INTEGER DEFAULT 0,
        failed INTEGER DEFAULT 0,
        warnings INTEGER DEFAULT 0,
        critical_failures INTEGER DEFAULT 0,
        qa_score REAL DEFAULT 0.0,
        verdict TEXT DEFAULT '',
        verdict_reason TEXT DEFAULT '',
        created_at {ts}
    )"""

    # Create contract_qa_findings table (per-rule results)
    create_findings = f"""CREATE TABLE IF NOT EXISTS contract_qa_findings (
        id {pk},
        submission_id INTEGER NOT NULL,
        rule_id INTEGER NOT NULL,
        rule_ref TEXT DEFAULT '',
        result TEXT DEFAULT 'PENDING',
        severity TEXT DEFAULT 'Medium',
        category TEXT DEFAULT '',
        description TEXT DEFAULT '',
        evidence_found TEXT DEFAULT '',
        evidence_expected TEXT DEFAULT '',
        documents_affected TEXT DEFAULT '',
        corrective_action TEXT DEFAULT '',
        confidence REAL DEFAULT 0.0,
        created_at {ts}
    )"""

    if _IS_PG:
        cur = conn.cursor()
        cur.execute(create_submissions)
        cur.execute(create_findings)
        conn.commit()
    else:
        conn.execute(create_submissions)
        conn.execute(create_findings)
        conn.commit()
    conn.close()

    # Tag existing rules with appropriate domain
    _tag_existing_rules_domain()
    # Seed the 18 Tier 1 Contract QA rules
    _seed_tier1_rules()


def _safe_add_column(conn, table, column, definition):
    """Add a column if it doesn't exist. Works on both Postgres and SQLite."""
    if _IS_PG:
        try:
            cur = conn.cursor()
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
    else:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.commit()
        except Exception:
            pass  # Column already exists


def _tag_existing_rules_domain():
    """Tag existing rules with their appropriate domain. Stage 2 = NHP, Stage 3 = Contract."""
    conn = db.get_db()
    try:
        # Stage 2 rules → NHP domain
        _exec(conn, "UPDATE qa_rules SET domain='NHP' WHERE stage_applicability='Stage 2' AND (domain IS NULL OR domain='' OR domain='Contract')")
        # Stage 3 rules → Contract domain
        _exec(conn, "UPDATE qa_rules SET domain='Contract' WHERE stage_applicability='Stage 3' AND (domain IS NULL OR domain='')")
        # Both → Contract (primary)
        _exec(conn, "UPDATE qa_rules SET domain='Contract' WHERE stage_applicability='Both' AND (domain IS NULL OR domain='')")
        conn.commit()
    except Exception as e:
        print(f"[WARN] _tag_existing_rules_domain: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tier 1 Contract QA Rules (18 rules)
# ---------------------------------------------------------------------------
TIER1_RULES = [
    {
        "rule_ref": "QAR-001",
        "category": "Specification",
        "description": "Specification completeness — all spec sections must be populated with content (no blank sections)",
        "severity": "Critical",
        "domain": "Contract",
        "trigger_condition": "Any spec section (painting, exclusions, general conditions, external works) is empty or missing content",
        "expected_outcome": "Every numbered section in the spec template contains relevant content or explicit N/A notation",
        "documents_checked": "Specification",
        "automation_type": "Rule-based",
        "parameters": json.dumps({
            "required_sections": [
                # Current AUSMAR spec index — updated 23/07/2026 from NHP.pdf
                "1.0 Preliminaries",
                "2.0 Site Works",
                "3.0 Slab",
                "4.0 Plumbing",
                "5.0 Electrical",
                "6.0 Framing",
                "7.0 Facade and Roof",
                "8.0 Doors and Windows",
                "9.0 General Internals",
                "11.0 Kitchen",
                "12.0 Bathroom",
                "13.0 Ensuite",
                "16.0 Powder Room",
                "18.0 WC",
                "19.0 Laundry",
                "20.0 Floor Coverings",
                "21.0 Home Accessories",
                "22.0 External Works",
                "23.0 Painting",
                "24.0 Exclusions",
                "24.2 General Conditions and Notes"
                # Note: 9.2 Other Cabinetry, 10.0 Staircase, 14.0 Ensuite #2, 15.0 Ensuite #3,
                # 17.0 Powder Room #2, 24.3 Additional Conditions are optional/build-type-dependent
            ],
            "min_content_length": 20
        }),
    },
    {
        "rule_ref": "QAR-002",
        "category": "Pricing",
        "description": "Floor covering quantity reconciliation — floor covering m² in spec must match plan area measurements",
        "severity": "High",
        "domain": "Contract",
        "trigger_condition": "Floor covering SQM in spec differs from measured areas on floor plan",
        "expected_outcome": "Spec SQM figures match plan-measured areas (main floors, bedrooms, carpet, tiling per room)",
        "documents_checked": "Specification, Floor Plan, NHP",
        "automation_type": "AI-assisted",
        "parameters": json.dumps({
            "tolerance_percent": 5,
            "floor_types": ["carpet", "tiles", "vinyl", "timber"]
        }),
    },
    {
        "rule_ref": "QAR-003",
        "category": "Pricing",
        "description": "Door count reconciliation — number of doors on plan must match door schedule/specification quantities",
        "severity": "High",
        "domain": "Contract",
        "trigger_condition": "Hinged door or cavity slider quantity in spec/NHP differs from count on floor plan",
        "expected_outcome": "Door quantities in spec match physical doors shown on plan, split by type (hinged, cavity slider)",
        "documents_checked": "Specification, NHP, Floor Plan",
        "automation_type": "AI-assisted",
        "parameters": json.dumps({
            "door_types": ["hinged", "cavity_slider", "bi-fold", "barn_door"],
            "count_method": "plan_vs_spec"
        }),
    },
    {
        "rule_ref": "QAR-004",
        "category": "Plumbing",
        "description": "Yard gully quantity reconciliation — minimum 4 yard gullies required, check spec 4.0 Plumbing",
        "severity": "High",
        "domain": "Contract",
        "trigger_condition": "Yard gully count in spec differs from plan or NHP pricing, or is below minimum",
        "expected_outcome": "Yard gully quantity is consistent across spec, plan, and NHP pricing, and meets minimum requirement",
        "documents_checked": "Specification, Site Plan, NHP",
        "automation_type": "Rule-based",
        "parameters": json.dumps({
            "minimum_yard_gullies": 4,
            "spec_section": "4.0 Plumbing"
        }),
    },
    {
        "rule_ref": "QAR-005",
        "category": "Pricing",
        "description": "Contract price reconciliation — NHP total plus all VOs must equal contract total",
        "severity": "Critical",
        "domain": "Contract",
        "trigger_condition": "Sum of NHP base + all VO debits - credits does not equal contract price",
        "expected_outcome": "Mathematical reconciliation is exact; any discrepancy is explained with a documented reason",
        "documents_checked": "NHP, NHP Changes, Contract",
        "automation_type": "Rule-based",
        "parameters": json.dumps({
            "tolerance_dollars": 0,
            "check_gst": True
        }),
    },
    {
        "rule_ref": "QAR-006",
        "category": "Drawings/Elevations",
        "description": "Window schedule reconciliation — windows on plan must match window schedule count and types",
        "severity": "High",
        "domain": "Contract",
        "trigger_condition": "Window code, size, or type in schedule differs from what is drawn/tagged on plan",
        "expected_outcome": "Every window on plan has matching entry in schedule with correct code, size, and type",
        "documents_checked": "Floor Plan, Window Schedule",
        "automation_type": "AI-assisted",
        "parameters": json.dumps({
            "check_codes": True,
            "check_sizes": True,
            "check_types": True
        }),
    },
    {
        "rule_ref": "QAR-007",
        "category": "Compliance",
        "description": "BAL pricing checks — if BAL rating applies, verify BAL surcharge is included in pricing",
        "severity": "Critical",
        "domain": "Contract",
        "trigger_condition": "Pre-lodgement advice identifies a BAL rating but costs not captured in NHP",
        "expected_outcome": "BAL rating flows through to specification (materials), NHP (pricing), and plans (compliant details)",
        "documents_checked": "PSE, Specification, NHP, Elevations",
        "automation_type": "Rule-based + AI",
        "parameters": json.dumps({
            "bal_keywords": ["BAL-12.5", "BAL-19", "BAL-29", "BAL-40", "BAL-FZ", "BAL rating", "bushfire"],
            "required_in_nhp": True,
            "required_in_spec": True
        }),
    },
    {
        "rule_ref": "QAR-008",
        "category": "Pricing",
        "description": "Promotional pricing reconciliation — if promotion applied, verify correct discount/package price",
        "severity": "Critical",
        "domain": "Contract",
        "trigger_condition": "Promotional inclusion removed but associated discount not also removed/adjusted",
        "expected_outcome": "When a promotional item is removed, the corresponding promotional discount is also removed or adjusted",
        "documents_checked": "NHP, NHP Changes",
        "automation_type": "Rule-based",
        "parameters": json.dumps({
            "check_removal_credits": True,
            "check_package_integrity": True
        }),
    },
    {
        "rule_ref": "QAR-009",
        "category": "Compliance",
        "description": "Flood level checks — if flood overlay applies, check floor levels comply with requirements",
        "severity": "Critical",
        "domain": "Contract",
        "trigger_condition": "Site is in flood area but slab level not confirmed against flood level requirements",
        "expected_outcome": "Slab/pad level meets flood level requirement with appropriate tolerance (e.g., +50mm) as confirmed by engineering",
        "documents_checked": "Site Plan, Slab Plan, Engineering",
        "automation_type": "Rule-based + AI",
        "parameters": json.dumps({
            "flood_keywords": ["flood", "flood level", "flood overlay", "Q100", "DFL", "defined flood level"],
            "tolerance_mm": 50
        }),
    },
    {
        "rule_ref": "QAR-010",
        "category": "Specification",
        "description": "AC brand consistency — AC brand in spec must match across all references",
        "severity": "High",
        "domain": "Contract",
        "trigger_condition": "AC brand in spec (e.g., MyAir) differs from what is priced in NHP (e.g., AirTouch)",
        "expected_outcome": "AC system brand and model consistent between specification, NHP pricing, and electrical plan notes",
        "documents_checked": "Specification, NHP, Electrical Plan",
        "automation_type": "Rule-based",
        "parameters": json.dumps({
            "ac_brands": ["MyAir", "AirTouch", "Daikin", "Fujitsu", "Mitsubishi", "Samsung", "ActronAir", "Advantage Air"],
            "check_sections": ["spec", "nhp", "electrical_plan"]
        }),
    },
    {
        "rule_ref": "QAR-011",
        "category": "Specification",
        "description": "Tapware consistency — tapware brand/range must be consistent across all spec sections (kitchen, bathroom, ensuite, laundry)",
        "severity": "High",
        "domain": "Contract",
        "trigger_condition": "Tapware finish changed in one room but not updated across all rooms",
        "expected_outcome": "When tapware finish is changed, all rooms updated consistently",
        "documents_checked": "Specification, NHP",
        "automation_type": "Rule-based",
        "parameters": json.dumps({
            "rooms_to_check": ["kitchen", "bathroom", "ensuite", "laundry", "powder room"],
            "attributes": ["brand", "range", "finish"]
        }),
    },
    {
        "rule_ref": "QAR-012",
        "category": "Drawings/Elevations",
        "description": "Ceiling height consistency — ceiling heights in spec must match plan notations",
        "severity": "High",
        "domain": "Contract",
        "trigger_condition": "Ceiling height in specification does not match what is noted on plans",
        "expected_outcome": "Ceiling heights consistent between specification and plan notations for each level",
        "documents_checked": "Specification, Floor Plan, Sections",
        "automation_type": "AI-assisted",
        "parameters": json.dumps({
            "standard_heights": {"ground": 2700, "upper": 2550},
            "check_per_level": True
        }),
    },
    {
        "rule_ref": "QAR-013",
        "category": "Structural",
        "description": "Slab class verification — slab class in plans must match soil report/engineering requirements",
        "severity": "High",
        "domain": "Contract",
        "trigger_condition": "Slab class on plans does not match engineering/soil report classification",
        "expected_outcome": "Slab class on working drawings matches the received engineering specification",
        "documents_checked": "Slab Plan, Engineering, Specification",
        "automation_type": "AI-assisted",
        "parameters": json.dumps({
            "slab_classes": ["M", "H1", "H2", "E", "P"],
            "source_priority": "engineering"
        }),
    },
    {
        "rule_ref": "QAR-014",
        "category": "Drawings/Elevations",
        "description": "Roof material consistency — roof material in spec must match elevation drawings",
        "severity": "Medium",
        "domain": "Contract",
        "trigger_condition": "Roof material specified does not match what is shown/noted on elevation drawings",
        "expected_outcome": "Roof material type and profile in specification matches elevation drawing notations",
        "documents_checked": "Specification, Elevations, Roof Plan",
        "automation_type": "AI-assisted",
        "parameters": json.dumps({
            "roof_types": ["Colorbond", "concrete tiles", "terracotta tiles", "metal deck"],
            "check_profile": True
        }),
    },
    {
        "rule_ref": "QAR-015",
        "category": "Drawings/Elevations",
        "description": "External cladding consistency — cladding type in spec must match elevations",
        "severity": "Medium",
        "domain": "Contract",
        "trigger_condition": "Cladding type in specification does not match what is shown on elevation drawings",
        "expected_outcome": "Cladding material and profile tags on elevations match specification description",
        "documents_checked": "Specification, Elevations",
        "automation_type": "AI-assisted",
        "parameters": json.dumps({
            "cladding_types": ["brick", "render", "weatherboard", "Scyon", "James Hardie", "Cemintel"],
            "check_orientation": True
        }),
    },
    {
        "rule_ref": "QAR-016",
        "category": "Drawings/Elevations",
        "description": "Garage door size verification — garage door dimensions must match plan opening",
        "severity": "Medium",
        "domain": "Contract",
        "trigger_condition": "Garage door dimensions in spec/NHP do not match the opening shown on floor plan",
        "expected_outcome": "Garage door width and height match the plan opening dimensions",
        "documents_checked": "Specification, NHP, Floor Plan",
        "automation_type": "AI-assisted",
        "parameters": json.dumps({
            "standard_sizes": {"single": "2550x2100", "double": "4800x2100", "triple": "5400x2100"},
            "tolerance_mm": 50
        }),
    },
    {
        "rule_ref": "QAR-017",
        "category": "Specification",
        "description": "Hot water system consistency — HWS type/size in spec must match plumbing plan",
        "severity": "Medium",
        "domain": "Contract",
        "trigger_condition": "Hot water system type or capacity in specification differs from plumbing plan notation",
        "expected_outcome": "HWS type (gas/electric/solar/heat pump) and capacity consistent across spec and plans",
        "documents_checked": "Specification, Plumbing Plan, NHP",
        "automation_type": "AI-assisted",
        "parameters": json.dumps({
            "hws_types": ["gas instantaneous", "gas storage", "electric", "solar", "heat pump"],
            "check_capacity": True
        }),
    },
    {
        "rule_ref": "QAR-018",
        "category": "Compliance",
        "description": "Smoke alarm compliance — minimum smoke alarm count must meet NCC requirements for plan layout",
        "severity": "Critical",
        "domain": "Contract",
        "trigger_condition": "Smoke alarm count or placement does not meet NCC minimum requirements for the dwelling layout",
        "expected_outcome": "Smoke alarms meet NCC requirements: interconnected, in every bedroom, hallways, and each level",
        "documents_checked": "Electrical Plan, Floor Plan",
        "automation_type": "Rule-based + AI",
        "parameters": json.dumps({
            "ncc_requirements": {
                "every_bedroom": True,
                "hallway_between_bedrooms_and_rest": True,
                "every_level": True,
                "interconnected": True
            },
            "minimum_per_level": 2
        }),
    },
]


def _seed_tier1_rules():
    """Seed the 18 Tier 1 Contract QA rules (idempotent — keyed on rule_ref)."""
    conn = db.get_db()
    try:
        # Fetch existing refs
        if _IS_PG:
            cur = conn.cursor()
            cur.execute("SELECT rule_ref FROM qa_rules")
            existing = {r["rule_ref"] for r in cur.fetchall()}
        else:
            existing = {r["rule_ref"] for r in conn.execute("SELECT rule_ref FROM qa_rules").fetchall()}

        for rule in TIER1_RULES:
            if rule["rule_ref"] in existing:
                # Update parameters and metadata for existing rules (don't duplicate)
                _exec(conn,
                      """UPDATE qa_rules SET domain=?, parameters=?, trigger_condition=?,
                         expected_outcome=?, documents_checked=?, automation_type=?, version=1
                         WHERE rule_ref=?""",
                      (rule["domain"], rule["parameters"], rule["trigger_condition"],
                       rule["expected_outcome"], rule["documents_checked"],
                       rule["automation_type"], rule["rule_ref"]))
            else:
                _exec(conn,
                      """INSERT INTO qa_rules (rule_ref, category, description, severity, active,
                         stage_applicability, source, domain, parameters, trigger_condition,
                         expected_outcome, documents_checked, automation_type, version)
                         VALUES (?,?,?,?,1,?,?,?,?,?,?,?,?,1)""",
                      (rule["rule_ref"], rule["category"], rule["description"],
                       rule["severity"], "Stage 4", "seed", rule["domain"],
                       rule["parameters"], rule["trigger_condition"],
                       rule["expected_outcome"], rule["documents_checked"],
                       rule["automation_type"]))
        conn.commit()
    except Exception as e:
        print(f"[WARN] _seed_tier1_rules failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Contract QA Submission helpers
# ---------------------------------------------------------------------------
def create_submission(deal_code, consultant_name):
    """Create a new Contract QA submission record."""
    conn = db.get_db()
    cur = _exec(conn,
                """INSERT INTO contract_qa_submissions (deal_code, consultant_name, status)
                   VALUES (?,?,?)""" + (" RETURNING id" if _IS_PG else ""),
                (deal_code, consultant_name, "processing"))
    sub_id = cur.fetchone()["id"] if _IS_PG else cur.lastrowid
    conn.commit()
    conn.close()
    return sub_id


def update_submission_results(sub_id, total_rules, passed, failed, warnings,
                              critical_failures, qa_score, verdict, verdict_reason):
    """Update submission with final results."""
    conn = db.get_db()
    _exec(conn,
          """UPDATE contract_qa_submissions SET status='completed',
             total_rules=?, passed=?, failed=?, warnings=?,
             critical_failures=?, qa_score=?, verdict=?, verdict_reason=?
             WHERE id=?""",
          (total_rules, passed, failed, warnings, critical_failures,
           qa_score, verdict, verdict_reason, sub_id))
    conn.commit()
    conn.close()


def fail_submission(sub_id, reason):
    """Mark a submission as failed."""
    conn = db.get_db()
    _exec(conn,
          "UPDATE contract_qa_submissions SET status='failed', verdict_reason=? WHERE id=?",
          (reason, sub_id))
    conn.commit()
    conn.close()


def save_finding(submission_id, rule_id, rule_ref, result, severity, category,
                 description, evidence_found, evidence_expected, documents_affected,
                 corrective_action, confidence=0.0):
    """Save a single rule finding."""
    conn = db.get_db()
    cur = _exec(conn,
                """INSERT INTO contract_qa_findings
                   (submission_id, rule_id, rule_ref, result, severity, category,
                    description, evidence_found, evidence_expected, documents_affected,
                    corrective_action, confidence)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""" + (" RETURNING id" if _IS_PG else ""),
                (submission_id, rule_id, rule_ref, result, severity, category,
                 description, evidence_found, evidence_expected, documents_affected,
                 corrective_action, confidence))
    finding_id = cur.fetchone()["id"] if _IS_PG else cur.lastrowid
    conn.commit()
    conn.close()
    return finding_id


def get_submission(sub_id):
    """Get a submission with its findings."""
    conn = db.get_db()
    row = _exec(conn, "SELECT * FROM contract_qa_submissions WHERE id=?", (sub_id,)).fetchone()
    if not row:
        conn.close()
        return None
    d = dict(row)
    findings = _exec(conn,
                     "SELECT * FROM contract_qa_findings WHERE submission_id=? ORDER BY id",
                     (sub_id,)).fetchall()
    conn.close()
    d["findings"] = [dict(f) for f in findings]
    return d


def get_all_submissions():
    """Get all Contract QA submissions (without findings for list view)."""
    conn = db.get_db()
    rows = _exec(conn,
                 "SELECT * FROM contract_qa_submissions ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tier1_rules():
    """Get all active Tier 1 Contract QA rules with their parameters."""
    conn = db.get_db()
    rows = _exec(conn,
                 """SELECT * FROM qa_rules WHERE domain='Contract' AND active=1
                    AND rule_ref LIKE 'QAR-%%' ORDER BY rule_ref""").fetchall()
    conn.close()
    rules = []
    for r in rows:
        d = dict(r)
        try:
            d["parameters_parsed"] = json.loads(d.get("parameters") or "{}")
        except Exception:
            d["parameters_parsed"] = {}
        rules.append(d)
    return rules


def get_all_rules_with_domain(domain=None, category=None, severity=None, active_only=False):
    """Get rules with optional domain/category/severity filtering."""
    conn = db.get_db()
    sql = "SELECT * FROM qa_rules"
    clauses = []
    params = []
    if active_only:
        clauses.append("active=1")
    if domain:
        clauses.append("domain=?")
        params.append(domain)
    if category:
        clauses.append("category=?")
        params.append(category)
    if severity:
        clauses.append("severity=?")
        params.append(severity)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY domain, category, rule_ref"
    rows = _exec(conn, sql, tuple(params)).fetchall()
    conn.close()
    rules = []
    for r in rows:
        d = dict(r)
        try:
            d["parameters_parsed"] = json.loads(d.get("parameters") or "{}")
        except Exception:
            d["parameters_parsed"] = {}
        rules.append(d)
    return rules
