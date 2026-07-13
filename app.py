"""
AUSMAR PSE QA Agent — Production Flask Application
Async review processing to avoid DigitalOcean 60s gateway timeout.
"""

import os
import json
import uuid
import traceback
import threading
from flask import Flask, request, jsonify, render_template, send_file, send_from_directory

import database as db
from qa_engine import run_qa_review

# V2 — Stage 2/3 engines and rule library (additive; Stage 1 untouched)
import db_v2
import nhp_engine
import contract_qa_engine

# V3 — Contract QA Intelligence (18 Tier 1 rules engine; additive)
import db_v3_contract_qa
import contract_qa_intelligence

# Users / roles (additive — login + role-based permissions + user management)
import users_db

# V1 — Contract QA Intelligence (additive; existing Stage 3 untouched)
import db_v1_migrations
import contract_qa_v1_engine

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB

# Persistent data dir. Falls back to ./data if the Volume path is unavailable.
def _make_data_dir(requested):
    try:
        os.makedirs(requested, exist_ok=True)
        # Verify it's actually writable
        test = os.path.join(requested, '.write_test')
        with open(test, 'w') as f:
            f.write('ok')
        os.remove(test)
        return requested
    except Exception:
        fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(fallback, exist_ok=True)
        return fallback

_requested_data_dir = os.environ.get("AUSMAR_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
_DATA_DIR = _make_data_dir(_requested_data_dir)
app.config["UPLOAD_FOLDER"] = os.path.join(_DATA_DIR, "uploads")
app.config["CORRECTED_FOLDER"] = os.path.join(_DATA_DIR, "corrected_zips")
app.config["PRELOG_FOLDER"] = os.path.join(_DATA_DIR, "prelog_uploads")

app.config["STAGE_FOLDER"] = os.path.join(_DATA_DIR, "stage_uploads")

for d in [app.config["UPLOAD_FOLDER"], app.config["CORRECTED_FOLDER"], app.config["PRELOG_FOLDER"], app.config["STAGE_FOLDER"]]:
    os.makedirs(d, exist_ok=True)

# Lazy DB init — called on first real request so a locked/corrupt Volume never blocks startup.
_db_ready = False

def _ensure_db():
    global _db_ready
    if not _db_ready:
        try:
            db.init_db()
            db_v2.init_v2()  # create V2 tables + seed rule library (idempotent)
            db_v3_contract_qa.init_v3()  # V3 Contract QA Intelligence tables + rules (idempotent)
            users_db.init_users()  # create users table + seed accounts (idempotent)
            db_v1_migrations.run_v1_migrations()  # V1: feature flags + rule upgrades (idempotent)
            contract_qa_v1_engine.seed_tier1_rules()  # V1: seed 18 Contract QA rules (idempotent)
            _db_ready = True
        except Exception as e:
            import sys, traceback
            traceback.print_exc(file=sys.stderr)
            raise RuntimeError(f"Database init failed: {e}") from e


# ---- Health (no DB touch — used by Railway healthcheck) ----
@app.route("/health")
def health():
    return "ok", 200


def _run_review_background(pending_id, filepath, filename, corrected_folder, consultant_name="", consultant_email="", notes="", deal_code_override="", submitted_by=""):
    """Background thread: runs the full QA review and updates status in DB."""
    try:
        db.update_pending_progress(pending_id, 5, "Extracting zip and checking structure...")

        result = run_qa_review(
            filepath, filename, corrected_folder,
            progress_callback=lambda pct, msg: db.update_pending_progress(pending_id, pct, msg),
            notes=notes,
        )

        if "error" in result and not result.get("checks"):
            db.fail_pending_review(pending_id, result.get("error", "Unknown error"))
            return

        # Save to database
        vd = result.get("verdict_data", {})
        # The Stage 1 form now sends an explicit, required Deal Code and Consultant.
        # Treat them as authoritative so History never shows blanks/UNKNOWN or a
        # mis-extracted consultant. Fall back to the engine's extracted values only
        # when the form value is empty.
        final_deal_code = (deal_code_override or "").strip() or result.get("deal_code", "")
        final_consultant = (consultant_name or "").strip() or result.get("consultant_name", "")
        review_data = {
            "deal_code": final_deal_code,
            "zip_name": result.get("zip_name", ""),
            "deposit_type": result.get("deposit_type", "UNKNOWN"),
            "verdict": vd.get("verdict", "ERROR"),
            "verdict_reason": vd.get("verdict_reason", ""),
            "critical_issues": vd.get("critical_issues", []),
            "warnings": vd.get("warnings", []),
            "heath_note": vd.get("heath_review_note", ""),
            "consultant_email": vd.get("consultant_feedback_email", ""),
            "check_results": result.get("checks", {}),
            "files_in_zip": result.get("checks", {}).get("file_structure", {}).get("files", []),
            "corrections_applied": result.get("corrections_applied", []),
            "corrected_zip_path": result.get("corrected_zip_path", ""),
            "consultant_name": final_consultant,
            "prelog_id": result.get("prelog_id"),
            "submitted_by": submitted_by,
        }
        review_id = db.save_review(review_data)

        # Mark prelog as matched
        if result.get("prelog_id"):
            db.mark_prelog_matched(result["prelog_id"], review_id)

        result["review_id"] = review_id
        result_json = json.dumps(result, default=str)
        db.complete_pending_review(pending_id, result_json, review_id)

    except Exception as e:
        traceback.print_exc()
        db.fail_pending_review(pending_id, f"Review failed: {str(e)}")
    finally:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


# ---- Pages ----
@app.route("/")
def index():
    _ensure_db()
    return render_template("index.html")


# ---- Review API (async) ----
@app.route("/api/review", methods=["POST"])
def api_review():
    _ensure_db()
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if not file.filename.endswith(".zip"):
        return jsonify({"error": "File must be a .zip"}), 400

    # Consultant identity + Deal Code now come from the Stage 1 form and are required.
    consultant_name = request.form.get("consultant_name", "").strip()
    consultant_email = request.form.get("consultant_email", "").strip()
    deal_code = request.form.get("deal_code", "").strip()
    submitted_by = request.form.get("submitted_by", "").strip()
    if not consultant_name:
        return jsonify({"error": "Consultant is required."}), 400
    if not deal_code:
        return jsonify({"error": "Deal Code is required."}), 400

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(filepath)

    # Create a pending review ID and return immediately
    pending_id = str(uuid.uuid4())[:12]
    db.create_pending_review(pending_id, file.filename)

    # Extract notes (Pre-Log info) from form
    notes = request.form.get("notes", "").strip()

    # Launch background thread
    t = threading.Thread(
        target=_run_review_background,
        args=(pending_id, filepath, file.filename, app.config["CORRECTED_FOLDER"], consultant_name, consultant_email, notes, deal_code, submitted_by),
        daemon=True,
    )
    t.start()

    return jsonify({"review_id": pending_id, "status": "processing"}), 202


# ---- Review Status (polling endpoint) ----
@app.route("/api/review/<review_id>/status")
def api_review_status(review_id):
    pending = db.get_pending_review(review_id)
    if not pending:
        return jsonify({"error": "Review not found"}), 404

    response = {
        "review_id": review_id,
        "status": pending["status"],
        "progress": pending["progress"],
        "progress_message": pending["progress_message"],
    }

    if pending["status"] == "completed":
        # Return the full result
        try:
            result = json.loads(pending["result"]) if pending["result"] else {}
        except (json.JSONDecodeError, TypeError):
            result = {}
        # If verdict_data is missing, reconstruct from DB so frontend never shows UNKNOWN.
        if not result.get("verdict_data") and pending.get("review_id"):
            try:
                saved = db.get_review(pending["review_id"])
                if saved and saved.get("verdict"):
                    result["verdict_data"] = {
                        "verdict": saved["verdict"],
                        "verdict_reason": saved.get("verdict_reason", ""),
                        "critical_issues": saved.get("critical_issues", []),
                        "warnings": saved.get("warnings", []),
                        "heath_review_note": saved.get("heath_note", ""),
                        "consultant_feedback_email": saved.get("consultant_email", ""),
                    }
            except Exception:
                pass
        response["result"] = result
        response["db_review_id"] = pending.get("review_id")
    elif pending["status"] == "failed":
        response["error"] = pending.get("error", "Unknown error")

    return jsonify(response)


# ---- Download corrected zip ----
@app.route("/api/download/<int:review_id>")
def download_corrected(review_id):
    review = db.get_review(review_id)
    if not review or not review.get("corrected_zip_path"):
        return jsonify({"error": "No corrected zip available"}), 404
    path = review["corrected_zip_path"]
    if not os.path.exists(path):
        return jsonify({"error": "Corrected zip file not found on disk"}), 404
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


# ---- Review History ----
def _first_name(name):
    return (name or "").strip().split(" ")[0].lower()


def _stage1_display_type(deposit_type):
    """Stage 1 is always a PSE submission. Map the deposit type to a clean label
    so the History table never shows 'UNKNOWN'. NHP/STC are kept as-is; anything
    blank or unknown shows the generic 'PSE'."""
    dt = (deposit_type or "").strip().upper()
    if dt in ("NHP", "STC"):
        return f"PSE ({dt})"
    return "PSE"


@app.route("/api/reviews")
def api_reviews():
    reviews = db.get_all_reviews()
    # Optional consultant scoping: consultants only see their own deals. Matching
    # is by first name (case-insensitive) so it works for both old extracted names
    # and the new full-name dropdown values.
    consultant = (request.args.get("consultant") or "").strip()
    if consultant:
        fn = _first_name(consultant)
        reviews = [r for r in reviews if _first_name(r.get("consultant_name")) == fn]
    # Strip large check_results for list view and attach a clean display type.
    for r in reviews:
        r.pop("check_results", None)
        r["display_type"] = _stage1_display_type(r.get("deposit_type"))
    return jsonify(reviews)


@app.route("/api/reviews/<int:review_id>")
def api_review_detail(review_id):
    review = db.get_review(review_id)
    if not review:
        return jsonify({"error": "Review not found"}), 404
    feedback = db.get_feedback_for_review(review_id)
    review["feedback"] = feedback
    return jsonify(review)


@app.route("/api/reviews/<int:review_id>", methods=["PATCH"])
def api_update_review(review_id):
    """Admin-only correction of review metadata (currently deal_code only).

    Reviews are permanent training records and are never deleted, but the
    deal_code can be corrected when a consultant's zip was mis-named and the
    code was extracted incorrectly. Requires the admin access code.
    """
    _ensure_db()
    data = request.get_json() or {}
    code = (data.get("code") or "").strip().upper()
    acc = db.get_access_code(code) if code else None
    if not acc or "admin" not in (acc.get("consultant_name") or "").lower():
        return jsonify({"error": "Admin access code required"}), 403
    allowed = ["deal_code"]
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400
    conn = db.get_db()
    if os.environ.get("DATABASE_URL"):
        sets = ", ".join(f"{k}=%s" for k in updates)
        vals = list(updates.values()) + [review_id]
        cur = conn.cursor()
        cur.execute(f"UPDATE reviews SET {sets} WHERE id=%s", vals)
    else:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [review_id]
        conn.execute(f"UPDATE reviews SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "updated": list(updates.keys())})


@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_review_stats())


# ---- Feedback ----
@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    _ensure_db()
    data = request.json
    if not data.get("review_id") or not data.get("check_name") or not data.get("issue_text"):
        return jsonify({"error": "review_id, check_name, and issue_text required"}), 400
    db.save_feedback(
        review_id=data["review_id"],
        check_name=data["check_name"],
        issue_text=data["issue_text"],
        is_correct=1 if data.get("is_correct", True) else 0,
        notes=data.get("notes", ""),
        submitted_by=data.get("submitted_by", ""),
    )
    return jsonify({"status": "ok"})


@app.route("/api/feedback/<int:review_id>")
def api_get_feedback(review_id):
    return jsonify(db.get_feedback_for_review(review_id))


# ---- Plans ----
@app.route("/api/plans")
def api_plans():
    _ensure_db()
    return jsonify(db.get_all_plans())


def _can_manage_plans(code):
    """Plan management is allowed for admins and Heath only (per role spec)."""
    u = users_db.get_user_by_code((code or "").strip().upper())
    return bool(u and u.get("role") in ("admin", "manager_heath"))


@app.route("/api/plans", methods=["POST"])
def api_add_plan():
    data = request.json or {}
    if not _can_manage_plans(data.get("code")):
        return jsonify({"error": "Plan management requires admin or Heath access"}), 403
    if not data.get("name") or not data.get("min_width") or not data.get("min_length"):
        return jsonify({"error": "name, min_width, min_length required"}), 400
    db.add_plan(
        name=data["name"],
        min_width=float(data["min_width"]),
        min_length=float(data["min_length"]),
        total_area=float(data.get("total_area", 0)),
        width_incl_eaves=float(data.get("width_incl_eaves", 0)),
        house_width=float(data.get("house_width", 0)),
    )
    return jsonify({"status": "ok"})


@app.route("/api/plans/<int:plan_id>", methods=["DELETE"])
def api_delete_plan(plan_id):
    code = request.args.get("code") or request.headers.get("X-User-Code", "")
    if not _can_manage_plans(code):
        return jsonify({"error": "Plan management requires admin or Heath access"}), 403
    db.delete_plan(plan_id)
    return jsonify({"status": "ok"})

@app.route("/api/plans/<int:plan_id>", methods=["PATCH"])
def api_update_plan(plan_id):
    _ensure_db()
    data = request.get_json() or {}
    if not _can_manage_plans(data.get("code")):
        return jsonify({"error": "Plan management requires admin or Heath access"}), 403
    allowed = ["name", "min_width", "min_length", "total_area", "width_incl_eaves", "house_width"]
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400
    conn = db.get_db()
    if os.environ.get("DATABASE_URL"):
        ph = "%s"
        ts = "NOW()"
        sets = ", ".join(f"{k}={ph}" for k in updates)
        vals = list(updates.values()) + [plan_id]
        cur = conn.cursor()
        cur.execute(f"UPDATE plans SET {sets}, updated_at={ts} WHERE id={ph}", vals)
    else:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [plan_id]
        conn.execute(f"UPDATE plans SET {sets}, updated_at=datetime('now') WHERE id=?", vals)
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "updated": list(updates.keys())})


