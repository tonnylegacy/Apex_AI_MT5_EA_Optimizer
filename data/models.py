"""
data/models.py
Pydantic v2 data models for all entities in the optimizer.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, computed_field, model_validator
import uuid


# ─────────────────────────────────────────────────────────────────────────────
# Trade
# ─────────────────────────────────────────────────────────────────────────────

class Trade(BaseModel):
    """One closed trade, enriched with MAE/MFE from the logger CSV."""
    ticket:       int
    open_time:    datetime
    close_time:   datetime
    direction:    Literal["buy", "sell"]
    open_price:   float
    close_price:  float
    sl:           float
    tp:           float
    lot_size:     float
    net_pips:     float
    net_money:    float
    duration_minutes: int
    commission:   float = 0.0
    swap:         float = 0.0

    # From TradeLogger CSV (may be None if logger not available)
    mfe_pips:     Optional[float] = None
    mae_pips:     Optional[float] = None

    # Derived — populated during ingest
    session:         Optional[str]   = None   # London | NY | Asian | LondonNY | Off
    day_of_week:     Optional[int]   = None   # 0=Mon … 4=Fri
    hour_broker:     Optional[int]   = None   # broker local hour
    hour_utc:        Optional[int]   = None   # UTC hour (after timezone normalisation)
    result_class:    Optional[str]   = None   # win | loss | be | reversal

    # Computed quality scores (None when MFE/MAE not available)
    mfe_capture_ratio: Optional[float] = None  # net_money / mfe_value; 1.0 = captured all
    entry_quality:     Optional[float] = None  # 1 - (mae_pips / max(mfe_pips,1))
    exit_quality:      Optional[float] = None  # net_pips / max(mfe_pips, 1)

    @property
    def won(self) -> bool:
        return self.net_money > 0

    @property
    def lost(self) -> bool:
        return self.net_money < 0


# ─────────────────────────────────────────────────────────────────────────────
# RunMetrics
# ─────────────────────────────────────────────────────────────────────────────

class RunMetrics(BaseModel):
    """Summary metrics for one backtest run."""
    run_id:           str
    net_profit:       float
    profit_factor:    float
    max_drawdown_abs: float   # in account currency
    max_drawdown_pct: float   # as fraction (0.15 = 15%)
    calmar_ratio:     float
    sharpe_ratio:     float
    total_trades:     int
    win_rate:         float   # fraction (0.55 = 55%)
    avg_win:          float
    avg_loss:         float
    recovery_factor:  float
    largest_loss:     float
    expected_payoff:  float

    # MAE/MFE derived (populated when logger CSV available)
    avg_mfe_capture:  Optional[float] = None
    avg_mfe_pips:     Optional[float] = None
    avg_mae_pips:     Optional[float] = None
    reversal_rate:    Optional[float] = None   # reverted trades / total losers

    # Filled by composite scorer
    composite_score:  float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────

class Run(BaseModel):
    """One complete backtest run record."""
    run_id:        str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    run_ts:        datetime = Field(default_factory=datetime.utcnow)
    ea_name:       str
    symbol:        str
    timeframe:     str
    period_start:  str
    period_end:    str
    params:        dict[str, Any]           # snapshot of all EA inputs used
    phase:         str
    hypothesis_id: Optional[str] = None
    tester_model:  int = 0                  # 0=Every Tick
    ini_snapshot:  Optional[str] = None     # full .ini content for reproducibility
    report_path:   Optional[str] = None
    log_csv_path:  Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Finding
# ─────────────────────────────────────────────────────────────────────────────

class Finding(BaseModel):
    """One actionable observation from an analyzer module."""
    finding_id:          str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    run_id:              str
    analyzer:            str
    description:         str
    severity:            Literal["high", "medium", "low"]
    confidence:          float               # 0.0–1.0
    impact_estimate_pnl: float = 0.0        # estimated PnL recovery if addressed
    suggested_params:    dict[str, Any] = {}
    evidence:            dict[str, Any] = {}  # raw supporting data (for reports)


# ─────────────────────────────────────────────────────────────────────────────
# Hypothesis
# ─────────────────────────────────────────────────────────────────────────────

class Hypothesis(BaseModel):
    """A proposed parameter change, motivated by one or more findings."""
    hypothesis_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    parent_run_id: str
    finding_ids:   list[str]
    description:   str
    param_delta:   dict[str, Any]           # {param_name: proposed_value}
    strategy:      Literal["targeted", "compound", "rollback", "explore"]
    kb_rule_id:    Optional[str] = None     # KB rule that generated this
    status:        Literal["pending", "tested", "validated", "rejected"] = "pending"
    tested_run_id: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Candidate
# ─────────────────────────────────────────────────────────────────────────────

class Candidate(BaseModel):
    """A parameter set that has passed all validation gates."""
    candidate_id:    str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    run_id:          str
    promoted_ts:     datetime = Field(default_factory=datetime.utcnow)
    composite_score: float
    oos_score:       Optional[float] = None
    params:          dict[str, Any]
    lineage:         list[str] = []         # run_ids from baseline to this candidate


# ─────────────────────────────────────────────────────────────────────────────
# RunResult   (returned by MT5Runner)
# ─────────────────────────────────────────────────────────────────────────────

class RunResult(BaseModel):
    """Raw file paths returned after a tester run completes."""
    run_id:         str
    report_xml:     Optional[str] = None    # path to MT5 XML report
    report_html:    Optional[str] = None    # path to MT5 HTML report
    trade_log_csv:  Optional[str] = None    # path to TradeLogger CSV
    success:        bool = True
    error_message:  Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# GateResult   (returned by ValidationGate)
# ─────────────────────────────────────────────────────────────────────────────

class GateResult(BaseModel):
    passed:  bool
    details: dict[str, bool | float | str] = {}
    reason:  Optional[str] = None
