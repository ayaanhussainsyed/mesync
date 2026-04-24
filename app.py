from flask import Flask, jsonify
import logging
import traceback
from config import Config
from services.embedding_service import embed

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

from routes.auth import auth_bp
from routes.onboarding import onboarding_bp
from routes.dashboard import dashboard_bp
from routes.rag import rag_bp
from routes.journal import journal_bp
from routes.twin import twin_bp
from routes.decision import decision_bp
from routes.graph import graph_bp
from routes.account import account_bp
from routes.drift import drift_bp
from routes.letters import letters_bp
from routes.integrations import integrations_bp
from routes.behavior_roots import behavior_roots_bp

app.register_blueprint(auth_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(rag_bp)
app.register_blueprint(journal_bp)
app.register_blueprint(twin_bp)
app.register_blueprint(decision_bp)
app.register_blueprint(graph_bp)
app.register_blueprint(account_bp)
app.register_blueprint(drift_bp)
app.register_blueprint(letters_bp)
app.register_blueprint(integrations_bp)
app.register_blueprint(behavior_roots_bp)


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


if __name__ == "__main__":
    app.run(debug=True, port=5000)