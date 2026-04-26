# MT5 EA Optimizer — Full Project Handoff Document

**Last Updated:** 2026-04-13  
**Status:** ✅ Phase 1 (Backend Engine) + ✅ Phase 2 (Live Web Dashboard) — BOTH COMPLETE  
**GitHub:** https://github.com/tonnylegacy/Apex_AI_MT5_EA_Optimizer  
**Primary EA:** LEGSTECH_EA_V2 | Symbol: XAUUSD | Timeframe: H1 | Broker TZ: UTC+2

---

## 🎯 Project Goal

Build an automated, iterative backtesting and analysis system for MT5 Expert Advisors.  
It is NOT a trading bot. It is an **optimization engine** that:

- Runs MT5 strategy tester automatically with different parameter sets
- Extracts rich trade-level data (MAE/MFE) after each run
- Analyzes results to find failure patterns using statistical methods
- Proposes and tests parameter mutations based on findings
- Validates improvements before accepting them (IS → WFV → OOS)
- Prevents overfitting via Walk-Forward + Out-of-Sample testing
- Displays everything live in a beautiful browser dashboard
- Writes human-readable reports to a visible `Reports\` folder

**End user:** Traders (non-technical). Double-click app, no terminal needed.

---

## 🏗️ Architecture Overview

```
[Trader double-clicks "Launch Optimizer.bat"]
        ↓
