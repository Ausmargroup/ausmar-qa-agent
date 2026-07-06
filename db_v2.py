"""
AUSMAR QA Agent V2 — database extensions.

This module ADDS the Stage 2 / Stage 3 / rule-library tables and helpers WITHOUT
touching the existing dual-backend logic in database.py. It reuses database.get_db()
and the same Postgres-vs-SQLite detection, so it works in both environments.

Design notes:
- All DDL uses `CREATE TABLE IF NOT EXISTS` so it is safe to run on every boot.
- Seeding uses idempotent inserts so an existing production DB upgrades cleanly
  and existing rows are never modified.
- Nothing here imports the Stage 1 reviews/feedback/prelogs tables — those are
  left exactly as they are.
"""

import json
import os

import database as db

_IS_PG = bool(os.environ.get("DATABASE_URL", ""))


# ---------------------------------------------------------------------------
# Low-level exec that works on both backends
# ---------------------------------------------------------------------------
def _q(sql):
    """Translate '?' placeholders to '%s' when running on Postgres."""
    return sql.replace("?", "%s") if _IS_PG else sql


def _exec(conn, sql, params=()):
    if _IS_PG:
        # database._exec handles the literal-% escaping; reuse it via a cursor.
        cur = conn.cursor()
        pg_sql = sql.replace("?", "%s")
        _SENTINEL = "\x00PARAM\x00"
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
# Schema
# ---------------------------------------------------------------------------
def init_v2():
    """Create V2 tables (idempotent) and seed the rule library once."""
    conn = db.get_db()
    ts = _now_default()
    pk = _serial_pk()

    statements = [
        f"""CREATE TABLE IF NOT EXISTS qa_rules (
            id {pk},
            rule_ref TEXT DEFAULT '',
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            severity TEXT DEFAULT 'Medium',
            active INTEGER DEFAULT 1,
            stage_applicability TEXT DEFAULT 'Stage 3',
            source TEXT DEFAULT 'seed',
            created_at {ts},
            updated_at {ts}
        )""",
        f"""CREATE TABLE IF NOT EXISTS rule_exclusions (
            id {pk},
            rule_id INTEGER NOT NULL,
            exclusion_text TEXT NOT NULL,
            created_by TEXT DEFAULT '',
            created_at {ts}
        )""",
        f"""CREATE TABLE IF NOT EXISTS rule_history (
            id {pk},
            rule_id INTEGER,
            action TEXT NOT NULL,
            details TEXT DEFAULT '',
            changed_by TEXT DEFAULT '',
            created_at {ts}
        )""",
        f"""CREATE TABLE IF NOT EXISTS contract_reviews (
            id {pk},
            deal_code TEXT DEFAULT '',
            stage INTEGER DEFAULT 3,
            consultant_name TEXT DEFAULT '',
            job_category TEXT DEFAULT '',
            status TEXT DEFAULT 'completed',
            verdict TEXT DEFAULT '',
            verdict_reason TEXT DEFAULT '',
            result_payload TEXT DEFAULT '{{}}',
            created_at {ts}
        )""",
        f"""CREATE TABLE IF NOT EXISTS contract_issues (
            id {pk},
            contract_review_id INTEGER NOT NULL,
            deal_code TEXT DEFAULT '',
            issue_ref TEXT DEFAULT '',
            severity TEXT DEFAULT '',
            category TEXT DEFAULT '',
            section TEXT DEFAULT '',
            signed_source TEXT DEFAULT '',
            contract_output TEXT DEFAULT '',
            discrepancy TEXT DEFAULT '',
            required_action TEXT DEFAULT '',
            status TEXT DEFAULT 'Open',
            status_note TEXT DEFAULT '',
            created_at {ts}
        )""",
    ]

    if _IS_PG:
        cur = conn.cursor()
        for s in statements:
            cur.execute(s)
        conn.commit()
    else:
        for s in statements:
            conn.execute(s)
        conn.commit()
    conn.close()

    _seed_rules()


