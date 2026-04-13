# MT5 EA Optimizer — Full Project Handoff Document
**Last Updated:** 2026-04-13  
**Status:** Phase 1 complete (backend engine). Phase 2 (GUI app) = NEXT STEP  
**Primary EA:** LEGSTECH_EA_V2 | Symbol: XAUUSD | Timeframe: H1  
**Broker Timezone:** UTC+2

---

## 🎯 Project Goal

Build an automated, iterative backtesting and analysis system for MT5 Expert Advisors.  
It is NOT a trading bot. It is an **optimization engine** that:
- Runs MT5 strategy tester automatically with different parameter sets
- Extracts rich trade-level data (MAE/MFE) after each run
- Analyzes results to find failure patterns
- Proposes and tests parameter mutations
- Validates improvements before accepting them
- Prevents overfitting via Walk-Forward + Out-of-Sample testing

**End user:** Traders (non-technical). Must be a double-click app, not a terminal tool.

---

## 🏗️ Architecture Overview

```
MT5 Terminal (GUI)
    └── Strategy Tester (automated via .ini files)
            └── LEGSTECH_EA_V2.ex5 (compiled EA with TradeLogger)
                    └── Writes: LEGSTECH_EA_V2_XAUUSD_TradeLog.csv

Python Optimizer (backend engine)
    ├── MT5 Runner     → launch terminal, wait for report, collect files
    ├── Report Parser  → parse MT5 XML/HTML report into metrics + trades
    ├── Log Reader     → merge TradeLogger CSV (MAE/MFE) into trade objects
    ├── Analysis Engine → 4 modules detecting failure patterns
    │   ├── ReversalAnalyzer       → trades that went in profit then reversed
    │   ├── TimePerformanceAnalyzer → bad sessions/hours/days
    │   ├── EntryExitQualityAnalyzer → MAE/MFE quality scores
    │   └── EquityCurveAnalyzer    → flatness, loss clusters, R²
    ├── Composite Scorer → Calmar-primary weighted score (0-1)
    ├── Mutation Engine  → findings → parameter hypotheses (13 KB rules)
    ├── Validation Gate  → IS check → Walk-Forward → OOS
    └── Data Store       → SQLite (metadata) + Parquet (trade arrays)

Web App (NEXT TO BUILD — Phase 2)
    ├── Flask backend   → serves UI, runs optimization loop
    ├── WebSocket       → pushes live updates to browser
    ├── HTML/JS frontend → live dashboard, charts, findings
    └── Reports folder  → HTML + CSV reports per run
```

---

## 📁 File Structure (Current State)

```
C:\Users\DELL\Desktop\MT5_Optimizer\
├── main.py                     ← CLI entry point (terminal-based, to be replaced by app.py)
├── config.yaml                 ← ALL settings (MT5 paths, symbols, scoring weights, thresholds)
├── requirements.txt            ← Python dependencies
├── PROJECT_HANDOFF.md          ← THIS DOCUMENT
│
├── mql5/
│   └── TradeLogger.mqh         ← MQL5 include file (ALREADY DEPLOYED to MT5)
│
├── data/
│   ├── models.py               ← Pydantic v2 models: Trade, RunMetrics, Run, Finding, Hypothesis, Candidate
│   └── store.py                ← DataStore: SQLite + Parquet storage layer
│
├── mt5/
│   ├── ini_builder.py          ← Builds MT5 tester .ini files from param dicts
│   ├── runner.py               ← Launches MT5 subprocess, waits for report
│   ├── report_parser.py        ← Parses MT5 XML/HTML report → RunMetrics + list[Trade]
│   └── log_reader.py           ← Merges TradeLogger CSV, computes sessions/quality scores
│
├── analysis/
│   ├── base.py                 ← BaseAnalyzer ABC + statistical helpers
│   ├── reversal.py             ← ReversalAnalyzer
│   ├── time_performance.py     ← TimePerformanceAnalyzer
│   ├── entry_exit_quality.py   ← EntryExitQualityAnalyzer
│   └── equity_curve.py         ← EquityCurveAnalyzer
│
├── scoring/
│   └── composite.py            ← CompositeScorer (Calmar + PF + MFE capture + stability + recovery)
│
├── mutation/
│   ├── engine.py               ← MutationEngine: findings → Hypothesis objects
│   ├── knowledge_base.yaml     ← 13 rules mapping findings to param changes
│   └── param_manifest.yaml     ← Full EA parameter space with types/bounds/defaults
│
├── validation/
│   └── gate.py                 ← IS check + Walk-Forward + OOS + sensitivity check
│
└── tests/
    └── test_analyzers.py       ← 10 unit tests (all passing ✅)
```

---

## ⚙️ Configuration (config.yaml) — Key Values

