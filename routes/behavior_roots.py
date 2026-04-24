from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from services.database_service import (
    get_user_by_id, get_user_knowledge, get_journal_entries,
    get_semantic_graph
)
from services.embedding_service import embed
from services.rag import rag_retrieve
from config import Config
import numpy as np
import json

behavior_roots_bp = Blueprint("behavior_roots", __name__)


@behavior_roots_bp.route("/behavior-roots")
def behavior_roots():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("behavior_roots.html", username=session.get("username"))


@behavior_roots_bp.route("/behavior-roots/analyze", methods=["POST"])
def analyze_behavior():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.get_json(force=True)
    behavior = data.get("behavior", "").strip()
    if not behavior:
        return jsonify({"error": "behavior text is required"}), 400

    user_id = session["user_id"]
    user = get_user_by_id(user_id)
    user_knowledge = get_user_knowledge(user_id)
    big_five = user.get("big_five") if user else None
    voice_sample = user.get("voice_sample") if user else None

    # RAG: pull most relevant memories for the behavior
    rag_results = rag_retrieve(user_id, behavior, top_k=12)
    rag_context = ""
    if rag_results:
        lines = []
        for r in rag_results:
            score = r.get("score", 0)
            text = r.get("text", "")
            source = r.get("source", "memory")
            lines.append(f"[relevance: {score:.2f}] [{source}] {text}")
        rag_context = "\n".join(lines)
    else:
        # Fallback: use most recent knowledge entries
        recent = user_knowledge[-20:] if user_knowledge else []
        rag_context = "\n".join(f"- {m.get('text', '')[:300]}" for m in recent if m.get("text"))

    # Fetch recent journal entries for additional context
    recent_journals = get_journal_entries(user_id, limit=10, skip=0)
    journal_context = ""
    if recent_journals:
        journal_context = "\n".join(
            f"- [{j.get('mood', 'unknown')}] {j.get('text', '')[:300]}"
            for j in recent_journals if j.get("text")
        )

    # Fetch semantic graph summary (nodes only, keep it brief)
    graph = get_semantic_graph(user_id)
    graph_summary = ""
    if graph and graph.get("nodes"):
        nodes = [n.get("label", n.get("id", "")) for n in graph["nodes"][:30]]
        graph_summary = "Known entities in your memory graph: " + ", ".join(nodes)

    # Build Big Five summary
    big_five_summary = ""
    if big_five:
        big_five_summary = (
            f"Personality traits: "
            f"Extraversion {big_five.get('extraversion', 50)}%, "
            f"Agreeableness {big_five.get('agreeableness', 50)}%, "
            f"Openness {big_five.get('openness', 50)}%, "
            f"Conscientiousness {big_five.get('conscientiousness', 50)}%, "
            f"Neuroticism {big_five.get('neuroticism', 50)}%."
        )

    # Build personality + voice context
    personality_context = _build_personality_context(user_knowledge, big_five, voice_sample)

    system_prompt = f"""You are a deeply empathetic reflective companion for the user. Your job is to help them understand the likely roots of a behavior they are exhibiting — not to judge, but to illuminate.

You have access to their personal data: journal entries, memories, personality profile, and a semantic memory graph. Use any and all of it to construct a thoughtful, compassionate analysis.

Be specific. Reference real memories, journal entries, or patterns from their data when possible. Avoid generic platitudes — the value is in personalized insight.

Your response should have the following structure and tone:
- Warm, conversational, like a wise friend who really knows them
- Use sections with clear labels so the user can scan or read deeply

Structure:
1. **What I noticed** — Briefly restate the behavior in your own words, showing you understood it.
2. **Threads from your life** — The most relevant memories, journal entries, or patterns from their data that connect to this behavior. Quote or paraphrase specific entries where possible.
3. **Likely root** — Your analysis of the deepest or earliest origin. Be honest but gentle.
4. **A reflection** — A compassionate closing thought or question to help them sit with this.

Keep the response focused and not overly long — 250 to 500 words. No bullet lists in the main body prose. No emojis."""

    user_prompt = f"""Behavior described by the user: "{behavior}"

---
Personality overview:
{personality_context}

---
Big Five profile:
{big_five_summary}

---
Semantic memory graph entities:
{graph_summary}

---
Most relevant memories / journal entries (ranked by relevance):
{rag_context}

---
Recent journal entries:
{journal_context}

---

Now analyze this behavior and share what you find."""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=Config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=Config.OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=900,
            temperature=0.75,
        )
        analysis = (response.choices[0].message.content or "").strip()
        return jsonify({"analysis": analysis})
    except Exception as e:
        return jsonify({"error": f"Analysis failed: {e}"}), 500


def _build_personality_context(user_knowledge, big_five, voice_sample):
    parts = []

    if big_five:
        parts.append(
            f"They score high in openness ({big_five.get('openness', 50)}%) and "
            f"extraversion ({big_five.get('extraversion', 50)}%), "
            f"with neuroticism at {big_five.get('neuroticism', 50)}%."
        )

    if voice_sample:
        lines = voice_sample.strip().split("\n")[:8]
        voice_lines = [l.strip()[:200] for l in lines if l.strip()]
        if voice_lines:
            parts.append("How they talk: " + " | ".join(voice_lines))

    recent_texts = [m.get("text", "") for m in user_knowledge[-10:] if m.get("text")]
    if recent_texts:
        combined = " ".join(recent_texts[:5])
        words = set(combined.lower().split())
        if len(words) > 200:
            parts.append("They tend to be articulate and reflective in their self-expression.")
        elif len(words) < 100:
            parts.append("They tend to be direct and concise in self-expression.")
        else:
            parts.append("They express themselves in a natural, conversational way.")

    if not parts:
        return "Not enough data yet to build a personality picture. Keep journaling."

    return " ".join(parts)