# ---- Access Codes ----
@app.route("/api/access-codes")
def api_access_codes():
    _ensure_db()
    return jsonify(db.get_all_access_codes())


@app.route("/api/access-codes", methods=["POST"])
def api_create_access_code():
    data = request.json or {}
    name = data.get("consultant_name", "").strip()
    if not name:
        return jsonify({"error": "consultant_name required"}), 400
    code = db.create_access_code(name, data.get("email", ""))
    return jsonify({"status": "ok", "code": code})


@app.route("/api/access-codes/<int:code_id>", methods=["DELETE"])
def api_deactivate_access_code(code_id):
    db.deactivate_access_code(code_id)
    return jsonify({"status": "ok"})


@app.route("/api/access-codes/validate", methods=["POST"])
def api_validate_access_code():
    _ensure_db()
    data = request.json or {}
    code = data.get("code", "").strip().upper()
    if not code:
        return jsonify({"valid": False, "error": "code required"}), 400
    record = db.get_access_code(code)
    if record:
        return jsonify({"valid": True, "consultant_name": record["consultant_name"], "email": record["email"]})
    return jsonify({"valid": False, "error": "Invalid or inactive access code"}), 401


# ===========================================================================
# AUTH / USERS (role-based login + user management)
# ===========================================================================
@app.route("/api/login", methods=["POST"])
def api_login():
    """Validate a login code and return the user's identity, role and the set of
    UI permissions the frontend uses to show/hide sections."""
    _ensure_db()
    data = request.json or {}
    code = (data.get("code") or "").strip().upper()
    if not code:
        return jsonify({"valid": False, "error": "Enter your login code."}), 400
    user = users_db.get_user_by_code(code)
    if not user:
        # Backward-compatibility: fall back to the legacy access_codes table so
        # any old code still works (treated as a consultant).
        legacy = db.get_access_code(code)
        if legacy:
            name = legacy["consultant_name"]
            role = "admin" if "admin" in (name or "").lower() else "consultant"
            return jsonify({
                "valid": True, "full_name": name, "email": legacy.get("email", ""),
                "role": role, "code": code,
                "permissions": users_db.permissions_for(role),
            })
        return jsonify({"valid": False, "error": "Invalid or inactive login code."}), 401
    return jsonify({
        "valid": True,
        "full_name": user["full_name"],
        "email": user.get("email", ""),
        "role": user["role"],
        "code": code,
        "permissions": users_db.permissions_for(user["role"]),
    })


