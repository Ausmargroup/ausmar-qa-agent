"""
AUSMAR QA Agent — User / Role layer.

Additive module (same pattern as db_v2.py): it ADDS a `users` table and role-aware
login/management helpers WITHOUT modifying the existing dual-backend logic in
database.py. It reuses database.get_db() and the same Postgres-vs-SQLite detection,
so it works in both environments.

Roles
-----
- admin           : sees everything, manages users, rules, plans, learning.
- manager_heath   : consultant view + Rules + Learning + Heath Dashboard + Plans.
- manager_lyana   : consultant view + Rules + Learning (no Plans).
- consultant      : Stage 1/2/3 submissions, own History, Help only.

A login uses a short code (same UX as the old access codes). Each seeded user has a
fixed code so existing people can log straight in; new users get an auto code.
"""

import os
import random
import string

import database as db

_IS_PG = bool(os.environ.get("DATABASE_URL", ""))

VALID_ROLES = ("admin", "manager_heath", "manager_lyana", "consultant")

# The 8 sales consultants (full names used in the dropdown). Order matters for the UI.
CONSULTANTS = [
    "Telford Louez",
    "Caitlyn Kent-Brown",
    "Ian Sullivan",
    "Johan Lundkvist",
    "Rod Kennerson",
    "Andrew Shand",
    "Kahl Meisenhelter",
    "Nadia Nemesagu",
]

# Seeded accounts: (code, full_name, role, email)
SEED_USERS = [
    # Admins — see everything
    ("ADMIN1", "Ben Carter",      "admin",          ""),
    ("ADMIN2", "Josh Green",      "admin",          ""),
    ("ADMIN3", "Nikole Cassin",   "admin",          "nik@ausmar.com.au"),
    # Managers
    ("HEATH1", "Heath Nunn",      "manager_heath",  ""),
    ("LYANA1", "Lyana Rossow",    "manager_lyana",  ""),
    # Consultants — login codes (fixed so they can be handed out once)
    ("TELFOR", "Telford Louez",       "consultant", ""),
    ("CAITLY", "Caitlyn Kent-Brown",  "consultant", ""),
    ("IANSUL", "Ian Sullivan",        "consultant", ""),
    ("JOHANL", "Johan Lundkvist",     "consultant", ""),
    ("RODKEN", "Rod Kennerson",       "consultant", ""),
    ("ANDREW", "Andrew Shand",        "consultant", ""),
    ("KAHLME", "Kahl Meisenhelter",   "consultant", ""),
    ("NADIAN", "Nadia Nemesagu",      "consultant", ""),
]


def _exec(conn, sql, params=()):
    """Run a query on either backend, translating '?' placeholders for Postgres
    and escaping literal '%' the same way database._exec does."""
    if _IS_PG:
        cur = conn.cursor()
        pg_sql = sql.replace("?", "%s")
        _SENTINEL = "\x00PARAM\x00"
        pg_sql = pg_sql.replace("%s", _SENTINEL).replace("%", "%%").replace(_SENTINEL, "%s")
        cur.execute(pg_sql, params)
        return cur
    return conn.execute(sql, params)


def init_users():
    """Create the users table (idempotent) and seed the named accounts once."""
    conn = db.get_db()
    if _IS_PG:
        ddl = """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'consultant',
                email TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """
        cur = conn.cursor()
        cur.execute(ddl)
        conn.commit()
    else:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'consultant',
                email TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
    conn.close()

    _seed_users()


def _seed_users():
    conn = db.get_db()
    try:
        for code, name, role, email in SEED_USERS:
            # Keyed on code so an existing production DB upgrades cleanly and
            # existing rows are never modified.
            if _IS_PG:
                _exec(conn,
                      "INSERT INTO users (code, full_name, role, email, active) VALUES (?,?,?,?,1) ON CONFLICT (code) DO NOTHING",
                      (code, name, role, email))
            else:
                _exec(conn,
                      "INSERT OR IGNORE INTO users (code, full_name, role, email, active) VALUES (?,?,?,?,1)",
                      (code, name, role, email))
        conn.commit()
    except Exception as e:
        print(f"[WARN] _seed_users failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def _row(r):
    return dict(r) if r else None


def get_user_by_code(code: str):
    """Return the active user matching a login code, or None."""
    if not code:
        return None
    conn = db.get_db()
    cur = _exec(conn, "SELECT * FROM users WHERE code=? AND active=1", (code.strip().upper(),))
    r = cur.fetchone()
    conn.close()
    return _row(r)


def get_all_users():
    conn = db.get_db()
    cur = _exec(conn, "SELECT * FROM users ORDER BY role, full_name")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _generate_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def add_user(full_name: str, role: str, email: str = "", code: str = "") -> dict:
    """Create a new user. Auto-generates a unique login code if none is given.
    Returns the created user dict. Raises ValueError on bad input."""
    full_name = (full_name or "").strip()
    role = (role or "consultant").strip()
    if not full_name:
        raise ValueError("full_name required")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}")

    conn = db.get_db()
    # Resolve a unique code
    code = (code or "").strip().upper()
    if not code:
        code = _generate_code()
    while _exec(conn, "SELECT 1 FROM users WHERE code=?", (code,)).fetchone():
        code = _generate_code()

    _exec(conn, "INSERT INTO users (code, full_name, role, email, active) VALUES (?,?,?,?,1)",
          (code, full_name, role, email))
    conn.commit()
    cur = _exec(conn, "SELECT * FROM users WHERE code=?", (code,))
    r = cur.fetchone()
    conn.close()
    return dict(r)


def update_user_role(user_id: int, role: str):
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}")
    conn = db.get_db()
    _exec(conn, "UPDATE users SET role=? WHERE id=?", (role, user_id))
    conn.commit(); conn.close()


def set_user_active(user_id: int, active: int):
    conn = db.get_db()
    _exec(conn, "UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))
    conn.commit(); conn.close()


# ---------------------------------------------------------------------------
# Permission helpers (single source of truth for the frontend + routes)
# ---------------------------------------------------------------------------
def permissions_for(role: str) -> dict:
    """Return the set of UI capabilities for a role. The frontend uses these to
    show/hide nav; routes can use them to authorise sensitive actions."""
    role = role or "consultant"
    base = {
        "stage1": False, "stage2": False, "stage3": False,
        "history": False, "history_own_only": False,
        "reports": False, "heath_dashboard": False, "prelog": False,
        "plans": False, "rules": False, "learning": False,
        "admin": False, "help": True,
    }
    if role == "admin":
        for k in base:
            base[k] = True
        base["history_own_only"] = False
    elif role == "manager_heath":
        base.update({
            "stage1": True, "stage2": True, "stage3": True,
            "history": True, "reports": True, "prelog": True,
            "heath_dashboard": True, "plans": True,
            "rules": True, "learning": True,
        })
    elif role == "manager_lyana":
        base.update({
            "stage1": True, "stage2": True, "stage3": True,
            "history": True, "reports": True, "prelog": True,
            "rules": True, "learning": True,
        })
    else:  # consultant
        base.update({
            "stage1": True, "stage2": True, "stage3": True,
            "history": True, "history_own_only": True,
        })
    return base


def is_admin_code(code: str) -> bool:
    u = get_user_by_code(code)
    return bool(u and u.get("role") == "admin")
