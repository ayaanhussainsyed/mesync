from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from services.database_service import (
    get_user_by_id, get_user_knowledge, create_decision, get_user_decisions, get_decision
)
from services.twin_service import simulate_decision

decision_bp = Blueprint("decision", __name__)


@decision_bp.route("/decisions")
def decisions_page():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("decisions.html", username=session.get("username"))


@decision_bp.route("/decisions/history")
def decisions_history():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    decisions = get_user_decisions(session["user_id"])
    clean = [{
        "id": str(d["_id"]),
        "title": d.get("title", "Untitled"),
        "description": d.get("description", ""),
        "created_at": d["created_at"].isoformat()
    } for d in decisions]
    return jsonify({"decisions": clean})


@decision_bp.route("/decisions/<decision_id>")
def get_dec(decision_id):
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    d = get_decision(decision_id)
    if not d:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": str(d["_id"]),
        "title": d.get("title"),
        "description": d.get("description"),
        "branches": d.get("branches", []),
        "created_at": d["created_at"].isoformat()
    })


@decision_bp.route("/decisions/simulate", methods=["POST"])
def simulate():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()

    if not description:
        return jsonify({"error": "description required"}), 400

    user = get_user_by_id(session["user_id"])
    user_knowledge = get_user_knowledge(session["user_id"])
    big_five = user.get("big_five") if user else None

    try:
        result = simulate_decision(session["user_id"], description, user_knowledge, big_five)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    decision_id = create_decision(
        session["user_id"],
        title or "Untitled Decision",
        description,
        result
    )

    return jsonify({
        "ok": True,
        "decision_id": decision_id,
        "branches": result,
        "summary": result.get("summary", "")
    })