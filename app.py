"""
app.py — MT5 Smart EA Optimizer Web App
Routes:
  /           → Landing page
  /setup      → Configure new optimization session
  /dashboard  → Live optimization dashboard
  /reports    → Past runs browser
"""
import sys, os, threading, webbrowser, time
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from flask import Flask, render_template, jsonify, request, send_from_directory, redirect
from flask_socketio import SocketIO, emit

from optimizer.pipeline import OptimizationPipeline
from optimizer.session_config import SessionConfig

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__,
            template_folder="ui/templates",
            static_folder="ui/static")
app.config["SECRET_KEY"] = "mt5optimizer2024"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

REPORTS_DIR = BASE_DIR / "Reports"
REPORTS_DIR.mkdir(exist_ok=True)

# Global pipeline instance
pipeline: OptimizationPipeline = None
pipeline_thread: threading.Thread = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/setup")
def setup():
    """Setup page: EA selector, dates, budget, objective."""
    import yaml
    from ea.registry import EARegistry
    try:
        reg  = EARegistry(str(BASE_DIR / "config.yaml"))
        eas  = reg.list_all()
        default_ea = eas[0].name if eas else "LEGSTECH_EA_V2"

        # Load param list for the first EA (or selected)
        ea_name = request.args.get("ea", default_ea)
        profile = reg.get(ea_name)
        schema  = reg.get_schema(profile, apply_optimize_selection=False)
        params  = [p for p in schema.all_params() if p.type != "fixed"]
    except Exception as e:
        eas     = []
        default_ea = "LEGSTECH_EA_V2"
        params  = []

    return render_template("setup.html",
                           registered_eas=eas,
                           default_ea=default_ea,
                           params=params)


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


# Keep old / redirect for muscle memory
@app.route("/index")
def old_index():
    return redirect("/dashboard")


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def status():
    if pipeline is None:
        return jsonify({"state": "idle", "run_count": 0, "total_runs": 0,
                        "best_score": 0, "phase": "idle"})
    return jsonify(pipeline.get_status())


@app.route("/api/start", methods=["POST"])
def start():
    global pipeline, pipeline_thread

    if pipeline and pipeline.running:
        return jsonify({"ok": False, "msg": "Optimization already running"})

    data = request.get_json(silent=True) or {}

    try:
        session = SessionConfig.from_dict(data)
        session.derive_samples()
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Invalid config: {e}"})

    pipeline = OptimizationPipeline(
        config_path=str(BASE_DIR / "config.yaml"),
        socketio=socketio,
        reports_dir=REPORTS_DIR,
    )
    pipeline.configure(session)

    pipeline_thread = threading.Thread(target=pipeline.run, daemon=True)
    pipeline_thread.start()

    return jsonify({"ok": True, "total_runs": session.total_budget_runs})


@app.route("/api/stop", methods=["POST"])
def stop():
    if pipeline:
        pipeline.stop()
    return jsonify({"ok": True})


@app.route("/api/history")
def history():
    """Score history for chart — built from pipeline results."""
    if pipeline is None:
        return jsonify([])
    results = pipeline.phase1_results + pipeline.phase2_results
    return jsonify([
        {
            "run_id":   r.run_id,
            "score":    round(r.score, 4),
            "calmar":   round(r.calmar, 3),
            "passing":  r.passing,
            "phase":    r.phase,
        }
        for r in results
    ])


@app.route("/api/ea_params")
def ea_params():
    """Return param list for a given EA (used by setup page AJAX)."""
    ea_name = request.args.get("ea", "")
    try:
        from ea.registry import EARegistry
        reg    = EARegistry(str(BASE_DIR / "config.yaml"))
        profile = reg.get(ea_name)
        schema  = reg.get_schema(profile, apply_optimize_selection=False)
        return jsonify([
            {"name": p.name, "type": p.type,
             "range": p.range_label, "optimize": p.optimize}
            for p in schema.all_params() if p.type != "fixed"
        ])
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/download_set/<run_id>")
def download_set(run_id):
    """Serve the optimized .set file for download."""
    run_dir = REPORTS_DIR / run_id
    set_files = list(run_dir.glob("*.set")) if run_dir.exists() else []
    if not set_files:
        return "No .set file found", 404
    return send_from_directory(run_dir, set_files[0].name, as_attachment=True)


# ── Reports routes (unchanged) ────────────────────────────────────────────────

@app.route("/reports")
@app.route("/reports/")
def reports_index():
    import json, re
    runs = []
    for run_dir in sorted(REPORTS_DIR.iterdir(), reverse=True) if REPORTS_DIR.exists() else []:
        if not run_dir.is_dir():
            continue
        summary = run_dir / "summary.json"
        if summary.exists():
            try:
                txt  = summary.read_text(encoding="utf-8")
                txt  = re.sub(r'\bNaN\b', 'null', txt)
                txt  = re.sub(r'\bInfinity\b', 'null', txt)
                data = json.loads(txt)
                data["score"]       = data.get("score") or 0
                data["score_delta"] = data.get("score_delta") or 0
                runs.append(data)
            except Exception:
                pass
    return render_template("reports_index.html", runs=runs[:100])


@app.route("/reports/<path:filename>")
def reports_file(filename):
    return send_from_directory(REPORTS_DIR, filename)


@app.route("/api/runs")
def runs_list():
    import json, re
    runs = []
    if REPORTS_DIR.exists():
        for run_dir in sorted(REPORTS_DIR.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            summary = run_dir / "summary.json"
            if summary.exists():
                try:
                    txt  = summary.read_text(encoding="utf-8")
                    txt  = re.sub(r'\bNaN\b', 'null', txt)
                    txt  = re.sub(r'\bInfinity\b', 'null', txt)
                    data = json.loads(txt)
                    data["score"]       = data.get("score") or 0
                    data["score_delta"] = data.get("score_delta") or 0
                    runs.append(data)
                except Exception:
                    pass
    return jsonify(runs[:50])


# ── SocketIO ──────────────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    if pipeline:
        emit("status_sync", pipeline.get_status())


# ── Launch ────────────────────────────────────────────────────────────────────

def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://localhost:5000")


if __name__ == "__main__":
    print("=" * 60)
    print("  MT5 Smart EA Optimizer — Starting...")
    print("  Opening browser at http://localhost:5000")
    print("=" * 60)
    threading.Thread(target=open_browser, daemon=True).start()
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, use_reloader=False)