```yaml
ea:
  name: "LEGSTECH_EA_V2"
  symbol: "XAUUSD"
  timeframe: "H1"

periods:
  train_start: "2022.01.01"
  train_end:   "2023.12.31"
  validate_start: "2024.01.01"
  validate_end:   "2024.06.30"
  oos_start:   "2024.07.01"    # LOCKED — never touch during optimization
  oos_end:     "2024.12.31"

mt5:
  terminal_exe: "C:/Program Files/MetaTrader 5/terminal64.exe"
  appdata_path: "C:/Users/DELL/AppData/Roaming/MetaQuotes/Terminal/D0E8209F77C8CF37AD8BF550E51FF075"
  mql5_files_path: "C:/Users/DELL/AppData/Roaming/MetaQuotes/Tester/D0E8209F77C8CF37AD8BF550E51FF075/Agent-127.0.0.1-3000/MQL5/Files"

broker:
  timezone_offset_hours: 2   # UTC+2

scoring:
  weights:
    calmar: 0.35
    profit_factor: 0.20
    mfe_capture: 0.20
    session_stability: 0.15
    recovery_factor: 0.10
```

---

## ✅ What is Done (Phase 1 — Backend Engine)

| Component | Status | Notes |
|---|---|---|
| TradeLogger.mqh | ✅ Complete + deployed | In MT5 Include folder, tested, CSV confirmed working |
| data/models.py | ✅ Complete | All Pydantic v2 models |
| data/store.py | ✅ Complete | SQLite + Parquet CRUD |
| mt5/ini_builder.py | ✅ Complete | Generates valid MT5 tester INI files |
| mt5/runner.py | ✅ Complete | Process launch + polling + timeout |
| mt5/report_parser.py | ✅ Complete | XML + HTML dual-format parser |
| mt5/log_reader.py | ✅ Complete | MAE/MFE merge + session classification |
| analysis/reversal.py | ✅ Complete | Permutation-tested |
| analysis/time_performance.py | ✅ Complete | Hour/Session/Day analysis |
| analysis/entry_exit_quality.py | ✅ Complete | 4-case diagnosis matrix |
| analysis/equity_curve.py | ✅ Complete | Flatness + R² + loss clusters |
| scoring/composite.py | ✅ Complete | Calmar-primary weighted scorer |
| mutation/engine.py | ✅ Complete | 13 KB rules, dedup, cascade |
| validation/gate.py | ✅ Complete | IS + WFV + OOS + sensitivity |
| main.py | ✅ Complete | Terminal CLI (will be replaced by app) |
| tests/ | ✅ 10/10 passing | Pure Python, no MT5 needed |
| Unit tests verified | ✅ Working | Python 3.11 required (see below) |

---

## 🔴 What is NOT Done Yet (Phase 2 — GUI App)

### The Big Next Step: Web App with Live Dashboard

**Goal:** Replace `main.py` terminal interface with a beautiful browser-based app that:

1. **Double-click `MT5_Optimizer.exe`** → browser opens automatically at `http://localhost:5000`
2. **Dashboard shows live:**
   - Iteration counter + current phase badge
   - Score history line chart (Calmar + composite over time)
   - Live MT5 run status with elapsed timer
   - Current parameters being tested
3. **Analysis panel shows** findings in plain English after each run
4. **Parameter changes panel** shows before → after with reason
5. **Results folder** `MT5_Optimizer\Reports\` gets:
   - `run_001\summary.html` — full backtest result in readable format
   - `run_001\trades.csv` — all trades with MAE/MFE
   - `run_001\findings.csv` — analysis findings
   - `run_001\parameters.json` — params used
6. **Controls:** Start, Pause, Skip Hypothesis, View Report buttons
7. **PyInstaller** bundles into single `MT5_Optimizer.exe`

### Tech Stack for Phase 2

```
Flask + Flask-SocketIO     → backend API + WebSocket push
Chart.js or Plotly.js      → charts in browser
Bootstrap 5 (dark theme)   → UI framework
Jinja2                     → HTML report templates
PyInstaller                → package to .exe
```

### Files to create in Phase 2

```
app.py                     ← Flask app entry point (replaces main.py)
ui/
  templates/
    index.html             ← Main dashboard
    report.html            ← Per-run report template
    findings.html          ← Findings detail page
  static/
    css/style.css
    js/dashboard.js        ← WebSocket + Chart.js logic
