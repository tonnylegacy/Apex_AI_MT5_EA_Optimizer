"""
optimizer_loop.py
Background thread that runs the full optimization loop and emits
real-time SocketIO events to the dashboard.
"""
from __future__ import annotations
import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from loguru import logger

# Project imports
from data.models import Run, RunMetrics, Candidate, Hypothesis
from data.store import DataStore
from mt5.ini_builder import IniBuilder
from mt5.runner import MT5Runner
from mt5.report_parser import ReportParser
from mt5.log_reader import TradeLogReader
from analysis.reversal import ReversalAnalyzer
from analysis.time_performance import TimePerformanceAnalyzer
from analysis.entry_exit_quality import EntryExitQualityAnalyzer
from analysis.equity_curve import EquityCurveAnalyzer
from scoring.composite import CompositeScorer
from mutation.engine import MutationEngine
from validation.gate import ValidationGate
from reports.writer import ReportWriter

import pandas as pd

BASE_DIR      = Path(__file__).parent
MANIFEST_PATH = BASE_DIR / "mutation" / "param_manifest.yaml"
KB_PATH       = BASE_DIR / "mutation" / "knowledge_base.yaml"
DB_PATH       = BASE_DIR / "optimizer.db"
RUNS_DIR      = BASE_DIR / "runs"


