"""
validation/monte_carlo.py
Vectorized Monte Carlo Trade-Sequence & Return Permutation Simulator + Quarter-Kelly Capital Guard.

Simulates 10,000+ randomized trade-order permutations in <50ms using NumPy vectorization to
measure tail-risk drawdown distributions (95% and 99% Confidence Intervals), Prop-Firm
Risk-of-Ruin probabilities, and optimal Quarter-Kelly (0.25 * f*) position sizing.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Sequence
import time
import numpy as np


@dataclass
class MonteCarloResult:
    """Structured telemetry returned by the vectorized Monte Carlo simulator."""
    n_simulations: int
    n_trades: int
    initial_capital: float
    median_net_profit: float
    median_max_drawdown_pct: float
    mc_95_max_drawdown_pct: float
    mc_99_max_drawdown_pct: float
    risk_of_ruin_pct: float
    full_kelly_fraction: float
    quarter_kelly_fraction: float
    recommended_risk_pct: float
    execution_ms: float
    passed: bool

    def to_dict(self) -> dict:
        return asdict(self)


class MonteCarloSimulator:
    """
    Institutional Vectorized Monte Carlo Engine & Quarter-Kelly Risk Guard.
    """

    def __init__(
        self,
        n_simulations: int = 10_000,
        initial_capital: float = 10_000.0,
        max_dd_limit_pct: float = 0.10,
        hard_risk_cap_pct: float = 0.02,
        seed: int | None = 42,
    ):
        self.n_simulations = max(100, int(n_simulations))
        self.initial_capital = float(initial_capital)
        self.max_dd_limit_pct = float(max_dd_limit_pct)
        self.hard_risk_cap_pct = float(hard_risk_cap_pct)
        self.seed = seed

    def simulate(self, trade_pnls: Sequence[float]) -> MonteCarloResult:
        """
        Run vectorized bootstrap permutation of closed trade PnLs.
        Computes peak-to-trough percentage drawdown across all 10,000 paths simultaneously.
        """
        t0 = time.perf_counter()
        pnl_arr = np.asarray(trade_pnls, dtype=np.float64)
        n_trades = pnl_arr.size

        if n_trades < 2:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return MonteCarloResult(
                n_simulations=self.n_simulations,
                n_trades=n_trades,
                initial_capital=self.initial_capital,
                median_net_profit=float(pnl_arr.sum()) if n_trades == 1 else 0.0,
                median_max_drawdown_pct=0.0,
                mc_95_max_drawdown_pct=0.0,
                mc_99_max_drawdown_pct=0.0,
                risk_of_ruin_pct=0.0,
                full_kelly_fraction=0.0,
                quarter_kelly_fraction=0.0,
                recommended_risk_pct=0.0,
                execution_ms=elapsed_ms,
                passed=False,
            )

        rng = np.random.default_rng(self.seed)
        # Vectorized bootstrap resampling with replacement: shape (n_simulations, n_trades)
        idx = rng.integers(0, n_trades, size=(self.n_simulations, n_trades))
        sampled_pnls = pnl_arr[idx]

        # Construct equity curves: shape (n_simulations, n_trades + 1)
        equity = np.empty((self.n_simulations, n_trades + 1), dtype=np.float64)
        equity[:, 0] = self.initial_capital
        np.cumsum(sampled_pnls, axis=1, out=equity[:, 1:])
        equity[:, 1:] += self.initial_capital

        # Running high-water mark and peak-to-trough percentage drawdown
        peaks = np.maximum.accumulate(equity, axis=1)
        safe_peaks = np.where(peaks > 0.0, peaks, self.initial_capital)
        dd_pct_matrix = (peaks - equity) / safe_peaks
        max_dd_per_path = np.max(dd_pct_matrix, axis=1)

        median_profit = float(np.median(equity[:, -1] - self.initial_capital))
        median_dd = float(np.percentile(max_dd_per_path, 50))
        dd_95 = float(np.percentile(max_dd_per_path, 95))
        dd_99 = float(np.percentile(max_dd_per_path, 99))

        # Probability of breaching prop-firm max drawdown limit (e.g. 10%)
        ruin_prob = float(np.mean(max_dd_per_path >= self.max_dd_limit_pct))

        # Compute Full Kelly & Quarter-Kelly (0.25 * f*) with 2.0% hard capital guard
        full_kelly, quarter_kelly, rec_risk = self.compute_kelly_allocation(pnl_arr)

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        passed = (dd_95 <= self.max_dd_limit_pct) and (median_profit > 0.0)

        return MonteCarloResult(
            n_simulations=self.n_simulations,
            n_trades=n_trades,
            initial_capital=self.initial_capital,
            median_net_profit=round(median_profit, 2),
            median_max_drawdown_pct=round(median_dd, 4),
            mc_95_max_drawdown_pct=round(dd_95, 4),
            mc_99_max_drawdown_pct=round(dd_99, 4),
            risk_of_ruin_pct=round(ruin_prob, 4),
            full_kelly_fraction=round(full_kelly, 4),
            quarter_kelly_fraction=round(quarter_kelly, 4),
            recommended_risk_pct=round(rec_risk, 4),
            execution_ms=elapsed_ms,
            passed=bool(passed),
        )

    def compute_kelly_allocation(self, pnl_arr: np.ndarray) -> tuple[float, float, float]:
        """
        Compute Full Kelly f* = p - (1 - p) / b and Quarter-Kelly (0.25 * f*),
        clamped by hard_risk_cap_pct (default 2.0%).
        """
        wins = pnl_arr[pnl_arr > 0]
        losses = pnl_arr[pnl_arr < 0]

        if wins.size == 0 or losses.size == 0:
            return 0.0, 0.0, 0.0

        p = wins.size / pnl_arr.size
        q = 1.0 - p
        avg_win = float(np.mean(wins))
        avg_loss = float(np.abs(np.mean(losses)))

        if avg_loss <= 1e-9:
            return 0.0, 0.0, 0.0

        b = avg_win / avg_loss
        f_star = max(0.0, p - (q / b))
        quarter_kelly = 0.25 * f_star
        recommended = min(quarter_kelly, self.hard_risk_cap_pct)
        return f_star, quarter_kelly, recommended
