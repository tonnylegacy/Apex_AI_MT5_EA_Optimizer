"""
app.py — MT5 EA Optimizer Web App
Double-click to launch. Browser opens automatically at http://localhost:5000
"""
import sys, os, threading, webbrowser, time
from pathlib import Path

# ── Make sure imports resolve from project root ───────────────────────────────
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from flask import Flask, render_template, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit

from optimizer_loop import OptimizerLoop

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__,
            template_folder="ui/templates",
            static_folder="ui/static")
app.config["SECRET_KEY"] = "mt5optimizer2024"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

REPORTS_DIR = BASE_DIR / "Reports"
REPORTS_DIR.mkdir(exist_ok=True)

# Global optimizer instance
optimizer: OptimizerLoop = None
optimizer_thread: threading.Thread = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    if optimizer is None:
        return jsonify({"state": "idle", "iteration": 0, "best_score": 0})
    return jsonify(optimizer.get_status())


@app.route("/api/start", methods=["POST"])
def start():
    global optimizer, optimizer_thread
    if optimizer and optimizer.running:
        return jsonify({"ok": False, "msg": "Already running"})
    
    data = request.get_json(silent=True) or {}
    optimizer = OptimizerLoop(
        config_path=str(BASE_DIR / "config.yaml"),
        socketio=socketio,
        reports_dir=REPORTS_DIR,
        auto_mode=data.get("auto", True),
    )
    optimizer_thread = threading.Thread(target=optimizer.run, daemon=True)
    optimizer_thread.start()
    return jsonify({"ok": True})


@app.route("/api/pause", methods=["POST"])
def pause():
    if optimizer:
        optimizer.toggle_pause()
        return jsonify({"ok": True, "paused": optimizer.paused})
    return jsonify({"ok": False})


@app.route("/api/stop", methods=["POST"])
def stop():
    if optimizer:
        optimizer.stop()
    return jsonify({"ok": True})


@app.route("/api/skip", methods=["POST"])
def skip():
    if optimizer:
        optimizer.skip_hypothesis()
    return jsonify({"ok": True})


@app.route("/api/history")
def history():
    if optimizer is None:
        return jsonify([])
    return jsonify(optimizer.score_history)


@app.route("/reports")
@app.route("/reports/")
def reports_index():
    """Reports browser page — fixes the 404 on the Reports button."""
    runs = []
    for run_dir in sorted(REPORTS_DIR.iterdir(), reverse=True) if REPORTS_DIR.exists() else []:
        summary = run_dir / "summary.json"
        if summary.exists():
            import json
            try:
                runs.append(json.loads(summary.read_text()))
            except Exception:
                pass
    return render_template("reports_index.html", runs=runs[:100])


@app.route("/reports/<path:filename>")
def reports_file(filename):
    """Serve individual report files (HTML, CSV, JSON)."""
    return send_from_directory(REPORTS_DIR, filename)


@app.route("/api/runs")
def runs_list():
    runs = []
    if REPORTS_DIR.exists():
        for run_dir in sorted(REPORTS_DIR.iterdir(), reverse=True):
            summary = run_dir / "summary.json"
            if summary.exists():
                import json
                try:
                    runs.append(json.loads(summary.read_text()))
                except Exception:
                    pass
    return jsonify(runs[:50])


# ── SocketIO events ───────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    if optimizer:
        emit("status_sync", optimizer.get_status())


# ── Launch ────────────────────────────────────────────────────────────────────

def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://localhost:5000")


if __name__ == "__main__":
    print("=" * 60)
    print("  MT5 EA Optimizer — Starting...")
    print("  Opening browser at http://localhost:5000")
    print("=" * 60)
    threading.Thread(target=open_browser, daemon=True).start()
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, use_reloader=False)
