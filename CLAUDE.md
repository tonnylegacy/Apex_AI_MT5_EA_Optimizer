# CLAUDE.md — APEX Autonomous AI-Powered MT5 EA Optimizer

This file provides architectural context, build/test commands, and engineering invariants for **Claude Code** and autonomous coding agents working inside this repository.

## 1. Project Overview
- **System**: APEX — Autonomous AI-Powered MetaTrader 5 Expert Advisor Optimizer (`Python 3.11` + `Anthropic Claude Opus 4.7 API` + `Flask/Socket.IO` + `SQLite`)
- **Core Pipeline (`optimizer/pipeline.py` & `optimizer/ai_guided_loop.py`)**:
  1. **Phase 1 (Exploration)**: Latin Hypercube / neighborhood sampling across the EA parameter schema (`ea_registry.yaml`).
  2. **Phase 2 (Autonomous Claude Opus 4.7 Reasoning Loop)**: `analysis/ai_reasoner.py` (`MODEL = "claude-opus-4-7"`) ingests structured backtest telemetry (`RunMetrics`, reversal rates, MAE/MFE capture, session performance, loss clusters), diagnoses regime bottlenecks, and proposes targeted parameter mutations (`suggest_next_params()`).
  3. **Phase 3 (Robustness & Capital Guard Validation)**:
     - **Out-of-Sample (OOS) Gate (`validation/gate.py`)**: Evaluates promoted candidates on unseen holdout dates.
     - **Parameter Sensitivity Gate (`validation/gate.py`)**: Perturbs key parameters (`±10%` to `±20%`) to detect fragile curve-fitted peaks.
     - **Vectorized Monte Carlo & Quarter-Kelly Simulator (`validation/monte_carlo.py`)**: Executes `10,000` bootstrap trade-order permutations in `<50ms` via `NumPy` vectorization to compute 95%/99% Maximum Drawdown confidence intervals, Prop-Firm Risk-of-Ruin, and **Quarter-Kelly ($0.25 \times f^*$)** position sizing capped at `2.0%`.

## 2. Commands (Run, Demo & Test)
```powershell
# Run full unit test suite (Analyzers + Composite Scorer + Vectorized Monte Carlo)
python -m pytest tests/ -v

# Launch real-time Flask + Socket.IO web dashboard (No MT5 required in demo mode)
$env:APEX_DEMO_MODE="1"
python app.py

# Run CLI demo pipeline
python demo/run_demo.py
```

## 3. Architectural Invariants
1. **Schema Safety**: Every AI-suggested parameter mutation in `optimizer/ai_guided_loop.py` must be clamped to `(min, max, step)` bounds in `ea_registry.yaml` before writing `.ini`/`.set` files.
2. **Graceful Degradation**: `AIReasoner` (`analysis/ai_reasoner.py`) must never crash the pipeline if the Anthropic API times out or rate-limits; it falls back deterministically to `_fallback_insight()` and local neighborhood search.
3. **Zero Curve-Fitting Promotion**: No candidate is marked `RECOMMENDED` unless it passes OOS validation, parameter sensitivity checks, and Monte Carlo 95% drawdown bounds.
