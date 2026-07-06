"""
AUSMAR QA Agent — V1 QA Intelligence Database Migrations.

Non-destructive, additive-only migrations:
1. ALTER qa_rules: add domain, parameters, version columns
2. CREATE feature_flags table
3. CREATE rule_results table
4. Default existing rules' domain to 'NHP' (Stage 2 rules) or 'NHP' (Stage 3 rules)
5. Seed the contract_qa feature flag (disabled by default)

All migrations are idempotent — safe to run on every boot.
"""

import json
import os

import database as db

_IS_PG = bool(os.environ.get("DATABASE_URL", ""))


def _exec(conn, sql, params=()):
    """Execute on either backend."""
    if _IS_PG:
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


def _add_column_if_not_exists(conn, table, column, col_type, default):
    """Safely add a column — idempotent on both backends."""
    if _IS_PG:
        try:
            cur = conn.cursor()
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type} DEFAULT {default}")
            conn.commit()
        except Exception:
            conn.rollback()
    else:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type} DEFAULT {default}")
            conn.commit()
        except Exception:
            # Column already exists
            pass


# ---------------------------------------------------------------------------
# Main migration entry point
# ---------------------------------------------------------------------------
def run_v1_migrations():
    """Run all V1 QA Intelligence migrations. Idempotent — safe on every boot."""
    conn = db.get_db()

    # 1. Add 'domain' column to qa_rules (PSE / NHP / Contract)
    _add_column_if_not_exists(conn, "qa_rules", "domain", "TEXT", "'NHP'")

    # 2. Add 'parameters' JSONB column to qa_rules
    if _IS_PG:
        _add_column_if_not_exists(conn, "qa_rules", "parameters", "JSONB", "'{}'::jsonb")
    else:
        _add_column_if_not_exists(conn, "qa_rules", "parameters", "TEXT", "'{}'")

    # 3. Add 'version' column to qa_rules
    _add_column_if_not_exists(conn, "qa_rules", "version", "INTEGER", "1")

    conn.close()

    # 4. Create feature_flags table
    _create_feature_flags_table()

    # 5. Create rule_results table
    _create_rule_results_table()

    # 6. Set domain based on existing stage_applicability
    _backfill_domains()

    # 7. Seed the contract_qa feature flag
    _seed_feature_flags()


def _create_feature_flags_table():
    conn = db.get_db()
    ts = _now_default()
    pk = _serial_pk()

    sql = f"""CREATE TABLE IF NOT EXISTS feature_flags (
        id {pk},
        flag_name TEXT NOT NULL UNIQUE,
        enabled INTEGER DEFAULT 0,
        allowed_roles TEXT DEFAULT '[]',
        description TEXT DEFAULT '',
        created_at {ts},
        updated_at {ts}
    )"""

    if _IS_PG:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
    else:
        conn.execute(sql)
        conn.commit()
    conn.close()


def _create_rule_results_table():
    conn = db.get_db()
    ts = _now_default()
    pk = _serial_pk()

    sql = f"""CREATE TABLE IF NOT EXISTS rule_results (
        id {pk},
        job_id INTEGER NOT NULL,
        rule_id INTEGER NOT NULL,
        domain TEXT DEFAULT 'Contract',
        result TEXT DEFAULT 'PENDING',
        severity TEXT DEFAULT '',
        evidence_found TEXT DEFAULT '',
        evidence_expected TEXT DEFAULT '',
        recommendation TEXT DEFAULT '',
        confidence REAL DEFAULT 0.0,
        execution_time_ms INTEGER DEFAULT 0,
        created_at {ts}
    )"""

    if _IS_PG:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
    else:
        conn.execute(sql)
        conn.commit()
    conn.close()


