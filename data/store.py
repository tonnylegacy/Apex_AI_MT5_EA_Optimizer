"""
data/store.py
SQLite (metadata) + Parquet (per-run trade data) storage layer.
"""
from __future__ import annotations
import json
import sqlite3
import shutil
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger

from data.models import (
    Run, RunMetrics, Finding, Hypothesis, Candidate, Trade
)


# ── Schema DDL ────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    run_ts          TEXT NOT NULL,
    ea_name         TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    phase           TEXT NOT NULL,
    hypothesis_id   TEXT,
    tester_model    INTEGER DEFAULT 0,
    ini_snapshot    TEXT,
    report_path     TEXT,
    log_csv_path    TEXT
);

CREATE TABLE IF NOT EXISTS run_metrics (
    run_id              TEXT PRIMARY KEY REFERENCES runs(run_id),
    net_profit          REAL,
    profit_factor       REAL,
    max_drawdown_abs    REAL,
    max_drawdown_pct    REAL,
    calmar_ratio        REAL,
    sharpe_ratio        REAL,
    total_trades        INTEGER,
    win_rate            REAL,
    avg_win             REAL,
    avg_loss            REAL,
    recovery_factor     REAL,
    largest_loss        REAL,
    expected_payoff     REAL,
    avg_mfe_capture     REAL,
    avg_mfe_pips        REAL,
    avg_mae_pips        REAL,
    reversal_rate       REAL,
    composite_score     REAL
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id          TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES runs(run_id),
    analyzer            TEXT NOT NULL,
    description         TEXT NOT NULL,
    severity            TEXT NOT NULL,
    confidence          REAL NOT NULL,
    impact_estimate_pnl REAL DEFAULT 0,
    suggested_params    TEXT,
    evidence            TEXT
);

