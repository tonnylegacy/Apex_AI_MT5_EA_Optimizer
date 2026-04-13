"""
mt5/ini_builder.py
Generates the MT5 strategy tester .ini file from a parameter dict + config.
"""
from __future__ import annotations
import configparser
from pathlib import Path
from typing import Any

import yaml
from loguru import logger


# MT5 period string names (used in [Tester] Period= field)
TIMEFRAME_NAMES = {
    "M1": "M1",  "M5": "M5",  "M15": "M15", "M30": "M30",
    "H1": "H1",  "H4": "H4",  "D1":  "D1",  "W1":  "W1",  "MN": "MN1",
}

# MT5 tester model codes
MODEL_CODES = {
    "every_tick":      0,
    "every_tick_real": 1,
    "ohlc_m1":         4,
}


class IniBuilder:
    """
    Builds MT5 strategy tester .ini files.

    Usage:
        builder = IniBuilder(config_path="config.yaml", manifest_path="mutation/param_manifest.yaml")
        ini_path = builder.build(
            run_id="run_001",
            params={"InpRiskPercent": 1.5, "InpUseTrailing": True, ...},
            period_start="2022.01.01",
            period_end="2023.12.31",
            output_dir=Path("runs/run_001"),
        )
    """

    def __init__(self, config_path: str | Path, manifest_path: str | Path):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        with open(manifest_path) as f:
            self.manifest = yaml.safe_load(f)["parameters"]

    # ── Public ────────────────────────────────────────────────────────────────

    def build(
        self,
        run_id: str,
        params: dict[str, Any],
        period_start: str,
        period_end: str,
        output_dir: Path,
        phase: str = "explore",
    ) -> Path:
        """
        Write <run_id>.ini to output_dir and return its path.
        params: dict of EA input values (partial OK — missing params use manifest defaults)
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        ini_path   = output_dir / f"{run_id}.ini"
        report_dir = output_dir / "report"
        report_dir.mkdir(parents=True, exist_ok=True)

        full_params = self._merge_with_defaults(params)
        ini_text    = self._render(run_id, full_params, period_start, period_end,
                                   report_dir, phase)

        ini_path.write_text(ini_text, encoding="utf-8")
        logger.debug(f"INI written: {ini_path}")
        return ini_path

    def default_params(self) -> dict[str, Any]:
        """Return all EA parameters at their default values."""
        return self._merge_with_defaults({})

    # ── Internal ──────────────────────────────────────────────────────────────

    def _merge_with_defaults(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Merge caller-supplied overrides with manifest defaults."""
        result: dict[str, Any] = {}
        for name, spec in self.manifest.items():
            if name in overrides:
                result[name] = overrides[name]
            else:
                result[name] = spec.get("default", 0)
        return result

    def _format_value(self, name: str, value: Any) -> str:
        """Format a parameter value for the [TesterInputs] section."""
        spec = self.manifest.get(name, {})
        ptype = spec.get("type", "float")

        if ptype == "bool":
            return "true" if value else "false"
        if ptype == "fixed":
            # Fixed params: write exact default
            return str(value)
        if ptype == "int" or ptype == "enum":
            return str(int(value))
        if ptype == "float":
            # Determine decimal places from step
            step = spec.get("step", 0.1)
            decimals = len(str(step).split(".")[-1]) if "." in str(step) else 0
            return f"{float(value):.{decimals}f}"
        return str(value)

    def _render(
        self,
        run_id: str,
        params: dict[str, Any],
        period_start: str,
        period_end: str,
        report_dir: Path,
        phase: str,
    ) -> str:
        """Render final INI content as a string."""
        ea_cfg     = self.cfg["ea"]
        mt5_cfg    = self.cfg["mt5"]
        broker_cfg = self.cfg["broker"]

        # Period must be the string name (H1, M30 etc) — NOT the ENUM integer
        tf_name    = TIMEFRAME_NAMES.get(ea_cfg["timeframe"].upper(), "H1")

        # Model: 0=Every Tick (slow), 4=OHLC M1 (fast, reliable for ini-based launch)
        model_code = mt5_cfg.get("tester_model", 4)

        # Report path must be RELATIVE to the MT5 terminal data folder
        # MT5 appends its own base path. Use run_id as the report name.
        report_name = f"Optimizer_{run_id}"

        lines = [
            f"; MT5 Optimizer INI — run_id={run_id} phase={phase}",
            f"",
            f"[Tester]",
            f"Expert={ea_cfg['file']}",
            f"Symbol={ea_cfg['symbol']}",
            f"Period={tf_name}",
            f"Optimization=0",
            f"Model={model_code}",
            f"FromDate={period_start}",
            f"ToDate={period_end}",
            f"ForwardMode=0",
            f"Report={report_name}",
            f"ReplaceReport=1",
            f"ShutdownTerminal={mt5_cfg.get('shutdown_terminal', 1)}",
            f"Deposit={broker_cfg['deposit']}",
            f"Currency={broker_cfg['currency']}",
            f"Leverage={broker_cfg['leverage']}",
            f"",
            f"[TesterInputs]",
        ]

        for name, value in params.items():
            formatted = self._format_value(name, value)
            lines.append(f"{name}={formatted}")

        lines.append("")  # trailing newline
        return "\n".join(lines)
