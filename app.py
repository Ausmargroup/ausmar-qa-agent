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

for d in [app.config["UPLOAD_FOLDER"], app.config["CORRECTED_FOLDER"], app.config["PRELOG_FOLDER"]]:
    os.makedirs(d, exist_ok=True)

# Lazy DB init — called on first real request so a locked/corrupt Volume never blocks startup.
_db_ready = False

def _ensure_db():
    global _db_ready
    if not _db_ready:
        try:
            db.init_db()
            _db_ready = True
        except Exception as e:
            import sys, traceback
            traceback.print_exc(file=sys.stderr)
            raise RuntimeError(f"Database init failed: {e}") from e


# ---- Health (no DB touch — used by Railway healthcheck) ----
@app.route("/health")
def health():
    return "ok", 200


def _run_review_background(pending_id, filepath, filename, corrected_folder, consultant_name="", consultant_email="", notes=""):
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
        review_data = {
            "deal_code": result.get("deal_code", ""),
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
            "consultant_name": consultant_name or result.get("consultant_name", ""),
            "prelog_id": result.get("prelog_id"),
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

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(filepath)

    # Consultant identity from access code (sent as form fields)
    consultant_name = request.form.get("consultant_name", "").strip()
    consultant_email = request.form.get("consultant_email", "").strip()

    # Create a pending review ID and return immediately
    pending_id = str(uuid.uuid4())[:12]
    db.create_pending_review(pending_id, file.filename)

    # Extract notes (Pre-Log info) from form
    notes = request.form.get("notes", "").strip()

    # Launch background thread
    t = threading.Thread(
        target=_run_review_background,
        args=(pending_id, filepath, file.filename, app.config["CORRECTED_FOLDER"], consultant_name, consultant_email, notes),
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
@app.route("/api/reviews")
def api_reviews():
    reviews = db.get_all_reviews()
    # Strip large check_results for list view
    for r in reviews:
        r.pop("check_results", None)
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


@app.route("/api/plans", methods=["POST"])
def api_add_plan():
    data = request.json
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
    db.delete_plan(plan_id)
    return jsonify({"status": "ok"})

@app.route("/api/plans/<int:plan_id>", methods=["PATCH"])
def api_update_plan(plan_id):
    _ensure_db()
    data = request.get_json() or {}
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
    _ensure_db()
    data = request.get_json() or {}
    allowed = ["consultant_name", "notes", "deposit_amount", "deal_code"]
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400
    conn = db.get_db()
    if os.environ.get("DATABASE_URL"):
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

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
