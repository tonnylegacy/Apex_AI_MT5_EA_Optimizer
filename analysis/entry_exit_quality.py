"""
analysis/entry_exit_quality.py
Scores trade entries and exits using MAE/MFE ratios.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from data.models import Finding, RunMetrics
from analysis.base import BaseAnalyzer


class EntryExitQualityAnalyzer(BaseAnalyzer):
    """
    Entry/Exit Quality Scorer.

    Uses MAE and MFE to independently assess:
    - Entry quality: how much did price move against us before moving our way?
    - Exit quality : what fraction of the available move did we capture?

    Diagnosis matrix:
    ┌─────────────┬──────────────┬──────────────────────────────────────┐
    │entry_quality│ exit_quality │ Diagnosis                            │
    ├─────────────┼──────────────┼──────────────────────────────────────┤
    │    High     │    High      │ Healthy — no action                  │
    │    High     │    Low       │ Good entries, poor exits → trail/TP  │
    │    Low      │    High      │ Bad entries, recovering → entry filt │
    │    Low      │    Low       │ Systematic issue → both sides broken │
    └─────────────┴──────────────┴──────────────────────────────────────┘
    """

    name = "entry_exit_quality"
    min_trades = 20

    def __init__(
        self,
        poor_exit_threshold:  float = 0.55,
        poor_entry_threshold: float = 0.40,
        good_entry_threshold: float = 0.65,
    ):
        self.poor_exit   = poor_exit_threshold
        self.poor_entry  = poor_entry_threshold
        self.good_entry  = good_entry_threshold

    def analyze(self, trades: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        if "mfe_pips" not in trades.columns or trades["mfe_pips"].isna().all():
            return []

        df = trades[trades["mfe_pips"].notna() & (trades["mfe_pips"] > 0)].copy()
        if len(df) < self.min_trades:
            return []

        # Compute quality scores if not already present
        if "exit_quality" not in df.columns or df["exit_quality"].isna().all():
            df["exit_quality"] = (df["net_pips"] / df["mfe_pips"].clip(lower=0.01)).clip(0, 1)
        if "entry_quality" not in df.columns or df["entry_quality"].isna().all():
            denom = df["mfe_pips"] + df["mae_pips"].fillna(0) + 0.01
            df["entry_quality"] = (1 - df["mae_pips"].fillna(0) / denom).clip(0, 1)

        mean_exit  = float(df["exit_quality"].mean())
        mean_entry = float(df["entry_quality"].mean())

        findings = []

        # ── Case 1: Good entries, poor exits ──────────────────────────────
        if mean_exit < self.poor_exit and mean_entry >= self.good_entry:
            z = (self.poor_exit - mean_exit) / max(0.01, df["exit_quality"].std())
            confidence = min(0.95, self._confidence_from_z(z))
            potential  = float((df["mfe_pips"] - df["net_pips"].clip(lower=0)).clip(lower=0).mean())
            impact     = potential * len(df) * 0.1  # rough dollar estimate

            findings.append(Finding(
                run_id=self.run_id,
                analyzer=self.name,
                description=(
                    f"Good entries (quality={mean_entry:.2f}) but poor exits "
                    f"(quality={mean_exit:.2f}). "
                    f"Capturing only {mean_exit*100:.0f}% of available MFE. "
                    f"Consider trailing stop or tighter TP."
                ),
                severity=self._severity(confidence, impact, metrics.net_profit),
                confidence=confidence,
                impact_estimate_pnl=impact,
                suggested_params={
                    "InpUseTrailing":    True,
                    "InpTrailStartPips": round(float(df["mfe_pips"].quantile(0.30)), 1),
                    "InpTrailStepPips":  10.0,
                },
                evidence={
                    "mean_entry_quality": round(mean_entry, 4),
                    "mean_exit_quality":  round(mean_exit, 4),
                    "diagnosis":          "good_entry_poor_exit",
                    "sample_size":        len(df),
                },
            ))

        # ── Case 2: Poor entries ───────────────────────────────────────────
        elif mean_entry < self.poor_entry:
            z = (self.poor_entry - mean_entry) / max(0.01, df["entry_quality"].std())
            confidence = min(0.95, self._confidence_from_z(z))
            # Trades with high MAE but positive result still suggest entry timing issue
            high_mae_losers = df[(df["mae_pips"] > df["mfe_pips"]) & (df["net_money"] < 0)]
            impact = abs(float(high_mae_losers["net_money"].sum()))

            findings.append(Finding(
                run_id=self.run_id,
                analyzer=self.name,
                description=(
                    f"Poor entry quality ({mean_entry:.2f}): "
                    f"significant adverse move before trades become profitable. "
                    f"{len(high_mae_losers)} trades had MAE > MFE and closed at a loss. "
                    f"Consider tightening entry filters (ATR, EMA slope, score gate)."
                ),
                severity=self._severity(confidence, impact, metrics.net_profit),
                confidence=confidence,
                impact_estimate_pnl=impact,
                suggested_params={
                    "InpUseATRFilter":   True,    # placeholder name; map to actual param
                    "InpATRMultiplier":  round(float(df["mae_pips"].quantile(0.70)) / 100, 1),
                    "InpMinScore":       9,        # tighten quality gate
                },
                evidence={
                    "mean_entry_quality": round(mean_entry, 4),
                    "mean_exit_quality":  round(mean_exit, 4),
                    "diagnosis":          "poor_entry",
                    "high_mae_loser_count": int(len(high_mae_losers)),
                    "sample_size":        len(df),
                },
            ))

        # ── Case 3: Both broken ────────────────────────────────────────────
        elif mean_exit < self.poor_exit and mean_entry < self.poor_entry:
            confidence = 0.75
            impact = abs(metrics.net_profit) * 0.5  # rough

            findings.append(Finding(
                run_id=self.run_id,
                analyzer=self.name,
                description=(
                    f"Both entry ({mean_entry:.2f}) and exit ({mean_exit:.2f}) quality are poor. "
                    f"This suggests a systematic issue with the strategy logic. "
                    f"Consider testing a different BotMode or EntryMode."
                ),
                severity="high",
                confidence=confidence,
                impact_estimate_pnl=impact,
                suggested_params={"InpBotMode": 2},  # conservative mode
                evidence={
                    "mean_entry_quality": round(mean_entry, 4),
                    "mean_exit_quality":  round(mean_exit, 4),
                    "diagnosis":          "both_broken",
                    "sample_size":        len(df),
                },
            ))

        # ── Always report summary stats as a LOW finding for visibility ───
        findings.append(Finding(
            run_id=self.run_id,
            analyzer=self.name,
            description=(
                f"Entry quality: {mean_entry:.2f} | Exit quality: {mean_exit:.2f} | "
                f"Sample: {len(df)} trades with MFE data."
            ),
            severity="low",
            confidence=0.99,
            impact_estimate_pnl=0.0,
            suggested_params={},
            evidence={
                "mean_entry_quality": round(mean_entry, 4),
                "mean_exit_quality":  round(mean_exit, 4),
                "sample_size":        len(df),
                "diagnosis":          "summary",
            },
        ))

        return sorted(findings, key=lambda f: f.confidence, reverse=True)
