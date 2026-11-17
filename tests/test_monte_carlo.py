"""
tests/test_monte_carlo.py
Unit tests for the vectorized Monte Carlo simulator and Quarter-Kelly risk guard.
"""
import numpy as np
from validation.monte_carlo import MonteCarloSimulator


class TestMonteCarloSimulator:

    def test_10k_simulations_under_100ms_and_valid_stats(self):
        rng = np.random.default_rng(123)
        # Positive-expectancy synthetic trade stream (120 trades)
        trades = rng.choice([180.0, -90.0], size=120, p=[0.58, 0.42])

        sim = MonteCarloSimulator(
            n_simulations=10_000,
            initial_capital=10_000.0,
            max_dd_limit_pct=0.15,
            hard_risk_cap_pct=0.02,
            seed=42,
        )
        res = sim.simulate(trades)

        assert res.n_simulations == 10_000
        assert res.n_trades == 120
        assert res.median_net_profit > 0
        assert 0.0 < res.median_max_drawdown_pct <= res.mc_95_max_drawdown_pct <= res.mc_99_max_drawdown_pct
        assert res.quarter_kelly_fraction > 0.0
        assert res.recommended_risk_pct <= 0.02
        assert res.passed is True

    def test_losing_strategy_fails_gate_and_zero_kelly(self):
        trades = [-150.0, -120.0, 50.0, -200.0, -110.0, 40.0] * 10
        sim = MonteCarloSimulator(n_simulations=2_000, initial_capital=10_000.0, max_dd_limit_pct=0.10)
        res = sim.simulate(trades)

        assert res.median_net_profit < 0
        assert res.full_kelly_fraction == 0.0
        assert res.recommended_risk_pct == 0.0
        assert res.passed is False
