"""
optimizer/result_ranker.py
Relative scoring and ranking of backtest results within a session.

KEY DESIGN: Scores are relative to the current session — the best run
in the session = 1.0, worst passing = 0.0. This eliminates the flat
0.2500 problem from absolute thresholds.

Failing runs (unprofitable or < 30 trades) always score 0.0 and
float to the bottom of the ranking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

MIN_TRADES = 30   # Runs with fewer trades have no statistical meaning


@dataclass
class RankedResult:
    """One backtest run's result after scoring and ranking."""
    run_id:       str
    params:       dict[str, Any]
    phase:        str                  # "phase1" | "phase2" | "phase3_oos" | "phase3_sens"

    # Raw metrics from ReportParser
    net_profit:   float = 0.0
    calmar:       float = 0.0
    profit_factor: float = 0.0
    win_rate:     float = 0.0
    max_drawdown: float = 0.0
    total_trades: int   = 0

    # Computed by ranker
    raw_score:    float = 0.0          # weighted before normalization
    score:        float = 0.0          # normalized 0–1 within session
    passing:      bool  = False        # True if profitable + enough trades
    rank:         int   = 0            # 1 = best

    error:        Optional[str] = None


class ResultRanker:
    """
    Ranks a list of RankedResult objects using relative scoring.

    Usage:
        ranker = ResultRanker(weights={"calmar": 0.5, "profit_factor": 0.3, "win_rate": 0.2})
        ranker.rank(results)   # modifies in-place: sets .raw_score, .score, .passing, .rank
    """

    def __init__(self, weights: dict[str, float] = None):
        self.weights = weights or {
            "calmar":        0.50,
            "profit_factor": 0.30,
            "win_rate":      0.20,
        }

    def rank(self, results: list[RankedResult]) -> list[RankedResult]:
        """
        Score, classify (passing/failing), and rank all results.
        Returns the same list sorted by rank (best first).
        Modifies results in-place.
        """
        # Step 1: Classify each result
        for r in results:
            r.passing = self._is_passing(r)
            r.raw_score = self._raw_score(r) if r.passing else 0.0

        # Step 2: Normalize scores within passing runs
        passing = [r for r in results if r.passing]
        failing = [r for r in results if not r.passing]

        if passing:
            max_raw = max(r.raw_score for r in passing)
            min_raw = min(r.raw_score for r in passing)
            span    = max_raw - min_raw

            for r in passing:
                if span > 1e-9:
                    r.score = (r.raw_score - min_raw) / span
                else:
                    r.score = 1.0   # all passing runs scored identically

        for r in failing:
            r.score = 0.0

        # Step 3: Sort (passing by score desc, failing at bottom)
        passing.sort(key=lambda r: r.score, reverse=True)
        failing.sort(key=lambda r: r.raw_score, reverse=True)
        ranked = passing + failing

        for i, r in enumerate(ranked):
            r.rank = i + 1

        n_pass = len(passing)
        n_fail = len(failing)
        logger.info(
            f"ResultRanker: {len(results)} runs — "
            f"{n_pass} passing, {n_fail} failing. "
            f"Best score: {passing[0].score:.3f} ({passing[0].run_id})"
            if passing else
            f"ResultRanker: {len(results)} runs — 0 passing (EA found no profitable config)"
        )

        return ranked

    def top_n(self, results: list[RankedResult], n: int) -> list[RankedResult]:
        """Return top n passing results."""
        return [r for r in results if r.passing][:n]

    # ── Internal ─────────────────────────────────────────────────────────────

    def _is_passing(self, r: RankedResult) -> bool:
        """A result passes if it's profitable AND has enough trades."""
        if r.error:
            return False
        if r.total_trades < MIN_TRADES:
            return False
        if r.net_profit <= 0:
            return False
        return True

    def _raw_score(self, r: RankedResult) -> float:
        """Weighted composite score (before normalization)."""
        w = self.weights

        # Calmar: cap at 5.0 to prevent wild outliers dominating
        calmar_capped = min(max(r.calmar, 0.0), 5.0) / 5.0

        # Profit factor: cap at 3.0
        pf_capped = min(max(r.profit_factor, 0.0), 3.0) / 3.0

        # Win rate: 0–1 already
        wr = max(0.0, min(1.0, r.win_rate / 100.0 if r.win_rate > 1 else r.win_rate))

        score = (
            w.get("calmar", 0.5)        * calmar_capped +
            w.get("profit_factor", 0.3) * pf_capped     +
            w.get("win_rate", 0.2)      * wr
        )

        # Optional net_profit boost (for max_profit objective)
        if "net_profit" in w and w["net_profit"] > 0:
            # Normalize profit to ~$10k scale
            profit_norm = min(max(r.net_profit / 10000.0, 0.0), 1.0)
            score += w["net_profit"] * profit_norm

        return score

    def make_result(
        self,
        run_id: str,
        params: dict,
        phase: str,
        metrics,         # RunMetrics from report_parser — or None on failure
        error: str = None,
    ) -> RankedResult:
        """Convenience constructor from RunMetrics."""
        if metrics is None or error:
            return RankedResult(
                run_id=run_id, params=params, phase=phase,
                error=error or "run_failed",
            )
        result = RankedResult(
            run_id        = run_id,
            params        = params,
            phase         = phase,
            net_profit    = getattr(metrics, "net_profit", 0.0) or 0.0,
            calmar        = getattr(metrics, "calmar_ratio", 0.0) or 0.0,
            profit_factor = getattr(metrics, "profit_factor", 0.0) or 0.0,
            win_rate      = getattr(metrics, "win_rate", 0.0) or 0.0,
            max_drawdown  = getattr(metrics, "max_drawdown_pct", 0.0) or 0.0,
            total_trades  = getattr(metrics, "total_trades", 0) or 0,
        )
        # Set passing + raw_score now so single-run dispatchers (Phase 2 AI loop,
        # validation runs) get correct values without waiting for a full rank() pass.
        result.passing   = self._is_passing(result)
        result.raw_score = self._raw_score(result) if result.passing else 0.0
        return result