def _backfill_domains():
    """Set domain based on stage_applicability for existing rules.
    Stage 2 rules -> domain='NHP', Stage 3 rules -> domain='NHP' (pre-contract).
    New Contract QA rules will be inserted with domain='Contract'."""
    conn = db.get_db()
    try:
        # Only update rules that still have the default domain='NHP' and haven't been manually set
        # Stage 2 rules stay as NHP, Stage 3 rules stay as NHP (they're pre-contract checks)
        # This is a no-op since default is already 'NHP', but explicit for clarity
        _exec(conn, "UPDATE qa_rules SET domain='NHP' WHERE domain='NHP' AND stage_applicability IN ('Stage 2', 'Stage 3', 'Both')")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def _seed_feature_flags():
    """Seed the contract_qa feature flag if not present."""
    conn = db.get_db()
    try:
        if _IS_PG:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM feature_flags WHERE flag_name='contract_qa'")
            exists = cur.fetchone()
        else:
            exists = conn.execute("SELECT 1 FROM feature_flags WHERE flag_name='contract_qa'").fetchone()

        if not exists:
            allowed = json.dumps(["admin", "manager_heath", "manager_lyana"])
            _exec(conn,
                  "INSERT INTO feature_flags (flag_name, enabled, allowed_roles, description) VALUES (?, ?, ?, ?)",
                  ("contract_qa", 0, allowed, "Contract QA V1 — Tier 1 rule-based quality checks on contract documents"))
            conn.commit()
    except Exception as e:
        print(f"[WARN] _seed_feature_flags: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Feature flag helpers
# ---------------------------------------------------------------------------
def get_feature_flag(flag_name):
    """Return the feature flag row as a dict, or None."""
    conn = db.get_db()
    row = _exec(conn, "SELECT * FROM feature_flags WHERE flag_name=?", (flag_name,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["allowed_roles"] = json.loads(d["allowed_roles"]) if d["allowed_roles"] else []
    except (json.JSONDecodeError, TypeError):
        d["allowed_roles"] = []
    return d


def is_feature_enabled(flag_name, user_role=None):
    """Check if a feature flag is enabled and (optionally) if the user's role is allowed."""
    flag = get_feature_flag(flag_name)
    if not flag or not flag.get("enabled"):
        return False
    if user_role:
        allowed = flag.get("allowed_roles", [])
        if allowed and user_role not in allowed:
            return False
    return True


def get_all_feature_flags():
    """Return all feature flags."""
    conn = db.get_db()
    rows = _exec(conn, "SELECT * FROM feature_flags ORDER BY flag_name").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["allowed_roles"] = json.loads(d["allowed_roles"]) if d["allowed_roles"] else []
        except (json.JSONDecodeError, TypeError):
            d["allowed_roles"] = []
        result.append(d)
    return result


def update_feature_flag(flag_name, enabled=None, allowed_roles=None):
    """Update a feature flag's enabled state and/or allowed roles."""
    conn = db.get_db()
    sets = []
    params = []
    if enabled is not None:
        sets.append("enabled=?")
        params.append(1 if enabled else 0)
    if allowed_roles is not None:
        sets.append("allowed_roles=?")
        params.append(json.dumps(allowed_roles))
    if _IS_PG:
        sets.append("updated_at=NOW()")
    else:
        sets.append("updated_at=datetime('now')")
    params.append(flag_name)
    _exec(conn, f"UPDATE feature_flags SET {', '.join(sets)} WHERE flag_name=?", tuple(params))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Rule results helpers
# ---------------------------------------------------------------------------
def save_rule_result(job_id, rule_id, domain, result, severity="", evidence_found="",
                     evidence_expected="", recommendation="", confidence=0.0, execution_time_ms=0):
    """Save a single rule execution result."""
    conn = db.get_db()
    _exec(conn,
          """INSERT INTO rule_results
             (job_id, rule_id, domain, result, severity, evidence_found, evidence_expected,
              recommendation, confidence, execution_time_ms)
             VALUES (?,?,?,?,?,?,?,?,?,?)""",
          (job_id, rule_id, domain, result, severity, evidence_found,
           evidence_expected, recommendation, confidence, execution_time_ms))
    conn.commit()
    conn.close()


def get_rule_results_for_job(job_id):
    """Get all rule results for a given job."""
    conn = db.get_db()
    rows = _exec(conn, "SELECT * FROM rule_results WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_qa_score(job_id):
    """Calculate QA score (% passed) for a job."""
    results = get_rule_results_for_job(job_id)
    if not results:
        return {"score": 0, "total": 0, "passed": 0, "failed": 0, "warnings": 0}
    total = len(results)
    passed = sum(1 for r in results if r["result"] == "PASS")
    failed = sum(1 for r in results if r["result"] == "FAIL")
    warnings = sum(1 for r in results if r["result"] == "WARNING")
    score = round((passed / total) * 100, 1) if total > 0 else 0
    return {"score": score, "total": total, "passed": passed, "failed": failed, "warnings": warnings}
