import threading
from datetime import datetime, timedelta

from flask import Blueprint, render_template, session, redirect, url_for, jsonify

from services.database_service import (
    get_user_by_id, get_user_knowledge,
    create_letter, get_user_letters, get_letter,
    mark_letter_read, get_last_letter_time,
    count_unread_letters, delete_letter,
)
from services.twin_service import generate_future_letter

letters_bp = Blueprint("letters", __name__)

# Auto-send cadence. Tweak freely.
AUTO_INTERVAL = timedelta(days=3)
MIN_KNOWLEDGE_FOR_AUTO = 2  # don't auto-send letters if the twin barely knows the user


def _should_auto_generate(user_id: str) -> bool:
    last = get_last_letter_time(user_id)
    if last is None:
        return True
    return (datetime.utcnow() - last) >= AUTO_INTERVAL


def _generate_and_store(user_id: str, trigger: str = "auto") -> str | None:
    user = get_user_by_id(user_id)
    if not user:
        return None
    knowledge = get_user_knowledge(user_id)
    if trigger == "auto" and len(knowledge) < MIN_KNOWLEDGE_FOR_AUTO:
        return None
    big_five = user.get("big_five")
    voice_sample = user.get("voice_sample")
    try:
        letter = generate_future_letter(knowledge, big_five, voice_sample)
    except Exception as e:
        print(f"[letters] generation failed: {e}")
        return None
    return create_letter(user_id, letter["subject"], letter["content"], trigger=trigger)


def _generate_in_background(user_id: str):
    """Fire-and-forget wrapper used during page visits so the UI stays fast."""
    try:
        _generate_and_store(user_id, trigger="auto")
    except Exception as e:
        print(f"[letters] background generation error: {e}")


@letters_bp.route("/letters")
def letters_page():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("letters.html", username=session.get("username"))


@letters_bp.route("/letters/data")
def letters_data():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    user_id = session["user_id"]
    # Kick off a background generation if one is due. The user sees existing letters
    # immediately and can poll /letters/data again to discover the new one.
    if _should_auto_generate(user_id):
        threading.Thread(
            target=_generate_in_background, args=(user_id,), daemon=True
        ).start()

    letters = get_user_letters(user_id)
    clean = [{
        "id": str(l["_id"]),
        "subject": l.get("subject") or "A letter from later",
        "content": l.get("content") or "",
        "created_at": l["created_at"].isoformat(),
        "read_at": l["read_at"].isoformat() if l.get("read_at") else None,
        "trigger": l.get("trigger", "auto"),
    } for l in letters]

    last = get_last_letter_time(user_id)
    next_due_at = None
    if last:
        next_due_at = (last + AUTO_INTERVAL).isoformat()

    return jsonify({
        "letters": clean,
        "unread": count_unread_letters(user_id),
        "next_due_at": next_due_at,
        "auto_interval_days": AUTO_INTERVAL.days,
    })


@letters_bp.route("/letters/generate", methods=["POST"])
def letters_generate():
    """User asked for a letter on demand."""
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    letter_id = _generate_and_store(session["user_id"], trigger="manual")
    if not letter_id:
        return jsonify({"error": "Could not generate a letter."}), 500
    return jsonify({"ok": True, "id": letter_id})


@letters_bp.route("/letters/<letter_id>/read", methods=["POST"])
def letters_read(letter_id):
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    mark_letter_read(letter_id, session["user_id"])
    return jsonify({"ok": True})


@letters_bp.route("/letters/<letter_id>", methods=["DELETE"])
def letters_delete(letter_id):
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    ok = delete_letter(letter_id, session["user_id"])
    return jsonify({"ok": ok})


@letters_bp.route("/letters/unread_count")
def letters_unread_count():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    return jsonify({"count": count_unread_letters(session["user_id"])})
