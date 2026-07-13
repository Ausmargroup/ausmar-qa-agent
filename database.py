"""
Database layer for AUSMAR PSE QA Agent.
Uses Postgres (psycopg2) when DATABASE_URL is set (Railway production).
Falls back to SQLite for local development.
"""

import json
import os
from datetime import datetime

_DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── Backend selection ──────────────────────────────────────────────────────────
if _DATABASE_URL:
    # ── Postgres ──────────────────────────────────────────────────────────────
    import psycopg2
    import psycopg2.extras

    def get_db():
        """Return a new psycopg2 connection with RealDictCursor as default cursor."""
        # Railway sometimes provides postgres:// but psycopg2 requires postgresql://
        url = _DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn

    def _exec(conn, sql, params=()):
        """Execute a query on conn.

        Placeholder handling: queries here use either '?' (SQLite-style,
        translated to '%s') or '%s' directly. psycopg2 treats every '%' in
        the SQL string as a parameter marker, so literal '%' characters
        (e.g. inside "LIKE '%NOT ACCEPTED%'") must be escaped to '%%' — but
        WITHOUT escaping the real '%s' placeholders. We protect '%s' first,
        escape remaining bare '%', then restore the placeholders.
        """
        # 1) Translate SQLite '?' placeholders to psycopg2 '%s'
        pg_sql = sql.replace("?", "%s")
        # 2) Protect real '%s' placeholders
        _SENTINEL = "\x00PARAM\x00"
        pg_sql = pg_sql.replace("%s", _SENTINEL)
        # 3) Escape any remaining literal '%' (e.g. LIKE patterns) to '%%'
        pg_sql = pg_sql.replace("%", "%%")
        # 4) Restore the placeholders
        pg_sql = pg_sql.replace(_SENTINEL, "%s")
        cur = conn.cursor()
        cur.execute(pg_sql, params)
        return cur

    def init_db():
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                min_width REAL NOT NULL,
                min_length REAL NOT NULL,
                total_area REAL DEFAULT 0,
                width_incl_eaves REAL DEFAULT 0,
                house_width REAL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS prelogs (
                id SERIAL PRIMARY KEY,
                deal_code TEXT NOT NULL,
                consultant_name TEXT DEFAULT '',
                deposit_amount REAL DEFAULT 0,
                customer_names TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                files TEXT DEFAULT '[]',
                file_paths TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending',
                matched_review_id INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id SERIAL PRIMARY KEY,
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
                prelog_id INTEGER REFERENCES prelogs(id),
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                review_id INTEGER NOT NULL REFERENCES reviews(id),
                check_name TEXT NOT NULL,
                issue_text TEXT NOT NULL,
                is_correct INTEGER DEFAULT 1,
                notes TEXT DEFAULT '',
                submitted_by TEXT DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS pending_reviews (
                id TEXT PRIMARY KEY,
                zip_name TEXT NOT NULL,
                status TEXT DEFAULT 'processing',
                progress INTEGER DEFAULT 0,
                progress_message TEXT DEFAULT 'Starting review...',
                result TEXT DEFAULT '',
                error TEXT DEFAULT '',
                review_id INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS access_codes (
                id SERIAL PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                consultant_name TEXT NOT NULL,
                email TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        # Migrate: add file_paths column to prelogs if not exists
        try:
            cur.execute("ALTER TABLE prelogs ADD COLUMN IF NOT EXISTS file_paths TEXT DEFAULT '[]'")
        except Exception:
            conn.rollback()

        # Migrate: add submitted_by column to reviews if not exists
        try:
            cur.execute("ALTER TABLE reviews ADD COLUMN IF NOT EXISTS submitted_by TEXT DEFAULT ''")
        except Exception:
            conn.rollback()

        # Seed plans — ON CONFLICT DO NOTHING
        plans = [
            ("Clearwater 225",          12.3,  29.1,  225.95, 11.98,  0.0),
            ("Clearwater 245",          13.0,  29.2,  245.51, 12.60,  0.0),
            ("Narrabeen",               10.0,  25.0,  212.33,  0.0,   9.24),
            ("Washington 285 Traditional", 12.5, 17.64, 285.1,  12.71, 11.81),
            ("Washington 315 Traditional", 12.5, 25.0,  315.6,  12.5,  11.0),
            ("Washington 340 Barn",        12.5, 25.0,  348.7,  12.5,  12.5),
            ("Washington 340 Coastal",     12.5, 25.0,  339.5,  11.54, 11.0),
            ("Washington 340 Hamptons",    12.5, 34.4,  339.5,  11.9,  11.0),
            ("Washington 340 Modern",      12.5, 25.0,  340.6,  11.0,  11.0),
            ("Washington 340 Palm Valley", 12.5, 25.0,  350.0,  11.8,  11.8),
            ("Washington 340 Traditional", 12.5, 25.0,  339.5,  11.0,  11.0),
            ("ASPEN 230", 12.5, 25.0, 224.5, 11.5, 11.5),
            ("ASPEN 281", 12.5, 25.0, 250.4, 11.5, 11.5),
            ("Aspen 255 Traditional Streetscape", 12.5, 25.0, 255.1, 11.5, 11.5),
            ("Aspen 281", 12.5, 25.0, 274.4, 11.5, 11.5),
            ("Aspen 281 Hamptons", 12.5, 25.0, 276.1, 11.5, 11.5),
            ("Aspen 281 Palm Valley Streetscape", 12.5, 25.0, 278.6, 11.5, 11.5),
            ("CLEARWATER 245 Barn Streetscape", 12.5, 20.0, 244.4, 12.6, 12.6),
            ("CLEARWATER 245 TRADITIONAL STREETSCAPE", 12.5, 20.0, 245.5, 12.6, 12.6),
            ("Clearwater 209 Traditional Streetscape", 12.3, 20.0, 204.4, 11.98, 11.98),
            ("Clearwater 225 Traditional Streetscape", 12.3, 20.142, 225.4, 11.5, 11.5),
            ("Clearwater 245 Coastal Streetscape", 15.0, 20.0, 247.1, 14.89, 14.89),
            ("Clearwater 245 Hamptons Streetscape", 10.0, 23.8, 244.4, 12.0, 11.5),
            ("Clearwater 245 Modern Streetscape", 10.0, 15.0, 246.1, 10.0, 10.0),
            ("Clearwater 245 Palm Valley Streetscape", 12.5, 30.0, 245.5, 10.0, 10.0),
            ("Island Bay 235 Traditional Streetscape", 14.0, 21.4, 254.1, 14.0, 14.0),
            ("Island Bay 250 Traditional Streetscape", 10.0, 28.0, 244.5, 10.0, 10.0),
            ("Island Bay 290", 12.5, 30.0, 291.4, 16.8, 16.8),
            ("Island Bay 290 Barn Streetscape", 12.5, 30.0, 240.5, 14.2, 14.2),
            ("Island Bay 290 Coastal Streetscape", 18.0, 30.0, 257.2, 18.4, 18.0),
            ("Island Bay 290 Modern Streetscape", 15.0, 23.0, 224.6, 13.0, 13.0),
            ("Island Bay 290 Palm Valley Streetscape", 12.5, 30.0, 290.8, 18.0, 18.0),
            ("Island Bay 290 Traditional Streetscape", 12.5, 28.0, 283.6, 11.5, 11.5),
            ("LONG BEACH 300 COASTAL STREETSCAPE", 8.0, 32.0, 302.9, 23.11, 23.11),
            ("Long Beach 260", 12.5, 20.0, 260.2, 11.7, 11.5),
            ("Long Beach 285 Traditional", 12.5, 30.0, 224.1, 22.06, 22.06),
            ("Long Beach 300 Modern Streetscape", 8.0, 23.25, 306.9, 12.0, 12.0),
            ("Miami 250 Traditional Streetscape", 12.5, 25.0, 250.8, 12.5, 12.5),
            ("Miami 272", 16.0, 21.0, 271.4, 16.0, 16.0),
            ("Miami 290 Barn Streetscape", 10.0, 21.71, 285.5, 15.9, 15.9),
            ("Miami 290 Hamptons Streetscape", 16.0, 19.0, 297.1, 17.21, 16.0),
            ("Miami 290 Modern Streetscape", 10.0, 19.0, 292.5, 16.0, 16.0),
            ("Miami 290 Palm Valley", 18.0, 14.77, 292.5, 22.1, 22.1),
            ("ORLANDO 300 TRADITIONAL STREETSCAPE", 12.5, 35.6, 296.6, 11.1, 11.1),
            ("ORLANDO 330 COASTAL STREETSCAPE", 12.5, 21.56, 330.2, 11.9, 11.5),
            ("ORLANDO 330 MODERN STREETSCAPE", 12.5, 23.0, 330.2, 11.5, 11.5),
            ("ORLANDO 330 TRADITIONAL STREETSCAPE", 12.5, 21.56, 330.2, 11.9, 11.9),
            ("Orlando 275 Traditional Streetscape", 10.5, 25.0, 275.0, 12.0, 10.5),
            ("Orlando 330 Barn", 12.5, 28.0, 330.2, 11.78, 11.5),
            ("Orlando 330 Hamptons Streetscape", 12.5, 28.0, 331.5, 11.9, 11.9),
            ("Orlando 330 Palm Valley Streetscape", 12.5, 29.0, 331.2, 10.3, 10.3),
            ("Palm Bay 174 Traditional Streetscape", 12.5, 19.2, 173.4, 11.1, 11.1),
            ("Pasadena 205", 12.5, 28.0, 205.4, 12.16, 11.7),
            ("Pasadena 220 Traditional Streetscape", 12.5, 28.0, 220.3, 12.5, 12.5),
            ("Pasadena 235 Barn", 14.0, 28.0, 257.6, 11.5, 11.5),
            ("Pasadena 235 Coastal Streetscape", 14.0, 28.0, 235.1, 11.8, 11.5),
            ("Pasadena 235 Hamptons Streetscape", 14.0, 22.44, 253.3, 11.99, 11.5),
            ("Pasadena 235 Modern Streetscape", 14.0, 28.0, 235.2, 14.0, 14.0),
            ("Pasadena 235 Traditional Streetscape", 14.0, 28.0, 255.2, 11.5, 11.5),
            ("Portland 175 Traditional Streetscape", 12.0, 25.0, 174.1, 14.5, 13.9),
            ("Portland 194", 12.5, 27.0, 165.2, 18.0, 17.53),
            ("Portland 204 Coastal Streetscape", 12.9, 11.5, 202.4, 12.0, 11.5),
            ("Portland 204 Hamptons Streetscape", 12.9, 21.94, 206.1, 11.73, 11.5),
            ("Portland 204 Traditional Streetscape", 12.9, 21.76, 205.5, 12.9, 11.5),
            ("Long Beach 300",  12.5, 32.0, 300.5, 12.5, 12.5),
            ("Miami 290",       12.0, 19.0, 290.4, 15.0, 15.0),
            ("Pasadena 235",    14.0, 21.5, 234.2, 14.0, 14.0),
            ("Portland 204",    12.9, 28.0, 201.8, 12.9, 12.9),
            ("Savannah 205", 12.5, 21.1, 205.2, 11.79, 11.5),
            ("Savannah 215", 12.5, 21.15, 215.1, 11.99, 11.5),
            ("Savannah 235 Coastal Streetscape", 12.0, 28.0, 254.1, 12.0, 11.5),
            ("Savannah 235 Hamptons Streetscape", 12.5, 28.0, 250.2, 11.99, 11.5),
            ("Malabar 153 Traditional", 10.0, 25.0, 153.0, 10.5, 10.0),
            ("Tampa Bay 185", 12.5, 25.0, 184.5, 12.5, 12.5),
            ("Tampa Bay 200", 12.5, 24.9, 200.6, 11.5, 11.5),
            ("Tampa Bay 200 Coastal Streetscape", 12.5, 27.82, 194.5, 11.1, 11.1),
            ("Tampa Bay 200 Hamptons Streetscape", 12.5, 28.0, 200.9, 11.1, 11.1),
            ("Tampa Bay 200 Modern Streetscape", 12.5, 28.0, 148.7, 11.7, 11.7),
            ("Tampa Bay 200 Palm Valley Streetscape", 12.5, 24.6, 199.5, 11.5, 11.5),
            ("Tampa Bay 200 Traditional Streetscape", 12.5, 27.2, 200.4, 11.1, 11.1),
            ("Tampa Bay 174 Traditional Streetscape", 12.5, 19.2, 173.4, 11.1, 11.1),
        ]
        for row in plans:
            cur.execute(
                """INSERT INTO plans (name, min_width, min_length, total_area, width_incl_eaves, house_width)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (name) DO NOTHING""",
                row,
            )

        # Seed access codes
        codes = [
            ("8E8QMG", "Andrew",      ""),
            ("QYN2DU", "Caitlyn",     ""),
            ("K7FNM6", "Heath",       ""),
            ("0G25Q7", "Ian",         ""),
            ("C71EEJ", "Johan",       ""),
            ("50LF6T", "Nik (Admin)", "nik@ausmar.com.au"),
            ("JB04JH", "Rod",         ""),
            ("9CLF1C", "Telford",     ""),
            ("NEM001", "Nadia",       ""),
        ]
        for code, name, email in codes:
            cur.execute(
                """INSERT INTO access_codes (code, consultant_name, email, active)
                   VALUES (%s,%s,%s,1)
                   ON CONFLICT (code) DO NOTHING""",
                (code, name, email),
            )

        # Seed historical pre-logs — keyed on deal_code + consultant_name
        may_prelogs = [
            ("S26ABJHM", "Telford",  '["ITP-Signed.pdf","DepositRemit.pdf","DriversLicence-JMandAB.jpg"]'),
            ("S26BGAR",  "Andrew",   '["IntentionToPurchaseandagreement.pdf","$2,500.00DepositfromEFTPOSTerminalatAURAClearwaterHouse.pdf","BibekGaireI.D..pdf","AmarRanaI.D..pdf"]'),
            ("S26DJJR",  "Ian",      '["IntentiontoPurchase.pdf","Receipt.jpg","DisclosurePlan.pdf","GeositePlan.pdf","DriversLicence.jpg","DriversLicence2.jpg"]'),
            ("S26JC",    "Ian",      '["IntentiontoPurchase.pdf","Receipt.webp","DriversLicence.webp"]'),
            ("S26BPS",   "Nadia",    '["INTENTIONTOPURCHASE.pdf","DepositReceipt.jpg","DriversLicence2.jpg","DriversLicence.jpg"]'),
            ("S26JJM",   "Andrew",   '["IntentionToPurchase.pdf","$2,500.00DirectDepositS26JJM.PNG","JoaquinI.D..PNG","JordenI.D..PNG"]'),
            ("S26NLSP",  "Rod",      '["INTENTION+TO+PURCHASE-STANDARD+HOMES(S26NLSP)signed.pdf","S26NLSPDLidentification.pdf","GeositeSTDMalabarS26NLSP.pdf","P15Stage54DisclosurePlan.pdf","BuildingEnvelopeandPODS26NLSPStage54Precinct15.pdf"]'),
        ]
        for deal_code, consultant, files_json in may_prelogs:
            cur.execute(
                "SELECT id FROM prelogs WHERE deal_code=%s AND consultant_name=%s",
                (deal_code, consultant)
            )
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO prelogs (deal_code, consultant_name, files, file_paths, status) VALUES (%s,%s,%s,'[]','pending')",
                    (deal_code, consultant, files_json)
                )

        conn.commit()
        conn.close()

    # ── Pending Reviews ────────────────────────────────────────────────────────
    def create_pending_review(pending_id: str, zip_name: str):
        conn = get_db()
        _exec(conn, "INSERT INTO pending_reviews (id, zip_name, status, progress, progress_message) VALUES (?,?,?,?,?)",
              (pending_id, zip_name, "processing", 0, "Starting review..."))
        conn.commit(); conn.close()

    def update_pending_progress(pending_id: str, progress: int, message: str):
        conn = get_db()
        _exec(conn, "UPDATE pending_reviews SET progress=?, progress_message=?, updated_at=NOW() WHERE id=?",
              (progress, message, pending_id))
        conn.commit(); conn.close()

    def complete_pending_review(pending_id: str, result_json: str, review_id: int):
        conn = get_db()
        _exec(conn, "UPDATE pending_reviews SET status='completed', progress=100, progress_message='Complete', result=?, review_id=?, updated_at=NOW() WHERE id=?",
              (result_json, review_id, pending_id))
        conn.commit(); conn.close()

    def fail_pending_review(pending_id: str, error: str):
        conn = get_db()
        _exec(conn, "UPDATE pending_reviews SET status='failed', progress_message=?, error=?, updated_at=NOW() WHERE id=?",
              (error, error, pending_id))
        conn.commit(); conn.close()

    def get_pending_review(pending_id: str):
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM pending_reviews WHERE id=?", (pending_id,))
        r = cur.fetchone()
        conn.close()
        return dict(r) if r else None

    def cleanup_old_pending(hours: int = 24):
        conn = get_db()
        _exec(conn, "DELETE FROM pending_reviews WHERE created_at < NOW() - (INTERVAL '1 hour' * %s)", (hours,))
        conn.commit(); conn.close()

    # ── Plans ──────────────────────────────────────────────────────────────────
    def get_all_plans():
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM plans ORDER BY name")
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_plan(name, min_width, min_length, total_area=0, width_incl_eaves=0, house_width=0):
        conn = get_db()
        _exec(conn,
              """INSERT INTO plans (name, min_width, min_length, total_area, width_incl_eaves, house_width, updated_at)
                 VALUES (?,?,?,?,?,?,NOW())
                 ON CONFLICT (name) DO UPDATE SET
                   min_width=EXCLUDED.min_width, min_length=EXCLUDED.min_length,
                   total_area=EXCLUDED.total_area, width_incl_eaves=EXCLUDED.width_incl_eaves,
                   house_width=EXCLUDED.house_width, updated_at=NOW()""",
              (name, min_width, min_length, total_area, width_incl_eaves, house_width))
        conn.commit(); conn.close()

    def delete_plan(plan_id):
        conn = get_db()
        _exec(conn, "DELETE FROM plans WHERE id=?", (plan_id,))
        conn.commit(); conn.close()

    # ── Reviews ────────────────────────────────────────────────────────────────
    def save_review(data: dict) -> int:
        conn = get_db()
        cur = _exec(conn,
            """INSERT INTO reviews (deal_code, zip_name, deposit_type, verdict, verdict_reason,
               critical_issues, warnings, heath_note, consultant_email, check_results,
               files_in_zip, corrections_applied, corrected_zip_path, consultant_name, prelog_id, submitted_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
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
                data.get("submitted_by", ""),
            ),
        )
        review_id = cur.fetchone()["id"]
        conn.commit(); conn.close()
        return review_id

    def _parse_review(d):
        for k in ("critical_issues", "warnings", "files_in_zip", "corrections_applied"):
            try:
                d[k] = json.loads(d[k]) if d[k] else []
            except Exception:
                d[k] = []
        try:
            d["check_results"] = json.loads(d["check_results"]) if d["check_results"] else {}
        except Exception:
            d["check_results"] = {}
        return d

    def get_all_reviews():
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM reviews ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        return [_parse_review(dict(r)) for r in rows]

    def get_review(review_id: int):
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM reviews WHERE id=?", (review_id,))
        r = cur.fetchone()
        conn.close()
        return _parse_review(dict(r)) if r else None

    # ── Feedback ───────────────────────────────────────────────────────────────
    def save_feedback(review_id, check_name, issue_text, is_correct, notes="", submitted_by=""):
        conn = get_db()
        _exec(conn,
              "INSERT INTO feedback (review_id, check_name, issue_text, is_correct, notes, submitted_by) VALUES (?,?,?,?,?,?)",
              (review_id, check_name, issue_text, is_correct, notes, submitted_by))
        conn.commit(); conn.close()

    def get_feedback_for_review(review_id):
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM feedback WHERE review_id=? ORDER BY created_at DESC", (review_id,))
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_feedback():
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM feedback ORDER BY created_at DESC LIMIT 200")
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_false_positives():
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM feedback WHERE is_correct=0 ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Pre-logs ───────────────────────────────────────────────────────────────
    def save_prelog(data: dict) -> int:
        conn = get_db()
        cur = _exec(conn,
            "INSERT INTO prelogs (deal_code, consultant_name, deposit_amount, customer_names, notes, files, file_paths, status) VALUES (?,?,?,?,?,?,?,?) RETURNING id",
            (
                data.get("deal_code", ""),
                data.get("consultant_name", ""),
                data.get("deposit_amount", 0),
                data.get("customer_names", ""),
                data.get("notes", ""),
                json.dumps(data.get("files", [])),
                json.dumps(data.get("file_paths", [])),
                "pending",
            ),
        )
        prelog_id = cur.fetchone()["id"]
        conn.commit(); conn.close()
        return prelog_id

    def _parse_prelog(d):
        try:
            d["files"] = json.loads(d["files"]) if d["files"] else []
        except Exception:
            d["files"] = []
        try:
            d["file_paths"] = json.loads(d.get("file_paths") or "[]")
        except Exception:
            d["file_paths"] = []
        return d

    def get_all_prelogs():
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM prelogs ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        return [_parse_prelog(dict(r)) for r in rows]

    def get_prelog(prelog_id):
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM prelogs WHERE id=?", (prelog_id,))
        r = cur.fetchone()
        conn.close()
        return _parse_prelog(dict(r)) if r else None

    def find_prelog_by_deal_code(deal_code):
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM prelogs WHERE deal_code=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (deal_code,))
        r = cur.fetchone()
        conn.close()
        return _parse_prelog(dict(r)) if r else None

    def mark_prelog_matched(prelog_id, review_id):
        conn = get_db()
        _exec(conn, "UPDATE prelogs SET status='matched', matched_review_id=?, updated_at=NOW() WHERE id=?", (review_id, prelog_id))
        conn.commit(); conn.close()

    # ── Stats ──────────────────────────────────────────────────────────────────
    def get_review_stats():
        conn = get_db()
        cur = _exec(conn, "SELECT COUNT(*) AS total FROM reviews")
        total = cur.fetchone()["total"]
        cur = _exec(conn, "SELECT COUNT(*) AS cnt FROM reviews WHERE verdict LIKE '%NOT ACCEPTED%'")
        not_accepted = cur.fetchone()["cnt"]
        conn.close()
        return {"total": total, "accepted": total - not_accepted, "not_accepted": not_accepted}

    def get_all_access_codes():
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM access_codes ORDER BY consultant_name")
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_access_code(code: str):
        conn = get_db()
        cur = _exec(conn, "SELECT * FROM access_codes WHERE code=? AND active=1", (code.upper(),))
        r = cur.fetchone()
        conn.close()
        return dict(r) if r else None

    def create_access_code(consultant_name: str, email: str = "") -> str:
        import random, string
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        conn = get_db()
        cur = _exec(conn, "SELECT 1 FROM access_codes WHERE code=?", (code,))
        while cur.fetchone():
            code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
            cur = _exec(conn, "SELECT 1 FROM access_codes WHERE code=?", (code,))
        _exec(conn, "INSERT INTO access_codes (code, consultant_name, email) VALUES (?,?,?)", (code, consultant_name, email))
        conn.commit(); conn.close()
        return code

    def deactivate_access_code(code_id: int):
        conn = get_db()
        _exec(conn, "UPDATE access_codes SET active=0 WHERE id=?", (code_id,))
        conn.commit(); conn.close()

    def get_staff_report():
        conn = get_db()
        cur = _exec(conn, """
            SELECT
                consultant_name,
                COUNT(*) AS total,
                SUM(CASE WHEN verdict NOT LIKE '%NOT ACCEPTED%' THEN 1 ELSE 0 END) AS accepted,
                SUM(CASE WHEN verdict LIKE '%NOT ACCEPTED%' THEN 1 ELSE 0 END) AS not_accepted,
                SUM(CASE WHEN deposit_type='NHP' THEN 1 ELSE 0 END) AS nhp,
                SUM(CASE WHEN deposit_type='STC' THEN 1 ELSE 0 END) AS stc
            FROM reviews
            WHERE consultant_name IS NOT NULL AND consultant_name != ''
            GROUP BY consultant_name
            ORDER BY total DESC
        """)
        rows = cur.fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            d["accept_rate"] = round(d["accepted"] / d["total"] * 100, 1) if d["total"] else 0
            result.append(d)
        return result

    def get_weekly_trend(weeks: int = 8):
        conn = get_db()
        cur = _exec(conn, """
            SELECT
                to_char(created_at, 'IYYY-IW') AS week,
                COUNT(*) AS total,
                SUM(CASE WHEN verdict NOT LIKE '%NOT ACCEPTED%' THEN 1 ELSE 0 END) AS accepted,
                SUM(CASE WHEN verdict LIKE '%NOT ACCEPTED%' THEN 1 ELSE 0 END) AS not_accepted
            FROM reviews
            WHERE created_at >= NOW() - INTERVAL '1 week' * %s
            GROUP BY week
            ORDER BY week DESC
            LIMIT %s
        """, (weeks * 2, weeks))
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]

    def get_top_issues(limit: int = 10):
        conn = get_db()
        cur = _exec(conn, "SELECT critical_issues FROM reviews WHERE critical_issues IS NOT NULL AND critical_issues != '[]'")
        rows = cur.fetchall()
        conn.close()
        from collections import Counter
        counts = Counter()
        for r in rows:
            try:
                issues = json.loads(r["critical_issues"])
                for iss in issues:
                    counts[str(iss)[:80]] += 1
            except Exception:
                pass
        return [{"issue": k, "count": v} for k, v in counts.most_common(limit)]

    def get_reviews_for_csv():
        conn = get_db()
        cur = _exec(conn, """
            SELECT id, deal_code, zip_name, deposit_type, verdict, verdict_reason,
                   consultant_name, heath_note, created_at
            FROM reviews ORDER BY created_at DESC
        """)
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

else:
    # ── SQLite fallback (local dev) ────────────────────────────────────────────
    import sqlite3

    _requested_data_dir = os.environ.get("AUSMAR_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
    try:
        os.makedirs(_requested_data_dir, exist_ok=True)
        DATA_DIR = _requested_data_dir
    except Exception:
        DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(DATA_DIR, exist_ok=True)
    DB_PATH = os.path.join(DATA_DIR, "qa_agent.db")

    def get_db():
        conn = sqlite3.connect(DB_PATH, timeout=5)
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
                file_paths TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending',
                matched_review_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS pending_reviews (
                id TEXT PRIMARY KEY,
                zip_name TEXT NOT NULL,
                status TEXT DEFAULT 'processing',
                progress INTEGER DEFAULT 0,
                progress_message TEXT DEFAULT 'Starting review...',
                result TEXT DEFAULT '',
                error TEXT DEFAULT '',
                review_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS access_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                consultant_name TEXT NOT NULL,
                email TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        try:
            conn.execute("ALTER TABLE prelogs ADD COLUMN file_paths TEXT DEFAULT '[]'")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE reviews ADD COLUMN submitted_by TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass
        conn.executemany(
            "INSERT OR IGNORE INTO plans (name, min_width, min_length, total_area, width_incl_eaves, house_width) VALUES (?,?,?,?,?,?)",
            [
                ("Clearwater 225", 12.3, 29.1, 225.95, 11.98, 0.0),
                ("Clearwater 245", 13.0, 29.2, 245.51, 12.60, 0.0),
                ("Narrabeen", 10.0, 25.0, 212.33, 0.0, 9.24),
                ("Malabar 153 Traditional", 10.0, 25.0, 153.0, 10.5, 10.0),
                ("Tampa Bay 200", 12.5, 24.9, 200.6, 11.5, 11.5),
            ],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO access_codes (code, consultant_name, email, active) VALUES (?,?,?,1)",
            [
                ("8E8QMG", "Andrew", ""), ("QYN2DU", "Caitlyn", ""), ("K7FNM6", "Heath", ""),
                ("0G25Q7", "Ian", ""), ("C71EEJ", "Johan", ""), ("50LF6T", "Nik (Admin)", "nik@ausmar.com.au"),
                ("JB04JH", "Rod", ""), ("9CLF1C", "Telford", ""),
            ],
        )
        conn.commit()
        conn.close()

    def create_pending_review(pending_id, zip_name):
        conn = get_db()
        conn.execute("INSERT INTO pending_reviews (id, zip_name, status, progress, progress_message) VALUES (?,?,?,?,?)",
                     (pending_id, zip_name, "processing", 0, "Starting review..."))
        conn.commit(); conn.close()

    def update_pending_progress(pending_id, progress, message):
        conn = get_db()
        conn.execute("UPDATE pending_reviews SET progress=?, progress_message=?, updated_at=datetime('now') WHERE id=?",
                     (progress, message, pending_id))
        conn.commit(); conn.close()

    def complete_pending_review(pending_id, result_json, review_id):
        conn = get_db()
        conn.execute("UPDATE pending_reviews SET status='completed', progress=100, progress_message='Complete', result=?, review_id=?, updated_at=datetime('now') WHERE id=?",
                     (result_json, review_id, pending_id))
        conn.commit(); conn.close()

    def fail_pending_review(pending_id, error):
        conn = get_db()
        conn.execute("UPDATE pending_reviews SET status='failed', progress_message=?, error=?, updated_at=datetime('now') WHERE id=?",
                     (error, error, pending_id))
        conn.commit(); conn.close()

    def get_pending_review(pending_id):
        conn = get_db()
        r = conn.execute("SELECT * FROM pending_reviews WHERE id=?", (pending_id,)).fetchone()
        conn.close()
        return dict(r) if r else None

    def cleanup_old_pending(hours=24):
        conn = get_db()
        conn.execute("DELETE FROM pending_reviews WHERE created_at < datetime('now', ?)", (f"-{hours} hours",))
        conn.commit(); conn.close()

    def get_all_plans():
        conn = get_db()
        rows = conn.execute("SELECT * FROM plans ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_plan(name, min_width, min_length, total_area=0, width_incl_eaves=0, house_width=0):
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO plans (name, min_width, min_length, total_area, width_incl_eaves, house_width, updated_at) VALUES (?,?,?,?,?,?,datetime('now'))",
                     (name, min_width, min_length, total_area, width_incl_eaves, house_width))
        conn.commit(); conn.close()

    def delete_plan(plan_id):
        conn = get_db()
        conn.execute("DELETE FROM plans WHERE id=?", (plan_id,))
        conn.commit(); conn.close()

    def save_review(data):
        conn = get_db()
        cur = conn.execute(
            """INSERT INTO reviews (deal_code, zip_name, deposit_type, verdict, verdict_reason,
               critical_issues, warnings, heath_note, consultant_email, check_results,
               files_in_zip, corrections_applied, corrected_zip_path, consultant_name, prelog_id, submitted_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (data.get("deal_code",""), data.get("zip_name",""), data.get("deposit_type","UNKNOWN"),
             data.get("verdict",""), data.get("verdict_reason",""),
             json.dumps(data.get("critical_issues",[])), json.dumps(data.get("warnings",[])),
             data.get("heath_note",""), data.get("consultant_email",""),
             json.dumps(data.get("check_results",{}), default=str),
             json.dumps(data.get("files_in_zip",[])), json.dumps(data.get("corrections_applied",[])),
             data.get("corrected_zip_path",""), data.get("consultant_name",""), data.get("prelog_id"),
             data.get("submitted_by","")),
        )
        review_id = cur.lastrowid
        conn.commit(); conn.close()
        return review_id

    def _parse_review(d):
        for k in ("critical_issues", "warnings", "files_in_zip", "corrections_applied"):
            try: d[k] = json.loads(d[k]) if d[k] else []
            except: d[k] = []
        try: d["check_results"] = json.loads(d["check_results"]) if d["check_results"] else {}
        except: d["check_results"] = {}
        return d

    def get_all_reviews():
        conn = get_db()
        rows = conn.execute("SELECT * FROM reviews ORDER BY created_at DESC").fetchall()
        conn.close()
        return [_parse_review(dict(r)) for r in rows]

    def get_review(review_id):
        conn = get_db()
        r = conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        conn.close()
        return _parse_review(dict(r)) if r else None

    def save_feedback(review_id, check_name, issue_text, is_correct, notes="", submitted_by=""):
        conn = get_db()
        conn.execute("INSERT INTO feedback (review_id, check_name, issue_text, is_correct, notes, submitted_by) VALUES (?,?,?,?,?,?)",
                     (review_id, check_name, issue_text, is_correct, notes, submitted_by))
        conn.commit(); conn.close()

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
        conn = get_db()
        rows = conn.execute("SELECT * FROM feedback WHERE is_correct=0 ORDER BY created_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def save_prelog(data):
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO prelogs (deal_code, consultant_name, deposit_amount, customer_names, notes, files, file_paths, status) VALUES (?,?,?,?,?,?,?,?)",
            (data.get("deal_code",""), data.get("consultant_name",""), data.get("deposit_amount",0),
             data.get("customer_names",""), data.get("notes",""),
             json.dumps(data.get("files",[])), json.dumps(data.get("file_paths",[])), "pending"),
        )
        prelog_id = cur.lastrowid
        conn.commit(); conn.close()
        return prelog_id

    def _parse_prelog(d):
        try: d["files"] = json.loads(d["files"]) if d["files"] else []
        except: d["files"] = []
        try: d["file_paths"] = json.loads(d.get("file_paths") or "[]")
        except: d["file_paths"] = []
        return d

    def get_all_prelogs():
        conn = get_db()
        rows = conn.execute("SELECT * FROM prelogs ORDER BY created_at DESC").fetchall()
        conn.close()
        return [_parse_prelog(dict(r)) for r in rows]

    def get_prelog(prelog_id):
        conn = get_db()
        r = conn.execute("SELECT * FROM prelogs WHERE id=?", (prelog_id,)).fetchone()
        conn.close()
        return _parse_prelog(dict(r)) if r else None

    def find_prelog_by_deal_code(deal_code):
        conn = get_db()
        r = conn.execute("SELECT * FROM prelogs WHERE deal_code=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (deal_code,)).fetchone()
        conn.close()
        return _parse_prelog(dict(r)) if r else None

    def mark_prelog_matched(prelog_id, review_id):
        conn = get_db()
        conn.execute("UPDATE prelogs SET status='matched', matched_review_id=?, updated_at=datetime('now') WHERE id=?", (review_id, prelog_id))
        conn.commit(); conn.close()

    def get_review_stats():
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        not_accepted = conn.execute("SELECT COUNT(*) FROM reviews WHERE verdict LIKE '%NOT ACCEPTED%'").fetchone()[0]
        conn.close()
        return {"total": total, "accepted": total - not_accepted, "not_accepted": not_accepted}

    def get_all_access_codes():
        conn = get_db()
        rows = conn.execute("SELECT * FROM access_codes ORDER BY consultant_name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_access_code(code):
        conn = get_db()
        row = conn.execute("SELECT * FROM access_codes WHERE code=? AND active=1", (code.upper(),)).fetchone()
        conn.close()
        return dict(row) if row else None

    def create_access_code(consultant_name, email=""):
        import random, string
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        conn = get_db()
        while conn.execute("SELECT 1 FROM access_codes WHERE code=?", (code,)).fetchone():
            code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        conn.execute("INSERT INTO access_codes (code, consultant_name, email) VALUES (?,?,?)", (code, consultant_name, email))
        conn.commit(); conn.close()
        return code

    def deactivate_access_code(code_id):
        conn = get_db()
        conn.execute("UPDATE access_codes SET active=0 WHERE id=?", (code_id,))
        conn.commit(); conn.close()

    def get_staff_report():
        conn = get_db()
        rows = conn.execute("""
            SELECT consultant_name, COUNT(*) AS total,
                SUM(CASE WHEN verdict NOT LIKE '%NOT ACCEPTED%' THEN 1 ELSE 0 END) AS accepted,
                SUM(CASE WHEN verdict LIKE '%NOT ACCEPTED%' THEN 1 ELSE 0 END) AS not_accepted,
                SUM(CASE WHEN deposit_type='NHP' THEN 1 ELSE 0 END) AS nhp,
                SUM(CASE WHEN deposit_type='STC' THEN 1 ELSE 0 END) AS stc
            FROM reviews WHERE consultant_name IS NOT NULL AND consultant_name != ''
            GROUP BY consultant_name ORDER BY total DESC
        """).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            d["accept_rate"] = round(d["accepted"] / d["total"] * 100, 1) if d["total"] else 0
            result.append(d)
        return result

    def get_weekly_trend(weeks=8):
        conn = get_db()
        rows = conn.execute("""
            SELECT strftime('%Y-W%W', created_at) AS week, COUNT(*) AS total,
                SUM(CASE WHEN verdict NOT LIKE '%NOT ACCEPTED%' THEN 1 ELSE 0 END) AS accepted,
                SUM(CASE WHEN verdict LIKE '%NOT ACCEPTED%' THEN 1 ELSE 0 END) AS not_accepted
            FROM reviews WHERE created_at >= datetime('now', '-' || ? || ' months')
            GROUP BY week ORDER BY week DESC LIMIT ?
        """, (weeks * 2, weeks)).fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]

    def get_top_issues(limit=10):
        conn = get_db()
        rows = conn.execute("SELECT critical_issues FROM reviews WHERE critical_issues IS NOT NULL AND critical_issues != '[]'").fetchall()
        conn.close()
        from collections import Counter
        counts = Counter()
        for r in rows:
            try:
                for iss in json.loads(r["critical_issues"]):
                    counts[str(iss)[:80]] += 1
            except Exception:
                pass
        return [{"issue": k, "count": v} for k, v in counts.most_common(limit)]

    def get_reviews_for_csv():
        conn = get_db()
        rows = conn.execute("""
            SELECT id, deal_code, zip_name, deposit_type, verdict, verdict_reason,
                   consultant_name, heath_note, created_at
            FROM reviews ORDER BY created_at DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