[Browser auto-opens http://localhost:5000]
        ↓
[Flask + SocketIO Dashboard]
        ↓
[Trader clicks ▶ Start]
        ↓
┌────────────────────────────────────────────────────┐
│               OPTIMIZATION LOOP                     │
│                                                     │
│  1. Kill any running MT5 process (psutil)           │
│  2. Clear stale report files                        │
│  3. Validate environment (EA exists, paths valid)   │
│  4. Build INI file (Period=H1, Report=relative)     │
│  5. Launch fresh MT5 via subprocess /config:        │
│  6. Wait 10s for data readiness                     │
│  7. Poll MT5 appdata root for Optimizer_*.htm       │
│  8. Parse report (UTF-16 LE HTML → metrics+trades)  │
│  9. Run 4 analyzers on trade data                   │
│ 10. Score composite (Calmar-primary)                │
│ 11. Mutate parameters → Hypotheses                  │
│ 12. Test each hypothesis (steps 1-10 repeated)      │
│ 13. Validate: IS check → WFV → promote candidate   │
│ 14. Write Reports\run_XXX\ (HTML + CSV + JSON)      │
│ 15. Repeat until converged or max_iterations        │
└────────────────────────────────────────────────────┘
        ↓
[Dashboard updates live via WebSocket events]
[Reports\ folder has all results]
```

---

## 📁 Complete File Structure

```
C:\Users\DELL\Desktop\MT5_Optimizer\
│
├── app.py                      ← Flask + SocketIO app entry point
│                                  Auto-opens browser at localhost:5000
│                                  Routes: /, /api/start, /api/pause,
│                                          /api/stop, /api/skip, /reports
│
├── optimizer_loop.py           ← Background thread: full optimization pipeline
│                                  Emits SocketIO events for every phase/run/finding
│
├── config.yaml                 ← ALL settings (see section below)
├── requirements.txt            ← Python dependencies
├── PROJECT_HANDOFF.md          ← THIS DOCUMENT
├── "Launch Optimizer.bat"      ← Double-click to start (opens browser automatically)
│                                  Uses Python 3.11 at hardcoded path
│
├── mql5/
│   └── TradeLogger.mqh         ← MQL5 include: per-trade MAE/MFE logger
│                                  DEPLOYED to: ...Terminal\...\MQL5\Include\
│                                  INTEGRATED in: LEGSTECH_EA_V2.mq5 via TL_OnTick()
│
├── data/
│   ├── models.py               ← Pydantic v2: Trade, RunMetrics, Run, Finding,
│   │                              Hypothesis, Candidate, RunResult
│   └── store.py                ← DataStore: SQLite metadata + Parquet trade arrays
│
├── mt5/
│   ├── ini_builder.py          ← Builds MT5 tester .ini files
│   │                              CRITICAL: Period=H1 (string), Report=relative name
│   │                              Model=4 (OHLC M1), ShutdownTerminal=1
│   ├── runner.py               ← Full MT5 process control:
│   │                              kill_mt5() → clear_stale() → validate() →
│   │                              launch() → wait_data() → poll_report() → retry
│   │                              Report location: appdata_path\ root (NOT /reports/)
│   ├── report_parser.py        ← Parses MT5 HTML report
│   │                              CRITICAL: MT5 reports are UTF-16 LE encoded!
│   │                              Uses lhtml.document_fromstring(raw_bytes)
│   │                              Pairs in/out deals → complete Trade objects
│   └── log_reader.py           ← Merges TradeLogger CSV (MAE/MFE) into trades
│
├── analysis/
│   ├── base.py                 ← BaseAnalyzer ABC + statistical helpers
│   ├── reversal.py             ← Trades that went to MFE then reversed (permutation test)
│   ├── time_performance.py     ← Bad sessions/hours/days (Z-score)
│   ├── entry_exit_quality.py   ← MAE/MFE quality matrix (4-case diagnosis)
│   └── equity_curve.py         ← Flatness, loss clusters, R² of equity curve
│
├── scoring/
│   └── composite.py            ← Weighted scorer: Calmar(35%) + PF(20%) +
│                                  MFE capture(20%) + stability(15%) + recovery(10%)
│
├── mutation/
│   ├── engine.py               ← Findings → Hypothesis objects (dedup, cascade)
│   ├── knowledge_base.yaml     ← 13 rules: finding_type → param_delta
│   └── param_manifest.yaml     ← Full EA parameter space: types, bounds, defaults
│
├── validation/
│   └── gate.py                 ← IS check → Walk-Forward (2 folds) → OOS
│
├── reports/
│   └── writer.py               ← Writes per-run: summary.json, summary.html,
│                                  trades.csv, findings.csv, parameters.json
│
├── ui/
│   ├── templates/
│   │   ├── index.html          ← Live dashboard (dark premium design)
│   │   ├── report.html         ← Per-run report template
│   │   └── reports_index.html  ← Reports browser page (card grid)
│   └── static/
│       ├── css/style.css       ← Full dark theme CSS (glassmorphism, animations)
│       └── js/dashboard.js     ← SocketIO events + Chart.js + live metrics
│
├── tests/
│   └── test_analyzers.py       ← 10 unit tests (all passing ✅)
│
└── runs/                       ← Generated INI + local run folders (gitignored)
    └── baseline_YYYYMMDD_.../
        └── run.ini
```

---

## ⚙️ Configuration (config.yaml)

```yaml
ea:
  name: "LEGSTECH_EA_V2"
  file: "LEGSTECH_EA_V2"         # no extension — MT5 adds .ex5
  symbol: "XAUUSD"
  timeframe: "H1"

periods:
  train_start:    "2022.01.01"
  train_end:      "2023.12.31"
  validate_start: "2024.01.01"
  validate_end:   "2024.06.30"
  oos_start:      "2024.07.01"   # SACRED — never optimize against this
  oos_end:        "2024.12.31"

mt5:
  terminal_exe: "C:/Program Files/MetaTrader 5/terminal64.exe"
  appdata_path: "C:/Users/DELL/AppData/Roaming/MetaQuotes/Terminal/D0E8209F77C8CF37AD8BF550E51FF075"
  mql5_files_path: "C:/Users/DELL/AppData/Roaming/MetaQuotes/Tester/D0E8209F77C8CF37AD8BF550E51FF075/Agent-127.0.0.1-3000/MQL5/Files"
  tester_model: 4                # 4=OHLC M1 (fast). 0=Every Tick (slow, needs tick data)
  tester_timeout_seconds: 1800
  shutdown_terminal: 1           # MT5 closes itself after each test
  data_readiness_wait_seconds: 10
  kill_on_start: true            # Always kill existing MT5 before each run

broker:
  timezone_offset_hours: 2      # UTC+2 (HFMarkets)
  deposit: 10000.0
  currency: "USD"
  leverage: 100
```

---

## ✅ What Is Complete

### Phase 1 — Backend Engine

| Component | Status | Notes |
|---|---|---|
| TradeLogger.mqh | ✅ Deployed | In MT5 Include folder, integrated in EA |
| data/models.py | ✅ | All Pydantic v2 models |
| data/store.py | ✅ | SQLite + Parquet |
| mt5/ini_builder.py | ✅ | Period=H1 string, relative Report path |
| mt5/runner.py | ✅ | Kill→validate→launch→wait→poll→retry |
| mt5/report_parser.py | ✅ | UTF-16 LE HTML, pairs 597 trades |
| mt5/log_reader.py | ✅ | MAE/MFE merge + session labelling |
| analysis/reversal.py | ✅ | Permutation-tested |
| analysis/time_performance.py | ✅ | Z-score per hour/session/day |
| analysis/entry_exit_quality.py | ✅ | 4-case diagnosis matrix |
| analysis/equity_curve.py | ✅ | Flatness + R² + loss clusters |
| scoring/composite.py | ✅ | Calmar-primary (0-1 score) |
| mutation/engine.py | ✅ | 13 KB rules, dedup, cascade |
| validation/gate.py | ✅ | IS → WFV → OOS → sensitivity |
| tests/ | ✅ 10/10 | Pure Python, no MT5 needed |

### Phase 2 — Live Web Dashboard

| Component | Status | Notes |
|---|---|---|
| app.py | ✅ | Flask + SocketIO, all API routes |
| optimizer_loop.py | ✅ | Background thread, live event emitter |
| ui/templates/index.html | ✅ | Score chart, metrics, findings, log, candidates |
| ui/templates/report.html | ✅ | Per-run full report with trade table |
| ui/templates/reports_index.html | ✅ | Card grid browser page |
| ui/static/css/style.css | ✅ | Premium dark theme |
| ui/static/js/dashboard.js | ✅ | SocketIO + Chart.js + live updates |
| reports/writer.py | ✅ | HTML + CSV + JSON per run |
| Launch Optimizer.bat | ✅ | Double-click launcher |
| GitHub | ✅ | https://github.com/tonnylegacy/Apex_AI_MT5_EA_Optimizer |

---

## 🐛 Critical Bugs Found & Fixed (Important for Future AI)

### 1. MT5 Report Encoding — UTF-16 LE
**Problem:** MT5 HTML reports (`.htm`) are UTF-16 LE encoded (BOM: `\xFF\xFE`).  
Standard `decode('utf-8')` silently failed, lxml returned a single `<p>` element.  
**Fix:** Detect BOM and use `lhtml.document_fromstring(raw_bytes)` directly.  
```python
if raw[:2] == b'\xff\xfe':
    tree = lhtml.document_fromstring(raw)   # handles UTF-16 internally
```

### 2. MT5 Report Location
**Problem:** Runner was looking in `appdata_path/reports/` — folder doesn't exist.  
**Reality:** MT5 writes reports to the **root** of `appdata_path\` directly.  
```
C:\Users\DELL\AppData\Roaming\MetaQuotes\Terminal\D0E8209F...\Optimizer_baseline_*.htm
```
**Fix:** `self.mt5_reports_dir = self.appdata_path` (not `/ "reports"`)

### 3. INI Period Format
**Problem:** Code used `Period=16385` (ENUM integer) — MT5 CLI ignores this.  
**Fix:** `Period=H1` (string name). `TIMEFRAME_NAMES = {"H1": "H1", ...}`

### 4. INI Report Path
**Problem:** `Report=C:/absolute/path/...` — MT5 ignores absolute paths in CLI mode.  
**Fix:** `Report=Optimizer_{run_id}` (relative name only — MT5 writes to appdata root).

### 5. MQL5 Reference Syntax
**Problem:** `TL_Positions[idx]&.fieldname` — C++ reference syntax, invalid in MQL5.  
**Fix:** `TL_Positions[idx].fieldname` — direct array index access.

### 6. Stale Report Detection
**Problem:** Old `Optimizer_*.htm` files from previous runs triggered false-positive detection.  
**Fix:** `_clear_stale_reports()` deletes old files before each new run.

---

## 🖥️ How to Run (For Any User)

### Prerequisites
1. MT5 installed at `C:\Program Files\MetaTrader 5\terminal64.exe`
2. LEGSTECH_EA_V2 compiled in MetaEditor (`.ex5` file must exist)
3. Python 3.11 installed at `C:\Users\DELL\AppData\Local\Programs\Python\Python311\`
4. Dependencies installed: `python.exe -m pip install -r requirements.txt`

### Starting the App
1. **Close MT5** if it's open (the optimizer kills and relaunches it automatically from the next run, but first run needs it clean)
2. **Double-click** `Launch Optimizer.bat` on Desktop
3. Browser opens at `http://localhost:5000`
4. Click **▶ Start Optimizer**
5. Watch it run live — MT5 launches automatically, results appear in real-time
6. Reports saved to `MT5_Optimizer\Reports\run_XXX\`

### Save Changes to GitHub
```powershell
cd "C:\Users\DELL\Desktop\MT5_Optimizer"
git add .
git commit -m "describe what changed"
git push
```

---

## 🔧 Environment Details

| Item | Value |
|---|---|
| Python | 3.11 — `C:\Users\DELL\AppData\Local\Programs\Python\Python311\python.exe` |
| GitHub | https://github.com/tonnylegacy/Apex_AI_MT5_EA_Optimizer |
| MT5 Terminal ID | `D0E8209F77C8CF37AD8BF550E51FF075` |
| MT5 Broker | HFMarketsGlobal-Live3 (Build 5660) |
| Tester Agent | `Agent-127.0.0.1-3000` |
| OS | Windows 11 |

**Install all dependencies:**
```powershell
C:\Users\DELL\AppData\Local\Programs\Python\Python311\python.exe -m pip install -r requirements.txt
```

**Key dependencies:**
```
flask, flask-socketio, eventlet          ← Web dashboard
pydantic>=2.5, pandas>=2.1, pyarrow      ← Data layer
lxml>=4.9, beautifulsoup4                ← Report parsing
psutil                                   ← MT5 process control
loguru, rich, scipy, pyyaml              ← Utilities
```

---

## 🧠 Key Design Decisions

1. **Hypothesis-driven, not brute-force** — Detect failure patterns → hypothesize fix → test → validate. Not grid search.
2. **Calmar ratio = primary score** — Return/MaxDrawdown most relevant for live trading.
3. **Three validation gates** — IS → WFV (2 folds) → OOS. No candidate promoted unless all pass.
4. **OOS is sacred** — `2024.07.01 → 2024.12.31` is NEVER used during optimization. Final check only.
5. **MT5 process control** — Always kill existing instance, launch clean, retry once on failure.
6. **UTF-16 LE** — ALL MT5 HTML reports are UTF-16 LE. Always use `document_fromstring(raw_bytes)`.
7. **Report location** — Always `appdata_path\Optimizer_{run_id}.htm` (root, not subfolder).
8. **Python 3.11 required** — System Python on this machine is 3.13 which is missing packages.
9. **Broker TZ = UTC+2** — Session analysis normalizes to UTC. London=07-16, NY=13-22.

---

## 📋 Live Dashboard Events (SocketIO)

The optimizer emits these events to the browser in real-time:

| Event | Payload | When |
|---|---|---|
| `status_change` | `{state, phase}` | Phase transitions |
| `run_started` | `{run_id, phase, period, params}` | MT5 launches |
| `run_complete` | `{score, calmar, pf, dd, trades, ...}` | Report parsed |
| `run_failed` | `{run_id, error}` | MT5 failed |
| `finding` | `{analyzer, severity, description, confidence}` | Each finding |
| `hypotheses` | `{items: [{id, desc, delta}]}` | Mutations proposed |
| `score_update` | `{iteration, score, calmar, pf}` | Chart data point |
| `candidate_promoted` | `{score, delta, params}` | Validation passed |
| `optimization_complete` | `{candidates, best_score, iterations}` | Loop done |
| `log` | `{level, msg}` | Live log feed |

---

## 🚀 Next Steps (Phase 3 — Packaging)

1. **PyInstaller EXE** — Bundle everything into `MT5_Optimizer.exe`
   ```bat
   pyinstaller --onefile --noconsole --name MT5_Optimizer app.py
   ```
   Note: Need `--add-data` flags for templates, static, config, yaml files

2. **Auto `.set` file export** — When a candidate is promoted, export its params as a valid MT5 `.set` file the trader can load directly into the EA

3. **Email/Telegram notification** — Alert the trader when a candidate is promoted

4. **Multi-symbol support** — Run optimization across EURUSD, GBPUSD alongside XAUUSD

5. **Monte Carlo simulation** — Test robustness of promoted candidates

---

## ⚠️ Known Issues & Watch Points

1. **First run needs MT5 closed manually** — From the second run onward, the system kills MT5 automatically via psutil.

2. **Tester Agent path** — `Agent-127.0.0.1-3000` may change if MT5 port changes. If TradeLog CSV not found, check Tester folder for new agent subfolder.

3. **WFV fold count** — Currently 2 folds (hardcoded in `validation/gate.py`). Easy to increase.

4. **XAUUSD data** — If the date range 2022-2023 gives errors, ensure XAUUSD H1 history is downloaded in MT5 (Tools → History Center).

5. **Model 4 vs 0** — `tester_model: 4` (OHLC M1) is fast and reliable. `tester_model: 0` (Every Tick) is more accurate but requires full tick data download and takes ~10x longer.

6. **ShutdownTerminal=1** — MT5 closes itself after each test. Cannot be used for live trading simultaneously.

---

## 📊 Baseline Run Results (Confirmed Working)

First successful parser test on real MT5 report (2026-04-13):

| Metric | Value |
|---|---|
| Net Profit | -$621.15 |
| Profit Factor | 0.95 |
| Max Drawdown | 19.25% |
| Calmar Ratio | -0.32 |
| Sharpe Ratio | -1.60 |
| Total Trades | 597 |
| Win Rate | 77.7% |
| Test Period | 2022.01.01 → 2023.12.31 |
| Model | OHLC M1 (Model 4) |
| Duration | ~56 seconds |

> This is the baseline — the optimizer's job is to improve these numbers over iterations.

---

*Last updated by Antigravity AI — 2026-04-13*  
*GitHub: https://github.com/tonnylegacy/Apex_AI_MT5_EA_Optimizer*  
*Both Phase 1 and Phase 2 are complete. Continue from Phase 3 (packaging).*
