"""
scoring/composite.py
Composite score function for LEGSTECH_EA_V2 / XAUUSD optimization.
Primary: Calmar Ratio | Secondary: PF, MFE capture, session stability, recovery.
"""
from __future__ import annotations
from typing import Optional
import numpy as np
import yaml
from loguru import logger

from data.models import RunMetrics


def _clip_normalize(value: float, lo: float, hi: float) -> float:
    """Normalize value to [0, 1] by clipping to [lo, hi]."""
    if hi <= lo:
        return 0.0
    return float(np.clip((value - lo) / (hi - lo), 0.0, 1.0))


def _session_stability(session_stats: dict) -> float:
    """
    Compute session stability as 1 - (std of per-session Calmar / mean Calmar).
    Returns 1.0 (perfectly stable) if only one session or no variance.
    session_stats: {session_name: {"calmar": float, "trade_count": int}}
    """
    calmars = [
        v["calmar"] for v in session_stats.values()
        if v.get("trade_count", 0) >= 10 and "calmar" in v
    ]
    if len(calmars) < 2:
        return 1.0  # not enough sessions to measure stability
    mean_c = np.mean(calmars)
    std_c  = np.std(calmars)
    if abs(mean_c) < 0.01:
        return 0.0
    cv = std_c / abs(mean_c)   # coefficient of variation
    return float(np.clip(1.0 - cv, 0.0, 1.0))


class CompositeScorer:
    """
    Weighted composite score calculator.

    All sub-scores are independently normalized to [0, 1].
    Two multiplicative penalties are applied after weighting:
    - Significance penalty: reduces weight for low trade counts
    - Reversal penalty    : reduces score if reversal rate is high
    """

    DEFAULT_WEIGHTS = {
        "calmar":            0.35,
        "profit_factor":     0.20,
        "mfe_capture":       0.20,
        "session_stability": 0.15,
        "recovery_factor":   0.10,
    }

    DEFAULT_NORMALIZATION = {
        "calmar":            {"lo": 0.0,  "hi": 4.0},
        "profit_factor":     {"lo": 1.0,  "hi": 3.5},
        "mfe_capture":       {"lo": 0.0,  "hi": 1.0},
        "session_stability": {"lo": 0.0,  "hi": 1.0},
        "recovery_factor":   {"lo": 0.0,  "hi": 6.0},
    }

    def __init__(self, config_path: str = "config.yaml"):
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            scoring_cfg = cfg.get("scoring", {})
            self.weights = scoring_cfg.get("weights", self.DEFAULT_WEIGHTS)
            norm_cfg     = scoring_cfg.get("normalization", {})
            self.norm    = {
                k: norm_cfg.get(k, self.DEFAULT_NORMALIZATION.get(k, {"lo": 0, "hi": 1}))
                for k in self.DEFAULT_WEIGHTS
            }
            self.min_trades         = cfg["thresholds"]["min_trades"]
            self.significance_at    = cfg["scoring"].get("significance_trades", 150)
        except Exception as e:
            logger.warning(f"Could not load scoring config: {e}. Using defaults.")
            self.weights         = self.DEFAULT_WEIGHTS
            self.norm            = self.DEFAULT_NORMALIZATION
            self.min_trades      = 50
            self.significance_at = 150

    def score(
        self,
        metrics: RunMetrics,
        session_stats: Optional[dict] = None,
    ) -> float:
        """
        Compute composite score for a RunMetrics object.
        Returns 0.0 if minimum trade count is not met.

        Args:
            metrics:       RunMetrics from the parsed backtest report
            session_stats: Optional {session: {calmar, trade_count, ...}} for stability
        """
        if metrics.total_trades < self.min_trades:
            logger.debug(
                f"Score=0 (trades {metrics.total_trades} < min {self.min_trades})"
            )
            return 0.0

        # ── Sub-scores ────────────────────────────────────────────────────────
        calmar_score  = _clip_normalize(
            metrics.calmar_ratio,
            **self.norm["calmar"]
        )
        pf_score      = _clip_normalize(
            metrics.profit_factor,
            **self.norm["profit_factor"]
        )
        capture_score = _clip_normalize(
            metrics.avg_mfe_capture if metrics.avg_mfe_capture is not None else 0.5,
            **self.norm["mfe_capture"]
        )
        stab_score    = _clip_normalize(
            _session_stability(session_stats or {}),
            **self.norm["session_stability"]
        )
        recovery_score = _clip_normalize(
            metrics.recovery_factor,
            **self.norm["recovery_factor"]
        )

        # ── Weighted sum ──────────────────────────────────────────────────────
        raw = (
            self.weights["calmar"]            * calmar_score   +
            self.weights["profit_factor"]     * pf_score       +
            self.weights["mfe_capture"]       * capture_score  +
            self.weights["session_stability"] * stab_score     +
            self.weights["recovery_factor"]   * recovery_score
        )

        # ── Penalties ─────────────────────────────────────────────────────────
        # Significance: scale up to 1.0 as trade count reaches significance_at
        significance = min(1.0, metrics.total_trades / self.significance_at)

        # Reversal penalty: each 10% reversal rate removes 20% of score
        reversal_penalty = 1.0
        if metrics.reversal_rate is not None:
            reversal_penalty = max(0.0, 1.0 - metrics.reversal_rate * 2.0)

        final = raw * significance * reversal_penalty

        logger.debug(
            f"Score breakdown: calmar={calmar_score:.3f} pf={pf_score:.3f} "
            f"capture={capture_score:.3f} stab={stab_score:.3f} "
            f"recovery={recovery_score:.3f} → raw={raw:.3f} "
            f"× sig={significance:.3f} × rev={reversal_penalty:.3f} = {final:.4f}"
        )
        return round(final, 6)

    def breakdown(
        self,
        metrics: RunMetrics,
        session_stats: Optional[dict] = None,
    ) -> dict[str, float]:
        """Return the full breakdown of sub-scores for display."""
        return {
            "calmar_score":    _clip_normalize(metrics.calmar_ratio,    **self.norm["calmar"]),
            "pf_score":        _clip_normalize(metrics.profit_factor,   **self.norm["profit_factor"]),
            "capture_score":   _clip_normalize(
                metrics.avg_mfe_capture or 0.5,                          **self.norm["mfe_capture"]),
            "stability_score": _clip_normalize(
                _session_stability(session_stats or {}),                  **self.norm["session_stability"]),
            "recovery_score":  _clip_normalize(metrics.recovery_factor, **self.norm["recovery_factor"]),
            "significance":    min(1.0, metrics.total_trades / self.significance_at),
            "reversal_penalty":max(0.0, 1.0 - (metrics.reversal_rate or 0) * 2),
            "final":           self.score(metrics, session_stats),
        }