@app.route("/api/consultants")
def api_consultants():
    """The fixed list of sales consultants for the Stage 1/2/3 dropdowns."""
    return jsonify(users_db.CONSULTANTS)


def _require_admin():
    """Return the admin user dict if the request carries a valid admin code,
    else None. Code is read from JSON body 'code' or 'X-User-Code' header."""
    code = ""
    if request.is_json:
        code = (request.json or {}).get("code", "")
    code = code or request.headers.get("X-User-Code", "")
    user = users_db.get_user_by_code((code or "").strip().upper())
    return user if (user and user.get("role") == "admin") else None


@app.route("/api/users")
def api_users():
    """List all users. Admin only."""
    _ensure_db()
    if not _require_admin():
        return jsonify({"error": "Admin access required"}), 403
    return jsonify(users_db.get_all_users())


@app.route("/api/users", methods=["POST"])
def api_add_user():
    """Add a new user and assign a role. Admin only."""
    _ensure_db()
    if not _require_admin():
        return jsonify({"error": "Admin access required"}), 403
    data = request.json or {}
    try:
        user = users_db.add_user(
            full_name=data.get("full_name", ""),
            role=data.get("role", "consultant"),
            email=data.get("email", ""),
            code=data.get("login_code", ""),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok", "user": user})


@app.route("/api/users/<int:user_id>/role", methods=["PATCH"])
def api_update_user_role(user_id):
    """Change a user's role. Admin only."""
    _ensure_db()
    if not _require_admin():
        return jsonify({"error": "Admin access required"}), 403
    data = request.json or {}
    try:
        users_db.update_user_role(user_id, (data.get("role") or "").strip())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok"})


@app.route("/api/users/<int:user_id>/active", methods=["PATCH"])
def api_set_user_active(user_id):
    """Activate/deactivate a user. Admin only."""
    _ensure_db()
    if not _require_admin():
        return jsonify({"error": "Admin access required"}), 403
    data = request.json or {}
    users_db.set_user_active(user_id, 1 if data.get("active") else 0)
    return jsonify({"status": "ok"})


# ---- Reports ----
@app.route("/api/reports/staff")
def api_staff_report():
    return jsonify(db.get_staff_report())


@app.route("/api/reports/weekly")
def api_weekly_trend():
    weeks = int(request.args.get("weeks", 8))
    return jsonify(db.get_weekly_trend(weeks))


@app.route("/api/reports/top-issues")
def api_top_issues():
    limit = int(request.args.get("limit", 10))
    return jsonify(db.get_top_issues(limit))


@app.route("/api/reports/export-csv")
def api_export_csv():
    import csv, io
    rows = db.get_reviews_for_csv()
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    else:
        output.write("No data")
    output.seek(0)
    from flask import Response
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=ausmar_qa_reviews.csv"}
    )


