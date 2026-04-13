"""
optimizer/session_config.py
Holds all user choices for one optimization session.
Passed from the /setup form → /api/start → pipeline.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional


ObjectiveType = Literal["balanced", "max_profit", "min_drawdown"]
BudgetMinutes = Literal[30, 60, 120]


@dataclass
class SessionConfig:
    """Everything the user configures on the /setup page."""

    # EA identity
    ea_name:    str = "LEGSTECH_EA_V2"
    symbol:     str = "XAUUSD"
    timeframe:  str = "H1"

    # Training period (in-sample)
    train_start: str = "2022.01.01"
    train_end:   str = "2023.12.31"

    # Validation period (out-of-sample)
    val_start:   str = "2024.01.01"
    val_end:     str = "2024.06.30"

    # Optimization objective
    objective:   ObjectiveType = "balanced"

    # Time budget
    budget_minutes: int = 60       # 30 | 60 | 120

    # Advanced: which params the user wants to optimize
    # Empty list means "use profile's default optimize_params"
    selected_params: list[str] = field(default_factory=list)

    # Phase 1 sample count (derived from budget, not user-set directly)
    phase1_samples: int = 20

    # ── Derived helpers ───────────────────────────────────────────────────────

    def derive_samples(self, seconds_per_run: float = 75.0) -> None:
        """
        Automatically set phase1_samples based on time budget.
        Reserves ~40% of budget for Phase 2 + Phase 3.
        """
        total_seconds  = self.budget_minutes * 60
        phase1_budget  = total_seconds * 0.55        # 55% for broad search
        n              = int(phase1_budget / seconds_per_run)
        self.phase1_samples = max(10, min(n, 50))    # clamp 10–50

    @property
    def phase2_samples(self) -> int:
        """Refinement runs: top 3 configs × 3 neighbors each = 9."""
        return 9

    @property
    def phase3_samples(self) -> int:
        """Validation: 2 OOS runs + 3 sensitivity = 5."""
        return 5

    @property
    def total_budget_runs(self) -> int:
        return self.phase1_samples + self.phase2_samples + self.phase3_samples

    # ── Scoring weights based on objective ───────────────────────────────────

    @property
    def scoring_weights(self) -> dict:
        if self.objective == "max_profit":
            return {"calmar": 0.3, "profit_factor": 0.3, "win_rate": 0.2, "net_profit": 0.2}
        if self.objective == "min_drawdown":
            return {"calmar": 0.6, "profit_factor": 0.25, "win_rate": 0.15, "net_profit": 0.0}
        # balanced (default)
        return {"calmar": 0.5, "profit_factor": 0.3, "win_rate": 0.2, "net_profit": 0.0}

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionConfig":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @classmethod
    def from_form(cls, form: dict) -> "SessionConfig":
        """
        Parse raw form POST data (all values are strings).
        Handles type coercion, validation, and defaults.
        """
        def s(key, default=""): return str(form.get(key, default)).strip()
        def i(key, default=0):
            try: return int(form.get(key, default))
            except (ValueError, TypeError): return default

        cfg = cls(
            ea_name         = s("ea_name", "LEGSTECH_EA_V2"),
            symbol          = s("symbol", "XAUUSD").upper(),
            timeframe       = s("timeframe", "H1").upper(),
            train_start     = s("train_start", "2022.01.01").replace("-", "."),
            train_end       = s("train_end", "2023.12.31").replace("-", "."),
            val_start       = s("val_start", "2024.01.01").replace("-", "."),
            val_end         = s("val_end", "2024.06.30").replace("-", "."),
            objective       = s("objective", "balanced"),
            budget_minutes  = i("budget_minutes", 60),
            selected_params = form.get("selected_params", []),
        )
        cfg.derive_samples()
        return cfg
