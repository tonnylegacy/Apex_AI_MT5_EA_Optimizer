"""
analysis/reversal.py
Detects trades that went into significant profit but ultimately closed as losses.
Measures profit giveback and proposes trailing / TP tightening adjustments.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from data.models import Finding, RunMetrics
from analysis.base import BaseAnalyzer


class ReversalAnalyzer(BaseAnalyzer):
    """
    Profit Reversal Detector.

    A "reversal" trade is one that:
    - Closed at a loss (net_money < 0)
    - Had MFE >= threshold pips (i.e. was at significant unrealised profit at some point)

    Also flags trades that won but captured less than X% of their MFE (partial giveback).
    """

    name = "reversal"

    def __init__(
        self,
        mfe_threshold_pips: float = 15.0,
        min_reversal_rate:  float = 0.15,
        poor_capture_rate:  float = 0.55,
        permutation_n:      int   = 500,
    ):
        self.mfe_threshold       = mfe_threshold_pips
        self.min_reversal_rate   = min_reversal_rate
        self.poor_capture_rate   = poor_capture_rate
        self.permutation_n       = permutation_n

    def analyze(self, trades: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        findings = []

        has_mfe = "mfe_pips" in trades.columns and trades["mfe_pips"].notna().sum() > 10

        if has_mfe:
            findings += self._check_reversals(trades, metrics)
            findings += self._check_capture_rate(trades, metrics)
        else:
            # Without MFE, do a simpler check using result_class if available
            if "result_class" in trades.columns:
                findings += self._check_result_classes(trades, metrics)

        return sorted(findings, key=lambda f: f.confidence, reverse=True)

    # ── Reversal rate check ───────────────────────────────────────────────────

    def _check_reversals(self, df: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        """Find losing trades that had significant unrealised profit."""
        losers  = df[df["net_money"] < 0]
        if len(losers) == 0:
            return []

        reversals = losers[losers["mfe_pips"] >= self.mfe_threshold]
        rate      = len(reversals) / len(losers)

        if rate < self.min_reversal_rate or len(reversals) < 5:
            return []

        avg_giveback_pips = float(reversals["mfe_pips"].mean())
        total_lost        = float(losers["net_money"].sum())
        reversal_lost     = float(reversals["net_money"].sum())

        # Permutation test: is the reversal rate unusually high?
        all_mfe = df[df["net_money"] < 0]["mfe_pips"].dropna().values
        if len(all_mfe) > 0:
            p_val = self._permutation_pvalue(
                reversals["mfe_pips"].values, all_mfe,
                n_permutations=self.permutation_n, alternative="greater"
            )
        else:
            p_val = 0.01  # assume significant

        confidence = max(0.0, min(1.0, 1 - p_val))
        impact_pnl = abs(reversal_lost)     # upper bound on recoverable PnL

        # Compute median reversal time for trailing stop suggestion
        if "duration_minutes" in df.columns:
            median_dur = float(reversals["duration_minutes"].median())
        else:
            median_dur = 0

        # Compute 25th percentile of MFE at reversal — suggest TrailStart at this level
        mfe_p25 = float(reversals["mfe_pips"].quantile(0.25))

        suggested = {
            "InpUseTrailing":    True,
            "InpTrailStartPips": round(max(10.0, mfe_p25 * 0.85), 1),
            "InpTrailStepPips":  10.0,
        }

        return [Finding(
            run_id=self.run_id,
            analyzer=self.name,
            description=(
                f"{rate*100:.0f}% of losing trades had MFE ≥ {self.mfe_threshold:.0f} pips "
                f"before reversing ({len(reversals)} trades). "
                f"Avg giveback: {avg_giveback_pips:.1f} pips. "
                f"Estimated recoverable PnL: ${impact_pnl:.0f}."
            ),
            severity=self._severity(confidence, impact_pnl, metrics.net_profit),
            confidence=confidence,
            impact_estimate_pnl=impact_pnl,
            suggested_params=suggested,
            evidence={
                "reversal_count":       len(reversals),
                "reversal_rate":        round(rate, 4),
                "avg_giveback_pips":    round(avg_giveback_pips, 2),
                "mfe_p25":              round(mfe_p25, 2),
                "median_duration_min":  round(median_dur, 0),
                "p_value":              round(p_val, 4),
            },
        )]

    # ── MFE Capture rate check ────────────────────────────────────────────────

    def _check_capture_rate(self, df: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        """Check if winning trades are capturing enough of their MFE."""
        winners = df[(df["net_money"] > 0) & df["mfe_pips"].notna() & (df["mfe_pips"] > 0)]
        if len(winners) < 10:
            return []

        if "exit_quality" not in df.columns or df["exit_quality"].isna().all():
            winners = winners.copy()
            winners["exit_quality"] = winners["net_pips"] / winners["mfe_pips"].clip(lower=0.01)

        mean_capture = float(winners["exit_quality"].mean())
        if mean_capture >= self.poor_capture_rate:
            return []

        potential_gain = float(
            (winners["mfe_pips"] - winners["net_pips"]).clip(lower=0).mean()
        ) * float(winners["lot_size"].mean()) * 100  # rough $

        confidence = self._confidence_from_z(
            (self.poor_capture_rate - mean_capture) / max(0.01, winners["exit_quality"].std())
        )

        return [Finding(
            run_id=self.run_id,
            analyzer=self.name,
            description=(
                f"Winners capture only {mean_capture*100:.0f}% of their MFE on average. "
                f"Potential gain with better exits: ~${potential_gain*len(winners):.0f}."
            ),
            severity=self._severity(confidence, potential_gain * len(winners), metrics.net_profit),
            confidence=min(0.95, confidence),
            impact_estimate_pnl=potential_gain * len(winners),
            suggested_params={
                "InpUseTrailing":    True,
                "InpTrailStartPips": round(float(winners["mfe_pips"].quantile(0.30)), 1),
            },
            evidence={
                "mean_capture_ratio": round(mean_capture, 4),
                "winner_count":       len(winners),
            },
        )]

    # ── Fallback: result_class based ─────────────────────────────────────────

    def _check_result_classes(self, df: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        """Simple reversal check using pre-classified result_class column."""
        reversals = df[df["result_class"] == "reversal"]
        losers    = df[df["net_money"] < 0]
        if len(losers) == 0 or len(reversals) == 0:
            return []

        rate = len(reversals) / len(losers)
        if rate < self.min_reversal_rate:
            return []

        return [Finding(
            run_id=self.run_id,
            analyzer=self.name,
            description=f"{rate*100:.0f}% of losers classified as reversals (MFE-based).",
            severity="medium",
            confidence=0.65,
            impact_estimate_pnl=abs(float(reversals["net_money"].sum())),
            suggested_params={"InpUseTrailing": True},
            evidence={"reversal_rate": round(rate, 4)},
        )]