# ---- Pre-logs ----
@app.route("/api/prelogs")
def api_prelogs():
    _ensure_db()
    return jsonify(db.get_all_prelogs())


@app.route("/api/prelogs", methods=["POST"])
def api_add_prelog():
    _ensure_db()
    data = request.form.to_dict() if request.form else request.json or {}
    if not data.get("deal_code"):
        return jsonify({"error": "deal_code required"}), 400

    # Re-ensure prelog uploads directory exists (volume may not have had subdirs on first mount)
    prelog_folder = app.config["PRELOG_FOLDER"]
    try:
        os.makedirs(prelog_folder, exist_ok=True)
    except Exception as e:
        import sys
        print(f"[WARN] Could not create prelog_uploads dir: {e}", file=sys.stderr)

    # Handle file uploads — save to disk, record both filename and full path
    # Use getlist() so ALL files sent under the same key (e.g. multiple 'files' fields) are captured.
    # Iterating request.files by key and using request.files[key] only returns the LAST file per key.
    saved_files = []
    saved_paths = []
    if request.files:
        all_uploads = []
        for key in request.files.keys():
            all_uploads.extend(request.files.getlist(key))
        for f in all_uploads:
            if f.filename:
                try:
                    # Sanitise filename: strip path separators and spaces
                    safe_name = os.path.basename(f.filename).replace(" ", "_")
                    save_path = os.path.join(prelog_folder, f"{data['deal_code']}_{safe_name}")
                    f.save(save_path)
                    saved_files.append(safe_name)
                    saved_paths.append(save_path)
                except Exception as fe:
                    import sys
                    print(f"[WARN] Could not save prelog file {f.filename}: {fe}", file=sys.stderr)
                    # Record filename even if disk save failed — metadata still useful
                    saved_files.append(os.path.basename(f.filename))

    try:
        prelog_data = {
            "deal_code": data.get("deal_code", ""),
            "consultant_name": data.get("consultant_name", ""),
            "deposit_amount": float(data.get("deposit_amount") or 0),
            "notes": data.get("notes", ""),
            "files": saved_files,
            "file_paths": saved_paths,
        }
        prelog_id = db.save_prelog(prelog_data)
        return jsonify({"status": "ok", "prelog_id": prelog_id})
    except Exception as e:
        import sys, traceback
        traceback.print_exc(file=sys.stderr)
        return jsonify({"error": f"Failed to save pre-log: {str(e)}"}), 500


@app.route("/api/prelogs/<int:prelog_id>")
def api_prelog_detail(prelog_id):
    prelog = db.get_prelog(prelog_id)
    if not prelog:
        return jsonify({"error": "Pre-log not found"}), 404
    return jsonify(prelog)


@app.route("/api/prelogs/<int:prelog_id>", methods=["PATCH"])
def api_update_prelog(prelog_id):
    """Full edit: supports multipart/form-data (with file uploads) or JSON.
    - Editable fields: deal_code, consultant_name, deposit_amount, notes
    - remove_files: JSON array of filename strings to remove from the record
    - New file uploads are appended to existing files list
    """
    _ensure_db()
    prelog = db.get_prelog(prelog_id)
    if not prelog:
        return jsonify({"error": "Pre-log not found"}), 404

    # Accept both multipart/form-data (file uploads) and JSON
    if request.content_type and "multipart" in request.content_type:
        data = request.form.to_dict()
    else:
        data = request.get_json() or {}

    # Build scalar field updates
    allowed = ["consultant_name", "notes", "deposit_amount", "deal_code"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if "deposit_amount" in updates:
        try:
            updates["deposit_amount"] = float(updates["deposit_amount"])
        except (ValueError, TypeError):
            updates["deposit_amount"] = 0.0

    # Handle file removal — remove_files is a JSON array of filenames to drop
    remove_files_raw = data.get("remove_files", "[]")
    try:
        remove_files = json.loads(remove_files_raw) if isinstance(remove_files_raw, str) else (remove_files_raw or [])
    except Exception:
        remove_files = []

    existing_files = list(prelog.get("files") or [])
    existing_paths = list(prelog.get("file_paths") or [])

    if remove_files:
        new_files = []
        new_paths = []
        for fname, fpath in zip(existing_files, existing_paths):
            if fname not in remove_files:
                new_files.append(fname)
                new_paths.append(fpath)
            else:
                # Delete the physical file if it exists
                try:
                    if fpath and os.path.exists(fpath):
                        os.remove(fpath)
                except Exception:
                    pass
        existing_files = new_files
        existing_paths = new_paths

    # Handle new file uploads — append to existing
    prelog_folder = app.config["PRELOG_FOLDER"]
    os.makedirs(prelog_folder, exist_ok=True)
    deal_code_for_prefix = updates.get("deal_code") or prelog.get("deal_code") or str(prelog_id)
    if request.files:
        all_uploads = []
        for key in request.files.keys():
            all_uploads.extend(request.files.getlist(key))
        for f in all_uploads:
            if f.filename:
                try:
                    safe_name = os.path.basename(f.filename).replace(" ", "_")
                    save_path = os.path.join(prelog_folder, f"{deal_code_for_prefix}_{safe_name}")
                    f.save(save_path)
                    existing_files.append(safe_name)
                    existing_paths.append(save_path)
                except Exception as fe:
                    import sys
                    print(f"[WARN] Could not save prelog file {f.filename}: {fe}", file=sys.stderr)
                    existing_files.append(os.path.basename(f.filename))

    # Merge file lists into updates
    updates["files"] = json.dumps(existing_files)
    updates["file_paths"] = json.dumps(existing_paths)

    # Write to DB
    conn = db.get_db()
    is_pg = bool(os.environ.get("DATABASE_URL"))
    if is_pg:
        ph = "%s"
        ts = "NOW()"
        sets = ", ".join(f"{k}={ph}" for k in updates)
        vals = list(updates.values()) + [prelog_id]
        cur = conn.cursor()
        cur.execute(f"UPDATE prelogs SET {sets}, updated_at={ts} WHERE id={ph}", vals)
    else:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [prelog_id]
        conn.execute(f"UPDATE prelogs SET {sets}, updated_at=datetime('now') WHERE id=?", vals)
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "updated": list(updates.keys())})


# ---- Admin: Wipe endpoint DISABLED — review history is a permanent training record ----
@app.route("/api/admin/wipe-history", methods=["POST"])
def api_wipe_history():
    """Permanently disabled — review history must never be deleted (training record)."""
    return jsonify({"error": "Review history deletion is permanently disabled. Reviews are a required training record and must not be deleted."}), 403


