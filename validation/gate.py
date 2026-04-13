"""
validation/gate.py
IS / Walk-Forward / OOS validation pipeline.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import uuid

import yaml
from loguru import logger

from data.models import GateResult, RunMetrics


@dataclass
class WFVResult:
    passed:       bool
    oos_is_ratio: float
    fold_results: list[dict]
    details:      dict


class ValidationGate:
    """
    Three-phase validation pipeline:
    1. IS check   — minimum thresholds on in-sample metrics
    2. Walk-Forward Validation — split training period, test metric consistency
    3. OOS test   — held-out period, called explicitly by orchestrator
    """

    def __init__(self, config_path: str | Path = "config.yaml"):
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        self.thresh = cfg["thresholds"]
        self.per    = cfg["periods"]

    # ── Phase 1: IS Check ─────────────────────────────────────────────────────

    def run_is_check(self, metrics: RunMetrics) -> GateResult:
        """
        Hard minimum thresholds. All must pass.
        """
        checks = {
            "min_trades":   metrics.total_trades  >= self.thresh["min_trades"],
            "min_pf":       metrics.profit_factor >= self.thresh["min_profit_factor"],
            "min_calmar":   metrics.calmar_ratio  >= self.thresh["min_calmar"],
        }
        passed = all(checks.values())
        reason = None
        if not passed:
            failed = [k for k, v in checks.items() if not v]
            reason = f"Failed gates: {', '.join(failed)}"

        logger.info(f"IS check {'PASSED' if passed else 'FAILED'}: {checks}")
        return GateResult(passed=passed, details=checks, reason=reason)

    # ── Phase 2: Walk-Forward Validation ─────────────────────────────────────

    def run_walk_forward(
        self,
        params:   dict[str, Any],
        executor,                 # callable(params, start_str, end_str, fold_id) -> Optional[RunMetrics]
        n_folds:  int = 2,
    ) -> WFVResult:
        """
        Split training period into n_folds sub-periods.
        Run the same params on each sub-period.
        Accept if mean OOS Calmar >= min_wfv_ratio × IS Calmar.

        In MVP we use n_folds=2 (first half / second half).
        v2 will use full rolling window WFV.
        """
        from datetime import datetime, timedelta

        train_start = datetime.strptime(self.per["train_start"], "%Y.%m.%d")
        train_end   = datetime.strptime(self.per["train_end"],   "%Y.%m.%d")
        total_days  = (train_end - train_start).days
        fold_days   = total_days // n_folds

        fold_metrics: list[RunMetrics] = []

        for i in range(n_folds):
            fold_start = train_start + timedelta(days=i * fold_days)
            fold_end   = fold_start  + timedelta(days=fold_days)
            if i == n_folds - 1:
                fold_end = train_end   # last fold gets remainder

            logger.info(f"WFV fold {i+1}/{n_folds}: {fold_start.date()} → {fold_end.date()}")

            fold_id = f"wfv_fold{i+1}_{uuid.uuid4().hex[:6]}"
            start_s = fold_start.strftime("%Y.%m.%d")
            end_s   = fold_end.strftime("%Y.%m.%d")

            fm = executor(params, start_s, end_s, fold_id)
            if fm:
                fold_metrics.append(fm)

        if not fold_metrics:
            return WFVResult(passed=False, oos_is_ratio=0.0, fold_results=[], details={})

        fold_calmars   = [fm.calmar_ratio for fm in fold_metrics]
        mean_oos_calmar = sum(fold_calmars) / len(fold_calmars)

        # Get IS calmar (best score so far) for comparison
        is_calmar = max((fm.calmar_ratio for fm in fold_metrics), default=0)
        if is_calmar <= 0:
            ratio = 0.0
        else:
            ratio = mean_oos_calmar / is_calmar

        passed = ratio >= self.thresh["min_wfv_ratio"]

        return WFVResult(
            passed=passed,
            oos_is_ratio=ratio,
            fold_results=[
                {"fold": i+1, "calmar": fm.calmar_ratio, "trades": fm.total_trades}
                for i, fm in enumerate(fold_metrics)
            ],
            details={
                "mean_fold_calmar": round(mean_oos_calmar, 4),
                "ratio":            round(ratio, 4),
                "threshold":        self.thresh["min_wfv_ratio"],
            },
        )

    # ── Parameter Sensitivity Check ───────────────────────────────────────────

    def check_sensitivity(
        self,
        params:      dict[str, Any],
        key_params:  list[str],
        cfg:         dict,
        store,
        builder,
        runner,
        parser,
        log_rdr,
        analyzers,
        scorer,
        perturbation: float = 0.10,
    ) -> tuple[bool, dict]:
        """
        For each key parameter, perturb by ±10% and measure Calmar change.
        Reject if any parameter causes > tolerance% degradation.

        In MVP this is optional — add to v2 workflow.
        """
        from main import execute_run
        import uuid

        tolerance   = self.thresh["sensitivity_tolerance"]
        degradations = {}

        base_metrics, _ = execute_run(
            run_id=f"sens_base_{uuid.uuid4().hex[:6]}",
            params=params,
            period_start=self.per["train_start"],
            period_end=self.per["train_end"],
            phase="validate",
            hypothesis_id=None,
            cfg=cfg, store=store, builder=builder, runner=runner,
            parser=parser, log_rdr=log_rdr, analyzers=analyzers, scorer=scorer,
        )
        if base_metrics is None or base_metrics.calmar_ratio <= 0:
            return True, {}  # can't test sensitivity — skip

        for param in key_params:
            if param not in params:
                continue
            base_val = params[param]
            if not isinstance(base_val, (int, float)):
                continue

            perturbed = {**params, param: base_val * (1 + perturbation)}
            pm, _ = execute_run(
                run_id=f"sens_{param[:8]}_{uuid.uuid4().hex[:6]}",
                params=perturbed,
                period_start=self.per["train_start"],
                period_end=self.per["train_end"],
                phase="validate",
                hypothesis_id=None,
                cfg=cfg, store=store, builder=builder, runner=runner,
                parser=parser, log_rdr=log_rdr, analyzers=analyzers, scorer=scorer,
            )
            if pm and base_metrics.calmar_ratio > 0:
                deg = (base_metrics.calmar_ratio - pm.calmar_ratio) / base_metrics.calmar_ratio
                degradations[param] = round(deg, 4)

        max_deg = max(degradations.values(), default=0)
        passed  = max_deg <= tolerance
        return passed, degradations
