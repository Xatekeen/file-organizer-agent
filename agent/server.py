"""
server.py — Lightweight Flask API that exposes agent status to the dashboard.
Runs in a background thread alongside the watchdog observer.
"""

from __future__ import annotations
import threading
from pathlib import Path

from flask import Flask, jsonify, request, make_response

from agent.agent import FileOrganizerAgent
from agent.storage import undo_last, get_recent_moves, reflect

app = Flask(__name__)


def _corsify(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.after_request
def add_cors(response):
    return _corsify(response)


@app.route("/api/status", methods=["GET", "OPTIONS"])
def status():
    if request.method == "OPTIONS":
        return _corsify(make_response("", 204))
    if not _agent:
        return jsonify({"error": "Agent not initialised"}), 503
    return jsonify(_agent.get_status())


@app.route("/api/events", methods=["GET", "OPTIONS"])
def events():
    if request.method == "OPTIONS":
        return _corsify(make_response("", 204))
    if not _agent:
        return jsonify([])
    limit = int(request.args.get("limit", 20))
    return jsonify(get_recent_moves(limit))


@app.route("/api/undo", methods=["POST", "OPTIONS"])
def undo():
    if request.method == "OPTIONS":
        return _corsify(make_response("", 204))
    n = int(request.json.get("n", 1))
    results = undo_last(n)
    return jsonify({"results": results})


@app.route("/api/reflect", methods=["GET", "OPTIONS"])
def reflect_route():
    if request.method == "OPTIONS":
        return _corsify(make_response("", 204))
    suggestions = reflect()
    return jsonify({"suggestions": suggestions})


# Shared agent instance (set by main.py)
_agent: FileOrganizerAgent | None = None


def set_agent(agent: FileOrganizerAgent):
    global _agent
    _agent = agent


def run_server(port: int = 5050):
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def start_server_thread(port: int = 5050) -> threading.Thread:
    t = threading.Thread(target=run_server, args=(port,), daemon=True)
    t.start()
    return t