@app.route("/api/debug/stats")
def api_debug_stats():
    import traceback as tb
    try:
        return jsonify(db.get_review_stats())
    except Exception as e:
        return jsonify({"error": str(e), "traceback": tb.format_exc()}), 500


# ===========================================================================
# V2 — STAGE 2 (NHP Review) / STAGE 3 (Pre-Contract QA) / RULES / LEARNING
# Additive only. None of the Stage 1 routes above are modified.
# ===========================================================================

def _is_admin(code):
    """Admin if the code maps to a user with the 'admin' role, OR (legacy) an
    access code whose consultant_name contains 'admin'. Rules/Learning editing
    is allowed for admins and the two managers (Heath, Lyana) per the role spec."""
    if not code:
        return False
    code = (code or "").strip().upper()
    u = users_db.get_user_by_code(code)
    if u and u.get("role") in ("admin", "manager_heath", "manager_lyana"):
        return True
    acc = db.get_access_code(code)
    return bool(acc and "admin" in (acc.get("consultant_name") or "").lower())


def _save_stage_uploads(prefix):
    """Save uploaded files into the stage_uploads folder, returning {field: path}.
    Files are sent under named form keys so each maps to a known document role."""
    saved = {}
    folder = app.config["STAGE_FOLDER"]
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass
    for key in request.files.keys():
        f = request.files.get(key)
        if f and f.filename:
            safe = os.path.basename(f.filename).replace(" ", "_")
            path = os.path.join(folder, f"{prefix}_{key}_{safe}")
            try:
                f.save(path)
                saved[key] = path
            except Exception as e:
                print(f"[WARN] could not save stage upload {key}: {e}")
    return saved


# ---- Stage 2: NHP Review (async) ----
def _run_nhp_background(pending_id, paths, deal_code, consultant_name, submitted_by=""):
    try:
        result = nhp_engine.run_nhp_review(
            paths.get("nhp_changes"), paths.get("final_nhp"),
            deal_code=deal_code, consultant_name=consultant_name,
            progress_cb=lambda pct, msg: db.update_pending_progress(pending_id, pct, msg),
        )
        review_id = db_v2.save_contract_review({
            "deal_code": result.get("deal_code", ""),
            "stage": 2,
            "consultant_name": consultant_name,
            "verdict": result.get("verdict", ""),
            "verdict_reason": result.get("verdict_reason", ""),
            "result_payload": result,
            "issues": result.get("issues", []),
            "submitted_by": submitted_by,
        })
        result["contract_review_id"] = review_id
        db.complete_pending_review(pending_id, json.dumps(result, default=str), review_id)
    except Exception as e:
        traceback.print_exc()
        db.fail_pending_review(pending_id, f"NHP review failed: {e}")
    finally:
        for p in paths.values():
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


@app.route("/api/stage2/review", methods=["POST"])
def api_stage2_review():
    _ensure_db()
    paths = _save_stage_uploads("s2")
    if not paths.get("nhp_changes") or not paths.get("final_nhp"):
        return jsonify({"error": "Both 'nhp_changes' and 'final_nhp' PDF files are required."}), 400
    deal_code = (request.form.get("deal_code") or "").strip()
    consultant_name = (request.form.get("consultant_name") or "").strip()
    submitted_by = (request.form.get("submitted_by") or "").strip()
    if not consultant_name:
        return jsonify({"error": "Consultant is required."}), 400
    pending_id = str(uuid.uuid4())[:12]
    db.create_pending_review(pending_id, paths.get("nhp_changes", "nhp"))
    threading.Thread(target=_run_nhp_background,
                     args=(pending_id, paths, deal_code, consultant_name, submitted_by), daemon=True).start()
    return jsonify({"review_id": pending_id, "status": "processing"}), 202


