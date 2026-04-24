import secrets
from datetime import datetime

from flask import (
    Blueprint, render_template, session, redirect, url_for, request, jsonify, flash
)

from services.database_service import (
    get_all_integrations, get_integration, clear_integration
)
from services.integrations import spotify as sp_svc
from services.integrations import github as gh_svc
from services.integrations import whatsapp as wa_svc

integrations_bp = Blueprint("integrations", __name__)


def _require_login():
    return "user_id" in session


def _cleanup_integration(data: dict | None) -> dict | None:
    """Strip tokens before returning to the client."""
    if not data:
        return None
    sanitized = {k: v for k, v in data.items()
                 if k not in {"access_token", "refresh_token"}}
    for k, v in list(sanitized.items()):
        if isinstance(v, datetime):
            sanitized[k] = v.isoformat()
    return sanitized


@integrations_bp.route("/integrations")
def integrations_page():
    if not _require_login():
        return redirect(url_for("auth.login"))
    return render_template("integrations.html", username=session.get("username"))


@integrations_bp.route("/integrations/status")
def integrations_status():
    if not _require_login():
        return jsonify({"error": "not logged in"}), 401
    all_ = get_all_integrations(session["user_id"])
    wa_state = wa_svc.status()
    return jsonify({
        "spotify": {
            "configured": sp_svc.is_configured(),
            "connected": bool(all_.get("spotify")),
            **(_cleanup_integration(all_.get("spotify")) or {}),
        },
        "github": {
            "configured": gh_svc.is_configured(),
            "connected": bool(all_.get("github")),
            **(_cleanup_integration(all_.get("github")) or {}),
        },
        "whatsapp": {
            "configured": True,  # sidecar check happens in status()
            "connected": bool(wa_state.get("connected")),
            "bridge_error": wa_state.get("error"),
            "me": wa_state.get("me"),
            **(_cleanup_integration(all_.get("whatsapp")) or {}),
        },
    })


# =============== Spotify ===============

@integrations_bp.route("/integrations/spotify/connect")
def spotify_connect():
    if not _require_login():
        return redirect(url_for("auth.login"))
    if not sp_svc.is_configured():
        return "Spotify client ID/secret not configured. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.", 500
    state = secrets.token_urlsafe(24)
    session["spotify_oauth_state"] = state
    return redirect(sp_svc.authorize_url(state))


@integrations_bp.route("/integrations/spotify/callback")
def spotify_callback():
    if not _require_login():
        return redirect(url_for("auth.login"))
    expected = session.pop("spotify_oauth_state", None)
    if not expected or request.args.get("state") != expected:
        return "OAuth state mismatch", 400
    if request.args.get("error"):
        return f"Spotify auth error: {request.args.get('error')}", 400
    code = request.args.get("code")
    if not code:
        return "Missing code", 400
    try:
        tokens = sp_svc.exchange_code(code)
        profile = sp_svc.get_profile(tokens["access_token"])
        sp_svc.store_tokens(session["user_id"], tokens, profile)
    except Exception as e:
        return f"Spotify connect failed: {e}", 500
    return redirect(url_for("integrations.integrations_page"))


@integrations_bp.route("/integrations/spotify/sync", methods=["POST"])
def spotify_sync():
    if not _require_login():
        return jsonify({"error": "not logged in"}), 401
    try:
        count = sp_svc.sync(session["user_id"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "ingested": count})


@integrations_bp.route("/integrations/spotify/disconnect", methods=["POST"])
def spotify_disconnect():
    if not _require_login():
        return jsonify({"error": "not logged in"}), 401
    clear_integration(session["user_id"], "spotify")
    return jsonify({"ok": True})


# =============== GitHub ===============

@integrations_bp.route("/integrations/github/connect")
def github_connect():
    if not _require_login():
        return redirect(url_for("auth.login"))
    if not gh_svc.is_configured():
        return "GitHub client ID/secret not configured.", 500
    state = secrets.token_urlsafe(24)
    session["github_oauth_state"] = state
    return redirect(gh_svc.authorize_url(state))


@integrations_bp.route("/integrations/github/callback")
def github_callback():
    if not _require_login():
        return redirect(url_for("auth.login"))
    expected = session.pop("github_oauth_state", None)
    if not expected or request.args.get("state") != expected:
        return "OAuth state mismatch", 400
    if request.args.get("error"):
        return f"GitHub auth error: {request.args.get('error')}", 400
    code = request.args.get("code")
    if not code:
        return "Missing code", 400
    try:
        tokens = gh_svc.exchange_code(code)
        profile = gh_svc.get_profile(tokens["access_token"])
        gh_svc.store_tokens(session["user_id"], tokens, profile)
    except Exception as e:
        return f"GitHub connect failed: {e}", 500
    return redirect(url_for("integrations.integrations_page"))


@integrations_bp.route("/integrations/github/sync", methods=["POST"])
def github_sync():
    if not _require_login():
        return jsonify({"error": "not logged in"}), 401
    try:
        count = gh_svc.sync(session["user_id"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "ingested": count})


@integrations_bp.route("/integrations/github/disconnect", methods=["POST"])
def github_disconnect():
    if not _require_login():
        return jsonify({"error": "not logged in"}), 401
    clear_integration(session["user_id"], "github")
    return jsonify({"ok": True})


# =============== WhatsApp ===============

@integrations_bp.route("/integrations/whatsapp/qr")
def whatsapp_qr():
    if not _require_login():
        return jsonify({"error": "not logged in"}), 401
    return jsonify(wa_svc.qr())


@integrations_bp.route("/integrations/whatsapp/status")
def whatsapp_status():
    if not _require_login():
        return jsonify({"error": "not logged in"}), 401
    return jsonify(wa_svc.status())


@integrations_bp.route("/integrations/whatsapp/sync", methods=["POST"])
def whatsapp_sync():
    if not _require_login():
        return jsonify({"error": "not logged in"}), 401
    body = request.get_json(silent=True) or {}
    try:
        count = wa_svc.sync(
            session["user_id"],
            chat_limit=int(body.get("chats") or 15),
            per_chat_limit=int(body.get("per_chat") or 40),
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "ingested": count})


@integrations_bp.route("/integrations/whatsapp/logout", methods=["POST"])
def whatsapp_logout():
    if not _require_login():
        return jsonify({"error": "not logged in"}), 401
    result = wa_svc.logout()
    clear_integration(session["user_id"], "whatsapp")
    return jsonify(result)
