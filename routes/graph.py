from flask import Blueprint, render_template, session, redirect, url_for, jsonify
from services.database_service import get_semantic_graph
from services.semantic_graph_service import get_graph_summary

graph_bp = Blueprint("graph", __name__)


@graph_bp.route("/graph")
def graph_page():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("graph.html", username=session.get("username"))


@graph_bp.route("/graph/data")
def graph_data():
    if "user_id" not in session:
        return jsonify({"error": "not logged in"}), 401
    graph = get_semantic_graph(session["user_id"])
    if not graph:
        return jsonify({"nodes": [], "edges": [], "summary": "No data yet"})
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    summary = get_graph_summary(nodes, edges)
    return jsonify({
        "nodes": nodes,
        "edges": edges,
        "summary": summary
    })