# ---- Stage 3: Unified Contract QA (NHP comparison + QA Intelligence rules) ----
def _run_stage3_background(pending_id, paths, deal_code, consultant_name, job_category, submitted_by=""):
    """Runs BOTH the existing NHP comparison engine AND the 18 Tier 1 QA Intelligence
    rules, then merges results into a single combined payload."""
    try:
        # --- Part A: Existing Stage 3 NHP comparison (Pass 1 + Pass 2) ---
        # Only run if we have the signed source docs needed for comparison
        stage3_result = None
        source_keys = ["signed_nhp", "signed_vos", "nhp_changes"]
        have_source = any(paths.get(k) for k in source_keys)
        have_contract_docs = paths.get("contract_spec") and paths.get("contract_pricing") and paths.get("working_drawings")

        if have_source and have_contract_docs:
            db.update_pending_progress(pending_id, 5, "Running NHP comparison (Pass 1 & 2)...")
            stage3_result = contract_qa_engine.run_contract_qa(
                paths, deal_code=deal_code, consultant_name=consultant_name,
                job_category=job_category,
                progress_cb=lambda pct, msg: db.update_pending_progress(
                    pending_id, 5 + int(pct * 0.4), f"NHP Comparison: {msg}"),
            )
        elif have_source or have_contract_docs:
            # Partial docs — still try, engine will PARK if insufficient
            db.update_pending_progress(pending_id, 5, "Running NHP comparison (partial docs)...")
            stage3_result = contract_qa_engine.run_contract_qa(
                paths, deal_code=deal_code, consultant_name=consultant_name,
                job_category=job_category,
                progress_cb=lambda pct, msg: db.update_pending_progress(
                    pending_id, 5 + int(pct * 0.4), f"NHP Comparison: {msg}"),
            )

        # --- Part B: QA Intelligence rules (18 Tier 1) ---
        # Map the uploaded field names to what the intelligence engine expects
        intel_paths = {
            "specification": paths.get("contract_spec") or paths.get("specification"),
            "working_drawings": paths.get("working_drawings"),
            "nhp": paths.get("contract_pricing") or paths.get("nhp"),
            "vos": paths.get("signed_vos") or paths.get("nhp_changes") or paths.get("vos"),
        }

        intel_result = None
        if intel_paths.get("specification"):
            db.update_pending_progress(pending_id, 50, "Running QA Intelligence rules...")
            intel_result = contract_qa_intelligence.run_contract_qa_intelligence(
                intel_paths, deal_code=deal_code, consultant_name=consultant_name,
                progress_cb=lambda pct, msg: db.update_pending_progress(
                    pending_id, 50 + int(pct * 0.45), f"QA Rules: {msg}"),
            )

        # --- Merge results ---
        db.update_pending_progress(pending_id, 96, "Merging results...")

        # Build combined result
        combined = {
            "stage": 3,
            "deal_code": deal_code,
            "consultant_name": consultant_name,
            "job_category": job_category,
        }

        # NHP comparison results
        if stage3_result:
            combined["nhp_comparison"] = {
                "verdict": stage3_result.get("verdict", ""),
                "verdict_reason": stage3_result.get("verdict_reason", ""),
                "issues": stage3_result.get("issues", []),
                "pass1_count": stage3_result.get("pass1_count", 0),
                "pass2_count": stage3_result.get("pass2_count", 0),
                "consultant_summary": stage3_result.get("consultant_summary", {}),
            }
            combined["issues"] = stage3_result.get("issues", [])
            combined["pass1_count"] = stage3_result.get("pass1_count", 0)
            combined["pass2_count"] = stage3_result.get("pass2_count", 0)
            combined["consultant_summary"] = stage3_result.get("consultant_summary", {})
        else:
            combined["nhp_comparison"] = None
            combined["issues"] = []
            combined["pass1_count"] = 0
            combined["pass2_count"] = 0
            combined["consultant_summary"] = {}

        # QA Intelligence results
        if intel_result and intel_result.get("status") == "completed":
            combined["qa_intelligence"] = {
                "submission_id": intel_result.get("submission_id"),
                "qa_score": intel_result.get("qa_score", 0),
                "total_rules": intel_result.get("total_rules", 0),
                "passed": intel_result.get("passed", 0),
                "failed": intel_result.get("failed", 0),
                "warnings": intel_result.get("warnings", 0),
                "findings": intel_result.get("findings", []),
                "verdict": intel_result.get("verdict", ""),
                "verdict_reason": intel_result.get("verdict_reason", ""),
            }
        else:
            combined["qa_intelligence"] = None

        # Unified verdict: worst of both engines
        verdicts_priority = ["DO NOT ISSUE", "ISSUE AFTER CORRECTIONS", "PARKED", "ISSUE WITH NOTED ITEMS", "READY TO ISSUE"]
        v1 = (stage3_result or {}).get("verdict", "")
        v2 = (intel_result or {}).get("verdict", "") if intel_result and intel_result.get("status") == "completed" else ""

        def _verdict_rank(v):
            for i, x in enumerate(verdicts_priority):
                if x in (v or "").upper():
                    return i
            return len(verdicts_priority)

        if v1 and v2:
            combined["verdict"] = v1 if _verdict_rank(v1) <= _verdict_rank(v2) else v2
            combined["verdict_reason"] = f"NHP Comparison: {(stage3_result or {}).get('verdict_reason','')} | QA Rules: {(intel_result or {}).get('verdict_reason','')}"
        elif v1:
            combined["verdict"] = v1
            combined["verdict_reason"] = (stage3_result or {}).get("verdict_reason", "")
        elif v2:
            combined["verdict"] = v2
            combined["verdict_reason"] = (intel_result or {}).get("verdict_reason", "")
        else:
            combined["verdict"] = "PARKED"
            combined["verdict_reason"] = "Insufficient documents to run either engine."

        # Save to contract_reviews (preserves Stage 3 history format)
        review_id = db_v2.save_contract_review({
            "deal_code": deal_code,
            "stage": 3,
            "consultant_name": consultant_name,
            "job_category": job_category,
            "verdict": combined["verdict"],
            "verdict_reason": combined["verdict_reason"],
            "result_payload": combined,
            "issues": combined.get("issues", []),
            "submitted_by": submitted_by,
        })
        combined["contract_review_id"] = review_id
        db.complete_pending_review(pending_id, json.dumps(combined, default=str), review_id)
    except Exception as e:
        traceback.print_exc()
        db.fail_pending_review(pending_id, f"Contract QA failed: {e}")
    finally:
        for p in paths.values():
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


@app.route("/api/stage3/review", methods=["POST"])
def api_stage3_review():
    _ensure_db()
    paths = _save_stage_uploads("s3")
    deal_code = (request.form.get("deal_code") or "").strip()
    consultant_name = (request.form.get("consultant_name") or "").strip()
    job_category = (request.form.get("job_category") or "").strip()
    submitted_by = (request.form.get("submitted_by") or "").strip()
    if not consultant_name:
        return jsonify({"error": "Consultant is required."}), 400
    # At minimum need the specification for QA Intelligence rules
    if not paths.get("contract_spec") and not paths.get("specification"):
        return jsonify({"error": "Contract Specification is required."}), 400
    pending_id = str(uuid.uuid4())[:12]
    db.create_pending_review(pending_id, paths.get("contract_spec", paths.get("specification", "contract")))
    threading.Thread(target=_run_stage3_background,
                     args=(pending_id, paths, deal_code, consultant_name, job_category, submitted_by), daemon=True).start()
    return jsonify({"review_id": pending_id, "status": "processing"}), 202


# Stage 2/3 reuse the existing /api/review/<id>/status polling endpoint, since
# both write to pending_reviews via the same create/update/complete helpers.


# ---- Stage 2/3 history ----
@app.route("/api/contract-reviews")
def api_contract_reviews():
    _ensure_db()
    stage = request.args.get("stage", type=int)
    rows = db_v2.get_contract_reviews(stage=stage)
    # Optional consultant scoping (consultants see only their own deals).
    consultant = (request.args.get("consultant") or "").strip()
    if consultant:
        fn = _first_name(consultant)
        rows = [r for r in rows if _first_name(r.get("consultant_name")) == fn]
    for r in rows:
        r.pop("result_payload", None)  # keep list light
        # Clean, never-UNKNOWN type label: Stage 2 = NHP, Stage 3 = Contract.
        r["display_type"] = "NHP" if r.get("stage") == 2 else ("Contract QA V1" if r.get("stage") == 4 else "Contract")
    return jsonify(rows)


@app.route("/api/contract-reviews/<int:review_id>")
def api_contract_review_detail(review_id):
    _ensure_db()
    cr = db_v2.get_contract_review(review_id)
    if not cr:
        return jsonify({"error": "Not found"}), 404
    return jsonify(cr)


# ---- Issue status (Open / In Review / Fixed / Accepted Exception / False Positive / Not Applicable) ----
@app.route("/api/issues/<int:issue_id>/status", methods=["PATCH"])
def api_update_issue_status(issue_id):
    _ensure_db()
    data = request.get_json() or {}
    status = (data.get("status") or "").strip()
    if not status:
        return jsonify({"error": "status required"}), 400
    db_v2.update_issue_status(issue_id, status, data.get("note", ""))
    return jsonify({"status": "ok"})


# ===========================================================================
# RULES ADMIN (UI-driven self-learning — no developer required)
# ===========================================================================
@app.route("/api/rules")
def api_rules():
    _ensure_db()
    stage = request.args.get("stage")  # 'Stage 2' | 'Stage 3' | None
    active_only = request.args.get("active_only") == "1"
    return jsonify(db_v2.get_rules(stage=stage, active_only=active_only))


