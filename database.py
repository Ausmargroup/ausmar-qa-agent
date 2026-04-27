"""
SQLite database layer for AUSMAR PSE QA Agent.
Stores reviews, feedback, plans, pre-logged jobs, and review history.
"""

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "qa_agent.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            min_width REAL NOT NULL,
            min_length REAL NOT NULL,
            total_area REAL DEFAULT 0,
            width_incl_eaves REAL DEFAULT 0,
            house_width REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_code TEXT,
            zip_name TEXT NOT NULL,
            deposit_type TEXT DEFAULT 'UNKNOWN',
            verdict TEXT,
            verdict_reason TEXT,
            critical_issues TEXT DEFAULT '[]',
            warnings TEXT DEFAULT '[]',
            heath_note TEXT DEFAULT '',
            consultant_email TEXT DEFAULT '',
            check_results TEXT DEFAULT '{}',
            files_in_zip TEXT DEFAULT '[]',
            corrections_applied TEXT DEFAULT '[]',
            corrected_zip_path TEXT DEFAULT '',
            consultant_name TEXT DEFAULT '',
            prelog_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (prelog_id) REFERENCES prelogs(id)
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id INTEGER NOT NULL,
            check_name TEXT NOT NULL,
            issue_text TEXT NOT NULL,
            is_correct INTEGER DEFAULT 1,
            notes TEXT DEFAULT '',
            submitted_by TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (review_id) REFERENCES reviews(id)
        );

        CREATE TABLE IF NOT EXISTS prelogs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_code TEXT NOT NULL,
            consultant_name TEXT DEFAULT '',
            deposit_amount REAL DEFAULT 0,
            customer_names TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            files TEXT DEFAULT '[]',
            status TEXT DEFAULT 'pending',
            matched_review_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # Seed default plans if empty
    existing = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    if existing == 0:
        conn.executemany(
            "INSERT INTO plans (name, min_width, min_length, total_area, width_incl_eaves, house_width) VALUES (?,?,?,?,?,?)",
            [
                ("Clearwater 225", 12.3, 29.1, 225.95, 11.98, 0),
                ("Clearwater 245", 13.0, 29.2, 245.51, 12.60, 0),
                ("Narrabeen", 10.0, 25.0, 212.33, 0, 9.24),
            ],
        )
    conn.commit()
    conn.close()


# --- Plans ---
def get_all_plans():
    conn = get_db()
    rows = conn.execute("SELECT * FROM plans ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_plan(name, min_width, min_length, total_area=0, width_incl_eaves=0, house_width=0):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO plans (name, min_width, min_length, total_area, width_incl_eaves, house_width, updated_at) VALUES (?,?,?,?,?,?,datetime('now'))",
        (name, min_width, min_length, total_area, width_incl_eaves, house_width),
    )
    conn.commit()
    conn.close()


def delete_plan(plan_id):
    conn = get_db()
    conn.execute("DELETE FROM plans WHERE id=?", (plan_id,))
    conn.commit()
    conn.close()


# --- Reviews ---
def save_review(data: dict) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO reviews (deal_code, zip_name, deposit_type, verdict, verdict_reason,
           critical_issues, warnings, heath_note, consultant_email, check_results,
           files_in_zip, corrections_applied, corrected_zip_path, consultant_name, prelog_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data.get("deal_code", ""),
            data.get("zip_name", ""),
            data.get("deposit_type", "UNKNOWN"),
            data.get("verdict", ""),
            data.get("verdict_reason", ""),
            json.dumps(data.get("critical_issues", [])),
            json.dumps(data.get("warnings", [])),
            data.get("heath_note", ""),
            data.get("consultant_email", ""),
            json.dumps(data.get("check_results", {}), default=str),
            json.dumps(data.get("files_in_zip", [])),
            json.dumps(data.get("corrections_applied", [])),
            data.get("corrected_zip_path", ""),
            data.get("consultant_name", ""),
            data.get("prelog_id"),
        ),
    )
    review_id = cur.lastrowid
    conn.commit()
    conn.close()
    return review_id


