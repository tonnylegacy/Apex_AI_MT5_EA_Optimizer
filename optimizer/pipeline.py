"""
optimizer/pipeline.py
The Smart 3-Phase Optimization Pipeline.

Replaces optimizer_loop.py as the core orchestration engine.
Receives a SessionConfig → runs Phase 1, 2, 3 → emits SocketIO events.

Architecture:
    Phase 1: Broad Discovery    (LHS samples, 20–30 runs)
    Phase 2: Deep Refinement    (neighbor search around top 3)
    Phase 3: Validation         (OOS backtest + sensitivity)
    Decision: RECOMMENDED / RISKY / NOT_RELIABLE + .set file download
"""
from __future__ import annotations

import time
import uuid
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Callable

import yaml
from loguru import logger

from ea.registry import EARegistry, EAProfile
from ea.schema import ParameterSchema
from mt5.ini_builder import IniBuilder
from mt5.runner import MT5Runner
from mt5.report_parser import ReportParser
from data.models import Run
from data.store import DataStore
from reports.writer import ReportWriter

from optimizer.session_config import SessionConfig
from optimizer.lhs_sampler import LatinHypercubeSampler
from optimizer.result_ranker import ResultRanker, RankedResult
from optimizer.budget import BudgetManager

import pandas as pd

BASE_DIR  = Path(__file__).parent.parent
RUNS_DIR  = BASE_DIR / "runs"
DB_PATH   = BASE_DIR / "optimizer.db"


