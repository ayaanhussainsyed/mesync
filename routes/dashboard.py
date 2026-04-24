from flask import Blueprint, render_template, session, redirect, url_for, jsonify
from services.database_service import get_user_by_id, reset_onboarding

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user = get_user_by_id(session["user_id"])
    return render_template("dashboard.html", username=session.get("username"), user=user)


@dashboard_bp.route("/dev/reset-onboarding-api", methods=["POST"])
def dev_reset_onboarding_api():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    reset_onboarding(session["user_id"])
    return jsonify({"ok": True})