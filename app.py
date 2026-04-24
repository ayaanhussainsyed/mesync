from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from bson import ObjectId
from openai import OpenAI
import certifi
import traceback
import numpy as np

app = Flask(__name__)


OPENAI_API_KEY = ""
openai_client = OpenAI(api_key=OPENAI_API_KEY)


MONGO_URI = "mongodb+srv://greensync:LljysdQhhLFxyG5t@cluster0.y31xe.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=5000)

try:
    client.admin.command("ping")
    print("MongoDB connected")
except Exception as e:
    print("MongoDB connection failed:", e)

db = client["MeSync"]
users_col = db["user_data"]

try:
    users_col.create_index("username", unique=True)
except Exception as e:
    print("Index warning:", e)


# ============================================================
# CHOICE LABEL MAPS
# ------------------------------------------------------------
# For each multi-choice question, map the numeric value back to
# the natural-language label the user picked. We embed this text,
# not the number.
# ============================================================

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

def render_answer_text(qid: str, question: str, value) -> str:
    """Turn a choice/slider value into an embedding-worthy sentence."""
    if qid in CHOICE_LABELS:
        label = CHOICE_LABELS[qid].get(int(value), str(value))
        # frame as a statement about the user
        return f"When asked '{question}', they answered: {label}."
    if qid == "q5":
        # slider 1..10
        band = (
            "rarely" if value <= 3
            else "sometimes" if value <= 6
            else "frequently" if value <= 8
            else "almost constantly"
        )
        return f"They {band} overthink decisions (self-rated {value}/10)."
    return str(value)


# ============================================================
# HELPERS
# ============================================================

def embed(text: str):
    resp = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return resp.data[0].embedding


def compute_big_five(answers: dict) -> dict:
    q1 = int(answers.get("q1", 3))
    q2 = int(answers.get("q2", 3))
    q3 = int(answers.get("q3", 3))
    q4 = int(answers.get("q4", 3))
    q5 = int(answers.get("q5", 5))
    def to100(x, mx): return round((x / mx) * 100)
    return {
        "extraversion": to100(q1, 5),
        "agreeableness": to100(q2, 5),
        "openness": to100(q3, 5),
        "conscientiousness": to100(q4, 5),
        "neuroticism": to100(q5, 10),
    }


def add_knowledge(user_id: str, entry: dict):
    """Append one knowledge entry to a user's RAG store."""
    entry["created_at"] = datetime.utcnow()
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$push": {"knowledge": entry}}
    )


def rag_retrieve(user_id: str, query: str, top_k: int = 5):
    """Cosine-similarity retrieval over user.knowledge (in-memory, numpy)."""
    user = users_col.find_one({"_id": ObjectId(user_id)}, {"knowledge": 1})
    if not user or not user.get("knowledge"):
        return []

    query_vec = np.array(embed(query))
    query_vec /= np.linalg.norm(query_vec)

    scored = []
    for k in user["knowledge"]:
        if not k.get("embedding"):
            continue
        v = np.array(k["embedding"])
        v /= np.linalg.norm(v)
        scored.append((float(np.dot(query_vec, v)), k))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"score": s, **k} for s, k in scored[:top_k]]


# ============================================================
# DIAGNOSTIC
# ============================================================

@app.route("/test-openai")
def test_openai():
    try:
        vec = embed("hello world")
        return jsonify({"ok": True, "dims": len(vec), "sample": vec[:5]})
    except Exception as e:
        return jsonify({
            "ok": False,
            "error_type": type(e).__name__,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }), 500


# ============================================================
# AUTH
# ============================================================

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Please fill in both fields.", "error")
            return render_template("login.html")
        user = users_col.find_one({"username": username})
        if user and check_password_hash(user["password"], password):
            session["user_id"] = str(user["_id"])
            session["username"] = user["username"]
            if user.get("onboarding_complete"):
                return redirect(url_for("dashboard"))
            return redirect(url_for("onboarding"))
        flash("Invalid username or password.", "error")
        return render_template("login.html")

    if "user_id" in session:
        return redirect(url_for("onboarding"))
    return render_template("login.html")


@app.route("/sign-up", methods=["GET", "POST"])
def sign_up():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Please fill in both fields.", "error")
            return render_template("sign-up.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("sign-up.html")
        if users_col.find_one({"username": username}):
            flash("Username already taken.", "error")
            return render_template("sign-up.html")
        new_user = {
            "username": username,
            "password": generate_password_hash(password),
            "created_at": datetime.utcnow(),
            "onboarding_complete": False,
            "big_five": None,
            "knowledge": [],
        }
        result = users_col.insert_one(new_user)
        session["user_id"] = str(result.inserted_id)
        session["username"] = username
        return redirect(url_for("onboarding"))
    return render_template("sign-up.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return f"<h2>Welcome, {session['username']}!</h2><a href='/logout'>Logout</a>"


@app.route("/onboarding")
def onboarding():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("onboarding.html", username=session.get("username"))


# ============================================================
# ONBOARDING SUBMIT — embeds EVERY answer
# ============================================================

@app.route("/onboarding/submit", methods=["POST"])
def onboarding_submit():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.get_json(force=True)
    answers = data.get("answers", {})
    questions = data.get("questions", {})
    user_id = session["user_id"]

    QUESTION_TYPES = {
        "q1": "trait_social",
        "q2": "trait_reaction",
        "q3": "trait_preference",
        "q4": "trait_organization",
        "q5": "trait_rumination",
        "q6": "belief",
        "q7": "decision",
        "q8": "self_message",
    }
    OPEN_ENDED = {"q6", "q7", "q8"}

    # STEP 1: build the natural-language text for each answer + embed ALL of them
    knowledge_entries = []
    now = datetime.utcnow()

    for qid, value in answers.items():
        question = questions.get(qid, "")

        if qid in OPEN_ENDED:
            text = value.strip() if isinstance(value, str) else str(value)
        else:
            text = render_answer_text(qid, question, value)

        # embed every entry
        try:
            vec = embed(text)
            print(f"✅ Embedded {qid}: {text[:60]}...")
        except Exception as e:
            print(f"❌ Embedding failed for {qid}: {e}")
            print(traceback.format_exc())
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
        if qid not in OPEN_ENDED:
            entry["raw_value"] = value  # keep numeric score for big-five math
        knowledge_entries.append(entry)

    # STEP 2: atomic write — big_five + knowledge + onboarding_complete
    try:
        big_five = compute_big_five(answers)
        users_col.update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "big_five": big_five,
                    "onboarding_complete": True,
                    "updated_at": now,
                },
                "$push": {"knowledge": {"$each": knowledge_entries}},
            },
        )
    except Exception as e:
        return jsonify({"error": f"Mongo write failed: {e}"}), 500

    return jsonify({"ok": True, "redirect": url_for("dashboard")})


# ============================================================
# RAG DEMO
# ============================================================

@app.route("/rag/query")
def rag_query():
    """e.g. /rag/query?q=how+do+i+handle+conflict"""
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "pass ?q=your+query"}), 400
    results = rag_retrieve(session["user_id"], q, top_k=5)
    clean = [{k: v for k, v in r.items() if k != "embedding"} for r in results]
    return jsonify({"query": q, "results": clean})


if __name__ == "__main__":
    app.run(debug=True, port=5000)