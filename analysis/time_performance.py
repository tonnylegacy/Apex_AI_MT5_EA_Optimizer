"""
analysis/time_performance.py
Analyzes trade performance by hour (UTC), session, and day of week.
Identifies statistically significant negative-edge time windows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data.models import Finding, RunMetrics
from analysis.base import BaseAnalyzer


class TimePerformanceAnalyzer(BaseAnalyzer):
    """
    Session / Hour / Day-of-Week Performance Analyzer.

    Buckets trades by time dimension and flags windows with:
    - Z-score < threshold (mean PnL very negative vs overall)
    - Statistically significant by permutation test (p < 0.10)
    - Minimum trade count (don't flag buckets with too few trades)
    """

    name = "time_performance"

    def __init__(
        self,
        z_score_threshold:      float = -1.5,
        min_bucket_trades:      int   = 10,
        permutation_n:          int   = 1000,
        pvalue_threshold:       float = 0.10,
    ):
        self.z_threshold     = z_score_threshold
        self.min_bucket      = min_bucket_trades
        self.perm_n          = permutation_n
        self.p_threshold     = pvalue_threshold

    def analyze(self, trades: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        if "hour_utc" not in trades.columns:
            return []

        findings = []
        findings += self._analyze_hours(trades, metrics)
        findings += self._analyze_sessions(trades, metrics)
        findings += self._analyze_days(trades, metrics)
        return sorted(findings, key=lambda f: f.confidence, reverse=True)

    # ── Hour analysis ─────────────────────────────────────────────────────────

    def _analyze_hours(self, df: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        """Flag individual UTC hours with poor performance."""
        global_mean = df["net_money"].mean()
        global_std  = df["net_money"].std()
        all_pnl     = df["net_money"].values

        if global_std == 0:
            return []

        findings = []
        for hour in sorted(df["hour_utc"].dropna().unique()):
            bucket = df[df["hour_utc"] == hour]
            if len(bucket) < self.min_bucket:
                continue

            mean_pnl = bucket["net_money"].mean()
            z        = (mean_pnl - global_mean) / global_std

            if z >= self.z_threshold:
                continue

            # Permutation test
            p_val = self._permutation_pvalue(
                bucket["net_money"].values, all_pnl,
                n_permutations=self.perm_n, alternative="less"
            )
            if p_val >= self.p_threshold:
                continue

            impact_pnl = abs(float(bucket[bucket["net_money"] < 0]["net_money"].sum()))
            confidence = max(0.0, min(0.97, 1 - p_val))

            findings.append(Finding(
                run_id=self.run_id,
                analyzer=self.name,
                description=(
                    f"Hour {hour:02d}:00 UTC: mean PnL ${mean_pnl:.2f} "
                    f"(Z={z:.2f}, {len(bucket)} trades). "
                    f"Estimated negative contribution: ${impact_pnl:.0f}."
                ),
                severity=self._severity(confidence, impact_pnl, metrics.net_profit),
                confidence=confidence,
                impact_estimate_pnl=impact_pnl,
                suggested_params={},  # session filter suggestion built by aggregator
                evidence={
                    "type":       "hour",
                    "hour_utc":   int(hour),
                    "mean_pnl":   round(mean_pnl, 2),
                    "z_score":    round(z, 3),
                    "trade_count": int(len(bucket)),
                    "p_value":    round(p_val, 4),
                },
            ))

        # Consolidate consecutive bad hours into a single window finding
        if findings:
            findings = self._consolidate_hour_findings(findings, df, metrics)

        return findings

    def _consolidate_hour_findings(
        self, hour_findings: list[Finding], df: pd.DataFrame, metrics: RunMetrics
    ) -> list[Finding]:
        """
        Group consecutive flagged hours into a single window finding.
        E.g. hours [14, 15, 16] → "14:00–17:00 UTC bad window"
        Returns a single consolidated finding (plus keeps top individual for detail).
        """
        bad_hours = sorted(
            int(f.evidence["hour_utc"]) for f in hour_findings
        )
        if not bad_hours:
            return hour_findings

        # Find contiguous groups
        groups = []
        group  = [bad_hours[0]]
        for h in bad_hours[1:]:
            if h == group[-1] + 1:
                group.append(h)
            else:
                groups.append(group)
                group = [h]
        groups.append(group)

        consolidated = []
        for g in groups:
            start_h  = g[0]
            end_h    = g[-1] + 1
            window   = df[df["hour_utc"].between(start_h, g[-1])]
            total_pnl = float(window["net_money"].sum())
            n_trades  = len(window)
            impact    = abs(float(window[window["net_money"] < 0]["net_money"].sum()))

            # Derive session filter params from window
            # Convert UTC to broker local time for session params
            broker_start = (start_h + 2) % 24  # UTC+2 (from config)
            broker_end   = (end_h   + 2) % 24

            max_conf = max(f.confidence for f in hour_findings
                          if f.evidence["hour_utc"] in g)

            consolidated.append(Finding(
                run_id=self.run_id,
                analyzer=self.name,
                description=(
                    f"Negative edge window {start_h:02d}:00–{end_h:02d}:00 UTC: "
                    f"${total_pnl:.0f} total, {n_trades} trades. "
                    f"Consider excluding this window via session filter."
                ),
                severity=self._severity(max_conf, impact, metrics.net_profit),
                confidence=max_conf,
                impact_estimate_pnl=impact,
                suggested_params={
                    "InpUseSession":   True,
                    # Preserve existing session start; cut end before bad window
                    # These are broker-local hours
                    "InpSessionEnd":   (broker_start) % 24,
                },
                evidence={
                    "type":           "hour_window",
                    "start_utc":      start_h,
                    "end_utc":        end_h,
                    "broker_start":   broker_start,
                    "broker_end":     broker_end,
                    "total_pnl":      round(total_pnl, 2),
                    "trade_count":    n_trades,
                    "hours_flagged":  g,
                },
            ))

        return consolidated

    # ── Session analysis ──────────────────────────────────────────────────────

    def _analyze_sessions(self, df: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        if "session" not in df.columns:
            return []

        global_mean = df["net_money"].mean()
        global_std  = df["net_money"].std()
        all_pnl     = df["net_money"].values

        if global_std == 0:
            return []

        findings = []
        for session in df["session"].dropna().unique():
            bucket = df[df["session"] == session]
            if len(bucket) < self.min_bucket:
                continue

            mean_pnl = bucket["net_money"].mean()
            z        = (mean_pnl - global_mean) / global_std
            if z >= self.z_threshold:
                continue

            p_val = self._permutation_pvalue(
                bucket["net_money"].values, all_pnl,
                n_permutations=self.perm_n, alternative="less"
            )
            if p_val >= self.p_threshold:
                continue

            pf = (
                bucket[bucket["net_money"] > 0]["net_money"].sum() /
                max(0.01, abs(bucket[bucket["net_money"] < 0]["net_money"].sum()))
            )
            impact = abs(float(bucket[bucket["net_money"] < 0]["net_money"].sum()))
            confidence = max(0.0, min(0.97, 1 - p_val))

            findings.append(Finding(
                run_id=self.run_id,
                analyzer=self.name,
                description=(
                    f"{session} session: PF {pf:.2f}, mean PnL ${mean_pnl:.2f} "
                    f"(Z={z:.2f}, {len(bucket)} trades). Recommend excluding this session."
                ),
                severity=self._severity(confidence, impact, metrics.net_profit),
                confidence=confidence,
                impact_estimate_pnl=impact,
                suggested_params={"InpUseSession": True},
                evidence={
                    "type":         "session",
                    "session":      session,
                    "profit_factor": round(float(pf), 3),
                    "mean_pnl":     round(mean_pnl, 2),
                    "z_score":      round(z, 3),
                    "trade_count":  int(len(bucket)),
                    "p_value":      round(p_val, 4),
                },
            ))

        return findings

    # ── Day-of-week analysis ──────────────────────────────────────────────────

    def _analyze_days(self, df: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        if "day_of_week" not in df.columns:
            return []

        DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        global_mean = df["net_money"].mean()
        global_std  = df["net_money"].std()
        all_pnl     = df["net_money"].values

        if global_std == 0:
            return []

        findings = []
        for day in range(5):   # 0=Mon … 4=Fri
            bucket = df[df["day_of_week"] == day]
            if len(bucket) < self.min_bucket:
                continue

            mean_pnl = bucket["net_money"].mean()
            z        = (mean_pnl - global_mean) / global_std
            if z >= self.z_threshold:
                continue

            p_val = self._permutation_pvalue(
                bucket["net_money"].values, all_pnl,
                n_permutations=self.perm_n, alternative="less"
            )
            if p_val >= self.p_threshold:
                continue

            impact     = abs(float(bucket[bucket["net_money"] < 0]["net_money"].sum()))
            confidence = max(0.0, min(0.97, 1 - p_val))

            findings.append(Finding(
                run_id=self.run_id,
                analyzer=self.name,
                description=(
                    f"{DAY_NAMES[day]}: mean PnL ${mean_pnl:.2f} "
                    f"(Z={z:.2f}, {len(bucket)} trades). "
                    f"Possible day-of-week edge degradation."
                ),
                severity="low",   # day-level findings are informational
                confidence=confidence,
                impact_estimate_pnl=impact,
                suggested_params={},
                evidence={
                    "type":         "day_of_week",
                    "day":          DAY_NAMES[day],
                    "day_index":    day,
                    "mean_pnl":     round(mean_pnl, 2),
                    "z_score":      round(z, 3),
                    "trade_count":  int(len(bucket)),
                    "p_value":      round(p_val, 4),
                },
            ))

        return findings