# ---------------------------------------------------------------------------
# Seed rule library (idempotent — keyed on rule_ref)
# ---------------------------------------------------------------------------
# Severity defaults and stage applicability are taken from the contract QA spec
# (Pass 1 / Pass 2 tables) and extracted_rules.md. Admins can change everything
# from the UI afterwards; seeding only inserts rules that are not already present.
SEED_RULES = [
    # --- Stage 2: NHP Review (VO -> Final NHP reconciliation) ---
    ("S2-VO-001", "Pricing", "Every signed VO in the NHP Changes document must appear in the Final NHP price (matched, superseded, or explained).", "High", "Stage 2"),
    ("S2-VO-002", "Pricing", "Debit/credit amount for each VO must equal the amount in the Final NHP (allow documented adjustments only).", "High", "Stage 2"),
    ("S2-VO-003", "Pricing", "Flag any VO that appears in the changes document but is missing from the Final NHP pricing.", "Critical", "Stage 2"),
    ("S2-VO-004", "Pricing", "Final NHP grand total must reconcile to base price plus all accepted VO debits minus credits.", "High", "Stage 2"),
    ("S2-VO-005", "Pricing", "Where a cost basis is shown, verify sell = cost x 1.45475 (GST-inclusive AUSMAR gross-up).", "Medium", "Stage 2"),
    ("S2-VO-006", "Pricing", "Later signed VO overrides an earlier VO for the same item; flag earlier amount carried through in error.", "High", "Stage 2"),

    # --- Stage 3 Pass 1: automated document logic ---
    ("P1-VO-001", "Pricing", "Every signed VO must be matched to a contract output item or marked superseded by a later signed VO.", "High", "Stage 3"),
    ("P1-PRICE-002", "Pricing", "Contract debit/credit amount must equal the signed VO amount unless a documented adjustment exists.", "High", "Stage 3"),
    ("P1-SPEC-003", "Specification", "If a signed VO deletes an item, that item must not remain a live inclusion in the specification.", "High", "Stage 3"),
    ("P1-CONTRA-004", "Specification", "Pricing, specification, and drawings must not contradict each other on the same item.", "High", "Stage 3"),
    ("P1-BASE-005", "Specification", "Base PSE inclusions must not be removed unless there is a signed deletion or substitution.", "High", "Stage 3"),
    ("P1-SUPER-006", "Pricing", "Later signed VOs override earlier NHP items or previous changes.", "High", "Stage 3"),
    ("P1-META-007", "Specification", "Client names, address, lot, plan, facade, series, and deal code must match across all documents.", "High", "Stage 3"),
    ("P1-ELEC-008", "Electrical", "Electrical quantities in pricing must reconcile to the electrical plan and signed selections.", "High", "Stage 3"),
    ("P1-CREDIT-009", "Pricing", "Every signed credit must be traceable from deletion to the contract price schedule and final total.", "High", "Stage 3"),

    # --- Stage 3 Pass 2: drawing / construction-intent review ---
    ("P2-ELEV-001", "Drawings/Elevations", "Window and door heads/heights must match elevations and signed markup where design intent requires alignment.", "High", "Stage 3"),
    ("P2-FIX-002", "Wet Areas", "Mixers, lights, powerpoints, niches, shelves, and hooks must be placed where signed.", "High", "Stage 3"),
    ("P2-DEL-003", "Drawings/Elevations", "Items deleted by signed VO or red pen must be removed from plans, elevations, electrical plans, and notes.", "High", "Stage 3"),
    ("P2-CLEAR-004", "Joinery", "Laundry, kitchen, garage, and wet-area appliances must have practical clearance and opening space.", "Medium", "Stage 3"),
    ("P2-ELEC-005", "Electrical", "Electrical symbols must correctly distinguish pendant, wall light, LED, provision, installed, single GPO, and DGPO.", "High", "Stage 3"),
    ("P2-PROV-006", "Electrical", "Drawings and pricing must distinguish rough-in/provision-only from full supply and install.", "Medium", "Stage 3"),
    ("P2-NOTE-007", "Drawings/Elevations", "Required notes from signed changes must be visible on the relevant sheets and elevations.", "Medium", "Stage 3"),
    ("P2-DIM-008", "Drawings/Elevations", "Dimensions must reflect signed reductions, extensions, or deleted sections.", "High", "Stage 3"),
    ("P2-LED-009", "Electrical", "LED positions must align with signed electrical intent and practical room layout.", "Medium", "Stage 3"),

    # --- High-error focus areas flagged by AUSMAR (apply to both Stage 3 passes) ---
    ("AUS-FACADE-01", "External", "Item 7 Facade: confirm chosen facade exists for the design and any non-standard facade upgrade is listed in spec and priced.", "High", "Stage 3"),
    ("AUS-WINDOW-01", "Drawings/Elevations", "Item 8 Window modifications: every window add/delete/resize on red pen must appear in plan, elevation, schedule, and pricing.", "High", "Stage 3"),
    ("AUS-SQM-01", "Pricing", "All square-metre quantities (tiling, vinyl, carpet, concrete, render) must reconcile between drawings, spec, and pricing.", "High", "Stage 3"),
    ("AUS-ASPERPLAN-01", "Specification", "'As per plan' items must be verified against the drawing; flag where elaboration is needed (e.g. floating shelf thickness not specified).", "Medium", "Stage 3"),
]


