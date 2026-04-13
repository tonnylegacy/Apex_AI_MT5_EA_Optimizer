"""
optimizer/budget.py
Time budget manager — tracks elapsed time and estimates remaining runs.
Updates its average-run-time estimate after every completed run.
"""
from __future__ import annotations

import time
from loguru import logger


class BudgetManager:
    """
    Tracks the time budget for an optimization session.

    Usage:
        budget = BudgetManager(budget_minutes=60)
        budget.start()
        ...after each run:
        budget.record_run(elapsed_seconds)
        if budget.is_exhausted():
            break
        remaining = budget.estimated_runs_remaining()
    """

    def __init__(self, budget_minutes: int, initial_seconds_per_run: float = 75.0):
        self.budget_seconds        = budget_minutes * 60
        self.avg_seconds_per_run   = initial_seconds_per_run
        self._start_ts: float      = None
        self._run_times: list[float] = []

    def start(self) -> None:
        self._start_ts = time.time()
        logger.info(f"BudgetManager: started. Budget = {self.budget_seconds/60:.0f} min")

    @property
    def elapsed_seconds(self) -> float:
        if self._start_ts is None:
            return 0.0
        return time.time() - self._start_ts

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.budget_seconds - self.elapsed_seconds)

    @property
    def elapsed_pct(self) -> float:
        return min(100.0, self.elapsed_seconds / self.budget_seconds * 100)

    def record_run(self, seconds: float) -> None:
        """Call after each run completes. Updates rolling average."""
        self._run_times.append(seconds)
        # Rolling average (last 5 runs for responsiveness)
        recent = self._run_times[-5:]
        self.avg_seconds_per_run = sum(recent) / len(recent)

    def estimated_runs_remaining(self) -> int:
        """How many more runs fit in the remaining budget."""
        if self.avg_seconds_per_run <= 0:
            return 0
        return max(0, int(self.remaining_seconds / self.avg_seconds_per_run))

    def is_exhausted(self) -> bool:
        """True when less than 1 average run's time remains."""
        return self.remaining_seconds < self.avg_seconds_per_run

    def can_fit(self, n_runs: int) -> bool:
        """True if n_runs more runs fit in remaining budget."""
        return self.remaining_seconds >= n_runs * self.avg_seconds_per_run

    def summary(self) -> str:
        return (
            f"Budget: {self.elapsed_seconds/60:.1f}/{self.budget_seconds/60:.0f} min used. "
            f"Avg run: {self.avg_seconds_per_run:.0f}s. "
            f"Est. remaining: {self.estimated_runs_remaining()} runs."
        )
