from flask import Blueprint, jsonify, request, session
import numpy as np
from services.database_service import get_user_knowledge, add_knowledge_entry
from services.embedding_service import embed

rag_bp = Blueprint("rag", __name__)


@rag_bp.route("/rag/query")
def rag_query():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "pass ?q=your+query"}), 400
    results = rag_retrieve(session["user_id"], q, top_k=5)
    clean = [{k: v for k, v in r.items() if k != "embedding"} for r in results]
    return jsonify({"query": q, "results": clean})


def rag_retrieve(user_id: str, query: str, top_k: int = 5):
    knowledge = get_user_knowledge(user_id)
    if not knowledge:
        return []

    query_vec = np.array(embed(query))
    query_vec /= np.linalg.norm(query_vec)

    scored = []
    for k in knowledge:
        if not k.get("embedding"):
            continue
        v = np.array(k["embedding"])
        v /= np.linalg.norm(v)
        scored.append((float(np.dot(query_vec, v)), k))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"score": s, **k} for s, k in scored[:top_k]]


@rag_bp.route("/rag/add", methods=["POST"])
def rag_add():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    entry_type = data.get("type", "manual")
    if not text:
        return jsonify({"error": "text is required"}), 400
    try:
        vec = embed(text)
        entry = {
            "source": "manual",
            "type": entry_type,
            "text": text,
            "embedding": vec,
        }
        add_knowledge_entry(session["user_id"], entry)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500