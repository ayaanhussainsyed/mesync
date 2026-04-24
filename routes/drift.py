from datetime import datetime, timedelta
from collections import Counter

from flask import Blueprint, render_template, session, redirect, url_for, jsonify

from services.database_service import get_user_by_id, get_journal_entries

drift_bp = Blueprint("drift", __name__)

MOODS = ["great", "good", "neutral", "low", "rough"]
MOOD_WEIGHT = {"great": 2, "good": 1, "neutral": 0, "low": -1, "rough": -2}


@drift_bp.route("/drift")
def drift_page():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("drift.html", username=session.get("username"))


@drift_bp.route("/drift/data")
def drift_data():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401

    user = get_user_by_id(session["user_id"])
    entries = get_journal_entries(session["user_id"], limit=500)  # newest first

    now = datetime.utcnow()
    recent_cutoff = now - timedelta(days=30)

    recent = [e for e in entries if e.get("created_at") and e["created_at"] >= recent_cutoff]
    older  = [e for e in entries if e.get("created_at") and e["created_at"] <  recent_cutoff]

    def mood_dist(items):
        counts = Counter()
        for it in items:
            m = it.get("mood")
            if m in MOODS:
                counts[m] += 1
        total = sum(counts.values()) or 1
        return {m: round(100 * counts.get(m, 0) / total) for m in MOODS}, sum(counts.values())

    recent_dist, recent_mood_count = mood_dist(recent)
    older_dist,  older_mood_count  = mood_dist(older)

    def wellbeing_score(items):
        weighted = [MOOD_WEIGHT[m] for m in (it.get("mood") for it in items) if m in MOOD_WEIGHT]
        if not weighted: return None
        return round(sum(weighted) / len(weighted), 2)

    recent_wb = wellbeing_score(recent)
    older_wb  = wellbeing_score(older)

    # Cadence: entries per ISO week over the last 8 weeks.
    cadence = []
    for i in range(7, -1, -1):
        start = now - timedelta(days=(i + 1) * 7)
        end   = now - timedelta(days=i * 7)
        n = sum(1 for e in entries if e.get("created_at") and start <= e["created_at"] < end)
        cadence.append({"week": f"{i}w ago" if i > 0 else "this week", "count": n})

    # Word volume: avg chars per entry, recent vs older
    def avg_len(items):
        if not items: return 0
        return round(sum(len(it.get("text", "")) for it in items) / len(items))

    recent_len = avg_len(recent)
    older_len  = avg_len(older)

    big_five = (user or {}).get("big_five") or {}

    return jsonify({
        "mood": {
            "recent": recent_dist, "recent_count": recent_mood_count,
            "older":  older_dist,  "older_count":  older_mood_count,
        },
        "wellbeing": {"recent": recent_wb, "older": older_wb},
        "cadence": cadence,
        "length":  {"recent": recent_len, "older": older_len},
        "big_five": big_five,
        "totals": {
            "entries": len(entries),
            "recent_entries": len(recent),
            "older_entries": len(older),
        },
    })
