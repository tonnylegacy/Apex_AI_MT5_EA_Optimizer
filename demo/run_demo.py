"""
demo/run_demo.py
APEX offline demo runner.

Spins up the Flask + SocketIO app with APEX_DEMO_MODE=1 so the optimizer
generates synthetic backtest results instead of calling MT5. Lets judges
without a Windows + MT5 install see the full live AI loop, validation,
and verdict flow.

By default, the demo **auto-runs**: it opens the dashboard in your browser
and immediately starts a ~3-4 minute optimization showcasing every phase
(exploration → AI iteration → validation → verdict). No manual setup needed.

Usage:
    python -m demo.run_demo            # auto-running showcase (default, ~3-4 min)
    python -m demo.run_demo --quick    # original fast/manual demo (you click "New Run")
    python -m demo.run_demo --loop     # auto-restart on completion (for unattended recording)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

ROOT      = Path(__file__).resolve().parent.parent
DEMO_DIR  = Path(__file__).resolve().parent
DEMO_SET  = DEMO_DIR / "demo_ea.set"
REGISTRY  = ROOT / "ea_registry.yaml"
CONFIG    = ROOT / "config.yaml"
EXAMPLE_CONFIG = ROOT / "config.example.yaml"

DEMO_PROFILE = {
    "name":         "APEX_DEMO_EA",
    "ex5_file":     "APEX_DEMO_EA",
    "set_template": str(DEMO_SET).replace("\\", "/"),
    "symbol":       "XAUUSD",
    "timeframe":    "H1",
    "mode":         "advanced",
    "registered_at": "2026-01-01T00:00:00+00:00",
    "optimize_params": {
        "InpRiskPercent":     True,
        "InpMaxDailyLossPct": True,
        "InpRRRatio":         True,
        "InpStopLossPips":    True,
        "InpTakeProfitPips":  True,
        "InpATRMultiplier":   True,
        "InpUseTrailing":     True,
        "InpTrailStartPips":  True,
        "InpUseBreakeven":    True,
        "InpBEPips":          True,
        "InpMinScore":        True,
    },
    "automation_overrides": {},
}


def ensure_demo_registry() -> None:
    """Make sure the demo EA profile exists in ea_registry.yaml."""
    if REGISTRY.exists():
        try:
            data = yaml.safe_load(REGISTRY.read_text()) or {"profiles": []}
        except Exception:
            data = {"profiles": []}
    else:
        data = {"profiles": []}

    profiles = data.get("profiles") or []
    if not any(p.get("name") == DEMO_PROFILE["name"] for p in profiles):
        profiles.append(DEMO_PROFILE)
        data["profiles"] = profiles
        REGISTRY.write_text(yaml.safe_dump(data, sort_keys=False))
        print(f"  [ok] Registered demo EA in {REGISTRY.name}")
    else:
        print(f"  [ok] Demo EA already in {REGISTRY.name}")


def ensure_config() -> None:
    """If config.yaml is missing, copy config.example.yaml as a starting point."""
    if not CONFIG.exists():
        if EXAMPLE_CONFIG.exists():
            CONFIG.write_text(EXAMPLE_CONFIG.read_text())
            print(f"  [ok] Created {CONFIG.name} from template")
        else:
            print(f"  [!] No config.yaml or config.example.yaml — app may fail to start")


def banner(quick: bool = False, loop: bool = False) -> None:
    bar = "=" * 72
    if quick:
        mode_line = "APEX -- DEMO MODE (quick / manual)"
        body = (
            "  * Backtests are synthetic; AI reasoning is real if API key is set\n"
            "  * Per-run latency is short for fast UI testing\n\n"
            '  Open http://localhost:5000 in your browser, hit "New Run", and\n'
            "  configure your own optimization."
        )
    else:
        suffix = " [LOOP]" if loop else ""
        mode_line = f"APEX -- DEMO MODE (auto-running showcase){suffix}"
        body = (
            "  * Auto-starts a ~3-4 min optimization showing every phase\n"
            "  * AI reasoning streams live (API key loaded from env or config.yaml)\n"
            "  * The dashboard opens itself; just sit back or hit your recorder hotkey\n"
            "  * Heads up: AI API calls add ~12-15s each — actual run can stretch\n"
            "    to ~5 min depending on Claude latency.\n"
            "  * To skip the auto-start and configure your own run: --quick"
        )
    print(f"\n{bar}\n{mode_line.center(72)}\n{bar}\n{body}\n{bar}\n")


# ── Showcase auto-start config ──────────────────────────────────────────────
# Tuned for ~2.5-3 min runtime that actually shows the AI loop iterating:
#   * Phase 1 (10 LHS samples × ~2.5s, no AI) ≈ 25-30s
#   * Phase 2 (5 AI iterations × ~15-18s incl. analyze + suggest_next_params)
#     ≈ 75-90s — targets are deliberately STIFF so the loop can't early-exit
#     at iteration 0; Phase 1's best typically lands around PF≈1.9 / Calmar≈1
#     which is below these targets, so the AI gets to actually do its job.
#   * Phase 3 (1 OOS + 3 sensitivity, each with AI analyze) ≈ 50-60s
# AI call latency dominates the math; the synthetic backtest delay is short.
CINEMATIC_PAYLOAD = {
    "ea_name":                     "APEX_DEMO_EA",
    "symbol":                      "XAUUSD",
    "timeframe":                   "H1",
    "train_start":                 "2022.01.01",
    "train_end":                   "2023.12.31",
    "val_start":                   "2024.01.01",
    "val_end":                     "2024.06.30",
    "objective":                   "balanced",
    "budget_minutes":              15,
    "autonomous_mode":             True,
    "autonomous_max_iterations":   5,
    "target_profit_factor":        2.5,
    "target_max_drawdown_pct":     5.0,
    "target_min_calmar":           1.5,
    "selected_params":             [],
}


def _autostart_cinematic_run(loop: bool = False) -> None:
    """
    Background thread: waits for the server to be up, then POSTs /api/start
    with cinematic settings. If --loop, polls /api/status and re-triggers
    when the pipeline goes idle (so an unattended recording keeps producing
    fresh footage).
    """
    import json
    import time
    import urllib.error
    import urllib.request

    base = "http://127.0.0.1:5000"

    def _server_ready() -> bool:
        try:
            with urllib.request.urlopen(f"{base}/api/status", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def _is_running() -> bool:
        try:
            with urllib.request.urlopen(f"{base}/api/status", timeout=2) as r:
                data = json.loads(r.read())
                state = (data.get("state") or "").lower()
                return state in ("running", "starting")
        except Exception:
            return False

    def _post_start() -> bool:
        body = json.dumps(CINEMATIC_PAYLOAD).encode("utf-8")
        req  = urllib.request.Request(
            f"{base}/api/start", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                resp = json.loads(r.read())
                return bool(resp.get("ok"))
        except Exception as e:
            print(f"  [cinematic] /api/start failed: {e}")
            return False

    # Wait for server to come up (~10 seconds max)
    for _ in range(40):
        if _server_ready():
            break
        time.sleep(0.25)
    else:
        print("  [cinematic] server didn't become ready — aborting auto-start")
        return

    # Small grace period so the dashboard tab finishes connecting via SocketIO
    time.sleep(2.0)

    while True:
        print("  [cinematic] starting optimization run …")
        if not _post_start():
            print("  [cinematic] failed to start — retrying in 10s")
            time.sleep(10)
            continue

        # Wait for run to complete (poll until idle for 3 consecutive checks)
        idle_streak = 0
        while idle_streak < 3:
            time.sleep(4)
            if _is_running():
                idle_streak = 0
            else:
                idle_streak += 1

        if not loop:
            print("  [cinematic] run complete — staying on verdict screen")
            return
        print("  [cinematic] run complete — restarting in 12s for next loop iteration")
        time.sleep(12)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="demo.run_demo",
        description="APEX offline demo runner.",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Skip the auto-start showcase. Server boots with fast per-run latency "
             "and you drive the demo manually from /setup.",
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Auto-restart a fresh showcase run when each one completes (for unattended "
             "screen recording). Ignored with --quick.",
    )
    parser.add_argument(
        "--per-run-seconds", type=float, default=None,
        help="Override APEX_DEMO_RUN_SECONDS (default: 5.0 showcase / 1.2 --quick).",
    )
    args = parser.parse_args()

    banner(quick=args.quick, loop=args.loop)

    print("Bootstrapping demo environment...")
    ensure_config()
    ensure_demo_registry()

    # Set the demo flag — the pipeline checks this in _execute_run.
    os.environ["APEX_DEMO_MODE"] = "1"
    # Per-run latency tunable. Default (auto-running showcase) is slower so
    # each phase is visible long enough to film and narrate over.
    if args.per_run_seconds is not None:
        os.environ["APEX_DEMO_RUN_SECONDS"] = str(args.per_run_seconds)
    elif args.quick:
        os.environ.setdefault("APEX_DEMO_RUN_SECONDS", "1.2")
    else:
        # 2.5s synthetic delay; AI call latency dominates total runtime anyway
        os.environ.setdefault("APEX_DEMO_RUN_SECONDS", "2.5")
        # Skip per-run AI analysis during Phase 1 exploration so the demo
        # finishes in ~3-4 min. Phase 2's autonomous AI loop (the headline
        # feature) still streams reasoning live.
        os.environ.setdefault("APEX_DEMO_SKIP_PHASE1_AI", "1")

    # AI reasoner pulls from env var OR config.yaml ai.anthropic_api_key
    cfg_has_key = False
    try:
        cfg = yaml.safe_load(CONFIG.read_text()) if CONFIG.exists() else {}
        cfg_has_key = bool(((cfg or {}).get("ai") or {}).get("anthropic_api_key", "").strip())
    except Exception:
        pass
    if not (os.environ.get("ANTHROPIC_API_KEY", "").strip() or cfg_has_key):
        print("  [!] No API key in env (ANTHROPIC_API_KEY) or config.yaml — AI reasoning will be skipped.")
    else:
        src = "config.yaml" if cfg_has_key else "env var"
        print(f"  [ok] API key found in {src} — AI reasoning enabled.")

    print()
    print(f"Launching APEX server at http://localhost:5000  (per-run: {os.environ['APEX_DEMO_RUN_SECONDS']}s) ...")
    sys.path.insert(0, str(ROOT))
    # Import after env vars are set so the pipeline picks them up.
    import threading
    import webbrowser
    from app import app as flask_app, socketio

    def _open_browser():
        import time as _t
        _t.sleep(1.5)
        try:
            # In auto-running mode jump straight to the dashboard so the user sees
            # the live run unfold; in --quick mode land on the landing page.
            url = "http://localhost:5000" if args.quick else "http://localhost:5000/dashboard"
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open_browser, daemon=True).start()

    if not args.quick:
        threading.Thread(
            target=_autostart_cinematic_run,
            kwargs={"loop": args.loop},
            daemon=True,
        ).start()

    socketio.run(
        flask_app, host="0.0.0.0", port=5000,
        debug=False, use_reloader=False, allow_unsafe_werkzeug=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
