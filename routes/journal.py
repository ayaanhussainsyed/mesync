from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from datetime import datetime
from bson import ObjectId
from services.database_service import get_user_by_id, add_knowledge_entry, delete_journal_entry as db_delete_journal_entry
from services.embedding_service import embed
from services.semantic_graph_service import extract_entities_and_relations, merge_graphs
from services.database_service import get_semantic_graph, upsert_semantic_graph

journal_bp = Blueprint("journal", __name__)


@journal_bp.route("/journal")
def journal():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("journal.html", username=session.get("username"))


@journal_bp.route("/journal/history")
def journal_history():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    from services.database_service import get_journal_entries
    limit = int(request.args.get("limit", 20))
    skip = int(request.args.get("skip", 0))
    entries = get_journal_entries(session["user_id"], limit=limit, skip=skip)
    clean = [{
        "id": str(e["_id"]),
        "text": e["text"],
        "mood": e.get("mood"),
        "source": e.get("source"),
        "created_at": e["created_at"].isoformat()
    } for e in entries]
    return jsonify({"entries": clean})


@journal_bp.route("/journal/entry", methods=["POST"])
def journal_entry():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    mood = data.get("mood")
    source = data.get("source", "journal")

    if not text:
        return jsonify({"error": "text is required"}), 400

    try:
        vec = embed(text)
    except Exception as e:
        return jsonify({"error": f"Embedding failed: {e}"}), 500

    from services.database_service import create_journal_entry
    entry_id = create_journal_entry(session["user_id"], text, mood, source, vec)

    knowledge_entry = {
        "source": source,
        "type": "journal_entry",
        "text": text,
        "embedding": vec,
        "mood": mood,
    }
    add_knowledge_entry(session["user_id"], knowledge_entry)

    try:
        extracted = extract_entities_and_relations(text)
        existing = get_semantic_graph(session["user_id"])
        if existing:
            merged = merge_graphs(existing, extracted)
        else:
            merged = extracted
        upsert_semantic_graph(session["user_id"], merged.get("nodes", []), merged.get("edges", []))
    except Exception as e:
        print(f"Graph update failed: {e}")

    return jsonify({"ok": True, "entry_id": entry_id})


@journal_bp.route("/journal/voice", methods=["POST"])
def journal_voice():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    if "audio" not in request.files:
        return jsonify({"error": "no audio file"}), 400

    audio_file = request.files["audio"]
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        from services.twin_service import transcribe_audio
        text = transcribe_audio(tmp_path)
    except Exception as e:
        os.unlink(tmp_path)
        return jsonify({"error": f"Transcription failed: {e}"}), 500
    finally:
        os.unlink(tmp_path)

    if not text or len(text.strip()) < 3:
        return jsonify({"error": "Could not understand audio"}), 400

    try:
        vec = embed(text)
    except Exception as e:
        return jsonify({"error": f"Embedding failed: {e}"}), 500

    from services.database_service import create_journal_entry
    entry_id = create_journal_entry(session["user_id"], text, None, "voice_note", vec)

    knowledge_entry = {
        "source": "voice_note",
        "type": "journal_entry",
        "text": text,
        "embedding": vec,
    }
    add_knowledge_entry(session["user_id"], knowledge_entry)

    try:
        extracted = extract_entities_and_relations(text)
        existing = get_semantic_graph(session["user_id"])
        if existing:
            merged = merge_graphs(existing, extracted)
        else:
            merged = extracted
        upsert_semantic_graph(session["user_id"], merged.get("nodes", []), merged.get("edges", []))
    except Exception as e:
        print(f"Graph update failed: {e}")

    return jsonify({"ok": True, "entry_id": entry_id, "transcribed": text})


@journal_bp.route("/journal/entry/<entry_id>", methods=["DELETE"])
def delete_entry(entry_id):
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    try:
        ok = db_delete_journal_entry(session["user_id"], entry_id)
    except Exception as e:
        return jsonify({"error": f"Delete failed: {e}"}), 500
    if not ok:
        return jsonify({"error": "Entry not found"}), 404
    return jsonify({"ok": True})