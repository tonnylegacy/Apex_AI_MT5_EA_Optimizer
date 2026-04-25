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
    """Full run history for chart/table restoration — includes in-progress runs."""
    if pipeline is None:
        return jsonify([])
    if hasattr(pipeline, '_completed_runs') and pipeline._completed_runs:
        # Sort newest first by timestamp (ts field added in _make_run_dict)
        runs = sorted(pipeline._completed_runs,
                      key=lambda r: r.get("ts", ""), reverse=True)
        return jsonify(runs)
    # Fallback: post-phase ranked results
    results = pipeline.phase1_results + pipeline.phase2_results
    return jsonify([
        {
            "run_id":        r.run_id,
            "score":         round(r.score, 4),
            "net_profit":    round(r.net_profit, 2),
            "calmar":        round(r.calmar, 3),
            "profit_factor": round(r.profit_factor, 3),
            "max_drawdown":  round(r.max_drawdown, 2),
            "total_trades":  r.total_trades,
            "win_rate":      round(r.win_rate, 1),
            "passing":       r.passing,
            "phase":         r.phase,
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
    if REPORTS_DIR.exists():
        for run_dir in REPORTS_DIR.iterdir():
            if not run_dir.is_dir():
                continue
            summary = run_dir / "summary.json"
            if not summary.exists():
                continue
            try:
                txt  = summary.read_text(encoding="utf-8")
                txt  = re.sub(r'\bNaN\b', 'null', txt)
                txt  = re.sub(r'\bInfinity\b', 'null', txt)
                data = json.loads(txt)
                # Defensive defaults so the template never crashes on legacy files.
                data["run_id"]        = data.get("run_id") or run_dir.name
                data["score"]         = data.get("score") or 0
                data["score_delta"]   = data.get("score_delta") or 0
                data["net_profit"]    = data.get("net_profit") or 0
                data["profit_factor"] = data.get("profit_factor") or 0
                data["calmar"]        = data.get("calmar") or 0
                data["drawdown_pct"]  = data.get("drawdown_pct") or 0
                data["win_rate"]      = data.get("win_rate") or 0
                data["total_trades"]  = data.get("total_trades") or 0
                data["ts"]            = data.get("ts") or ""
                data["phase"]         = data.get("phase") or "phase1"
                # Surface per-card flags the template uses
                data["has_set"]       = bool(list(run_dir.glob("*.set")))
                data["has_ai"]        = (run_dir / "ai_insight.json").exists() or bool(data.get("has_ai"))
                runs.append(data)
            except Exception:
                pass
    # Sort by ts desc — newest first (was relying on filesystem sort order before)
    runs.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return render_template("reports_index.html", runs=runs[:100])


@app.route("/reports/<path:filename>")
def reports_file(filename):
    return send_from_directory(REPORTS_DIR, filename)


@app.route("/api/runs")
def runs_list():
    import json, re
    runs = []
    if REPORTS_DIR.exists():
        for run_dir in REPORTS_DIR.iterdir():
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
    # Sort by timestamp field (newest first)
    runs.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return jsonify(runs[:50])


@app.route("/api/run/<run_id>")
def run_detail(run_id):
    """Return full detail for one run: metrics + params + AI insight + .set link."""
    import json, re

    run_dir = REPORTS_DIR / run_id
    if not run_dir.exists():
        return jsonify({"error": "Run not found"}), 404

    def read_json(path):
        try:
            txt = path.read_text(encoding="utf-8")
            txt = re.sub(r'\bNaN\b', 'null', txt)
            txt = re.sub(r'\bInfinity\b', 'null', txt)
            return json.loads(txt)
        except Exception:
            return None

    summary    = read_json(run_dir / "summary.json") or {}
    params     = read_json(run_dir / "parameters.json") or {}
    ai_insight = read_json(run_dir / "ai_insight.json")

    # Also check live pipeline for AI insight (current session, not yet on disk)
    if ai_insight is None and pipeline and hasattr(pipeline, '_run_insights'):
        ai_insight = pipeline._run_insights.get(run_id)

    # Detect .set file
    set_files = list(run_dir.glob("*.set"))
    set_url   = f"/download_set/{run_id}" if set_files else None

    return jsonify({
        **summary,
        "params":     params,
        "ai_insight": ai_insight,
        "set_url":    set_url,
        "has_set":    bool(set_files),
    })


@app.route("/api/live_activity")
def live_activity():
    """
    Single endpoint the dashboard hits on (re)connect to restore everything
    that's not already in /api/history: AI thinking feed, parameter changes,
    validation runs, early-termination state, current phase + mode.
    """
    if not pipeline:
        return jsonify({
            "thinking": [], "param_changes": [], "validation": [],
            "early_termination": None,
            "phase": "idle", "phase_mode": None,
            "running": False,
        })

    # Determine phase mode (autonomous?) for label hints
    phase_mode = None
    if getattr(pipeline, "session", None):
        phase_mode = "autonomous" if getattr(pipeline.session, "autonomous_mode", False) else None

    return jsonify({
        "thinking":          getattr(pipeline, "_thinking_log",   []) or [],
        "param_changes":     getattr(pipeline, "_param_changes",  []) or [],
        "validation":        getattr(pipeline, "_validation_log", []) or [],
        "early_termination": getattr(pipeline, "_early_term",     None),
        "phase":             getattr(pipeline, "_phase",          "idle"),
        "phase_mode":        phase_mode,
        "running":           bool(getattr(pipeline, "running",    False)),
        "run_count":         getattr(pipeline, "_run_count",      0),
        "total_runs":        getattr(pipeline, "_total_runs",     0),
    })


@app.route("/api/best_result")
def best_result():
    """
    Return the current best run plus its evolution path — the ordered sequence
    of AI iterations that led to it (so the user can see how the AI arrived).
    """
    if not pipeline:
        return jsonify({"error": "No best result yet — optimization has not started."}), 404

    best_run = None
    if getattr(pipeline, "final_result", None):
        best_run = pipeline.final_result
    elif getattr(pipeline, "_live_best", None):
        best_run = pipeline._live_best
    if best_run is None:
        return jsonify({"error": "No best result yet — no passing run found so far."}), 404

    # Build evolution path: walk through _completed_runs up to (and including) the best
    evolution = []
    for r in getattr(pipeline, "_completed_runs", []):
        phase = r.get("phase", "")
        if not (phase.startswith("phase1") or phase.startswith("phase2")):
            continue
        ai_insight = r.get("ai_insight") or {}
        evolution.append({
            "run_id":        r.get("run_id"),
            "phase":         phase,
            "ts":            r.get("ts"),
            "score":         r.get("score"),
            "net_profit":    r.get("net_profit"),
            "profit_factor": r.get("profit_factor"),
            "calmar":        r.get("calmar"),
            "max_drawdown":  r.get("max_drawdown"),
            "passing":       r.get("passing"),
            "changes":       ai_insight.get("changes") or [],
            "analysis":      ai_insight.get("analysis") or ai_insight.get("diagnosis") or "",
            "is_best":       r.get("run_id") == best_run.run_id,
        })
        if r.get("run_id") == best_run.run_id:
            break

    return jsonify({
        "run_id":         best_run.run_id,
        "score":          round(best_run.score, 4),
        "net_profit":     round(best_run.net_profit, 2),
        "profit_factor":  round(best_run.profit_factor, 3),
        "calmar":         round(best_run.calmar, 3),
        # max_drawdown + win_rate are stored as fractions on RankedResult; emit as %
        "max_drawdown":   round(best_run.max_drawdown * 100, 2),
        "win_rate":       round(best_run.win_rate * 100, 1),
        "total_trades":   best_run.total_trades,
        "passing":        bool(best_run.passing),
        "phase":          getattr(best_run, "phase", "phase2_ai"),
        "params":         best_run.params,
        "evolution":      evolution,
        "set_url":        f"/download_set/{best_run.run_id}",
    })


# ── SocketIO ──────────────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    if pipeline:
        emit("status_sync", pipeline.get_status())


@app.route("/api/ai_insight/latest")
def ai_insight_latest():
    """Return the latest AI insight from the running pipeline."""
    if pipeline and hasattr(pipeline, 'get_latest_insight'):
        insight = pipeline.get_latest_insight()
        if insight:
            return jsonify(insight)
    return jsonify(None)


@app.route("/api/ai_insights")
def ai_insights_all():
    """Return all AI insights from this session."""
    if pipeline and hasattr(pipeline, 'get_all_insights'):
        return jsonify(pipeline.get_all_insights())
    return jsonify([])


def _mask_key(k: str) -> str:
    """Mask an API key so the GET response never exposes the secret."""
    if not k:
        return ""
    if k.startswith("${") or k in ("YOUR_API_KEY", "sk-ant-..."):
        return ""           # placeholder — return empty so the field shows blank
    if len(k) <= 12:
        return "***"
    return k[:8] + "…" + k[-4:]


@app.route("/api/settings", methods=["GET"])
def get_settings():
    import yaml
    import os
    config_path = BASE_DIR / "config.yaml"
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        ai_cfg = cfg.get("ai", {})
        mt5_cfg = cfg.get("mt5", {})
        broker_cfg = cfg.get("broker", {})
        thresh_cfg = cfg.get("thresholds", {})
        # Resolve the active API key — placeholder in config falls back to env var.
        raw_key = ai_cfg.get("anthropic_api_key", "")
        if not raw_key or raw_key.startswith("${"):
            raw_key = os.environ.get("ANTHROPIC_API_KEY", "")
        return jsonify({
            "ai": {
                "enabled":          ai_cfg.get("enabled", True),
                "anthropic_api_key": _mask_key(raw_key),
                "anthropic_api_key_set": bool(raw_key),
                "model":            ai_cfg.get("model", "claude-opus-4-7"),
                "timeout_seconds":  ai_cfg.get("timeout_seconds", 30),
            },
            "mt5": {
                "terminal_exe":          mt5_cfg.get("terminal_exe", ""),
                "appdata_path":          mt5_cfg.get("appdata_path", ""),
                "mql5_files_path":       mt5_cfg.get("mql5_files_path", ""),
                "tester_timeout_seconds": mt5_cfg.get("tester_timeout_seconds", 120),
                "tester_model":          mt5_cfg.get("tester_model", 1),
            },
            "broker": {
                "timezone_offset_hours": broker_cfg.get("timezone_offset_hours", 3),
                "deposit":              broker_cfg.get("deposit", 10000),
                "leverage":             broker_cfg.get("leverage", 500),
            },
            "thresholds": {
                "min_trades":            thresh_cfg.get("min_trades", 30),
                "min_profit_factor":     thresh_cfg.get("min_profit_factor", 1.2),
                "min_calmar":            thresh_cfg.get("min_calmar", 0.5),
                "max_oos_degradation":   thresh_cfg.get("max_oos_degradation", 0.3),
                "sensitivity_tolerance": thresh_cfg.get("sensitivity_tolerance", 0.15),
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings", methods=["POST"])
def save_settings():
    import yaml
    config_path = BASE_DIR / "config.yaml"
    data = request.get_json(silent=True) or {}
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        for section in ["ai", "mt5", "broker", "thresholds"]:
            if section in data and isinstance(data[section], dict):
                if section not in cfg:
                    cfg[section] = {}
                # Don't overwrite the real API key with the masked one we sent
                # the client. We accept a key only if it's empty (clearing) or
                # looks like a full key (sk-ant-… with no mask markers and at
                # least 30 chars). Anything ambiguous → preserve the existing.
                if section == "ai":
                    incoming_key = data["ai"].get("anthropic_api_key", "")
                    if incoming_key:
                        looks_masked = (
                            "…" in incoming_key
                            or "..." in incoming_key
                            or "***" in incoming_key
                            or len(incoming_key) < 30
                        )
                        if looks_masked:
                            data["ai"].pop("anthropic_api_key", None)
                cfg[section].update(data[section])
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        return jsonify({"ok": True, "note": "Settings saved. Will apply on the next optimization run."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ea/register", methods=["POST"])
def ea_register():
    """Register a new EA profile."""
    from ea.registry import EARegistry, EAProfile
    data = request.get_json(silent=True) or {}
    try:
        reg = EARegistry(str(BASE_DIR / "config.yaml"))
        profile = EAProfile(
            name=data["name"],
            ex5_file=data.get("ex5_file", data["name"]),
            set_template=data["set_template"],
            symbol=data.get("symbol", "XAUUSD"),
            timeframe=data.get("timeframe", "H1"),
            mode=data.get("mode", "generic"),
        )
        reg.register(profile)
        return jsonify({"ok": True, "name": profile.name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/ea/list")
def ea_list():
    """List all registered EAs."""
    from ea.registry import EARegistry
    try:
        reg = EARegistry(str(BASE_DIR / "config.yaml"))
        profiles = reg.list_all()
        return jsonify([{
            "name": p.name,
            "symbol": p.symbol,
            "timeframe": p.timeframe,
            "mode": p.mode,
            "set_template": p.set_template,
        } for p in profiles])
    except Exception as e:
        return jsonify([])


@app.route("/ai_insights")
def ai_insights_page():
    """Legacy alias — AI insights now live inline on the dashboard."""
    return redirect("/dashboard")


@app.route("/api/ea/scan")
def ea_scan():
    """Scan common MT5 locations for .ex5 and .set files."""
    import glob as _glob
    import os

    home = Path(os.path.expanduser("~"))
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    desktop = home / "Desktop"

    # Directories to scan for .ex5 files — MetaQuotes terminal data dirs
    ex5_dirs = []
    mq_base = appdata / "MetaQuotes" / "Terminal"
    if mq_base.exists():
        for td in mq_base.iterdir():
            if td.is_dir():
                ex5_dirs.append(td / "MQL5" / "Experts")
    ex5_dirs.append(Path("C:/Program Files/MetaTrader 5/MQL5/Experts"))
    ex5_dirs.append(Path("C:/Program Files (x86)/MetaTrader 5/MQL5/Experts"))

    # Scan .ex5 files (skip Examples / Advisors / Free Robots subfolders — likely default)
    SKIP_DIRS = {"Examples", "Advisors", "Free Robots", "Market"}
    ex5_found = []
    seen_names = set()
    for d in ex5_dirs:
        if not d.exists():
            continue
        for f in d.rglob("*.ex5"):
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            name = f.stem
            if name not in seen_names:
                seen_names.add(name)
                ex5_found.append({
                    "name": name,
                    "path": str(f).replace("\\", "/"),
                    "dir": str(f.parent).replace("\\", "/"),
                })

    # Scan .set files — Desktop, Desktop subfolders, MT5 tester agents
    set_dirs = [desktop]
    # Desktop subfolders (1 level deep)
    for item in desktop.iterdir() if desktop.exists() else []:
        if item.is_dir():
            set_dirs.append(item)
    # MT5 tester agent MQL5/Files
    tester_base = appdata / "MetaQuotes" / "Tester"
    if tester_base.exists():
        for td in tester_base.rglob("MQL5/Files"):
            set_dirs.append(td)

    set_found = []
    seen_set = set()
    for d in set_dirs:
        if not d.exists():
            continue
        for f in d.glob("*.set"):
            key = f.name
            if key not in seen_set:
                seen_set.add(key)
                set_found.append({
                    "name": f.stem,
                    "filename": f.name,
                    "path": str(f).replace("\\", "/"),
                })

    # Build best-match hints: for each ex5, find the most likely .set file
    def best_set_for(ea_name):
        ea_lower = ea_name.lower()
        # exact match first
        for s in set_found:
            if s["name"].lower() == ea_lower:
                return s["path"]
        # prefix/suffix match
        for s in set_found:
            sl = s["name"].lower()
            if ea_lower in sl or sl in ea_lower:
                return s["path"]
        return ""

    for ea in ex5_found:
        ea["suggested_set"] = best_set_for(ea["name"])

    return jsonify({
        "ex5": ex5_found,
        "set": set_found,
    })


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
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
