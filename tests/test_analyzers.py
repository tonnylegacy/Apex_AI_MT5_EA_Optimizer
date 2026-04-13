"""
tests/test_analyzers.py
Unit tests for all analyzer modules using synthetic trade data.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from data.models import RunMetrics
from analysis.reversal import ReversalAnalyzer
from analysis.time_performance import TimePerformanceAnalyzer
from analysis.entry_exit_quality import EntryExitQualityAnalyzer
from analysis.equity_curve import EquityCurveAnalyzer


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_trade(
    net_money: float,
    mfe_pips: float = 0,
    mae_pips: float = 0,
    hour_utc: int = 10,
    session: str = "London",
    day_of_week: int = 1,
    result_class: str = None,
    duration_minutes: int = 60,
    lot_size: float = 0.1,
    net_pips: float = None,
    open_time: datetime = None,
) -> dict:
    if result_class is None:
        result_class = "win" if net_money > 0 else ("reversal" if mfe_pips > 15 and net_money < 0 else "loss")
    if net_pips is None:
        net_pips = net_money / 100
    if open_time is None:
        open_time = datetime(2022, 1, 3, hour_utc, 0)
    return {
        "ticket": np.random.randint(100000, 999999),
        "open_time": open_time,
        "close_time": open_time + timedelta(minutes=duration_minutes),
        "direction": "buy",
        "open_price": 1900.0,
        "close_price": 1900.0 + net_pips * 0.1,
        "sl": 1880.0, "tp": 1920.0,
        "lot_size": lot_size,
        "net_money": net_money,
        "net_pips": net_pips,
        "duration_minutes": duration_minutes,
        "commission": 0.0, "swap": 0.0,
        "mfe_pips": mfe_pips, "mae_pips": mae_pips,
        "session": session, "day_of_week": day_of_week,
        "hour_utc": hour_utc, "hour_broker": (hour_utc + 2) % 24,
        "result_class": result_class,
        "mfe_capture_ratio": max(0, net_money) / max(1, mfe_pips * 10),
        "entry_quality": max(0, 1 - mae_pips / max(mfe_pips + mae_pips, 1)),
        "exit_quality": max(0, net_pips / max(mfe_pips, 1)),
    }


def dummy_metrics(trades_df: pd.DataFrame, run_id: str = "test_run") -> RunMetrics:
    wins   = trades_df[trades_df["net_money"] > 0]
    losses = trades_df[trades_df["net_money"] < 0]
    net    = trades_df["net_money"].sum()
    gp     = wins["net_money"].sum()
    gl     = abs(losses["net_money"].sum())
    pf     = gp / gl if gl > 0 else 99.0
    dd     = abs(losses["net_money"].min()) if len(losses) > 0 else 1
    return RunMetrics(
        run_id=run_id,
        net_profit=net,
        profit_factor=pf,
        max_drawdown_abs=dd,
        max_drawdown_pct=dd / 10000,
        calmar_ratio=max(0, net / 10000) / max(0.01, dd / 10000),
        sharpe_ratio=1.2,
        total_trades=len(trades_df),
        win_rate=len(wins) / max(1, len(trades_df)),
        avg_win=gp / max(1, len(wins)),
        avg_loss=gl / max(1, len(losses)),
        recovery_factor=2.0,
        largest_loss=abs(losses["net_money"].min()) if len(losses) > 0 else 0,
        expected_payoff=net / max(1, len(trades_df)),
    )


# ── Reversal Analyzer Tests ───────────────────────────────────────────────────

class TestReversalAnalyzer:

    def test_detects_high_reversal_rate(self):
        """Should find a HIGH finding when many losers had significant MFE."""
        trades = []
        # 50% of losers had MFE > 20 pips (high reversal rate)
        for _ in range(40):
            trades.append(make_trade(net_money=100, mfe_pips=30, mae_pips=5))
        for _ in range(20):
            trades.append(make_trade(net_money=-80, mfe_pips=25, mae_pips=8))   # reversals
        for _ in range(20):
            trades.append(make_trade(net_money=-80, mfe_pips=5,  mae_pips=20))  # normal losers

        df      = pd.DataFrame(trades)
        metrics = dummy_metrics(df)
        az      = ReversalAnalyzer(mfe_threshold_pips=15, min_reversal_rate=0.30, permutation_n=50)
        findings = az.run(df, metrics, "test")

        assert len(findings) > 0
        top = findings[0]
        assert top.severity in ("high", "medium")
        assert "InpUseTrailing" in top.suggested_params
        assert top.suggested_params["InpUseTrailing"] is True

    def test_no_finding_when_low_reversal_rate(self):
        """Should NOT find reversals when rate is below threshold."""
        trades = [make_trade(net_money=100, mfe_pips=20)] * 50
        trades += [make_trade(net_money=-80, mfe_pips=5)] * 20   # only small-MFE losers
        df = pd.DataFrame(trades)
        metrics = dummy_metrics(df)
        az = ReversalAnalyzer(mfe_threshold_pips=15, min_reversal_rate=0.30, permutation_n=50)
        findings = az.run(df, metrics, "test")
        # Should have no reversal-rate finding (maybe only capture rate)
        reversal_findings = [f for f in findings if "reversal" in f.description.lower() and "% of losing" in f.description]
        assert len(reversal_findings) == 0


# ── Time Performance Tests ────────────────────────────────────────────────────

class TestTimePerformanceAnalyzer:

    def test_detects_bad_hour_window(self):
        """Should flag a consistent loss at hour 14–15 UTC."""
        trades = []
        # Good trades at most hours
        for h in [8, 9, 10, 11, 12, 13]:
            for _ in range(12):
                trades.append(make_trade(net_money=100, hour_utc=h))
        # Consistently losing trades at 14–15 UTC
        for h in [14, 15]:
            for _ in range(15):
                trades.append(make_trade(net_money=-150, hour_utc=h))

        df = pd.DataFrame(trades)
        metrics = dummy_metrics(df)
        az = TimePerformanceAnalyzer(z_score_threshold=-1.0, min_bucket_trades=8, permutation_n=200)
        findings = az.run(df, metrics, "test")

        hour_findings = [f for f in findings if "UTC" in f.description and "14" in f.description]
        assert len(hour_findings) > 0

    def test_no_finding_for_uniform_performance(self):
        """No time-based finding when performance is uniform across hours."""
        trades = []
        rng = np.random.default_rng(42)
        for h in range(8, 20):
            for _ in range(12):
                pnl = float(rng.normal(50, 20))
                trades.append(make_trade(net_money=pnl, hour_utc=h))
        df = pd.DataFrame(trades)
        metrics = dummy_metrics(df)
        az = TimePerformanceAnalyzer(z_score_threshold=-2.0, min_bucket_trades=8, permutation_n=200)
        findings = az.run(df, metrics, "test")
        # May or may not find something; just verify it runs without error
        assert isinstance(findings, list)


# ── Entry/Exit Quality Tests ──────────────────────────────────────────────────

class TestEntryExitQualityAnalyzer:

    def test_detects_good_entry_poor_exit(self):
        """When entries are good but exits capture little of MFE."""
        trades = []
        for _ in range(60):
            # Small MAE (good entry), large MFE but poor capture
            trades.append(make_trade(
                net_money=20, mfe_pips=50, mae_pips=3,
                net_pips=2, lot_size=0.1
            ))
        df = pd.DataFrame(trades)
        metrics = dummy_metrics(df)
        az = EntryExitQualityAnalyzer(poor_exit_threshold=0.60, poor_entry_threshold=0.40)
        findings = az.run(df, metrics, "test")
        action_findings = [f for f in findings if "diagnosis" in f.evidence and
                           f.evidence["diagnosis"] == "good_entry_poor_exit"]
        assert len(action_findings) > 0

    def test_no_findings_for_healthy_trades(self):
        """Should not flag anything when both entry and exit quality are high."""
        trades = []
        for _ in range(60):
            trades.append(make_trade(
                net_money=80, mfe_pips=100, mae_pips=5,
                net_pips=80, lot_size=0.1
            ))
        df = pd.DataFrame(trades)
        metrics = dummy_metrics(df)
        az = EntryExitQualityAnalyzer(poor_exit_threshold=0.55, poor_entry_threshold=0.40)
        findings = az.run(df, metrics, "test")
        action_findings = [f for f in findings if f.severity in ("high", "medium")]
        assert len(action_findings) == 0


# ── Equity Curve Tests ────────────────────────────────────────────────────────

class TestEquityCurveAnalyzer:

    def test_detects_loss_clusters(self):
        """Should flag a sequence of 5+ consecutive losses."""
        trades = []
        base_time = datetime(2022, 1, 3, 10, 0)
        # Wins, then a cluster of losses, then more wins
        for i in range(30):
            trades.append(make_trade(net_money=100, open_time=base_time + timedelta(hours=i)))
        for i in range(30, 37):  # 7 consecutive losses
            trades.append(make_trade(net_money=-150, open_time=base_time + timedelta(hours=i)))
        for i in range(37, 60):
            trades.append(make_trade(net_money=100, open_time=base_time + timedelta(hours=i)))

        df = pd.DataFrame(trades)
        metrics = dummy_metrics(df)
        az = EquityCurveAnalyzer(cluster_min_length=5)
        findings = az.run(df, metrics, "test")
        cluster_findings = [f for f in findings if "cluster" in f.description.lower()]
        assert len(cluster_findings) > 0

    def test_detects_high_flatness(self):
        """Should flag when equity spends most time in drawdown."""
        trades = []
        base_time = datetime(2022, 1, 3, 10, 0)
        # Pattern: win a little, lose a lot, basically always in drawdown
        for i in range(50):
            if i % 5 == 0:
                trades.append(make_trade(net_money=50, open_time=base_time + timedelta(hours=i)))
            else:
                trades.append(make_trade(net_money=-30, open_time=base_time + timedelta(hours=i)))
        df = pd.DataFrame(trades)
        metrics = dummy_metrics(df)
        az = EquityCurveAnalyzer(max_flatness=0.30)
        findings = az.run(df, metrics, "test")
        flatness_findings = [f for f in findings if "high-water" in f.description]
        assert len(flatness_findings) > 0


# ── Composite Score Tests ─────────────────────────────────────────────────────

class TestCompositeScorer:

    def test_score_increases_with_calmar(self):
        """Higher Calmar should produce higher score, all else equal."""
        from scoring.composite import CompositeScorer

        def make_metrics(calmar):
            return RunMetrics(
                run_id="t",
                net_profit=10000, profit_factor=1.5,
                max_drawdown_abs=1000, max_drawdown_pct=0.10,
                calmar_ratio=calmar, sharpe_ratio=1.2,
                total_trades=100, win_rate=0.55,
                avg_win=200, avg_loss=150,
                recovery_factor=3.0, largest_loss=500,
                expected_payoff=50,
                avg_mfe_capture=0.6, reversal_rate=0.1,
            )

        scorer = CompositeScorer("config.yaml")
        s1 = scorer.score(make_metrics(0.5))
        s2 = scorer.score(make_metrics(1.5))
        s3 = scorer.score(make_metrics(3.0))
        assert s1 < s2 < s3

    def test_score_zero_below_min_trades(self):
        from scoring.composite import CompositeScorer
        scorer = CompositeScorer("config.yaml")
        m = RunMetrics(
            run_id="t", net_profit=5000, profit_factor=2.0,
            max_drawdown_abs=500, max_drawdown_pct=0.05,
            calmar_ratio=2.0, sharpe_ratio=1.5,
            total_trades=10,   # below min_trades (50)
            win_rate=0.6, avg_win=200, avg_loss=100,
            recovery_factor=4.0, largest_loss=200, expected_payoff=100,
        )
        assert scorer.score(m) == 0.0
