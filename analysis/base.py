"""
analysis/base.py
Abstract base class for all analyzer modules.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np
import pandas as pd

from data.models import Finding, RunMetrics


class BaseAnalyzer(ABC):
    """
    Every analyzer receives a trades DataFrame and RunMetrics,
    and returns a list of Finding objects sorted by confidence descending.
    """

    name: str = "base"
    min_trades: int = 30          # refuse to analyze below this count
    run_id: str = ""

    def run(
        self,
        trades: pd.DataFrame,
        metrics: RunMetrics,
        run_id: str,
    ) -> list[Finding]:
        """Entry point — enforces minimum trade count gate."""
        self.run_id = run_id
        if len(trades) < self.min_trades:
            return []
        return self.analyze(trades, metrics)

    @abstractmethod
    def analyze(self, trades: pd.DataFrame, metrics: RunMetrics) -> list[Finding]:
        """Implement analysis logic. Return list of findings."""
        ...

    # ── Statistical helpers ────────────────────────────────────────────────────

    def _confidence_from_z(self, z: float) -> float:
        """Map a Z-score to a [0,1] confidence value using normal CDF."""
        from scipy.stats import norm
        return float(min(1.0, max(0.0, 2 * norm.cdf(abs(z)) - 1)))

    def _permutation_pvalue(
        self,
        group_values: np.ndarray,
        all_values: np.ndarray,
        n_permutations: int = 500,
        alternative: str = "less",   # 'less' = testing if group mean < overall mean
    ) -> float:
        """
        Non-parametric permutation test.
        Returns p-value: probability that observed group mean is due to chance.
        Lower p-value = more statistically significant.
        """
        if len(group_values) == 0 or len(all_values) == 0:
            return 1.0

        observed_stat = np.mean(group_values)
        n_group = len(group_values)
        count_extreme = 0

        rng = np.random.default_rng(seed=42)   # deterministic
        for _ in range(n_permutations):
            sample = rng.choice(all_values, size=n_group, replace=False)
            sample_stat = np.mean(sample)
            if alternative == "less" and sample_stat <= observed_stat:
                count_extreme += 1
            elif alternative == "greater" and sample_stat >= observed_stat:
                count_extreme += 1

        return count_extreme / n_permutations

    def _severity(self, confidence: float, impact_pnl: float, total_pnl: float) -> str:
        """Derive severity from confidence and relative $ impact."""
        impact_fraction = abs(impact_pnl) / max(abs(total_pnl), 1)
        if confidence >= 0.80 or impact_fraction >= 0.15:
            return "high"
        elif confidence >= 0.60 or impact_fraction >= 0.07:
            return "medium"
        return "low"