class OptimizerLoop:
    """
    Runs the full optimization pipeline in a background thread.
    Pushes events to the SocketIO broadcast channel so the dashboard
    updates in real time.
    """

    def __init__(self, config_path: str, socketio, reports_dir: Path, auto_mode: bool = True):
        self.config_path  = config_path
        self.socketio     = socketio
        self.reports_dir  = reports_dir
        self.auto_mode    = auto_mode
        self.running      = False
        self.paused       = False
        self._stop_flag   = False
        self._skip_flag   = False
        self._pause_event = threading.Event()
        self._pause_event.set()   # not paused initially

        # Live state (read by /api/status)
        self.iteration    = 0
        self.phase        = "idle"
        self.best_score   = 0.0
        self.current_run_id: Optional[str] = None
        self.score_history: list[dict]     = []
        self.run_start_ts: Optional[float] = None

        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

    # ── Controls ──────────────────────────────────────────────────────────────

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self._pause_event.clear()
            self._emit("status_change", {"state": "paused"})
        else:
            self._pause_event.set()
            self._emit("status_change", {"state": "running"})

    def stop(self):
        self._stop_flag = True
        self._pause_event.set()
        self.running = False

    def skip_hypothesis(self):
        self._skip_flag = True

    def get_status(self) -> dict:
        elapsed = int(time.time() - self.run_start_ts) if self.run_start_ts else 0
        return {
            "state":       "running" if self.running else ("paused" if self.paused else "idle"),
            "iteration":   self.iteration,
            "phase":       self.phase,
            "best_score":  round(self.best_score, 4),
            "run_id":      self.current_run_id,
            "elapsed_s":   elapsed,
        }

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        self.running   = True
        self._stop_flag = False
        self.run_start_ts = time.time()

        cfg = self.cfg
        store, builder, runner, parser, log_rdr, analyzers, scorer, mutator, gate, writer = (
            self._build_components()
        )

        per = cfg["periods"]
        self._emit("status_change", {"state": "running", "phase": "baseline"})
        self._emit("log", {"level": "info", "msg": "🚀 Optimizer started"})

        # ── Baseline run ──────────────────────────────────────────────────────
        self.phase = "baseline"
        default_params = builder.default_params()
        baseline_id    = f"baseline_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        baseline_metrics, baseline_trades = self._execute_run(
            run_id=baseline_id,
            params=default_params,
            period_start=per["train_start"],
            period_end=per["train_end"],
            phase="baseline",
            hypothesis_id=None,
            store=store, builder=builder, runner=runner,
            parser=parser, log_rdr=log_rdr, analyzers=analyzers, scorer=scorer,
        )

        if baseline_metrics is None:
            self._emit("error", {"msg": "Baseline run failed. Check MT5 configuration."})
            self.running = False
            return

        # Write baseline report
        findings = self._run_analysis(baseline_id, baseline_trades, baseline_metrics,
                                      analyzers, store)
        writer.write(baseline_id, baseline_metrics, baseline_trades, findings, default_params)

        self.best_score    = baseline_metrics.composite_score
        current_params     = default_params.copy()
        current_metrics    = baseline_metrics
        no_improve_count   = 0

        max_iter  = cfg["optimization"]["max_iterations"]
        conv_win  = cfg["optimization"]["convergence_window"]
        conv_thr  = cfg["optimization"]["convergence_threshold"]

        # ── Iteration loop ────────────────────────────────────────────────────
        while self.iteration < max_iter and not self._stop_flag:
            self._pause_event.wait()
            if self._stop_flag:
                break

            self.iteration += 1
            self.phase = "analyze"
            self._emit("iteration_start", {"iteration": self.iteration})
            self._emit("log", {"level": "info", "msg": f"━━ Iteration {self.iteration} ━━"})

            # Load latest trades (for re-analysis)
            trades_df = store.load_trades(current_metrics.run_id)
            if trades_df.empty and not baseline_trades.empty:
                trades_df = baseline_trades

            # Analysis
            self.phase = "analyze"
            findings = self._run_analysis(
                current_metrics.run_id, trades_df, current_metrics, analyzers, store
            )

            if not findings:
                self._emit("log", {"level": "warn", "msg": "No actionable findings. Stopping."})
                break

            # Mutation proposals
            recent_deltas = store.get_recent_param_deltas(cfg["mutation"]["dedup_lookback_runs"])
            hypotheses = mutator.propose(
                findings=findings,
                current_params=current_params,
                recent_deltas=recent_deltas,
                max_proposals=cfg["mutation"]["max_hypotheses_per_cycle"],
            )

            if not hypotheses:
                self._emit("log", {"level": "warn", "msg": "No new hypotheses. Stopping."})
                break

            self._emit("hypotheses", {"items": [
                {
                    "id":      h.hypothesis_id,
                    "desc":    h.description,
                    "delta":   h.param_delta,
                    "strategy": h.strategy,
                }
                for h in hypotheses
            ]})

            # Test each hypothesis
            iteration_best: Optional[RunMetrics] = None
            iteration_best_params: Optional[dict] = None
            iteration_best_hyp: Optional[Hypothesis] = None

            for idx, hyp in enumerate(hypotheses):
                self._pause_event.wait()
                if self._stop_flag or self._skip_flag:
                    self._skip_flag = False
                    break

                test_params = {**current_params, **hyp.param_delta}
                run_id      = f"iter{self.iteration:03d}_h{idx+1}"
                store.save_hypothesis(hyp)

                self._emit("log", {
                    "level": "info",
                    "msg":   f"Testing H{idx+1}: {hyp.description[:60]}"
                })
                self._emit("hypothesis_testing", {"idx": idx+1, "desc": hyp.description})

                test_metrics, test_trades = self._execute_run(
                    run_id=run_id,
                    params=test_params,
                    period_start=per["train_start"],
                    period_end=per["train_end"],
                    phase="explore",
                    hypothesis_id=hyp.hypothesis_id,
                    store=store, builder=builder, runner=runner,
                    parser=parser, log_rdr=log_rdr, analyzers=analyzers, scorer=scorer,
                )

                if test_metrics is None:
                    continue

                delta_score = test_metrics.composite_score - current_metrics.composite_score
                color = "green" if delta_score > 0 else "red"
                self._emit("log", {
                    "level":  "success" if delta_score > 0 else "warn",
                    "msg":    f"H{idx+1} score: {current_metrics.composite_score:.4f} → "
                              f"{test_metrics.composite_score:.4f} ({delta_score:+.4f})"
                })

                # Write per-run report
                h_findings = self._run_analysis(run_id, test_trades, test_metrics, analyzers, store)
                writer.write(run_id, test_metrics, test_trades, h_findings, test_params,
                             hypothesis=hyp, baseline_score=current_metrics.composite_score)

                store.update_hypothesis_status(hyp.hypothesis_id, "tested", run_id)

                if iteration_best is None or test_metrics.composite_score > iteration_best.composite_score:
                    iteration_best        = test_metrics
                    iteration_best_params = test_params
                    iteration_best_hyp    = hyp

            if iteration_best is None:
                no_improve_count += 1
                continue

            # Validation gate
            self.phase = "validate"
            gate_result = gate.run_is_check(iteration_best)
            if not gate_result.passed:
                self._emit("log", {"level": "error", "msg": f"IS gate failed: {gate_result.reason}"})
                store.update_hypothesis_status(iteration_best_hyp.hypothesis_id, "rejected")
                no_improve_count += 1
                continue

            # Walk-forward
            self._emit("log", {"level": "info", "msg": "Running walk-forward validation..."})
            wfv = gate.run_walk_forward(
                iteration_best_params, cfg, store, builder, runner,
                parser, log_rdr, analyzers, scorer,
            )
            self._emit("log", {
                "level": "success" if wfv.passed else "warn",
                "msg":   f"WFV: OOS/IS ratio = {wfv.oos_is_ratio:.2f} "
                         f"({'PASS ✅' if wfv.passed else 'FAIL ❌'})"
            })

            if not wfv.passed:
                store.update_hypothesis_status(iteration_best_hyp.hypothesis_id, "rejected")
                no_improve_count += 1
                continue

            # Promote candidate
            candidate = Candidate(
                run_id=iteration_best.run_id,
                composite_score=iteration_best.composite_score,
                params=iteration_best_params,
            )
            store.save_candidate(candidate)
            store.update_hypothesis_status(iteration_best_hyp.hypothesis_id, "validated")

            self._emit("candidate_promoted", {
                "candidate_id":  candidate.candidate_id,
                "score":         round(candidate.composite_score, 4),
                "delta":         round(candidate.composite_score - self.best_score, 4),
                "params":        candidate.params,
            })
            self._emit("log", {
                "level": "success",
                "msg":   f"✅ Candidate promoted! Score: {candidate.composite_score:.4f}"
            })

            improvement = iteration_best.composite_score - self.best_score
            if improvement >= conv_thr:
                current_params   = iteration_best_params
                current_metrics  = iteration_best
                self.best_score  = iteration_best.composite_score
                no_improve_count = 0
            else:
                no_improve_count += 1

            # Update score chart
            self.score_history.append({
                "iteration": self.iteration,
                "score":     round(self.best_score, 4),
                "calmar":    round(current_metrics.calmar_ratio, 4),
                "pf":        round(current_metrics.profit_factor, 4),
                "ts":        datetime.utcnow().isoformat(),
            })
            self._emit("score_update", self.score_history[-1])

            if no_improve_count >= conv_win:
                self._emit("log", {
                    "level": "warn",
                    "msg":   f"Converged: no improvement in {no_improve_count} iterations."
                })
                break

        # ── Done ─────────────────────────────────────────────────────────────
        candidates = store.list_candidates()
        self._emit("optimization_complete", {
            "candidates":  len(candidates),
            "best_score":  round(self.best_score, 4),
            "iterations":  self.iteration,
        })
        self._emit("log", {"level": "success", "msg": "🏁 Optimization complete!"})
        self.running = False
        self.phase   = "idle"

    # ── Single run ────────────────────────────────────────────────────────────

    def _execute_run(
        self,
        run_id, params, period_start, period_end, phase, hypothesis_id,
        store, builder, runner, parser, log_rdr, analyzers, scorer,
    ):
        cfg = self.cfg
        self.current_run_id = run_id
        run_dir = RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        self._emit("run_started", {
            "run_id":  run_id,
            "phase":   phase,
            "period":  f"{period_start} → {period_end}",
            "params":  {k: v for k, v in list(params.items())[:8]},  # first 8 for display
        })

        ini_path = builder.build(
            run_id=run_id, params=params,
            period_start=period_start, period_end=period_end,
            output_dir=run_dir, phase=phase,
        )

        run = Run(
            run_id=run_id, ea_name=cfg["ea"]["name"],
            symbol=cfg["ea"]["symbol"], timeframe=cfg["ea"]["timeframe"],
            period_start=period_start, period_end=period_end,
            params=params, phase=phase, hypothesis_id=hypothesis_id,
            tester_model=cfg["mt5"]["tester_model"],
            ini_snapshot=ini_path.read_text(),
        )
        store.save_run(run)

        self._emit("log", {"level": "info", "msg": f"⏳ MT5 running: {run_id}"})
        result = runner.run(run_id, ini_path, run_dir / "report",
                            log_csv_search_dir=Path(cfg["mt5"]["mql5_files_path"]))

        if not result.success:
            self._emit("run_failed", {"run_id": run_id, "error": result.error_message})
            return None, pd.DataFrame()

        metrics, trades = parser.parse(result.report_xml, result.report_html)
        if metrics is None:
            self._emit("run_failed", {"run_id": run_id, "error": "Could not parse report"})
            return None, pd.DataFrame()
        metrics.run_id = run_id

        if trades:
            trades = log_rdr.merge(
                trades, result.trade_log_csv,
                reversal_mfe_threshold_pips=cfg["analysis"]["reversal"]["mfe_threshold_pips"],
            )
            trades_df = pd.DataFrame([t.model_dump() for t in trades])
        else:
            trades_df = pd.DataFrame()

        if not trades_df.empty:
            if "result_class" in trades_df.columns:
                losers    = trades_df[trades_df["net_money"] < 0]
                reversals = trades_df[trades_df["result_class"] == "reversal"]
                metrics.reversal_rate = len(reversals) / max(1, len(losers))
            if "mfe_capture_ratio" in trades_df.columns:
                metrics.avg_mfe_capture = float(trades_df["mfe_capture_ratio"].dropna().mean() or 0)

        metrics.composite_score = scorer.score(metrics)
        store.save_metrics(metrics)
        if not trades_df.empty:
            store.save_trades(run_id, trades)
        run.report_path = result.report_xml
        store.save_run(run)

        self._emit("run_complete", {
            "run_id":       run_id,
            "phase":        phase,
            "net_profit":   round(metrics.net_profit, 2),
            "profit_factor": round(metrics.profit_factor, 3),
            "calmar":       round(metrics.calmar_ratio, 3),
            "drawdown_pct": round(metrics.max_drawdown_pct * 100, 1),
            "win_rate":     round(metrics.win_rate * 100, 1),
            "total_trades": metrics.total_trades,
            "score":        round(metrics.composite_score, 4),
            "reversal_rate": round((metrics.reversal_rate or 0) * 100, 1),
            "mfe_capture":  round((metrics.avg_mfe_capture or 0) * 100, 1),
        })

        return metrics, trades_df

    def _run_analysis(self, run_id, trades_df, metrics, analyzers, store):
        all_findings = []
        for az in analyzers:
            findings = az.run(trades_df, metrics, run_id)
            for f in findings:
                self._emit("finding", {
                    "analyzer":    f.analyzer,
                    "severity":    f.severity,
                    "description": f.description,
                    "confidence":  round(f.confidence, 2),
                    "impact":      round(f.impact_estimate_pnl, 0),
                })
            all_findings.extend(findings)
        all_findings.sort(key=lambda f: f.confidence, reverse=True)
        store.save_findings(all_findings)
        return all_findings

    # ── SocketIO emit helper ──────────────────────────────────────────────────

    def _emit(self, event: str, data: dict = {}):
        try:
            self.socketio.emit(event, data)
        except Exception as e:
            logger.debug(f"Emit error ({event}): {e}")

    # ── Component factory ─────────────────────────────────────────────────────

    def _build_components(self):
        cfg = self.cfg
        store    = DataStore(DB_PATH, RUNS_DIR)
        builder  = IniBuilder(self.config_path, str(MANIFEST_PATH))
        runner   = MT5Runner(self.config_path)
        parser   = ReportParser()
        log_rdr  = TradeLogReader(
            broker_tz_offset_hours=cfg["broker"]["timezone_offset_hours"],
        )
        analyzers = [
            ReversalAnalyzer(
                mfe_threshold_pips=cfg["analysis"]["reversal"]["mfe_threshold_pips"],
                min_reversal_rate=cfg["analysis"]["reversal"]["min_reversal_rate"],
                permutation_n=cfg["analysis"]["reversal"]["permutation_n"],
            ),
            TimePerformanceAnalyzer(
                z_score_threshold=cfg["analysis"]["time_performance"]["z_score_threshold"],
                min_bucket_trades=cfg["analysis"]["time_performance"]["min_trades_per_bucket"],
                permutation_n=cfg["analysis"]["time_performance"]["permutation_n"],
            ),
            EntryExitQualityAnalyzer(
                poor_exit_threshold=cfg["analysis"]["entry_exit"]["poor_exit_quality"],
                poor_entry_threshold=cfg["analysis"]["entry_exit"]["poor_entry_quality"],
            ),
            EquityCurveAnalyzer(
                max_flatness=cfg["analysis"]["equity_curve"]["max_flatness_score"],
                min_r_squared=cfg["analysis"]["equity_curve"]["min_r_squared"],
            ),
        ]
        scorer  = CompositeScorer(self.config_path)
        mutator = MutationEngine(KB_PATH, MANIFEST_PATH,
                                 dedup_lookback=cfg["mutation"]["dedup_lookback_runs"])
        gate    = ValidationGate(self.config_path)
        writer  = ReportWriter(self.reports_dir)
        return store, builder, runner, parser, log_rdr, analyzers, scorer, mutator, gate, writer
