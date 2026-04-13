"""
analysis/equity_curve.py
Analyzes equity curve shape: drawdown clusters, flatness, recovery efficiency.
"""
from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd
from scipy.stats import linregress

from data.models import Finding, RunMetrics
from analysis.base import BaseAnalyzer


class EquityCurveAnalyzer(BaseAnalyzer):
    """
    Equity Curve Shape Analyzer.

    Metrics computed:
    - Flatness score  : % of trades where equity is below its running high-water mark
    - Recovery time   : average trades needed to recover from a drawdown
    - Equity R²       : linearity of cumulative PnL (high = consistent growth)
    - Loss clusters   : sequences of consecutive losses (≥ N in a row)
    """

    name = "equity_curve"

    def __init__(
        self,
        max_flatness:       float = 0.50,
        min_r_squared:      float = 0.70,
        cluster_min_length: int   = 3,
    ):
        self.max_flatness    = max_flatness
        self.min_r_sq        = min_r_squared
        self.cluster_min     = cluster_min_length

    def analyze(self, trades: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        if "net_money" not in trades.columns or len(trades) < self.min_trades:
            return []

        df = trades.sort_values("open_time").reset_index(drop=True)
        df["cumulative_pnl"] = df["net_money"].cumsum()
        df["hwm"] = df["cumulative_pnl"].cummax()   # high-water mark

        findings = []
        findings += self._check_flatness(df, metrics)
        findings += self._check_r_squared(df, metrics)
        findings += self._check_loss_clusters(df, metrics)
        return sorted(findings, key=lambda f: f.confidence, reverse=True)

    # ── Flatness ──────────────────────────────────────────────────────────────

    def _check_flatness(self, df: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        """% of time equity is below its high-water mark."""
        below_hwm  = (df["cumulative_pnl"] < df["hwm"]).sum()
        flatness   = below_hwm / len(df)

        if flatness <= self.max_flatness:
            return []

        confidence = min(0.90, (flatness - self.max_flatness) * 4)
        impact     = abs(metrics.net_profit) * (flatness - self.max_flatness)

        # Compute average recovery length (trades to get back to HWM)
        recovery_lengths = self._compute_recovery_lengths(df)
        avg_recovery = float(np.mean(recovery_lengths)) if recovery_lengths else 0.0

        return [Finding(
            run_id=self.run_id,
            analyzer=self.name,
            description=(
                f"Equity below high-water mark {flatness*100:.0f}% of the time "
                f"(threshold: {self.max_flatness*100:.0f}%). "
                f"Avg recovery: {avg_recovery:.0f} trades. "
                f"Consider reducing risk or adding drawdown pause logic."
            ),
            severity=self._severity(confidence, impact, metrics.net_profit),
            confidence=confidence,
            impact_estimate_pnl=impact,
            suggested_params={
                "InpRiskPercent":     round(max(0.5, metrics.net_profit / 10000 * 0.75), 1),
                "InpMaxDailyLossPct": 2.0,
            },
            evidence={
                "flatness_score":  round(float(flatness), 4),
                "avg_recovery":    round(avg_recovery, 1),
                "below_hwm_count": int(below_hwm),
                "total_trades":    len(df),
            },
        )]

    def _compute_recovery_lengths(self, df: pd.DataFrame) -> list[int]:
        """Count how many trades it takes to recover from each drawdown trough."""
        lengths = []
        in_dd   = False
        count   = 0
        for _, row in df.iterrows():
            below = row["cumulative_pnl"] < row["hwm"]
            if below and not in_dd:
                in_dd = True
                count = 1
            elif below and in_dd:
                count += 1
            elif not below and in_dd:
                lengths.append(count)
                in_dd = False
                count = 0
        return lengths

    # ── R² linearity ──────────────────────────────────────────────────────────

    def _check_r_squared(self, df: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        """Linear regression on cumulative PnL; low R² = high variance / choppy growth."""
        x = np.arange(len(df))
        y = df["cumulative_pnl"].values

        try:
            slope, intercept, r_value, p_value, _ = linregress(x, y)
        except Exception:
            return []

        r_sq = r_value ** 2
        if r_sq >= self.min_r_sq or slope <= 0:
            return []

        confidence = min(0.85, (self.min_r_sq - r_sq) * 3)

        return [Finding(
            run_id=self.run_id,
            analyzer=self.name,
            description=(
                f"Equity curve linearity R²={r_sq:.2f} (threshold {self.min_r_sq:.2f}). "
                f"High variance in growth pattern — inconsistent performance. "
                f"May indicate regime sensitivity or scattered trade timing."
            ),
            severity="medium" if r_sq < 0.50 else "low",
            confidence=confidence,
            impact_estimate_pnl=0.0,
            suggested_params={},
            evidence={
                "r_squared":  round(r_sq, 4),
                "slope":      round(float(slope), 4),
                "p_value":    round(float(p_value), 4),
            },
        )]

    # ── Loss clusters ─────────────────────────────────────────────────────────

    def _check_loss_clusters(self, df: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        """Find sequences of ≥ N consecutive losing trades."""
        clusters = []
        streak   = 0
        start_idx = None

        for idx, row in df.iterrows():
            if row["net_money"] < 0:
                if streak == 0:
                    start_idx = idx
                streak += 1
            else:
                if streak >= self.cluster_min:
                    cluster_df = df.loc[start_idx:idx - 1]
                    clusters.append({
                        "length":    streak,
                        "total_pnl": float(cluster_df["net_money"].sum()),
                        "start_time": str(df.loc[start_idx, "open_time"]) if "open_time" in df.columns else "?",
                    })
                streak = 0

        # Handle cluster at end of data
        if streak >= self.cluster_min and start_idx is not None:
            cluster_df = df.loc[start_idx:]
            clusters.append({
                "length":    streak,
                "total_pnl": float(cluster_df["net_money"].sum()),
                "start_time": str(df.loc[start_idx, "open_time"]) if "open_time" in df.columns else "?",
            })

        if not clusters:
            return []

        max_cluster   = max(c["length"] for c in clusters)
        total_cluster_loss = sum(c["total_pnl"] for c in clusters if c["total_pnl"] < 0)
        confidence    = min(0.85, len(clusters) * 0.12 + max_cluster * 0.05)

        return [Finding(
            run_id=self.run_id,
            analyzer=self.name,
            description=(
                f"Found {len(clusters)} loss cluster(s) of ≥ {self.cluster_min} consecutive losses. "
                f"Worst streak: {max_cluster} trades. "
                f"Total cluster losses: ${total_cluster_loss:.0f}."
            ),
            severity=self._severity(confidence, abs(total_cluster_loss), metrics.net_profit),
            confidence=confidence,
            impact_estimate_pnl=abs(total_cluster_loss),
            suggested_params={
                "InpMaxDailyLossPct": 2.0,
                "InpMaxTradesPerDay": 3,
            },
            evidence={
                "cluster_count":      len(clusters),
                "max_streak":         max_cluster,
                "total_cluster_loss": round(total_cluster_loss, 2),
                "clusters":           clusters[:5],  # keep top 5 for display
            },
        )]