reports/                   ← All run outputs go here (user-visible)
MT5_Optimizer.spec         ← PyInstaller spec file
build.bat                  ← One-click build to .exe
```

---

## 🔧 Environment & Dependencies

**Python version:** 3.11 (NOT 3.13 — use `C:\Users\DELL\AppData\Local\Programs\Python\Python311\python.exe`)

**Install command:**
```powershell
C:\Users\DELL\AppData\Local\Programs\Python\Python311\python.exe -m pip install -r requirements.txt
```

**Run tests:**
```powershell
cd C:\Users\DELL\Desktop\MT5_Optimizer
C:\Users\DELL\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/ -v
```

**Key dependency versions:**
```
pydantic>=2.5, pandas>=2.1, pyarrow>=14.0, lxml>=4.9
loguru>=0.7, rich>=13.0, scipy>=1.11, pyyaml>=6.0
```

**Additional needed for Phase 2:**
```
flask, flask-socketio, eventlet, jinja2, pyinstaller
```

---

## 🧠 Key Design Decisions (Important Context)

1. **Hypothesis-driven, not brute-force** — We don't grid-search all params. We detect failure patterns, hypothesize a fix, test it, validate it.

2. **Calmar ratio is primary score metric** — Return / MaxDrawdown is most relevant for live trading.

3. **Three validation gates** — IS threshold → Walk-Forward (2 folds) → OOS (locked period). No candidate is promoted unless it passes all three.

4. **MFE/MAE is critical** — Without the TradeLogger, the analyzer still works but is less powerful. Always confirm CSV is being generated.

5. **OOS is sacred** — `2024.07.01 → 2024.12.31` is NEVER used during optimization. Only tested as final confirmation.

6. **Broker timezone = UTC+2** — All session analysis normalizes to UTC internally. Sessions: London=07-16 UTC, NY=13-22 UTC.

7. **Python 3.11 is required** — The system Python on this machine is 3.13 which doesn't have the packages. Always use the 3.11 path.

8. **MQL5 reference syntax fix** — MQL5 does not support C++ `&` references to array elements. `TradeLogger.mqh` was patched to use direct `TL_Positions[idx].field` access.

---

## 📍 Current Machine Paths (This User's System)

```
MT5 Terminal EXE:   C:\Program Files\MetaTrader 5\terminal64.exe
MT5 Terminal Data:  C:\Users\DELL\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\
MT5 Tester Files:   C:\Users\DELL\AppData\Roaming\MetaQuotes\Tester\D0E8209F77C8CF37AD8BF550E51FF075\Agent-127.0.0.1-3000\MQL5\Files\
EA Source File:     C:\Users\DELL\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Experts\LEGSTECH_EA_V2.mq5
TradeLogger (deployed): ...Terminal\...\MQL5\Include\TradeLogger.mqh
Project Folder:     C:\Users\DELL\Desktop\MT5_Optimizer\
Python 3.11:        C:\Users\DELL\AppData\Local\Programs\Python\Python311\python.exe
```

---

## 🚀 Phase 2 Build Instructions (For Next AI Session)

When continuing this project, build Phase 2 in this order:

### Step 1 — Flask app skeleton
Create `app.py` with:
- Flask app + SocketIO
- Route: `GET /` → serve dashboard
- Route: `GET /api/status` → current run status JSON
- Route: `POST /api/start` → start optimization loop in background thread
- Route: `POST /api/pause` → pause loop
- SocketIO event: emit `run_update` after each test completes

### Step 2 — Dashboard HTML
Create `ui/templates/index.html`:
- Dark theme (Bootstrap 5 dark)
- Left panel: score history chart (Chart.js line chart)
- Right panel: current run metrics card
- Bottom panel: scrollable findings feed
- Top bar: Start/Pause button, iteration counter, phase badge, timer

### Step 3 — Report template
Create `ui/templates/report.html`:
- Full metrics table
- Findings list in plain English
- Parameter delta table (old → new → why)
- Trade table with MAE/MFE columns

### Step 4 — Reports folder writer
Create `reports/writer.py`:
- `write_run_report(run_id, metrics, trades_df, findings)` → writes HTML + CSV
- All outputs go to `MT5_Optimizer\Reports\run_XXX\`

### Step 5 — PyInstaller packaging
Create `build.bat`:
```bat
pyinstaller --onefile --noconsole --name MT5_Optimizer app.py
```
Create `MT5_Optimizer.spec` with proper hidden imports for Flask, SocketIO, etc.

### Step 6 — Test end-to-end
- Double-click `MT5_Optimizer.exe`
- Browser opens at localhost:5000
- Click Start
- Watch live updates
- Check Reports folder

---

## 💡 Future Enhancements (v2)

- Multi-symbol optimization (EURUSD, GBPUSD alongside XAUUSD)
- Full rolling window WFV (currently 2-fold MVP)
- Portfolio-level Calmar (across symbols)
- Monte Carlo simulation for robustness testing
- Email/Telegram notification when a candidate is promoted
- Parameter sensitivity heatmap visualization
- Automatic .set file export for validated candidates

---

## ⚠️ Known Issues / Watch Points

1. **MT5 tester model** — Currently config uses `tester_model: 0` (Every Tick). For XAUUSD this is slow. Use model `4` (OHLC M1) for faster iteration during development.

2. **ShutdownTerminal=1** — The INI closes MT5 after each test. If MT5 is also being used for live trading, this will interrupt it. Separate terminal instances are recommended.

3. **Agent path** — The Tester Agent path (`Agent-127.0.0.1-3000`) may change if the MT5 tester port changes. If CSV is not found, check the Tester folder.

4. **WFV fold count** — Currently hardcoded to 2 folds in `validation/gate.py`. Easy to increase.

5. **INI Period code** — H1 = 16385 in MT5 (ENUM_TIMEFRAMES). This is hardcoded in `config.yaml` as `mt5_period_code: 16385`. Do not change unless changing timeframe.

---

*This document was generated by Antigravity AI on 2026-04-13.*  
*Continue building from Phase 2 — GUI App.*
