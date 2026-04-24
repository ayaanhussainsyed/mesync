from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from services.database_service import get_user_by_username, create_user, reset_onboarding
from werkzeug.security import generate_password_hash, check_password_hash

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Please enter username and password", "error")
            return render_template("login.html")

        user = get_user_by_username(username)

        if user and check_password_hash(user["password"], password):
            session["user_id"] = str(user["_id"])
            session["username"] = user["username"]
            if user.get("onboarding_complete"):
                return redirect(url_for("dashboard.dashboard"))
            return redirect(url_for("onboarding.onboarding"))

        flash("Invalid username or password", "error")
        return render_template("login.html")

    if "user_id" in session:
        return redirect(url_for("onboarding.onboarding"))
    return render_template("login.html")


@auth_bp.route("/sign-up", methods=["GET", "POST"])
def sign_up():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Please enter username and password", "error")
            return render_template("sign-up.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters", "error")
            return render_template("sign-up.html")

        if get_user_by_username(username):
            flash("Username already taken", "error")
            return render_template("sign-up.html")

        user_id = create_user(username, generate_password_hash(password))
        session["user_id"] = user_id
        session["username"] = username
        return redirect(url_for("onboarding.onboarding"))

    return render_template("sign-up.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/dev/reset-onboarding")
def dev_reset_onboarding():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    reset_onboarding(session["user_id"])
    return redirect(url_for("onboarding.onboarding"))