def _seed_rules():
    conn = db.get_db()
    try:
        # Fetch existing refs to avoid duplicate inserts
        if _IS_PG:
            cur = conn.cursor()
            cur.execute("SELECT rule_ref FROM qa_rules")
            existing = {r["rule_ref"] for r in cur.fetchall()}
        else:
            existing = {r["rule_ref"] for r in conn.execute("SELECT rule_ref FROM qa_rules").fetchall()}

        to_add = [r for r in SEED_RULES if r[0] not in existing]
        for ref, cat, desc, sev, stage in to_add:
            _exec(conn,
                  "INSERT INTO qa_rules (rule_ref, category, description, severity, active, stage_applicability, source) VALUES (?,?,?,?,1,?,'seed')",
                  (ref, cat, desc, sev, stage))
        conn.commit()
    except Exception as e:
        print(f"[WARN] _seed_rules failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rule library helpers
# ---------------------------------------------------------------------------
def get_rules(stage=None, active_only=False):
    conn = db.get_db()
    sql = "SELECT * FROM qa_rules"
    clauses = []
    params = []
    if active_only:
        clauses.append("active=1")
    if stage:
        if stage == 'Contract':
            # V1: filter by domain column for Contract QA rules
            clauses.append("domain='Contract'")
        else:
            clauses.append("(stage_applicability=? OR stage_applicability='Both')")
            params.append(stage)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY category, rule_ref"
    rows = _exec(conn, sql, tuple(params)).fetchall()
    conn.close()
    rules = [dict(r) for r in rows]
    # attach exclusions
    for r in rules:
        r["exclusions"] = get_exclusions(r["id"])
    return rules


def get_rule(rule_id):
    conn = db.get_db()
    r = _exec(conn, "SELECT * FROM qa_rules WHERE id=?", (rule_id,)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r)
    d["exclusions"] = get_exclusions(rule_id)
    return d


def add_rule(category, description, severity="Medium", stage_applicability="Stage 3", changed_by="", rule_ref=""):
    conn = db.get_db()
    cur = _exec(conn,
                "INSERT INTO qa_rules (rule_ref, category, description, severity, active, stage_applicability, source) VALUES (?,?,?,?,1,?,'user')"
                + (" RETURNING id" if _IS_PG else ""),
                (rule_ref, category, description, severity, stage_applicability))
    new_id = cur.fetchone()["id"] if _IS_PG else cur.lastrowid
    conn.commit(); conn.close()
    _log_history(new_id, "Created",
                 {"category": category, "description": description, "severity": severity, "stage": stage_applicability},
                 changed_by)
    return new_id


def update_rule(rule_id, fields, changed_by=""):
    """fields: dict possibly containing severity, active, category, description, stage_applicability."""
    allowed = {"severity", "active", "category", "description", "stage_applicability"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    conn = db.get_db()
    set_sql = ", ".join(f"{k}=?" for k in sets)
    set_sql += (", updated_at=NOW()" if _IS_PG else ", updated_at=datetime('now')")
    _exec(conn, f"UPDATE qa_rules SET {set_sql} WHERE id=?", tuple(sets.values()) + (rule_id,))
    conn.commit(); conn.close()
    _log_history(rule_id, "Updated", sets, changed_by)


def add_exclusion(rule_id, exclusion_text, created_by=""):
    conn = db.get_db()
    _exec(conn,
          "INSERT INTO rule_exclusions (rule_id, exclusion_text, created_by) VALUES (?,?,?)",
          (rule_id, exclusion_text, created_by))
    conn.commit(); conn.close()
    _log_history(rule_id, "Exclusion Added", {"exclusion_text": exclusion_text}, created_by)


def get_exclusions(rule_id):
    conn = db.get_db()
    rows = _exec(conn, "SELECT * FROM rule_exclusions WHERE rule_id=? ORDER BY created_at DESC", (rule_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _log_history(rule_id, action, details, changed_by=""):
    conn = db.get_db()
    try:
        _exec(conn,
              "INSERT INTO rule_history (rule_id, action, details, changed_by) VALUES (?,?,?,?)",
              (rule_id, action, json.dumps(details, default=str), changed_by))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def get_rule_history(rule_id):
    conn = db.get_db()
    rows = _exec(conn, "SELECT * FROM rule_history WHERE rule_id=? ORDER BY created_at DESC", (rule_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rules_prompt_block(stage):
    """Build the rules + exclusions text injected into the AI prompt for a stage."""
    rules = get_rules(stage=stage, active_only=True)
    if not rules:
        return ""
    lines = ["\n\nAUSMAR ACTIVE RULES (apply these; respect the exclusions):"]
    for r in rules:
        lines.append(f"- [{r.get('rule_ref') or r['id']}] ({r['severity']}) {r['category']}: {r['description']}")
        for ex in r.get("exclusions", []):
            lines.append(f"    EXCLUSION (do NOT flag): {ex['exclusion_text']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Contract review + issue helpers (Stage 2 & 3)
# ---------------------------------------------------------------------------
def save_contract_review(data):
    conn = db.get_db()
    cur = _exec(conn,
        """INSERT INTO contract_reviews
           (deal_code, stage, consultant_name, job_category, status, verdict, verdict_reason, result_payload)
           VALUES (?,?,?,?,?,?,?,?)""" + (" RETURNING id" if _IS_PG else ""),
        (data.get("deal_code", ""), data.get("stage", 3), data.get("consultant_name", ""),
         data.get("job_category", ""), data.get("status", "completed"),
         data.get("verdict", ""), data.get("verdict_reason", ""),
         json.dumps(data.get("result_payload", {}), default=str)))
    review_id = cur.fetchone()["id"] if _IS_PG else cur.lastrowid
    conn.commit(); conn.close()

    # Persist each issue as a row for status tracking / learning.
    # Backfill the saved row id onto the in-memory issue dict so the result
    # payload (and the live results screen) can drive per-issue status buttons.
    deal_code = data.get("deal_code", "")
    for iss in data.get("issues", []):
        iid = save_contract_issue(review_id, iss, deal_code)
        iss["id"] = iid
    return review_id


def save_contract_issue(contract_review_id, iss, deal_code=""):
    conn = db.get_db()
    cur = _exec(conn,
        """INSERT INTO contract_issues
           (contract_review_id, deal_code, issue_ref, severity, category, section, signed_source,
            contract_output, discrepancy, required_action, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""" + (" RETURNING id" if _IS_PG else ""),
        (contract_review_id, deal_code, iss.get("issue_ref", ""), iss.get("severity", ""),
         iss.get("category", ""), iss.get("section", ""), iss.get("signed_source", ""),
         iss.get("contract_output", ""), iss.get("discrepancy", ""),
         iss.get("required_action", ""), iss.get("status", "Open")))
    issue_id = cur.fetchone()["id"] if _IS_PG else cur.lastrowid
    conn.commit(); conn.close()
    return issue_id


def _parse_contract_review(d):
    try:
        d["result_payload"] = json.loads(d["result_payload"]) if d["result_payload"] else {}
    except Exception:
        d["result_payload"] = {}
    return d


def get_contract_reviews(stage=None):
    conn = db.get_db()
    if stage:
        rows = _exec(conn, "SELECT * FROM contract_reviews WHERE stage=? ORDER BY created_at DESC", (stage,)).fetchall()
    else:
        rows = _exec(conn, "SELECT * FROM contract_reviews ORDER BY created_at DESC").fetchall()
    conn.close()
    return [_parse_contract_review(dict(r)) for r in rows]


def get_contract_review(review_id):
    conn = db.get_db()
    r = _exec(conn, "SELECT * FROM contract_reviews WHERE id=?", (review_id,)).fetchone()
    if not r:
        conn.close()
        return None
    d = _parse_contract_review(dict(r))
    rows = _exec(conn, "SELECT * FROM contract_issues WHERE contract_review_id=? ORDER BY id", (review_id,)).fetchall()
    conn.close()
    d["issue_rows"] = [dict(x) for x in rows]
    return d


def update_issue_status(issue_id, status, note=""):
    conn = db.get_db()
    _exec(conn, "UPDATE contract_issues SET status=?, status_note=? WHERE id=?", (status, note, issue_id))
    conn.commit(); conn.close()


def get_recent_false_positive_issues(days=14):
    """Issues staff marked as False Positive or Not Applicable in the last N days,
    for the Learning panel. Both are signals that a rule may need an exclusion or
    a severity change."""
    conn = db.get_db()
    if _IS_PG:
        sql = ("SELECT * FROM contract_issues WHERE status IN ('False Positive','Not Applicable') "
               "AND created_at >= NOW() - INTERVAL '1 day' * %s ORDER BY created_at DESC")
        rows = _exec(conn, sql, (days,)).fetchall()
    else:
        sql = ("SELECT * FROM contract_issues WHERE status IN ('False Positive','Not Applicable') "
               "AND created_at >= datetime('now', ?) ORDER BY created_at DESC")
        rows = _exec(conn, sql, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