class OptimizationPipeline:
    """
    3-phase autonomous optimization pipeline. Run in a background thread.

    Usage:
        pipeline = OptimizationPipeline("config.yaml", socketio, reports_dir)
        pipeline.configure(session_config)
        thread = threading.Thread(target=pipeline.run, daemon=True)
        thread.start()
    """

    def __init__(self, config_path: str, socketio, reports_dir: Path):
        self.config_path  = config_path
        self.socketio     = socketio
        self.reports_dir  = reports_dir

        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        # Runtime state
        self.session: Optional[SessionConfig] = None
        self.running   = False
        self._stop_flag = False
        self._phase    = "idle"
        self._run_count = 0
        self._total_runs = 0
        self.best_result: Optional[RankedResult] = None
        self.phase1_results: list[RankedResult] = []
        self.phase2_results: list[RankedResult] = []
        self.final_result: Optional[RankedResult] = None
        self.verdict: Optional[str] = None
        self.best_set_path: Optional[Path] = None
        self.run_start_ts: Optional[float] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def configure(self, session: SessionConfig) -> None:
        self.session = session

    def stop(self) -> None:
        self._stop_flag = True
        self._emit("status_change", {"state": "stopping"})

    def get_status(self) -> dict:
        elapsed = int(time.time() - self.run_start_ts) if self.run_start_ts else 0
        return {
            "state":         "running" if self.running else "idle",
            "phase":         self._phase,
            "run_count":     self._run_count,
            "total_runs":    self._total_runs,
            "best_score":    round(self.best_result.score, 4) if self.best_result else 0.0,
            "verdict":       self.verdict,
            "elapsed_s":     elapsed,
            "ea_name":       self.session.ea_name if self.session else "",
            "symbol":        self.session.symbol if self.session else "",
            "timeframe":     self.session.timeframe if self.session else "",
        }

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self) -> None:
        self.running     = True
        self._stop_flag  = False
        self.run_start_ts = time.time()

        try:
            self._run_pipeline()
        except Exception as e:
            logger.exception(f"Pipeline crashed: {e}")
            self._emit("error", {"msg": f"Pipeline error: {e}"})
        finally:
            self.running = False
            self._phase  = "idle"
            self._emit("status_change", {"state": "idle"})

    def _run_pipeline(self) -> None:
        cfg     = self.session
        self._total_runs = cfg.total_budget_runs

        self._emit("status_change", {"state": "running", "phase": "setup"})
        self._log("info", f"🚀 Smart Optimizer started — {cfg.ea_name} | {cfg.symbol} | {cfg.timeframe}")
        self._log("info", f"📋 Budget: {cfg.budget_minutes} min | Samples: {cfg.phase1_samples} Phase1 + {cfg.phase2_samples} Phase2 + {cfg.phase3_samples} Phase3")

        # ── Build components ─────────────────────────────────────────────────
        reg     = EARegistry(self.config_path)
        profile = reg.get(cfg.ea_name)
        schema  = reg.get_schema(profile)

        # Apply user's param selection if specified
        if cfg.selected_params:
            for p in schema.parameters.values():
                if p.type != "fixed":
                    p.optimize = p.name in cfg.selected_params

        builder  = IniBuilder(self.config_path, schema=schema)
        runner   = MT5Runner(self.config_path)
        parser   = ReportParser()
        store    = DataStore(DB_PATH, RUNS_DIR)
        writer   = ReportWriter(self.reports_dir)
        sampler  = LatinHypercubeSampler(seed=int(time.time()))
        ranker   = ResultRanker(weights=cfg.scoring_weights)
        budget   = BudgetManager(cfg.budget_minutes)

        budget.start()

        # ── Phase 1: Broad Discovery ─────────────────────────────────────────
        if self._stop_flag:
            return

        self._phase = "phase1"
        self._emit("phase_start", {"phase": "phase1", "total": cfg.phase1_samples})
        self._log("info", f"━━ Phase 1: Broad Discovery ({cfg.phase1_samples} configurations) ━━")

        samples = sampler.sample(schema, cfg.phase1_samples)
        phase1_raw: list[RankedResult] = []

        for i, params in enumerate(samples):
            if self._stop_flag:
                break

            run_id = f"p1_{i+1:02d}_{datetime.utcnow().strftime('%H%M%S')}"
            self._log("info", f"[{i+1}/{cfg.phase1_samples}] Testing configuration {i+1}...")

            t0 = time.time()
            result = self._execute_run(
                run_id, params, cfg.train_start, cfg.train_end,
                "phase1", builder, runner, parser, store, writer, ranker, profile
            )
            budget.record_run(time.time() - t0)
            phase1_raw.append(result)
            self._run_count += 1

            # Emit progress after each run
            self._emit("run_complete", {
                "run_id":         run_id,
                "phase":          "phase1",
                "run_number":     i + 1,
                "total":          cfg.phase1_samples,
                "net_profit":     round(result.net_profit, 2),
                "calmar":         round(result.calmar, 3),
                "profit_factor":  round(result.profit_factor, 3),
                "win_rate":       round(result.win_rate, 1),
                "max_drawdown":   round(result.max_drawdown, 2),
                "total_trades":   result.total_trades,
                "passing":        result.passing,
                "progress_pct":   round((i + 1) / cfg.phase1_samples * 100),
                "budget_summary": budget.summary(),
            })

            if budget.is_exhausted():
                self._log("warning", "⏱ Time budget reached during Phase 1")
                break

        # Rank Phase 1 results
        self.phase1_results = ranker.rank(phase1_raw)
        top5 = ranker.top_n(self.phase1_results, 5)

        n_passing = sum(1 for r in self.phase1_results if r.passing)
        self._log("info" if n_passing > 0 else "warning",
            f"Phase 1 complete: {n_passing}/{len(self.phase1_results)} profitable configurations found"
        )

        # Emit Phase 1 summary for checkpoint UI
        self._emit("phase1_complete", {
            "total_tested": len(self.phase1_results),
            "n_passing":    n_passing,
            "top_results":  [self._result_to_dict(r) for r in top5],
        })

        if not top5:
            self._emit("no_profitable_config", {
                "msg": (
                    "Phase 1 found no profitable configuration. "
                    "Suggestions: try a different date range, check EA settings, "
                    "or try a different timeframe."
                )
            })
            self._log("error", "❌ No profitable configuration found in Phase 1. Stopping.")
            return

        if self._stop_flag:
            return

        # ── Phase 2: Deep Refinement ─────────────────────────────────────────
        if not budget.can_fit(3):
            self._log("warning", "⏱ Not enough budget for Phase 2 — using Phase 1 winner directly")
            self.final_result = top5[0]
        else:
            self._phase = "phase2"
            self._emit("phase_start", {"phase": "phase2", "total": cfg.phase2_samples})
            self._log("info", f"━━ Phase 2: Deep Refinement (refining top {min(3, len(top5))} configs) ━━")

            top3        = top5[:3]
            phase2_raw  = []
            neighbors_per = cfg.phase2_samples // max(1, len(top3))

            for rank_i, base_result in enumerate(top3):
                if self._stop_flag:
                    break

                self._log("info", f"  Refining config #{rank_i+1}: {base_result.run_id}")
                neighbors = sampler.sample_neighbors(
                    base_result.params, schema,
                    n_neighbors=neighbors_per, step_pct=0.20
                )

                for j, params in enumerate(neighbors):
                    if self._stop_flag or budget.is_exhausted():
                        break

                    run_id = f"p2_{rank_i+1}_{j+1:02d}_{datetime.utcnow().strftime('%H%M%S')}"
                    t0 = time.time()
                    result = self._execute_run(
                        run_id, params, cfg.train_start, cfg.train_end,
                        "phase2", builder, runner, parser, store, writer, ranker, profile
                    )
                    budget.record_run(time.time() - t0)
                    phase2_raw.append(result)
                    self._run_count += 1

                    self._emit("run_complete", {
                        "run_id":     run_id,
                        "phase":      "phase2",
                        "net_profit": round(result.net_profit, 2),
                        "calmar":     round(result.calmar, 3),
                        "passing":    result.passing,
                        "progress_pct": round(self._run_count / self._total_runs * 100),
                    })

            # Best from Phase 1 + Phase 2 combined
            all_results = list(self.phase1_results) + ranker.rank(phase2_raw)
            all_ranked  = ranker.rank(
                [r for r in all_results if r.passing]
                or list(self.phase1_results)  # fallback to Phase 1 if P2 all fail
            )
            self.phase2_results = ranker.rank(phase2_raw)
            self.final_result   = all_ranked[0] if all_ranked else top5[0]

            self._emit("phase2_complete", {
                "best_run_id":   self.final_result.run_id,
                "best_score":    round(self.final_result.score, 4),
                "best_profit":   round(self.final_result.net_profit, 2),
                "best_calmar":   round(self.final_result.calmar, 3),
            })
            self._log("info",
                f"Phase 2 complete. Best config: {self.final_result.run_id} "
                f"(profit=${self.final_result.net_profit:.0f}, calmar={self.final_result.calmar:.2f})"
            )

        if self._stop_flag:
            return

        # ── Phase 3: Validation ───────────────────────────────────────────────
        if not budget.can_fit(2):
            self._log("warning", "⏱ Not enough budget for Phase 3 validation — skipping OOS test")
            self.verdict = "RISKY"
            oos_result = None
        else:
            self._phase = "phase3"
            self._emit("phase_start", {"phase": "phase3", "total": cfg.phase3_samples})
            self._log("info", "━━ Phase 3: Validation (out-of-sample + sensitivity) ━━")

            # OOS test
            oos_id = f"oos_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            self._log("info", f"  OOS test: {cfg.val_start} → {cfg.val_end}")
            t0 = time.time()
            oos_result = self._execute_run(
                oos_id, self.final_result.params,
                cfg.val_start, cfg.val_end,
                "phase3_oos", builder, runner, parser, store, writer, ranker, profile
            )
            budget.record_run(time.time() - t0)
            self._run_count += 1

            self._emit("run_complete", {
                "run_id":     oos_id,
                "phase":      "phase3_oos",
                "net_profit": round(oos_result.net_profit, 2),
                "calmar":     round(oos_result.calmar, 3),
                "passing":    oos_result.passing,
            })

            # Sensitivity test (2 runs: nudge top param up and down)
            sens_results = []
            opts = schema.optimizable()
            if opts and budget.can_fit(2):
                top_param = opts[0]  # first optimizable param
                for direction in [1, -1]:
                    if budget.is_exhausted() or self._stop_flag:
                        break
                    nudged = dict(self.final_result.params)
                    current = float(nudged.get(top_param.name, top_param.default))
                    span    = float(top_param.max) - float(top_param.min)
                    nudged[top_param.name] = top_param.clamp(current + direction * span * 0.20)

                    sens_id = f"sens_{direction}_{datetime.utcnow().strftime('%H%M%S')}"
                    t0 = time.time()
                    sr = self._execute_run(
                        sens_id, nudged, cfg.train_start, cfg.train_end,
                        "phase3_sens", builder, runner, parser, store, writer, ranker, profile
                    )
                    budget.record_run(time.time() - t0)
                    sens_results.append(sr)
                    self._run_count += 1

            # Determine verdict
            self.verdict = self._determine_verdict(self.final_result, oos_result, sens_results)

        # ── Generate .set output ─────────────────────────────────────────────
        self.best_set_path = self._write_set_file(self.final_result, schema, cfg)

        # ── Final emit ───────────────────────────────────────────────────────
        self._emit("optimization_complete", {
            "verdict":       self.verdict,
            "best_run_id":   self.final_result.run_id,
            "net_profit":    round(self.final_result.net_profit, 2),
            "calmar":        round(self.final_result.calmar, 3),
            "profit_factor": round(self.final_result.profit_factor, 3),
            "win_rate":      round(self.final_result.win_rate, 1),
            "max_drawdown":  round(self.final_result.max_drawdown, 2),
            "total_trades":  self.final_result.total_trades,
            "oos_profit":    round(oos_result.net_profit, 2) if oos_result else None,
            "oos_calmar":    round(oos_result.calmar, 3) if oos_result else None,
            "set_file_url":  f"/download_set/{self.final_result.run_id}" if self.best_set_path else None,
            "total_runs":    self._run_count,
            "elapsed_min":   round((time.time() - self.run_start_ts) / 60, 1),
        })

        verdict_icon = {"RECOMMENDED": "✅", "RISKY": "⚠️", "NOT_RELIABLE": "❌"}.get(self.verdict, "?")
        self._log("success" if self.verdict == "RECOMMENDED" else "warning",
            f"{verdict_icon} VERDICT: {self.verdict} | "
            f"Profit: ${self.final_result.net_profit:.0f} | "
            f"Calmar: {self.final_result.calmar:.2f}"
        )

    # ── Single run executor ───────────────────────────────────────────────────

    def _execute_run(
        self, run_id, params, period_start, period_end, phase,
        builder, runner, parser, store, writer, ranker, profile
    ) -> RankedResult:
        """Execute one MT5 backtest and return a RankedResult."""
        run_dir = RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        try:
            ini_path = builder.build(
                run_id=run_id, params=params,
                period_start=period_start, period_end=period_end,
                output_dir=run_dir, phase=phase,
                ea_file=profile.ex5_file,
                ea_symbol=profile.symbol,
                ea_timeframe=profile.timeframe,
            )

            run = Run(
                run_id=run_id,
                ea_name=profile.name, symbol=profile.symbol,
                timeframe=profile.timeframe,
                period_start=period_start, period_end=period_end,
                params=params, phase=phase,
                tester_model=self.cfg["mt5"]["tester_model"],
                ini_snapshot=ini_path.read_text(),
            )
            store.save_run(run)

            result = runner.run(
                run_id, ini_path, run_dir / "report",
                log_csv_search_dir=Path(self.cfg["mt5"]["mql5_files_path"]),
                profile=profile,
            )

            if not result.success:
                return ranker.make_result(run_id, params, phase, None,
                                          error=result.error_message)

            metrics, _ = parser.parse(result.report_xml, result.report_html)
            if metrics is None:
                return ranker.make_result(run_id, params, phase, None,
                                          error="parse_failed")

            metrics.run_id = run_id
            writer.write(run_id, metrics, pd.DataFrame(), [], params)

            return ranker.make_result(run_id, params, phase, metrics)

        except Exception as e:
            logger.warning(f"[{run_id}] Run error: {e}")
            return ranker.make_result(run_id, params, phase, None, error=str(e))

    # ── Verdict logic ─────────────────────────────────────────────────────────

    def _determine_verdict(
        self,
        best: RankedResult,
        oos: Optional[RankedResult],
        sens: list[RankedResult],
    ) -> str:
        """
        RECOMMENDED: IS profitable + OOS profitable + not fragile
        RISKY:       IS profitable but OOS weak OR fragile
        NOT_RELIABLE: IS marginal or OOS loss
        """
        if best.calmar < 0.1 or best.net_profit <= 0:
            return "NOT_RELIABLE"

        oos_ok = False
        if oos and oos.net_profit > 0:
            # OOS degradation: acceptable if OOS calmar ≥ 50% of IS calmar
            oos_ratio = oos.calmar / max(best.calmar, 0.001)
            oos_ok    = oos_ratio >= 0.50
        elif oos is None:
            oos_ok = True  # No OOS test — can't penalize

        # Sensitivity: fragile if any nudge drops Calmar by >50%
        fragile = any(
            s.passing and s.calmar < best.calmar * 0.50
            for s in sens
        ) if sens else False

        if oos_ok and not fragile and best.calmar >= 0.30:
            return "RECOMMENDED"
        if oos_ok or (not fragile and best.calmar >= 0.20):
            return "RISKY"
        return "NOT_RELIABLE"

    # ── .set file output ──────────────────────────────────────────────────────

    def _write_set_file(
        self, result: RankedResult, schema: ParameterSchema, cfg: SessionConfig
    ) -> Optional[Path]:
        """Write a clean .set file for MT5 import."""
        try:
            out_dir  = self.reports_dir / result.run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{cfg.ea_name}_optimized_{cfg.symbol}_{cfg.timeframe}.set"

            header = (
                f"Optimized by MT5 Smart Optimizer\n"
                f"EA: {cfg.ea_name} | Symbol: {cfg.symbol} | TF: {cfg.timeframe}\n"
                f"Training: {cfg.train_start} – {cfg.train_end}\n"
                f"Verdict: {self.verdict}\n"
                f"Net Profit: ${result.net_profit:.2f} | Calmar: {result.calmar:.2f} | "
                f"Win Rate: {result.win_rate:.1f}%\n"
                f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
            )

            content = schema.to_set_file(result.params, header_comment=header)
            out_path.write_text(content, encoding="utf-8")
            logger.info(f"Optimized .set file written: {out_path}")
            return out_path
        except Exception as e:
            logger.warning(f"Could not write .set file: {e}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _result_to_dict(self, r: RankedResult) -> dict:
        return {
            "run_id":       r.run_id,
            "rank":         r.rank,
            "score":        round(r.score, 4),
            "net_profit":   round(r.net_profit, 2),
            "calmar":       round(r.calmar, 3),
            "profit_factor": round(r.profit_factor, 3),
            "win_rate":     round(r.win_rate, 1),
            "max_drawdown": round(r.max_drawdown, 2),
            "total_trades": r.total_trades,
            "passing":      r.passing,
            "params_summary": self._params_summary(r.params),
        }

    @staticmethod
    def _params_summary(params: dict) -> str:
        """Show a few key params for display."""
        keys = ["InpRiskPercent", "InpRRRatio", "InpMaxDailyLossPct",
                "InpTrailStartPips", "InpMinScore", "InpSessionStart", "InpSessionEnd"]
        parts = []
        for k in keys:
            if k in params:
                short = k.replace("Inp", "")
                parts.append(f"{short}={params[k]}")
        return " | ".join(parts[:4])

    def _log(self, level: str, msg: str) -> None:
        getattr(logger, level, logger.info)(msg)
        self._emit("log", {"level": level, "msg": msg})

    def _emit(self, event: str, data: dict = {}) -> None:
        try:
            self.socketio.emit(event, data)
        except Exception as e:
            logger.debug(f"Emit error ({event}): {e}")