def get_all_reviews():
    conn = get_db()
    rows = conn.execute("SELECT * FROM reviews ORDER BY created_at DESC").fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        for k in ("critical_issues", "warnings", "files_in_zip", "corrections_applied"):
            try:
                d[k] = json.loads(d[k]) if d[k] else []
            except:
                d[k] = []
        try:
            d["check_results"] = json.loads(d["check_results"]) if d["check_results"] else {}
        except:
            d["check_results"] = {}
        results.append(d)
    return results


def get_review(review_id: int):
    conn = get_db()
    r = conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r)
    for k in ("critical_issues", "warnings", "files_in_zip", "corrections_applied"):
        try:
            d[k] = json.loads(d[k]) if d[k] else []
        except:
            d[k] = []
    try:
        d["check_results"] = json.loads(d["check_results"]) if d["check_results"] else {}
    except:
        d["check_results"] = {}
    return d


# --- Feedback ---
def save_feedback(review_id, check_name, issue_text, is_correct, notes="", submitted_by=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO feedback (review_id, check_name, issue_text, is_correct, notes, submitted_by) VALUES (?,?,?,?,?,?)",
        (review_id, check_name, issue_text, is_correct, notes, submitted_by),
    )
    conn.commit()
    conn.close()


def get_feedback_for_review(review_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM feedback WHERE review_id=? ORDER BY created_at DESC", (review_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_feedback():
    conn = get_db()
    rows = conn.execute("SELECT * FROM feedback ORDER BY created_at DESC LIMIT 200").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_false_positives():
    """Get all feedback marked as incorrect (false positives) for learning."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM feedback WHERE is_correct=0 ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Pre-logs ---
def save_prelog(data: dict) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO prelogs (deal_code, consultant_name, deposit_amount, customer_names, notes, files, status) VALUES (?,?,?,?,?,?,?)",
        (
            data.get("deal_code", ""),
            data.get("consultant_name", ""),
            data.get("deposit_amount", 0),
            data.get("customer_names", ""),
            data.get("notes", ""),
            json.dumps(data.get("files", [])),
            "pending",
        ),
    )
    prelog_id = cur.lastrowid
    conn.commit()
    conn.close()
    return prelog_id


def get_all_prelogs():
    conn = get_db()
    rows = conn.execute("SELECT * FROM prelogs ORDER BY created_at DESC").fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["files"] = json.loads(d["files"]) if d["files"] else []
        except:
            d["files"] = []
        results.append(d)
    return results


def get_prelog(prelog_id):
    conn = get_db()
    r = conn.execute("SELECT * FROM prelogs WHERE id=?", (prelog_id,)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r)
    try:
        d["files"] = json.loads(d["files"]) if d["files"] else []
    except:
        d["files"] = []
    return d


def find_prelog_by_deal_code(deal_code):
    conn = get_db()
    r = conn.execute("SELECT * FROM prelogs WHERE deal_code=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (deal_code,)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r)
    try:
        d["files"] = json.loads(d["files"]) if d["files"] else []
    except:
        d["files"] = []
    return d


def mark_prelog_matched(prelog_id, review_id):
    conn = get_db()
    conn.execute("UPDATE prelogs SET status='matched', matched_review_id=?, updated_at=datetime('now') WHERE id=?", (review_id, prelog_id))
    conn.commit()
    conn.close()


# --- Stats ---
def get_review_stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    accepted = conn.execute("SELECT COUNT(*) FROM reviews WHERE verdict LIKE '%ACCEPTED%' AND verdict NOT LIKE '%NOT%' AND verdict NOT LIKE '%CONCERN%'").fetchone()[0]
    not_accepted = conn.execute("SELECT COUNT(*) FROM reviews WHERE verdict LIKE '%NOT ACCEPTED%'").fetchone()[0]
    concerns = conn.execute("SELECT COUNT(*) FROM reviews WHERE verdict LIKE '%CONCERN%'").fetchone()[0]
    parked = conn.execute("SELECT COUNT(*) FROM reviews WHERE verdict LIKE '%PARKED%'").fetchone()[0]
    conn.close()
    return {
        "total": total,
        "accepted": accepted,
        "not_accepted": not_accepted,
        "concerns": concerns,
        "parked": parked,
    }
