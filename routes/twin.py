from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from datetime import datetime
import tempfile
import os
from services.database_service import (
    get_user_by_id, get_user_knowledge, create_conversation,
    add_message_to_conversation, get_conversation, get_user_conversations
)
from services.twin_service import (
    chat_with_twin, generate_twin_context, generate_vocabulary_style,
    generate_speech, transcribe_audio
)
import logging

logger = logging.getLogger(__name__)

twin_bp = Blueprint("twin", __name__)


@twin_bp.route("/chat")
def chat():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("chat.html", username=session.get("username"))


@twin_bp.route("/chat/history")
def chat_history():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    conversations = get_user_conversations(session["user_id"])
    clean = [{
        "id": str(c["_id"]),
        "mode": c["mode"],
        "message_count": len(c.get("messages", [])),
        "created_at": c["created_at"].isoformat(),
        "updated_at": c["updated_at"].isoformat(),
    } for c in conversations]
    return jsonify({"conversations": clean})


@twin_bp.route("/chat/conversation", methods=["POST"])
def new_conversation():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    data = request.get_json(force=True)
    mode = data.get("mode", "chat")
    conv_id = create_conversation(session["user_id"], mode)
    return jsonify({"conversation_id": conv_id})


@twin_bp.route("/chat/conversation/<conv_id>")
def get_conv(conv_id):
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    conv = get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "not found"}), 404
    messages = [{
        "role": m["role"],
        "content": m["content"],
        "audio_url": m.get("audio_url"),
        "created_at": m["created_at"].isoformat() if isinstance(m.get("created_at"), datetime) else m.get("created_at")
    } for m in conv.get("messages", [])]
    return jsonify({
        "id": str(conv["_id"]),
        "mode": conv["mode"],
        "messages": messages
    })


@twin_bp.route("/chat/send", methods=["POST"])
def send_message():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.get_json(force=True)
    conversation_id = data.get("conversation_id")
    user_message = data.get("message", "").strip()
    mode = data.get("mode", "chat")

    if not conversation_id or not user_message:
        return jsonify({"error": "conversation_id and message required"}), 400

    user = get_user_by_id(session["user_id"])
    user_knowledge = get_user_knowledge(session["user_id"])
    big_five = user.get("big_five") if user else None
    voice_sample = user.get("voice_sample") if user else None

    personality_context = generate_twin_context(user_knowledge, big_five, voice_sample)
    vocabulary_style = generate_vocabulary_style(user_knowledge)

    add_message_to_conversation(conversation_id, "user", user_message)

    conv = get_conversation(conversation_id)
    messages_for_llm = conv.get("messages", [])[:-1]

    logger.debug(f"send_message: conv_id={conversation_id}, mode={mode}, msg={user_message[:50]}")

    conv = get_conversation(conversation_id)
    logger.debug(f"Messages before LLM call: {len(conv.get('messages', []))}")

    try:
        twin_response = chat_with_twin(
            session["user_id"], conv.get("messages", []), mode,
            personality_context, vocabulary_style
        )
        logger.debug(f"Twin response: {twin_response[:100]}")
    except Exception as e:
        logger.error(f"Twin chat failed: {e}")
        return jsonify({"error": f"Twin response failed: {e}"}), 500

    add_message_to_conversation(conversation_id, "assistant", twin_response)

    return jsonify({
        "response": twin_response,
        "mode": mode
    })


@twin_bp.route("/chat/speak", methods=["POST"])
def speak_message():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    try:
        user = get_user_by_id(session["user_id"])
        voice_id = user.get("voice_id") if user else None
        audio_bytes = generate_speech(text, voice_id=voice_id)
        import base64
        audio_b64 = base64.b64encode(audio_bytes).decode()
        return jsonify({"audio": f"data:audio/mp3;base64,{audio_b64}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@twin_bp.route("/chat/transcribe", methods=["POST"])
def transcribe():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    if "audio" not in request.files:
        return jsonify({"error": "no audio file provided"}), 400

    audio_file = request.files["audio"]
    if not audio_file.filename:
        return jsonify({"error": "no audio file selected"}), 400

    suffix = os.path.splitext(audio_file.filename)[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        text = transcribe_audio(tmp_path)
    except Exception as e:
        os.unlink(tmp_path)
        return jsonify({"error": str(e)}), 500

    os.unlink(tmp_path)
    return jsonify({"text": text})