@app.route("/api/rules", methods=["POST"])
def api_add_rule():
    _ensure_db()
    data = request.get_json() or {}
    if not _is_admin(data.get("code")):
        return jsonify({"error": "Admin access code required"}), 403
    if not data.get("category") or not data.get("description"):
        return jsonify({"error": "category and description required"}), 400
    rid = db_v2.add_rule(
        category=data["category"].strip(),
        description=data["description"].strip(),
        severity=data.get("severity", "Medium"),
        stage_applicability=data.get("stage_applicability", "Stage 3"),
        changed_by=data.get("submitted_by", ""),
        rule_ref=data.get("rule_ref", ""),
    )
    return jsonify({"status": "ok", "rule_id": rid})


@app.route("/api/rules/<int:rule_id>", methods=["PATCH"])
def api_update_rule(rule_id):
    _ensure_db()
    data = request.get_json() or {}
    if not _is_admin(data.get("code")):
        return jsonify({"error": "Admin access code required"}), 403
    fields = {k: v for k, v in data.items()
              if k in ("severity", "active", "category", "description", "stage_applicability")}
    if not fields:
        return jsonify({"error": "No valid fields to update"}), 400
    db_v2.update_rule(rule_id, fields, changed_by=data.get("submitted_by", ""))
    return jsonify({"status": "ok"})


@app.route("/api/rules/<int:rule_id>/exclusions", methods=["POST"])
def api_add_exclusion(rule_id):
    _ensure_db()
    data = request.get_json() or {}
    if not _is_admin(data.get("code")):
        return jsonify({"error": "Admin access code required"}), 403
    text = (data.get("exclusion_text") or "").strip()
    if not text:
        return jsonify({"error": "exclusion_text required"}), 400
    db_v2.add_exclusion(rule_id, text, created_by=data.get("submitted_by", ""))
    return jsonify({"status": "ok"})


@app.route("/api/rules/<int:rule_id>/history")
def api_rule_history(rule_id):
    _ensure_db()
    return jsonify(db_v2.get_rule_history(rule_id))


# ===========================================================================
# STAGE 4: CONTRACT QA INTELLIGENCE (Tier 1 Rules Engine)
# Additive — does not modify Stage 1/2/3 routes or engines.
# ===========================================================================

def _run_stage4_background(pending_id, paths, deal_code, consultant_name):
    """Background thread: runs all 18 Tier 1 Contract QA rules."""
    try:
        result = contract_qa_intelligence.run_contract_qa_intelligence(
            paths, deal_code=deal_code, consultant_name=consultant_name,
            progress_cb=lambda pct, msg: db.update_pending_progress(pending_id, pct, msg),
        )
        if result.get("status") == "failed":
            db.fail_pending_review(pending_id, result.get("error", "Contract QA Intelligence failed"))
        else:
            db.complete_pending_review(pending_id, json.dumps(result, default=str), result.get("submission_id"))
    except Exception as e:
        traceback.print_exc()
        db.fail_pending_review(pending_id, f"Contract QA Intelligence failed: {e}")
    finally:
        for p in paths.values():
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


@app.route("/api/stage4/review", methods=["POST"])
def api_stage4_review():
    """Submit documents for Contract QA Intelligence (Tier 1 rules check)."""
    _ensure_db()
    paths = _save_stage_uploads("s4")
    deal_code = (request.form.get("deal_code") or "").strip()
    consultant_name = (request.form.get("consultant_name") or "").strip()
    if not consultant_name:
        return jsonify({"error": "Consultant is required."}), 400
    if not paths.get("specification"):
        return jsonify({"error": "Specification PDF is required."}), 400
    pending_id = str(uuid.uuid4())[:12]
    db.create_pending_review(pending_id, paths.get("specification", "contract_qa"))
    threading.Thread(target=_run_stage4_background,
                     args=(pending_id, paths, deal_code, consultant_name), daemon=True).start()
    return jsonify({"review_id": pending_id, "status": "processing"}), 202


@app.route("/api/stage4/submissions")
def api_stage4_submissions():
    """List all Contract QA Intelligence submissions."""
    _ensure_db()
    rows = db_v3_contract_qa.get_all_submissions()
    # Optional consultant scoping
    consultant = (request.args.get("consultant") or "").strip()
    if consultant:
        fn = _first_name(consultant)
        rows = [r for r in rows if _first_name(r.get("consultant_name")) == fn]
    return jsonify(rows)


@app.route("/api/stage4/submissions/<int:sub_id>")
def api_stage4_submission_detail(sub_id):
    """Get a single Contract QA submission with all findings."""
    _ensure_db()
    result = db_v3_contract_qa.get_submission(sub_id)
    if not result:
        return jsonify({"error": "Not found"}), 404
    return jsonify(result)


@app.route("/api/stage4/rules")
def api_stage4_rules():
    """Get all Contract QA Intelligence rules (with parameters)."""
    _ensure_db()
    domain = request.args.get("domain")
    category = request.args.get("category")
    severity = request.args.get("severity")
    active_only = request.args.get("active_only") == "1"
    rules = db_v3_contract_qa.get_all_rules_with_domain(
        domain=domain, category=category, severity=severity, active_only=active_only
    )
    return jsonify(rules)


@app.route("/api/stage4/rules/<int:rule_id>", methods=["PATCH"])
def api_stage4_update_rule(rule_id):
    """Update a Contract QA rule's parameters (admin only)."""
    _ensure_db()
    data = request.get_json() or {}
    if not _is_admin(data.get("code")):
        return jsonify({"error": "Admin access code required"}), 403
    conn = db.get_db()
    fields = {}
    for k in ("parameters", "severity", "active", "trigger_condition",
              "expected_outcome", "documents_checked", "automation_type"):
        if k in data:
            fields[k] = data[k]
    if not fields:
        return jsonify({"error": "No valid fields to update"}), 400
    # Build SET clause
    set_parts = []
    params = []
    for k, v in fields.items():
        if k == "parameters" and isinstance(v, dict):
            v = json.dumps(v)
        set_parts.append(f"{k}=?")
        params.append(v)
    # Increment version
    set_parts.append("version=version+1")
    params.append(rule_id)
    sql = f"UPDATE qa_rules SET {', '.join(set_parts)} WHERE id=?"
    if os.environ.get("DATABASE_URL"):
        import psycopg2, psycopg2.extras
        pg_sql = sql.replace("?", "%s")
        cur = conn.cursor()
        cur.execute(pg_sql, tuple(params))
    else:
        conn.execute(sql, tuple(params))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