CREATE TABLE IF NOT EXISTS hypotheses (
    hypothesis_id   TEXT PRIMARY KEY,
    parent_run_id   TEXT NOT NULL REFERENCES runs(run_id),
    finding_ids     TEXT NOT NULL,
    description     TEXT NOT NULL,
    param_delta     TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    kb_rule_id      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    tested_run_id   TEXT
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id    TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    promoted_ts     TEXT NOT NULL,
    composite_score REAL NOT NULL,
    oos_score       REAL,
    params_json     TEXT NOT NULL,
    lineage_json    TEXT
);
"""


# ── DataStore ─────────────────────────────────────────────────────────────────

class DataStore:
    """
    Central storage interface.
    - SQLite for all structured metadata (runs, metrics, findings, hypotheses, candidates)
    - Parquet for per-run trade arrays (cheap columnar access for analysis)
    """

    def __init__(self, db_path: str | Path, runs_dir: str | Path):
        self.db_path  = Path(db_path)
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
        logger.debug(f"Database initialised at {self.db_path}")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Runs ──────────────────────────────────────────────────────────────────

    def save_run(self, run: Run) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO runs
                (run_id, run_ts, ea_name, symbol, timeframe, period_start, period_end,
                 params_json, phase, hypothesis_id, tester_model, ini_snapshot,
                 report_path, log_csv_path)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                run.run_id,
                run.run_ts.isoformat(),
                run.ea_name, run.symbol, run.timeframe,
                run.period_start, run.period_end,
                json.dumps(run.params),
                run.phase, run.hypothesis_id, run.tester_model,
                run.ini_snapshot, run.report_path, run.log_csv_path,
            ))
        logger.debug(f"Saved run {run.run_id}")

    def get_run(self, run_id: str) -> Optional[Run]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json"))
        return Run(**d)

    def list_runs(self, phase: Optional[str] = None, n: int = 100) -> list[dict]:
        q = "SELECT run_id, run_ts, phase, hypothesis_id FROM runs"
        args: list[Any] = []
        if phase:
            q += " WHERE phase=?"
            args.append(phase)
        q += " ORDER BY run_ts DESC LIMIT ?"
        args.append(n)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(q, args).fetchall()]

    # ── Run Metrics ───────────────────────────────────────────────────────────

    def save_metrics(self, m: RunMetrics) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO run_metrics
                (run_id, net_profit, profit_factor, max_drawdown_abs, max_drawdown_pct,
                 calmar_ratio, sharpe_ratio, total_trades, win_rate, avg_win, avg_loss,
                 recovery_factor, largest_loss, expected_payoff,
                 avg_mfe_capture, avg_mfe_pips, avg_mae_pips, reversal_rate,
                 composite_score)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                m.run_id, m.net_profit, m.profit_factor,
                m.max_drawdown_abs, m.max_drawdown_pct,
                m.calmar_ratio, m.sharpe_ratio,
                m.total_trades, m.win_rate, m.avg_win, m.avg_loss,
                m.recovery_factor, m.largest_loss, m.expected_payoff,
                m.avg_mfe_capture, m.avg_mfe_pips, m.avg_mae_pips,
                m.reversal_rate, m.composite_score,
            ))

    def get_metrics(self, run_id: str) -> Optional[RunMetrics]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM run_metrics WHERE run_id=?", (run_id,)
            ).fetchone()
        return RunMetrics(**dict(row)) if row else None

    def best_candidate_score(self) -> float:
        """Return the highest composite_score ever achieved by a promoted candidate."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(composite_score) FROM candidates"
            ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    # ── Findings ──────────────────────────────────────────────────────────────

    def save_findings(self, findings: list[Finding]) -> None:
        with self._conn() as conn:
            for f in findings:
                conn.execute("""
                    INSERT OR REPLACE INTO findings
                    (finding_id, run_id, analyzer, description, severity,
                     confidence, impact_estimate_pnl, suggested_params, evidence)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    f.finding_id, f.run_id, f.analyzer, f.description,
                    f.severity, f.confidence, f.impact_estimate_pnl,
                    json.dumps(f.suggested_params), json.dumps(f.evidence),
                ))

    def get_findings(self, run_id: str) -> list[Finding]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM findings WHERE run_id=? ORDER BY confidence DESC",
                (run_id,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["suggested_params"] = json.loads(d["suggested_params"] or "{}")
            d["evidence"]         = json.loads(d["evidence"] or "{}")
            out.append(Finding(**d))
        return out

    # ── Hypotheses ────────────────────────────────────────────────────────────

    def save_hypothesis(self, h: Hypothesis) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO hypotheses
                (hypothesis_id, parent_run_id, finding_ids, description,
                 param_delta, strategy, kb_rule_id, status, tested_run_id)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                h.hypothesis_id, h.parent_run_id,
                json.dumps(h.finding_ids), h.description,
                json.dumps(h.param_delta), h.strategy,
                h.kb_rule_id, h.status, h.tested_run_id,
            ))

    def update_hypothesis_status(
        self, hypothesis_id: str,
        status: str,
        tested_run_id: Optional[str] = None
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE hypotheses SET status=?, tested_run_id=? WHERE hypothesis_id=?",
                (status, tested_run_id, hypothesis_id)
            )

    def get_recent_param_deltas(self, n: int = 10) -> list[dict]:
        """Return param_delta dicts from the last N tested hypotheses (for dedup)."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT param_delta FROM hypotheses
                WHERE status IN ('tested','validated','rejected')
                ORDER BY rowid DESC LIMIT ?
            """, (n,)).fetchall()
        return [json.loads(r["param_delta"]) for r in rows]

    # ── Candidates ────────────────────────────────────────────────────────────

    def save_candidate(self, c: Candidate) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO candidates
                (candidate_id, run_id, promoted_ts, composite_score,
                 oos_score, params_json, lineage_json)
                VALUES (?,?,?,?,?,?,?)
            """, (
                c.candidate_id, c.run_id,
                c.promoted_ts.isoformat(),
                c.composite_score, c.oos_score,
                json.dumps(c.params),
                json.dumps(c.lineage),
            ))
        logger.info(f"Promoted candidate {c.candidate_id} (score={c.composite_score:.4f})")

    def list_candidates(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM candidates ORDER BY composite_score DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Trade Data (Parquet) ──────────────────────────────────────────────────

    def save_trades(self, run_id: str, trades: list[Trade]) -> Path:
        """Serialise Trade objects to Parquet. Returns path to written file."""
        parquet_path = self.runs_dir / run_id / "trades.parquet"
        parquet_path.parent.mkdir(parents=True, exist_ok=True)

        rows = [t.model_dump() for t in trades]
        df = pd.DataFrame(rows)
        # Ensure datetime columns are proper dtype
        for col in ["open_time", "close_time"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True)

        df.to_parquet(parquet_path, index=False, engine="pyarrow")
        logger.debug(f"Saved {len(trades)} trades for run {run_id}")
        return parquet_path

    def load_trades(self, run_id: str) -> pd.DataFrame:
        """Load trade Parquet for a given run. Returns empty DataFrame if not found."""
        parquet_path = self.runs_dir / run_id / "trades.parquet"
        if not parquet_path.exists():
            logger.warning(f"No trade parquet found for run {run_id}")
            return pd.DataFrame()
        return pd.read_parquet(parquet_path, engine="pyarrow")
