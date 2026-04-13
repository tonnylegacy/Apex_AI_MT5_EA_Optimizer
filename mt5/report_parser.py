"""
mt5/report_parser.py
Parses the MT5 strategy tester HTML report (production format: pure HTML tables).
Extracts RunMetrics and paired in/out deal trades.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from lxml import html as lhtml
from loguru import logger

from data.models import RunMetrics, Trade


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    """Remove HTML entity remnants, spaces, currency symbols."""
    if not s:
        return ""
    # MT5 uses non-breaking spaces (0xa0) and regular spaces
    s = s.replace("\xa0", "").replace(",", "").replace(" ", "").strip()
    return s


def _parse_float(s: str) -> float:
    s = _clean(s)
    # Remove everything except digits, dot, minus
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _parse_int(s: str) -> int:
    s = _clean(s)
    s = re.sub(r"[^\d\-]", "", s.split("(")[0])
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _parse_dt(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _cell_text(td) -> str:
    """Get all inner text from an lxml element, stripping tags."""
    return "".join(td.itertext()).strip()


# ── Main Parser ───────────────────────────────────────────────────────────────

class ReportParser:
    """
    Parses the MT5 HTML strategy tester report.

    Report format (confirmed from live MT5 output):
     - Summary metrics: <td>Label:</td><td><b>Value</b></td> pairs
     - Deals table: Time | Deal | Symbol | Type | Direction | Volume |
                    Price | Order | Commission | Swap | Profit | Balance | Comment
       Direction='in'  → position open (entry deal)
       Direction='out' → position close (exit deal, has Profit value)
    """

    def parse(
        self, xml_path: Optional[str], html_path: Optional[str]
    ) -> tuple[Optional[RunMetrics], list[Trade]]:
        """
        Parse the MT5 HTML report. xml_path is ignored (MT5 command-line
        runs produce .htm, not .xml). Falls back gracefully if html is missing.
        """
        path = None
        if html_path and Path(html_path).exists():
            path = Path(html_path)
        elif xml_path and Path(xml_path).exists():
            path = Path(xml_path)

        if path is None:
            logger.error("No report file available to parse.")
            return None, []

        logger.debug(f"Parsing report: {path}")
        try:
            raw = path.read_bytes()
            # MT5 HTML reports are UTF-16 LE (BOM: ff fe) — detect and decode
            if raw[:2] == b'\xff\xfe':
                # Pass raw bytes; lxml's HTML parser handles UTF-16 correctly
                tree = lhtml.document_fromstring(raw)
            else:
                # Regular UTF-8 or latin-1
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    content = raw.decode("windows-1252", errors="replace")
                tree = lhtml.document_fromstring(content.encode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to parse HTML: {e}")
            return None, []

        summary = self._extract_summary(tree)
        deals   = self._extract_deals(tree)
        trades  = self._pair_deals(deals)

        if not summary:
            logger.warning("No summary data found in MT5 HTML report.")
            return None, trades

        metrics = self._build_metrics(summary)
        logger.info(f"Parsed: {metrics.total_trades} trades, PF={metrics.profit_factor:.3f}")
        return metrics, trades

    # ── Summary ───────────────────────────────────────────────────────────────

    def _extract_summary(self, tree) -> dict[str, str]:
        """
        Extract label→value pairs from the stats tables.
        MT5 pattern: <td ...>Label:</td> <td ...><b>Value</b></td>
        The label and value are adjacent siblings in the same <tr>.
        """
        result: dict[str, str] = {}
        for tr in tree.iter("tr"):
            tds = list(tr.findall(".//td"))
            if len(tds) < 2:
                continue
            for i in range(len(tds) - 1):
                label = _cell_text(tds[i]).rstrip(":")
                val   = _cell_text(tds[i + 1])
                if label and val and len(label) < 60:
                    result[label] = val
        logger.debug(f"Summary fields: {len(result)}")
        return result

    # ── Deals ─────────────────────────────────────────────────────────────────

    def _extract_deals(self, tree) -> list[dict]:
        """
        Find the Deals table and parse every row.
        Columns: Time|Deal|Symbol|Type|Direction|Volume|Price|Order|Commission|Swap|Profit|Balance|Comment
        """
        # Find the <th> that contains "Deals" text
        deals_header = None
        for th in tree.iter("th"):
            if "Deals" in (_cell_text(th) or ""):
                deals_header = th
                break

        if deals_header is None:
            logger.warning("Deals table not found in report.")
            return []

        # Walk up to find the table element
        table = deals_header
        while table is not None and table.tag != "table":
            table = table.getparent()
        if table is None:
            return []

        rows = table.findall(".//tr")
        # Skip header rows (first 2 rows: table title + column headers)
        data_rows = []
        header_seen = 0
        for row in rows:
            ths = row.findall(".//th")
            tds = row.findall(".//td")
            if ths:
                header_seen += 1
                continue
            if not tds:
                continue
            data_rows.append(tds)

        deals = []
        for tds in data_rows:
            texts = [_cell_text(td) for td in tds]
            if len(texts) < 13:
                continue
            # Cols: 0=Time 1=Deal 2=Symbol 3=Type 4=Direction 5=Volume
            #       6=Price 7=Order 8=Commission 9=Swap 10=Profit 11=Balance 12=Comment
            deal_type = texts[3].lower()
            if "balance" in deal_type or "credit" in deal_type:
                continue   # skip balance entries at start

            deals.append({
                "time":       texts[0],
                "deal":       _parse_int(texts[1]),
                "symbol":     texts[2],
                "type":       deal_type,          # buy / sell
                "direction":  texts[4].lower(),   # in / out
                "volume":     _parse_float(texts[5]),
                "price":      _parse_float(texts[6]),
                "order":      _parse_int(texts[7]),
                "commission": _parse_float(texts[8]),
                "swap":       _parse_float(texts[9]),
                "profit":     _parse_float(texts[10]),
                "balance":    _parse_float(texts[11]),
                "comment":    texts[12] if len(texts) > 12 else "",
            })

        logger.debug(f"Raw deals extracted: {len(deals)}")
        return deals

    # ── Pairing in→out ────────────────────────────────────────────────────────

    def _pair_deals(self, deals: list[dict]) -> list[Trade]:
        """
        Pair 'in' (open) and 'out' (close) deals to form complete trades.
        MT5 reports alternate: in-deal → out-deal for each closed position.
        """
        trades: list[Trade] = []
        pending: Optional[dict] = None   # the last 'in' deal

        for d in deals:
            if d["direction"] == "in":
                pending = d
            elif d["direction"] == "out" and pending is not None:
                open_dt  = _parse_dt(pending["time"])
                close_dt = _parse_dt(d["time"])
                if not open_dt or not close_dt:
                    pending = None
                    continue

                direction = pending["type"]   # buy / sell
                open_price  = pending["price"]
                close_price = d["price"]
                net_money   = d["profit"]
                lot_size    = d["volume"]
                commission  = d["commission"] + pending["commission"]
                swap        = d["swap"] + pending["swap"]
                duration_m  = max(0, int((close_dt - open_dt).total_seconds() / 60))

                # Net pips (XAUUSD: price moves in dollars, 1 pip = 0.1)
                price_diff = (close_price - open_price) * (1 if direction == "buy" else -1)
                net_pips   = round(price_diff / 0.1, 2) if price_diff != 0 else 0.0

                trades.append(Trade(
                    ticket       = d["deal"],
                    open_time    = open_dt,
                    close_time   = close_dt,
                    direction    = direction,
                    open_price   = open_price,
                    close_price  = close_price,
                    lot_size     = lot_size,
                    net_pips     = net_pips,
                    net_money    = net_money,
                    duration_minutes = duration_m,
                    commission   = commission,
                    swap         = swap,
                    sl           = 0.0,  # not in deals table
                    tp           = 0.0,
                ))
                pending = None
            # if direction is empty/"" skip it

        logger.info(f"Paired {len(trades)} complete trades from deals.")
        return trades

    # ── Metrics ───────────────────────────────────────────────────────────────

    def _build_metrics(self, raw: dict[str, str]) -> RunMetrics:
        """Build RunMetrics from the extracted label→value dictionary."""

        def get(*keys) -> str:
            for k in keys:
                v = raw.get(k, "")
                if v:
                    return v
            return "0"

        net_profit    = _parse_float(get("Total Net Profit", "Net Profit", "Balance"))
        gross_profit  = _parse_float(get("Gross Profit"))
        gross_loss    = _parse_float(get("Gross Loss"))
        profit_factor = _parse_float(get("Profit Factor"))
        # Total Deals = number of deal rows; Total Trades = positions
        total_trades  = _parse_int(get("Total Trades", "Total Deals"))
        win_trades    = _parse_int(get("Profit Trades", "Profit Trades (% of total)",
                                       "Profit Deals"))

        # Drawdown: "2 160.22 (19.25%)"
        dd_str       = get("Equity Drawdown Maximal", "Equity Drawdown Relative",
                           "Balance Drawdown Maximal")
        max_dd_abs   = _parse_float(dd_str.split("(")[0])
        pct_match    = re.search(r"([\d.]+)%", dd_str)
        max_dd_pct   = float(pct_match.group(1)) / 100 if pct_match else 0.0

        initial_dep  = _parse_float(get("Initial Deposit", "Deposit"))
        if initial_dep <= 0:
            initial_dep = 10_000.0

        sharpe          = _parse_float(get("Sharpe Ratio", "Sharp Ratio"))
        recovery_factor = _parse_float(get("Recovery Factor"))
        expected_payoff = _parse_float(get("Expected Payoff"))

        # Compute max_dd_pct if only absolute was found
        if max_dd_pct == 0.0 and max_dd_abs > 0:
            total_equity = initial_dep + net_profit
            max_dd_pct = max_dd_abs / max(1, total_equity)

        # Calmar = annualised return / max drawdown
        # Use simple ratio since we don't know exact test duration
        calmar = 0.0
        if max_dd_pct > 0:
            annual_return = net_profit / initial_dep
            calmar = round(annual_return / max_dd_pct, 4)

        win_rate   = win_trades / total_trades if total_trades > 0 else 0.0
        loss_trades = max(0, total_trades - win_trades)
        avg_win    = gross_profit / win_trades  if win_trades   > 0 else 0.0
        avg_loss   = gross_loss   / loss_trades if loss_trades  > 0 else 0.0

        return RunMetrics(
            run_id          = "__placeholder__",
            net_profit      = net_profit,
            profit_factor   = profit_factor,
            max_drawdown_abs= max_dd_abs,
            max_drawdown_pct= max_dd_pct,
            calmar_ratio    = calmar,
            sharpe_ratio    = sharpe,
            total_trades    = total_trades,
            win_rate        = win_rate,
            avg_win         = avg_win,
            avg_loss        = avg_loss,
            recovery_factor = recovery_factor,
            largest_loss    = _parse_float(get("Largest loss trade")),
            expected_payoff = expected_payoff,
        )
