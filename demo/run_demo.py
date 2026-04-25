"""
demo/run_demo.py
APEX offline demo runner.

Spins up the Flask + SocketIO app with APEX_DEMO_MODE=1 so the optimizer
generates synthetic backtest results instead of calling MT5. Lets judges
without a Windows + MT5 install see the full live AI loop, validation,
and verdict flow.

Usage:
    python -m demo.run_demo
    # or
    python demo/run_demo.py
"""
from __future__ import annotations

import os
import sys
import textwrap
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


def banner() -> None:
    bar = "=" * 72
    print(textwrap.dedent(f"""
        {bar}
                          APEX -- DEMO MODE (offline / no MT5)
        {bar}
          * Backtests are synthetic (deterministic from params + jitter)
          * The AI loop, validation, and verdict flow are 100% real
          * Set ANTHROPIC_API_KEY to see live AI reasoning

          Open http://localhost:5000 in your browser, hit "New Run", and
          watch the AI think.
        {bar}
    """))


def main() -> int:
    banner()

    print("Bootstrapping demo environment...")
    ensure_config()
    ensure_demo_registry()

    # Set the demo flag — the pipeline checks this in _execute_run.
    os.environ["APEX_DEMO_MODE"] = "1"
    # Per-run latency tunable — keep small so demo feels snappy.
    os.environ.setdefault("APEX_DEMO_RUN_SECONDS", "1.2")

    if "ANTHROPIC_API_KEY" not in os.environ:
        print("  [!] ANTHROPIC_API_KEY not set — AI reasoning will be skipped (synthetic metrics still flow).")

    print()
    print("Launching APEX server at http://localhost:5000 ...")
    sys.path.insert(0, str(ROOT))
    # Import after env vars are set so the pipeline picks them up.
    import threading
    import webbrowser
    from app import app as flask_app, socketio

    def _open_browser():
        import time as _t
        _t.sleep(1.5)
        try:
            webbrowser.open("http://localhost:5000")
        except Exception:
            pass

    threading.Thread(target=_open_browser, daemon=True).start()
    socketio.run(
        flask_app, host="0.0.0.0", port=5000,
        debug=False, use_reloader=False, allow_unsafe_werkzeug=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
