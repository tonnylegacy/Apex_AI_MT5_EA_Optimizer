"""
mt5/ini_builder.py
Generates the MT5 strategy tester .ini file from a ParameterSchema + config.

Accepts either:
  - ParameterSchema object (new universal path)
  - manifest_path YAML string (legacy path, still supported for transition)
"""
from __future__ import annotations
import configparser
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import yaml
from loguru import logger

if TYPE_CHECKING:
    from ea.schema import ParameterSchema


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

    New usage (universal, recommended):
        builder = IniBuilder(config_path="config.yaml", schema=my_schema)

    Legacy usage (LEGSTECH transition):
        builder = IniBuilder(config_path="config.yaml", manifest_path="mutation/param_manifest.yaml")

    The EA identity (ex5_file, symbol, timeframe) comes from the EAProfile
    passed to build(), or falls back to config["ea"] for backwards compat.
    """

    def __init__(
        self,
        config_path: str | Path,
        manifest_path: Optional[str | Path] = None,
        schema: Optional["ParameterSchema"] = None,
    ):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self._schema = schema

        # Legacy manifest support
        self._manifest: dict = {}
        if manifest_path is not None and schema is None:
            with open(manifest_path) as f:
                self._manifest = yaml.safe_load(f)["parameters"]

    def set_schema(self, schema: "ParameterSchema") -> None:
        """Update the schema (useful when EA changes mid-session)."""
        self._schema = schema
        self._manifest = {}

    # ── Public ────────────────────────────────────────────────────────────────

    def build(
        self,
        run_id: str,
        params: dict[str, Any],
        period_start: str,
        period_end: str,
        output_dir: Path,
        phase: str = "explore",
        ea_file: Optional[str] = None,
        ea_symbol: Optional[str] = None,
        ea_timeframe: Optional[str] = None,
        optimize_mode: bool = False,
    ) -> Path:
        """
        Write <run_id>.ini to output_dir and return its path.

        ea_file, ea_symbol, ea_timeframe: override config["ea"] values.
          Pass these from EAProfile when using the universal path.

        optimize_mode=True: write Phase A optimization INI (Optimization=2).
        optimize_mode=False: write Phase B single backtest INI (Optimization=0).
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        ini_path   = output_dir / f"{run_id}.ini"
        report_dir = output_dir / "report"
        report_dir.mkdir(parents=True, exist_ok=True)

        full_params = self._merge_with_defaults(params)
        ini_text    = self._render(
            run_id, full_params, period_start, period_end,
            report_dir, phase,
            ea_file=ea_file, ea_symbol=ea_symbol, ea_timeframe=ea_timeframe,
            optimize_mode=optimize_mode,
        )

        ini_path.write_text(ini_text, encoding="utf-8")
        logger.debug(f"INI written: {ini_path}")
        return ini_path

    def default_params(self) -> dict[str, Any]:
        """Return all EA parameters at their default values."""
        if self._schema is not None:
            return self._schema.defaults()
        return self._merge_with_defaults({})

    # ── Internal ──────────────────────────────────────────────────────────────

    def _merge_with_defaults(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Merge caller-supplied overrides with defaults."""
        if self._schema is not None:
            return self._schema.with_overrides(overrides)
        # Legacy manifest path
        result: dict[str, Any] = {}
        manifest = self._manifest
        for name, spec in manifest.items():
            result[name] = overrides.get(name, spec.get("default", 0))
        return result

    def _format_value(self, name: str, value: Any) -> str:
        """Format a parameter value for the [TesterInputs] section."""
        # Schema path
        if self._schema is not None:
            p = self._schema.get(name)
            if p is not None:
                return self._schema._fmt(p, value)
            return str(value)
        # Legacy manifest path
        spec  = self._manifest.get(name, {})
        ptype = spec.get("type", "float")
        if ptype == "bool":
            return "true" if value else "false"
        if ptype == "fixed":
            return str(value)
        if ptype in ("int", "enum"):
            return str(int(value))
        if ptype == "float":
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
        ea_file: Optional[str] = None,
        ea_symbol: Optional[str] = None,
        ea_timeframe: Optional[str] = None,
        optimize_mode: bool = False,
    ) -> str:
        """Render final INI content as a string."""
        mt5_cfg    = self.cfg["mt5"]
        broker_cfg = self.cfg["broker"]

        # EA identity: prefer explicit args, fall back to config["ea"] for legacy
        ea_cfg     = self.cfg.get("ea", {})
        _ea_file   = ea_file      or ea_cfg.get("file",      "EA")
        _symbol    = ea_symbol    or ea_cfg.get("symbol",    "XAUUSD")
        _tf_str    = ea_timeframe or ea_cfg.get("timeframe", "H1")
        tf_name    = TIMEFRAME_NAMES.get(_tf_str.upper(), "H1")

        model_code   = mt5_cfg.get("tester_model", 4)
        report_name  = f"Optimizer_{run_id}"
        opt_value    = 2 if optimize_mode else 0   # 2=genetic, 0=single backtest

        lines = [
            f"; MT5 Optimizer INI — run_id={run_id} phase={phase}",
            f"",
            f"[Tester]",
            f"Expert={_ea_file}",
            f"Symbol={_symbol}",
            f"Period={tf_name}",
            f"Optimization={opt_value}",
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

        # Use schema's INI renderer if available
        if self._schema is not None:
            lines.append(self._schema.to_ini_inputs(params, optimize_mode=optimize_mode))
        else:
            for name, value in params.items():
                formatted = self._format_value(name, value)
                lines.append(f"{name}={formatted}")

        lines.append("")  # trailing newline
        return "\n".join(lines)
