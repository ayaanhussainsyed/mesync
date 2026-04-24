from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
import logging
from datetime import datetime
from bson import ObjectId
from services.database_service import get_user_by_id, update_user_onboarding
from services.embedding_service import embed, compute_big_five

logger = logging.getLogger(__name__)

onboarding_bp = Blueprint("onboarding", __name__)

CHOICE_LABELS = {
    "q1": {
        5: "looks for the loudest group and joins in",
        4: "strikes up a chat with whoever's closest",
        3: "waits for someone to approach first",
        2: "finds a quiet corner and observes",
        1: "regrets showing up in the first place",
    },
    "q2": {
        5: "thinks 'no worries, they must have a reason'",
        4: "feels mildly annoyed but understands",
        3: "says it's fine but will remember it",
        2: "wonders why this always happens to them",
        1: "feels something must be wrong with them",
    },
    "q3": {
        5: "goes somewhere they've never been before",
        4: "tries a new restaurant or activity nearby",
        3: "does something familiar but with friends",
        2: "sticks to their usual routine",
        1: "does nothing because plans feel exhausting",
    },
    "q4": {
        5: "an immaculate workspace with a place for everything",
        4: "mostly tidy with a few piles",
        3: "organized chaos where they know where things are",
        2: "a mess they plan to clean eventually",
        1: "a state they'd rather not talk about",
    },
}

QUESTION_TYPES = {
    "q1": "trait_social",
    "q2": "trait_reaction",
    "q3": "trait_preference",
    "q4": "trait_organization",
    "q5": "trait_rumination",
    "q6": "energizers",
    "q7": "value_hierarchy",
    "q8": "belief",
    "q9": "decision",
    "q10": "self_message",
    "q12": "voice_sample",
}
OPEN_ENDED = {"q8", "q9", "q10", "q12"}


def render_answer_text(qid: str, question: str, value) -> str:
    if qid in CHOICE_LABELS:
        label = CHOICE_LABELS[qid].get(int(value), str(value))
        return f"When asked '{question}', they answered: {label}."
    if qid == "q5":
        band = (
            "rarely" if value <= 3
            else "sometimes" if value <= 6
            else "frequently" if value <= 8
            else "almost constantly"
        )
        return f"They {band} overthink decisions (self-rated {value}/10)."
    if qid == "q6":
        if isinstance(value, list):
            return f"They feel energized by: {', '.join(value)}."
        return f"They feel energized by: {value}."
    if qid == "q7":
        if isinstance(value, list):
            return f"When it comes to what matters most, they rank: {', '.join(value)}."
        return f"When it comes to what matters most, they rank: {value}."
    return str(value)


@onboarding_bp.route("/onboarding")
def onboarding():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("onboarding.html", username=session.get("username"))


@onboarding_bp.route("/onboarding/submit", methods=["POST"])
def onboarding_submit():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    user_id = session["user_id"]
    now = datetime.utcnow()
    knowledge_entries = []
    voice_id = None

    content_type = request.content_type or ""
    data = request.get_json(force=True) if "application/json" in content_type else None
    answers = data.get("answers", {}) if data else {}

    logger.info(f"onboarding_submit for user_id={user_id}")

    for qid, value in answers.items():
        if qid in ("q11", "q12"):
            continue

        question = f"Question {qid}"

        if qid in OPEN_ENDED:
            text = value.strip() if isinstance(value, str) else str(value)
        elif qid in ("q6", "q7"):
            text = render_answer_text(qid, question, value)
        else:
            text = render_answer_text(qid, question, value)

        try:
            vec = embed(text)
        except Exception as e:
            print(f"Embedding failed for {qid}: {e}")
            return jsonify({
                "error": f"OpenAI embedding failed: {type(e).__name__}: {e}",
                "hint": "Check /test-openai to diagnose.",
            }), 500

        entry = {
            "id": qid,
            "source": "onboarding",
            "type": QUESTION_TYPES.get(qid, "unknown"),
            "question": question,
            "text": text,
            "embedding": vec,
            "created_at": now,
        }
        if qid not in OPEN_ENDED and qid not in ("q6", "q7"):
            entry["raw_value"] = value
        elif qid in ("q6", "q7"):
            entry["raw_value"] = value
        knowledge_entries.append(entry)

    voice_sample = answers.get("q12", "").strip() or None

    try:
        big_five = compute_big_five(answers)
        update_user_onboarding(user_id, big_five, knowledge_entries, voice_id=voice_id, voice_sample=voice_sample)
    except Exception as e:
        return jsonify({"error": f"Mongo write failed: {e}"}), 500

    return jsonify({"ok": True, "redirect": url_for("dashboard.dashboard")})
