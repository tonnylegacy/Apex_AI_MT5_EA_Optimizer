"""
mt5/log_reader.py
Reads the TradeLogger.mqh CSV and merges MAE/MFE data into parsed trades.
Also computes derived fields: session, day_of_week, result_class, quality scores.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from data.models import Trade


# Session definitions in UTC hours (inclusive start, exclusive end)
SESSIONS_UTC = {
    "Asian":    (0,  9),
    "London":   (7,  16),
    "LondonNY": (13, 16),
    "NY":       (13, 22),
}


def classify_session(hour_utc: int) -> str:
    """Classify a UTC hour into its primary trading session."""
    in_london = SESSIONS_UTC["London"][0]  <= hour_utc < SESSIONS_UTC["London"][1]
    in_ny     = SESSIONS_UTC["NY"][0]      <= hour_utc < SESSIONS_UTC["NY"][1]

    if in_london and in_ny:
        return "LondonNY"
    elif in_london:
        return "London"
    elif in_ny:
        return "NY"
    elif SESSIONS_UTC["Asian"][0] <= hour_utc < SESSIONS_UTC["Asian"][1]:
        return "Asian"
    else:
        return "Off"


# ── Main reader/merger ────────────────────────────────────────────────────────

class TradeLogReader:
    """
    Reads the CSV produced by TradeLogger.mqh and merges into a list of Trade objects.

    Strategy:
    1. Load CSV, index by ticket
    2. For each Trade, look up ticket in CSV
    3. Fill mfe_pips, mae_pips, duration_minutes if found
    4. Compute all derived fields for every trade (session, quality scores, etc.)
    """

    def __init__(self, broker_tz_offset_hours: int = 2, pip_size: float = 0.1):
        self.tz_offset = broker_tz_offset_hours   # broker local = UTC + offset
        self.pip_size  = pip_size                  # XAUUSD: 0.1 per pip

    # ── Public ────────────────────────────────────────────────────────────────

    def merge(
        self,
        trades: list[Trade],
        csv_path: Optional[str | Path],
        reversal_mfe_threshold_pips: float = 15.0,
    ) -> list[Trade]:
        """
        Merge TradeLogger CSV into trade list, compute all derived fields.
        If csv_path is None or unreadable, derived fields are computed without MFE/MAE.
        """
        log_df = self._load_csv(csv_path) if csv_path else None

        enriched = []
        for trade in trades:
            # Fill MAE/MFE from logger if available
            if log_df is not None and trade.ticket in log_df.index:
                row            = log_df.loc[trade.ticket]
                trade.mfe_pips = float(row.get("mfe_pips", 0) or 0)
                trade.mae_pips = float(row.get("mae_pips", 0) or 0)
                # Override duration with logger value (tick-accurate)
                if "duration_minutes" in row:
                    trade.duration_minutes = int(row["duration_minutes"] or trade.duration_minutes)

            # Compute all derived fields
            trade = self._enrich(trade, reversal_mfe_threshold_pips)
            enriched.append(trade)

        logger.info(
            f"Enriched {len(enriched)} trades. "
            f"MAE/MFE available: {sum(1 for t in enriched if t.mfe_pips is not None)}"
        )
        return enriched

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load_csv(self, csv_path: str | Path) -> Optional[pd.DataFrame]:
        path = Path(csv_path)
        if not path.exists():
            logger.warning(f"TradeLogger CSV not found: {path}")
            return None
        try:
            df = pd.read_csv(path, dtype={"ticket": int})
            if "ticket" not in df.columns:
                logger.error("TradeLogger CSV missing 'ticket' column.")
                return None
            df = df.set_index("ticket")
            logger.debug(f"Loaded {len(df)} rows from TradeLogger CSV.")
            return df
        except Exception as e:
            logger.error(f"Failed to read TradeLogger CSV: {e}")
            return None

    def _enrich(self, trade: Trade, threshold_pips: float) -> Trade:
        """Compute all derived classification and quality fields."""
        # --- Timezone normalisation ---
        # Broker timestamps are in broker local time (UTC+offset).
        # We compute UTC hour by subtracting the offset.
        broker_hour      = trade.open_time.hour
        hour_utc         = (broker_hour - self.tz_offset) % 24
        trade.hour_broker = broker_hour
        trade.hour_utc    = hour_utc
        trade.day_of_week = trade.open_time.weekday()   # 0=Mon, 4=Fri
        trade.session     = classify_session(hour_utc)

        # --- Result class ---
        won = trade.net_money > 0
        be  = abs(trade.net_money) < 0.01  # effectively breakeven

        if be:
            trade.result_class = "be"
        elif won:
            trade.result_class = "win"
        else:
            # Check if it's a reversal: lost, but had positive MFE above threshold
            if trade.mfe_pips is not None and trade.mfe_pips >= threshold_pips:
                trade.result_class = "reversal"
            else:
                trade.result_class = "loss"

        # --- Quality scores (only when MFE/MAE available) ---
        if trade.mfe_pips is not None and trade.mae_pips is not None:
            mfe = max(trade.mfe_pips, 0.01)   # prevent division by zero
            mae = max(trade.mae_pips, 0.0)

            # Entry quality: how far against you before move in your favour
            # High = entered well (little adverse move relative to favourable move)
            trade.entry_quality = max(0.0, min(1.0, 1.0 - (mae / (mfe + mae + 0.01))))

            # Exit quality: what fraction of MFE did we capture
            mfe_value = mfe * self.pip_size * trade.lot_size * 100  # approx value in $
            if mfe_value > 0:
                trade.mfe_capture_ratio = max(0.0, trade.net_money / mfe_value)
                trade.exit_quality      = max(0.0, min(1.0, trade.net_pips / mfe))
            else:
                trade.mfe_capture_ratio = 0.0
                trade.exit_quality      = 0.0

        return trade
