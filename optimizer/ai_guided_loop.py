"""
optimizer/ai_guided_loop.py
AI-Guided Autonomous Optimization Loop.

Replaces Phase 2's blind random neighbor search with directed,
AI-driven parameter evolution. Each iteration:

  1. Build rich context: parameter schema + full history
  2. Ask AI: "what parameter values should I try next?"
  3. Apply changes with bounds checking
  4. Deduplicate (don't re-test seen param sets)
  5. Run backtest via existing pipeline._execute_run()
  6. Check stop conditions (targets met OR max iterations)
  7. Emit progress to frontend, update pipeline state
  8. Loop

The loop terminates when:
  - All quality targets are met by the current best result
  - Max iterations reached
  - Stop flag set externally (user clicked Stop)
  - Budget exhausted
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from datetime import datetime
from typing import Optional

from loguru import logger

from ea.schema import ParameterSchema
from optimizer.result_ranker import RankedResult, ResultRanker
from optimizer.session_config import SessionConfig
from analysis.ai_reasoner import AIReasoner, AIParamSuggestion


class AIGuidedLoop:
    """
    Autonomous AI-driven parameter search.

    Usage (from inside OptimizationPipeline._run_pipeline):
        loop = AIGuidedLoop(pipeline, schema, cfg, builder, runner,
                            parser, store, writer, ranker, profile, budget)
        loop.run(seed_results, max_iterations, targets)
        # Results available in loop.all_results, loop.best_result
    """

    # If last N iterations show less than this score improvement → escape
    STUCK_WINDOW    = 3
    STUCK_THRESHOLD = 0.005

    def __init__(
        self,
        pipeline,           # OptimizationPipeline — for _execute_run / _emit / _log
        schema: ParameterSchema,
        cfg: SessionConfig,
        builder, runner, parser, store, writer,
        ranker: ResultRanker,
        profile,
        budget,
    ):
        self.pipeline = pipeline
        self.schema   = schema
        self.cfg      = cfg
        self.builder  = builder
        self.runner   = runner
        self.parser   = parser
        self.store    = store
        self.writer   = writer
        self.ranker   = ranker
        self.profile  = profile
        self.budget   = budget

        # Public results — populated during run()
        self.all_results:  list[RankedResult] = []
        self.best_result:  Optional[RankedResult] = None

        # Internal state
        self._iteration_history: list[dict] = []  # rich history for AI prompt
        self._seen_hashes:       set[str]   = set()
        self._rng = random.Random(int(time.time()))

    # ── Public entry point ────────────────────────────────────────────────────

    def run(
        self,
        seed_results: list[RankedResult],
        max_iterations: int,
        targets: dict,
    ) -> RankedResult:
        """
        Run the autonomous loop. Returns the best result found.

        seed_results:   Phase 1 ranked results (provides initial best + seen params)
        max_iterations: hard cap on AI-directed iterations
        targets:        {min_profit_factor, max_drawdown_pct, min_calmar}
        """
        self._initialize_from_seeds(seed_results)

        self._log("info", f"━━ AI-Guided Loop: up to {max_iterations} iterations ━━")
        self._log("info",
            f"   Targets → PF≥{targets.get('min_profit_factor',1.5)} | "
            f"DD≤{targets.get('max_drawdown_pct',20)}% | "
            f"Calmar≥{targets.get('min_calmar',0.5)}"
        )
        self._think(
            f"Targets set — PF≥{targets.get('min_profit_factor',1.5)}, "
            f"DD≤{targets.get('max_drawdown_pct',20)}%, Calmar≥{targets.get('min_calmar',0.5)}. "
            f"I'll stop as soon as I hit them, or after {max_iterations} iterations.",
            kind="reasoning",
        )

        schema_info = self._build_schema_info()

        for iteration in range(1, max_iterations + 1):
            if self.pipeline._stop_flag:
                self._log("info", "Loop stopped by user.")
                break

            if self.budget.is_exhausted():
                self._log("warning", "⏱ Time budget exhausted — stopping AI loop.")
                break

            # Check if current best already satisfies all targets
            if self.best_result and self._targets_met(self.best_result, targets):
                self._log("info",
                    f"✅ All targets met after iteration {iteration - 1}! "
                    f"PF={self.best_result.profit_factor:.2f}, "
                    f"DD={self.best_result.max_drawdown:.1f}%, "
                    f"Calmar={self.best_result.calmar:.2f}"
                )
                self._think(
                    f"All quality targets reached after iteration {iteration - 1}. "
                    f"Best config: PF={self.best_result.profit_factor:.2f}, "
                    f"DD={self.best_result.max_drawdown:.1f}%, "
                    f"Calmar={self.best_result.calmar:.2f}. Stopping early — no need to keep iterating.",
                    kind="success",
                )
                self._emit("ai_targets_met", {
                    "iteration": iteration - 1,
                    "profit_factor": round(self.best_result.profit_factor, 3),
                    "max_drawdown":  round(self.best_result.max_drawdown * 100, 2),
                    "calmar":        round(self.best_result.calmar, 3),
                })
                self.pipeline._emit_early_termination(
                    reason_code="targets_met",
                    message=f"All targets met at iteration {iteration - 1}. Optimization complete.",
                    details={
                        "iteration":     iteration - 1,
                        "profit_factor": round(self.best_result.profit_factor, 3),
                        "max_drawdown":  round(self.best_result.max_drawdown * 100, 2),
                        "calmar":        round(self.best_result.calmar, 3),
                    },
                )
                break

            self._log("info",
                f"[AI Loop {iteration}/{max_iterations}] "
                f"Best so far: PF={self.best_result.profit_factor:.2f}, "
                f"Calmar={self.best_result.calmar:.2f}, "
                f"DD={self.best_result.max_drawdown:.1f}%"
                if self.best_result else f"[AI Loop {iteration}/{max_iterations}] Starting..."
            )
            self._think(
                f"Iteration {iteration}: reviewing history and deciding what to change next...",
                kind="info", iteration=iteration,
            )

            # Get AI suggestion for next params
            suggestion = self._get_suggestion(schema_info, targets)

            # Surface the AI's reasoning as its own thinking message
            if suggestion.analysis:
                self._think(suggestion.analysis, kind="reasoning", iteration=iteration)

            # Apply changes to best params → candidate param set
            base_params = self.best_result.params if self.best_result else self.schema.defaults()
            next_params = self._apply_changes(
                base=base_params,
                changes=suggestion.changes,
            )

            # Escape if stuck or AI returned no changes
            is_stuck = self._check_stuck()
            if is_stuck or not suggestion.changes:
                if is_stuck:
                    self._log("warning", f"  ⚠ Stuck detected — applying random escape at iteration {iteration}")
                    self._think(
                        f"Recent scores are flat — the AI is stuck in a local optimum. "
                        f"Applying a random ±30% perturbation to escape and explore a new region.",
                        kind="warning", iteration=iteration,
                    )
                else:
                    self._log("warning", f"  ⚠ AI returned no changes — applying random escape")
                    self._think(
                        "AI returned no changes — falling back to a random perturbation so we keep exploring.",
                        kind="warning", iteration=iteration,
                    )
                next_params = self._random_escape(next_params)
                self._emit("ai_stuck", {"iteration": iteration})

            # Deduplicate — ensure we're not re-testing an identical config
            next_params = self._ensure_unique(next_params, max_attempts=5)

            # Build rich param change records (prev → new + reason) for the UI
            change_records = self._build_change_records(base_params, next_params, suggestion.changes)

            # Narrate the actual parameter changes
            for c in change_records[:4]:  # cap noise
                self._think(
                    f"{c['param']}: {c['from']} → {c['to']} — {c['reason']}",
                    kind="decision",
                    iteration=iteration,
                    meta=c,
                )

            # Emit iteration start
            self._emit("ai_iteration_start", {
                "iteration":       iteration,
                "max_iterations":  max_iterations,
                "analysis":        suggestion.analysis,
                "changes":         suggestion.changes,
                "change_records":  change_records,
                "confidence":      round(suggestion.confidence, 2),
                "is_stuck_escape": is_stuck or not suggestion.changes,
            })

            # Dedicated richer event that the dashboard subscribes to
            self._emit("param_changes", {
                "iteration":  iteration,
                "run_id":     None,  # filled in after run below via complete event
                "analysis":   suggestion.analysis,
                "changes":    change_records,
                "confidence": round(suggestion.confidence, 2),
            })

            # Run the backtest
            run_id = f"ai_{iteration:02d}_{datetime.utcnow().strftime('%H%M%S')}"
            t0     = time.time()
            result = self.pipeline._execute_run(
                run_id, next_params,
                self.cfg.train_start, self.cfg.train_end,
                "phase2_ai",
                self.builder, self.runner, self.parser,
                self.store, self.writer, self.ranker, self.profile,
            )
            elapsed = time.time() - t0
            self.budget.record_run(elapsed)

            # Register result
            self.all_results.append(result)
            self._mark_seen(next_params)
            self._update_best(result)
            self.pipeline._run_count += 1

            # Update pipeline live state
            if (self.pipeline._live_best is None
                    or result.score > self.pipeline._live_best.score):
                self.pipeline._live_best = result

            run_dict = self.pipeline._make_run_dict(run_id, result, "phase2_ai")
            self.pipeline._completed_runs.append(run_dict)

            # Record iteration for AI history
            targets_met = self.best_result and self._targets_met(self.best_result, targets)
            self._record_iteration(iteration, run_id, result, suggestion)

            goal_status = {
                "profit_factor_met": result.profit_factor >= targets.get("min_profit_factor", 1.5),
                "drawdown_ok":       result.max_drawdown  <= targets.get("max_drawdown_pct",   20.0),
                "calmar_met":        result.calmar        >= targets.get("min_calmar",           0.5),
            }

            # Narrate the outcome of this iteration
            improved = (self.best_result is self.all_results[-1]) if self.all_results else False
            if result.passing and improved:
                self._think(
                    f"✓ Iteration {iteration} improved the best score to {result.score:.3f} "
                    f"(PF={result.profit_factor:.2f}, Calmar={result.calmar:.2f}, "
                    f"DD={result.max_drawdown:.1f}%). Keeping these params as the new baseline.",
                    kind="success", iteration=iteration,
                )
            elif result.passing:
                self._think(
                    f"Iteration {iteration} passed thresholds but didn't beat the best — "
                    f"score {result.score:.3f} vs best {self.best_result.score:.3f}.",
                    kind="info", iteration=iteration,
                )
            else:
                diagnosis = self._diagnose_failure(result, targets)
                self._think(
                    f"✗ Iteration {iteration} failed: {diagnosis} "
                    f"(PF={result.profit_factor:.2f}, DD={result.max_drawdown:.1f}%). "
                    f"Will adjust in the next step.",
                    kind="warning", iteration=iteration,
                )

            # Emit iteration complete (max_drawdown emitted as %)
            self._emit("ai_iteration_complete", {
                "iteration":       iteration,
                "max_iterations":  max_iterations,
                "run_id":          run_id,
                "score":           round(result.score, 4),
                "profit_factor":   round(result.profit_factor, 3),
                "calmar":          round(result.calmar, 3),
                "max_drawdown":    round(result.max_drawdown * 100, 2),
                "net_profit":      round(result.net_profit, 2),
                "total_trades":    result.total_trades,
                "passing":         bool(result.passing),
                "best_score":      round(self.best_result.score, 4) if self.best_result else 0,
                "best_pf":         round(self.best_result.profit_factor, 3) if self.best_result else 0,
                "best_calmar":     round(self.best_result.calmar, 3) if self.best_result else 0,
                "goal_status":     goal_status,
                "targets_met":     bool(targets_met),
                "improved":        bool(improved),
                "confidence":      round(suggestion.confidence, 2),
                "analysis":        suggestion.analysis,
                "change_records":  change_records,
            })

            self._emit("run_complete", {
                "run_id":        run_id,
                "phase":         "phase2_ai",
                "net_profit":    round(result.net_profit, 2),
                "calmar":        round(result.calmar, 3),
                "profit_factor": round(result.profit_factor, 3),
                "win_rate":      round(result.win_rate * 100, 1),
                "max_drawdown":  round(result.max_drawdown * 100, 2),
                "total_trades":  result.total_trades,
                "passing":       bool(result.passing),
                "score":         round(result.score, 4),
                "progress_pct":  round(self.pipeline._run_count / max(self.pipeline._total_runs, 1) * 100),
            })

            status = "✅" if result.passing else "❌"
            self._log(
                "info" if result.passing else "warning",
                f"  {status} iter={iteration} | PF={result.profit_factor:.2f} | "
                f"Calmar={result.calmar:.2f} | DD={result.max_drawdown:.1f}% | "
                f"trades={result.total_trades} | confidence={suggestion.confidence:.2f}"
            )

        return self.best_result

    # ── Initialization ────────────────────────────────────────────────────────

    def _initialize_from_seeds(self, seed_results: list[RankedResult]) -> None:
        """Register Phase 1 results as seen and find initial best."""
        for r in seed_results:
            self._mark_seen(r.params)

        passing = [r for r in seed_results if r.passing]
        if passing:
            self.best_result = max(passing, key=lambda r: r.score)
            self._log("info",
                f"AI loop seed: best Phase 1 result is {self.best_result.run_id} "
                f"(PF={self.best_result.profit_factor:.2f}, score={self.best_result.score:.4f})"
            )

        # Populate initial iteration history from Phase 1 top results
        top_seeds = sorted(passing, key=lambda r: r.score, reverse=True)[:5]
        for i, r in enumerate(top_seeds):
            self._iteration_history.append({
                "iteration":    f"p1_top{i+1}",
                "run_id":       r.run_id,
                "score":        round(r.score, 4),
                "pf":           round(r.profit_factor, 3),
                "calmar":       round(r.calmar, 3),
                "dd":           round(r.max_drawdown, 2),
                "trades":       r.total_trades,
                "changes":      [],  # LHS seeds have no "changes"
                "params":       r.params,
            })

    # ── AI interaction ────────────────────────────────────────────────────────

    def _get_suggestion(
        self, schema_info: list[dict], targets: dict
    ) -> AIParamSuggestion:
        """Ask AIReasoner for the next parameter set."""
        reasoner: AIReasoner = self.pipeline._ai_reasoner
        if not reasoner or not reasoner.enabled:
            return AIParamSuggestion(
                analysis="AI unavailable — using random escape.",
                changes=[], confidence=0.0, goal_status={}, error="no_ai",
            )

        current_params = self.best_result.params if self.best_result else self.schema.defaults()

        return reasoner.suggest_next_params(
            current_best_params=current_params,
            schema_info=schema_info,
            iteration_history=self._iteration_history,
            targets=targets,
        )

    # ── Parameter manipulation ────────────────────────────────────────────────

    def _build_schema_info(self) -> list[dict]:
        """Convert schema optimizable params to serializable dicts for the AI prompt."""
        return [
            {
                "name":    p.name,
                "type":    p.type,
                "min":     p.min,
                "max":     p.max,
                "step":    p.step,
                "default": p.default,
                "enum_values": p.enum_values if p.type == "enum" else [],
            }
            for p in self.schema.optimizable()
        ]

    def _apply_changes(self, base: dict, changes: list[dict]) -> dict:
        """
        Apply AI-suggested changes to base params.
        Uses ParameterDef.clamp() to enforce valid ranges and types.
        """
        result = dict(base)
        param_map = {p.name: p for p in self.schema.optimizable()}

        for change in changes:
            name  = change.get("param", "")
            value = change.get("value")
            if name not in param_map or value is None:
                continue
            pdef = param_map[name]
            try:
                result[name] = pdef.clamp(float(value) if pdef.type in ("float", "int") else value)
            except Exception as e:
                logger.debug(f"AIGuidedLoop: skipping change {name}={value}: {e}")

        return result

    def _build_change_records(
        self, before: dict, after: dict, ai_changes: list[dict]
    ) -> list[dict]:
        """
        Produce a list of {param, from, to, reason} records for the UI.

        The AI's suggested changes may include a `reason` per change; we match
        those by name. Parameters that differ without a matching reason still
        get recorded (labelled "random perturbation").
        """
        reason_by_param = {
            c.get("param"): c.get("reason", "").strip()
            for c in (ai_changes or [])
            if c.get("param")
        }
        records = []
        for name, new_val in after.items():
            old_val = before.get(name)
            if old_val == new_val:
                continue
            records.append({
                "param":  name,
                "from":   old_val,
                "to":     new_val,
                "reason": reason_by_param.get(name) or "random perturbation (escape from stuck region)",
            })
        return records

    def _random_escape(self, base: dict) -> dict:
        """Random perturbation when stuck — perturbs 2-4 random optimizable params by ±30% of range."""
        opts = self.schema.optimizable()
        if not opts:
            return dict(base)

        candidate = dict(base)
        n_perturb = min(len(opts), self._rng.randint(2, 4))
        to_perturb = self._rng.sample(opts, n_perturb)

        for p in to_perturb:
            if p.type == "bool":
                candidate[p.name] = not candidate.get(p.name, p.default)
            elif p.type == "enum":
                candidate[p.name] = self._rng.choice(p.enum_values)
            else:
                span  = float(p.max) - float(p.min)
                delta = span * 0.30 * self._rng.choice([-1, 1])
                candidate[p.name] = p.clamp(float(candidate.get(p.name, p.default)) + delta)

        return candidate

    def _ensure_unique(self, params: dict, max_attempts: int = 5) -> dict:
        """If params already seen, perturb until unique (or give up)."""
        for _ in range(max_attempts):
            if self._hash(params) not in self._seen_hashes:
                return params
            params = self._random_escape(params)
        return params  # best effort

    def _mark_seen(self, params: dict) -> None:
        self._seen_hashes.add(self._hash(params))

    @staticmethod
    def _hash(params: dict) -> str:
        key = json.dumps(params, sort_keys=True, default=str)
        return hashlib.md5(key.encode()).hexdigest()

    # ── Best tracking ─────────────────────────────────────────────────────────

    def _update_best(self, result: RankedResult) -> None:
        if result.passing:
            if self.best_result is None or result.score > self.best_result.score:
                self.best_result = result

    # ── Stop conditions ───────────────────────────────────────────────────────

    def _diagnose_failure(self, result: RankedResult, targets: dict) -> str:
        """Human-readable reason this iteration didn't pass quality gates."""
        reasons = []
        if result.max_drawdown > targets.get("max_drawdown_pct", 20.0):
            reasons.append(f"drawdown too high ({result.max_drawdown:.1f}%)")
        if result.profit_factor < targets.get("min_profit_factor", 1.5):
            reasons.append(f"profit factor too low ({result.profit_factor:.2f})")
        if result.calmar < targets.get("min_calmar", 0.5):
            reasons.append(f"Calmar too low ({result.calmar:.2f})")
        if result.net_profit <= 0:
            reasons.append(f"unprofitable (${result.net_profit:.0f})")
        if result.total_trades < 10:
            reasons.append(f"too few trades ({result.total_trades})")
        return ", ".join(reasons) or "result below quality threshold"

    def _targets_met(self, result: RankedResult, targets: dict) -> bool:
        if not result or not result.passing:
            return False
        return (
            result.profit_factor >= targets.get("min_profit_factor", 1.5)
            and result.max_drawdown  <= targets.get("max_drawdown_pct",   20.0)
            and result.calmar        >= targets.get("min_calmar",           0.5)
        )

    def _check_stuck(self) -> bool:
        """Return True if last STUCK_WINDOW iterations improved less than STUCK_THRESHOLD."""
        ai_iters = [h for h in self._iteration_history if str(h.get("iteration", "")).startswith(("1","2","3","4","5","6","7","8","9"))]
        if len(ai_iters) < self.STUCK_WINDOW:
            return False
        recent_scores = [h["score"] for h in ai_iters[-self.STUCK_WINDOW:]]
        return (max(recent_scores) - min(recent_scores)) < self.STUCK_THRESHOLD

    # ── History tracking ──────────────────────────────────────────────────────

    def _record_iteration(
        self,
        iteration: int,
        run_id: str,
        result: RankedResult,
        suggestion: AIParamSuggestion,
    ) -> None:
        self._iteration_history.append({
            "iteration": iteration,
            "run_id":    run_id,
            "score":     round(result.score, 4),
            "pf":        round(result.profit_factor, 3),
            "calmar":    round(result.calmar, 3),
            "dd":        round(result.max_drawdown, 2),
            "trades":    result.total_trades,
            "changes":   suggestion.changes,
            "params":    result.params,
        })

    # ── Pipeline helpers ──────────────────────────────────────────────────────

    def _emit(self, event: str, data: dict = {}) -> None:
        self.pipeline._emit(event, data)

    def _log(self, level: str, msg: str) -> None:
        self.pipeline._log(level, msg)

    def _think(self, msg: str, kind: str = "info", iteration: Optional[int] = None, meta: Optional[dict] = None) -> None:
        """Stream an AI-thinking message to the dashboard."""
        self.pipeline._emit_thinking(msg, kind=kind, iteration=iteration, meta=meta)
