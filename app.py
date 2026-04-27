"""
AUSMAR PSE QA Agent — Production Flask Application
"""

import os
import json
import traceback
from flask import Flask, request, jsonify, render_template, send_file, send_from_directory

import database as db
from qa_engine import run_qa_review

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["CORRECTED_FOLDER"] = os.path.join(os.path.dirname(__file__), "corrected_zips")
app.config["PRELOG_FOLDER"] = os.path.join(os.path.dirname(__file__), "prelog_uploads")

for d in [app.config["UPLOAD_FOLDER"], app.config["CORRECTED_FOLDER"], app.config["PRELOG_FOLDER"]]:
    os.makedirs(d, exist_ok=True)

# Initialize database
db.init_db()


# ---- Pages ----
@app.route("/")
def index():
    return render_template("index.html")


# ---- Review API ----
@app.route("/api/review", methods=["POST"])
def api_review():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if not file.filename.endswith(".zip"):
        return jsonify({"error": "File must be a .zip"}), 400

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(filepath)

    try:
        result = run_qa_review(filepath, file.filename, app.config["CORRECTED_FOLDER"])

        if "error" in result and not result.get("checks"):
            return jsonify(result), 500

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
            "consultant_name": result.get("consultant_name", ""),
            "prelog_id": result.get("prelog_id"),
        }
        review_id = db.save_review(review_data)

        # Mark prelog as matched
        if result.get("prelog_id"):
            db.mark_prelog_matched(result["prelog_id"], review_id)

        result["review_id"] = review_id
        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Review failed: {str(e)}"}), 500
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)


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


@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_review_stats())


# ---- Feedback ----
@app.route("/api/feedback", methods=["POST"])
def api_feedback():
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


# ---- Pre-logs ----
@app.route("/api/prelogs")
def api_prelogs():
    return jsonify(db.get_all_prelogs())


@app.route("/api/prelogs", methods=["POST"])
def api_add_prelog():
    data = request.form.to_dict() if request.form else request.json or {}
    if not data.get("deal_code"):
        return jsonify({"error": "deal_code required"}), 400

    # Handle file uploads
    saved_files = []
    if request.files:
        for key in request.files:
            f = request.files[key]
            if f.filename:
                save_path = os.path.join(app.config["PRELOG_FOLDER"], f"{data['deal_code']}_{f.filename}")
                f.save(save_path)
                saved_files.append(f.filename)

    prelog_data = {
        "deal_code": data.get("deal_code", ""),
        "consultant_name": data.get("consultant_name", ""),
        "deposit_amount": float(data.get("deposit_amount", 0)),
        "customer_names": data.get("customer_names", ""),
        "notes": data.get("notes", ""),
        "files": saved_files,
    }
    prelog_id = db.save_prelog(prelog_data)
    return jsonify({"status": "ok", "prelog_id": prelog_id})


@app.route("/api/prelogs/<int:prelog_id>")
def api_prelog_detail(prelog_id):
    prelog = db.get_prelog(prelog_id)
    if not prelog:
        return jsonify({"error": "Pre-log not found"}), 404
    return jsonify(prelog)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
