from flask import Blueprint, session, jsonify, request
import base64
from bson import ObjectId
from services.database_service import get_user_by_id, users_col
from services.elevenlabs_service import delete_voice, save_voice_id
from services.twin_service import generate_speech

account_bp = Blueprint("account", __name__)


@account_bp.route("/account/delete-voice", methods=["POST"])
def delete_voice_route():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    user = get_user_by_id(session["user_id"])
    voice_id = user.get("voice_id") if user else None

    if voice_id:
        delete_voice(voice_id)
        users_col.update_one(
            {"_id": ObjectId(session["user_id"])},
            {"$set": {"voice_id": None}}
        )

    return jsonify({"ok": True})


@account_bp.route("/account/test-voice", methods=["POST"])
def test_voice():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    user = get_user_by_id(session["user_id"])
    voice_id = user.get("voice_id") if user else None

    test_text = (
        "Hey, it's me — your twin. This is what I sound like. "
        "Every time we talk, it'll be in this voice. Kind of strange, right? "
        "But also kind of cool."
    )

    try:
        audio_bytes = generate_speech(test_text, voice_id=voice_id)
        audio_b64 = base64.b64encode(audio_bytes).decode()
        return jsonify({"audio": f"data:audio/mp3;base64,{audio_b64}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@account_bp.route("/account/save-voice-id", methods=["POST"])
def save_voice_id_route():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.get_json(force=True)
    voice_id = (data.get("voice_id") or "").strip()
    if not voice_id:
        return jsonify({"error": "voice_id required"}), 400

    save_voice_id(session["user_id"], voice_id)
    return jsonify({"ok": True})


@account_bp.route("/account/debug-voice", methods=["GET"])
def debug_voice():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    user = get_user_by_id(session["user_id"])
    db_voice_id = user.get("voice_id") if user else None
    return jsonify({"db_voice_id": db_voice_id})