# ===========================================================================
# LEARNING PANEL
# ===========================================================================
@app.route("/api/learning/false-positives")
def api_learning_fps():
    _ensure_db()
    days = request.args.get("days", default=30, type=int)
    return jsonify(db_v2.get_recent_false_positive_issues(days))


# ===========================================================================
# V1 — FEATURE FLAGS API
# ===========================================================================
@app.route("/api/feature-flags")
def api_feature_flags():
    """Return all feature flags. Admin only."""
    _ensure_db()
    if not _require_admin():
        return jsonify({"error": "Admin access required"}), 403
    return jsonify(db_v1_migrations.get_all_feature_flags())


@app.route("/api/feature-flags/<flag_name>", methods=["PATCH"])
def api_update_feature_flag(flag_name):
    """Toggle a feature flag. Admin only."""
    _ensure_db()
    if not _require_admin():
        return jsonify({"error": "Admin access required"}), 403
    data = request.get_json() or {}
    enabled = data.get("enabled")
    allowed_roles = data.get("allowed_roles")
    db_v1_migrations.update_feature_flag(flag_name, enabled=enabled, allowed_roles=allowed_roles)
    return jsonify({"status": "ok"})


@app.route("/api/feature-flags/check")
def api_check_feature_flag():
    """Check if a feature flag is enabled for the current user's role.
    Query params: flag_name, role"""
    _ensure_db()
    flag_name = request.args.get("flag_name", "")
    role = request.args.get("role", "")
    enabled = db_v1_migrations.is_feature_enabled(flag_name, user_role=role)
    return jsonify({"enabled": enabled, "flag_name": flag_name})


# ===========================================================================
# V1 — CONTRACT QA V1 (Rule-Based Quality Checks)
# ===========================================================================
def _run_contract_qa_v1_background(pending_id, paths, deal_code, consultant_name):
    """Background thread for Contract QA V1 rule execution."""
    try:
        result = contract_qa_v1_engine.run_contract_qa_v1(
            paths, deal_code=deal_code, consultant_name=consultant_name,
            progress_cb=lambda pct, msg: db.update_pending_progress(pending_id, pct, msg),
        )
        # Save as a contract_review with stage=4 (Contract QA V1)
        review_id = db_v2.save_contract_review({
            "deal_code": result.get("deal_code", ""),
            "stage": 4,
            "consultant_name": consultant_name,
            "job_category": "Contract QA V1",
            "verdict": result.get("verdict", ""),
            "verdict_reason": result.get("verdict_reason", ""),
            "result_payload": result,
            "issues": _v1_findings_to_issues(result.get("findings", [])),
        })
        result["contract_review_id"] = review_id
        # Save individual rule results
        for rr in result.get("results", []):
            try:
                db_v1_migrations.save_rule_result(
                    job_id=review_id,
                    rule_id=rr.get("rule_id", 0),
                    domain="Contract",
                    result=rr.get("result", "WARNING"),
                    severity=rr.get("severity", ""),
                    evidence_found=rr.get("evidence_found", ""),
                    evidence_expected=rr.get("evidence_expected", ""),
                    recommendation=rr.get("recommendation", ""),
                    confidence=rr.get("confidence", 0.0),
                    execution_time_ms=rr.get("execution_time_ms", 0),
                )
            except Exception:
                pass
        db.complete_pending_review(pending_id, json.dumps(result, default=str), review_id)
    except Exception as e:
        traceback.print_exc()
        db.fail_pending_review(pending_id, f"Contract QA V1 failed: {e}")
    finally:
        for p in paths.values():
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


def _v1_findings_to_issues(findings):
    """Convert V1 rule findings to the contract_issues format for DB storage."""
    issues = []
    for f in findings:
        issues.append({
            "issue_ref": f.get("rule_ref", ""),
            "severity": f.get("severity", ""),
            "category": f.get("category", ""),
            "section": f.get("category", ""),
            "signed_source": f.get("evidence_expected", ""),
            "contract_output": f.get("evidence_found", ""),
            "discrepancy": f.get("description", ""),
            "required_action": f.get("recommendation", ""),
            "status": "Open",
        })
    return issues


@app.route("/api/contract-qa-v1/review", methods=["POST"])
def api_contract_qa_v1_review():
    """Submit documents for Contract QA V1 rule-based review.
    Feature-flagged: returns 403 if contract_qa is disabled."""
    _ensure_db()
    # Check feature flag
    # Get user role from the request (form field or header)
    user_code = (request.form.get("user_code") or request.headers.get("X-User-Code") or "").strip().upper()
    user = users_db.get_user_by_code(user_code) if user_code else None
    user_role = user["role"] if user else ""
    if not db_v1_migrations.is_feature_enabled("contract_qa", user_role=user_role):
        return jsonify({"error": "Contract QA V1 is not enabled for your account. Contact your admin."}), 403

    paths = _save_stage_uploads("v1")
    deal_code = (request.form.get("deal_code") or "").strip()
    consultant_name = (request.form.get("consultant_name") or "").strip()
    if not consultant_name:
        return jsonify({"error": "Consultant is required."}), 400
    if not paths.get("specification") and not paths.get("nhp"):
        return jsonify({"error": "At least a Specification or NHP document is required."}), 400

    pending_id = str(uuid.uuid4())[:12]
    db.create_pending_review(pending_id, paths.get("specification", "contract_qa_v1"))
    threading.Thread(target=_run_contract_qa_v1_background,
                     args=(pending_id, paths, deal_code, consultant_name), daemon=True).start()
    return jsonify({"review_id": pending_id, "status": "processing"}), 202


@app.route("/api/contract-qa-v1/history")
def api_contract_qa_v1_history():
    """Return Contract QA V1 reviews (stage=4)."""
    _ensure_db()
    rows = db_v2.get_contract_reviews(stage=4)
    consultant = (request.args.get("consultant") or "").strip()
    if consultant:
        fn = _first_name(consultant)
        rows = [r for r in rows if _first_name(r.get("consultant_name")) == fn]
    for r in rows:
        r.pop("result_payload", None)
        r["display_type"] = "Contract QA V1"
    return jsonify(rows)


@app.route("/api/contract-qa-v1/score/<int:review_id>")
def api_contract_qa_v1_score(review_id):
    """Return the QA score for a Contract QA V1 job."""
    _ensure_db()
    return jsonify(db_v1_migrations.get_qa_score(review_id))


# ===========================================================================
# DOCS PAGE
# ===========================================================================
@app.route("/docs")
def docs_page():
    return render_template("docs.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
