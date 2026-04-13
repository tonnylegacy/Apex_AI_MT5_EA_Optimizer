"""
ea/schema.py
ParameterDef and ParameterSchema — the universal parameter representation.
Replaces mutation/param_manifest.yaml entirely.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ── Parameter types ───────────────────────────────────────────────────────────

PARAM_TYPES = {"float", "int", "bool", "enum", "fixed"}


@dataclass
class ParameterDef:
    """
    One EA input parameter, as parsed from a .set file.

    type:
        float  — continuous, has decimal step
        int    — discrete integer range
        bool   — true/false toggle (min=0, max=1, step=1)
        enum   — small discrete set of integer values
        fixed  — never changed during optimization (min==max, or zeroed range)
    """
    name:        str
    default:     Any                        # Value at default (typed: float/int/bool)
    type:        str                        # float | int | bool | enum | fixed
    min:         Optional[float] = None
    max:         Optional[float] = None
    step:        Optional[float] = None
    optimize:    bool = False               # User has selected this for optimization
    enum_values: list = field(default_factory=list)  # Populated for type==enum

    def __post_init__(self):
        assert self.type in PARAM_TYPES, f"Unknown param type '{self.type}' for {self.name}"
        if self.type == "enum" and not self.enum_values and self.min is not None:
            # Auto-populate enum values from range
            v = self.min
            while v <= self.max + 1e-9:
                self.enum_values.append(int(round(v)))
                v += self.step

    @property
    def range_label(self) -> str:
        """Human-readable range string, e.g. '0.5 – 3.0 (step 0.5)'."""
        if self.type == "fixed":
            return f"{self.default} (fixed)"
        if self.type == "bool":
            return "true / false"
        if self.type == "enum":
            return " | ".join(str(v) for v in self.enum_values)
        return f"{self.min} – {self.max} (step {self.step})"

    def clamp(self, value: float) -> Any:
        """Clamp a proposed value to valid range, return correctly typed value."""
        if self.type == "fixed":
            return self.default
        if self.type == "bool":
            return bool(round(value))
        if self.min is not None:
            value = max(float(self.min), min(float(self.max), float(value)))
        if self.type == "int":
            return int(round(value))
        if self.type == "enum":
            # Snap to nearest enum value
            return min(self.enum_values, key=lambda v: abs(v - value))
        # float — round to same decimal places as step
        if self.step and self.step > 0:
            decimals = len(str(self.step).rstrip("0").split(".")[-1]) if "." in str(self.step) else 0
            return round(value, decimals)
        return value

    def step_up(self, current: Any) -> Optional[Any]:
        """Return value one step above current, or None if already at max."""
        if self.type in ("fixed", "bool"):
            return None
        if self.type == "enum":
            idx = self.enum_values.index(int(current)) if int(current) in self.enum_values else -1
            return self.enum_values[idx + 1] if idx < len(self.enum_values) - 1 else None
        nxt = float(current) + float(self.step)
        return self.clamp(nxt) if nxt <= self.max + 1e-9 else None

    def step_down(self, current: Any) -> Optional[Any]:
        """Return value one step below current, or None if already at min."""
        if self.type in ("fixed", "bool"):
            return None
        if self.type == "enum":
            idx = self.enum_values.index(int(current)) if int(current) in self.enum_values else -1
            return self.enum_values[idx - 1] if idx > 0 else None
        nxt = float(current) - float(self.step)
        return self.clamp(nxt) if nxt >= self.min - 1e-9 else None


# ── ParameterSchema ───────────────────────────────────────────────────────────

class ParameterSchema:
    """
    Full parameter schema for one EA, derived from its .set file.
    Replaces mutation/param_manifest.yaml.
    """

    def __init__(self, ea_name: str, source_set: Path, parameters: dict[str, ParameterDef]):
        self.ea_name    = ea_name
        self.source_set = source_set
        self.parameters = parameters   # ordered dict: name → ParameterDef

    # ── Accessors ─────────────────────────────────────────────────────────────

    def optimizable(self) -> list[ParameterDef]:
        """Parameters the user has selected to optimize."""
        return [p for p in self.parameters.values() if p.optimize]

    def fixed(self) -> list[ParameterDef]:
        """Parameters that never change."""
        return [p for p in self.parameters.values() if not p.optimize]

    def all_params(self) -> list[ParameterDef]:
        return list(self.parameters.values())

    def get(self, name: str) -> Optional[ParameterDef]:
        return self.parameters.get(name)

    def __len__(self) -> int:
        return len(self.parameters)

    # ── Baseline / override helpers ───────────────────────────────────────────

    def defaults(self) -> dict[str, Any]:
        """All parameters at their default values."""
        return {name: p.default for name, p in self.parameters.items()}

    def with_overrides(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """
        Merge override values with defaults.
        Overrides only apply to known parameters; unknown keys are dropped.
        Values are clamped to valid range.
        """
        result = self.defaults()
        for name, value in overrides.items():
            if name in self.parameters:
                result[name] = self.parameters[name].clamp(value)
        return result

    # ── INI rendering ─────────────────────────────────────────────────────────

    def to_ini_inputs(self, params: dict[str, Any], optimize_mode: bool = False) -> str:
        """
        Render [TesterInputs] block for an MT5 .ini file.

        optimize_mode=False → plain values  (Phase B single backtest)
        optimize_mode=True  → value|min|max|step ranges (Phase A genetic search)

        Bool optimization ranges use 1/0 (not true/false) per MT5 spec.
        """
        lines = []
        full = self.with_overrides(params)

        for name, p in self.parameters.items():
            value = full.get(name, p.default)

            if optimize_mode and p.optimize and p.type != "fixed":
                # Write optimization range — booleans use 1/0 in range format
                if p.type == "bool":
                    v = "1" if value else "0"
                    lines.append(f"{name}={v}|0|1|1")
                else:
                    formatted = self._fmt(p, value)
                    lines.append(
                        f"{name}={formatted}|{self._fmt(p, p.min)}|{self._fmt(p, p.max)}|{self._fmt(p, p.step)}"
                    )
            else:
                lines.append(f"{name}={self._fmt(p, value)}")

        return "\n".join(lines)

    def to_set_file(self, params: dict[str, Any], header_comment: str = "") -> str:
        """
        Render a clean output .set file (no optimization ranges).
        This is what gets downloaded by the user.
        """
        lines = []
        if header_comment:
            for line in header_comment.strip().splitlines():
                lines.append(f"; {line}")
            lines.append("")

        full = self.with_overrides(params)
        for name, p in self.parameters.items():
            value = full.get(name, p.default)
            lines.append(f"{name}={self._fmt(p, value)}")

        return "\n".join(lines)

    # ── Internal formatting ───────────────────────────────────────────────────

    @staticmethod
    def _fmt(p: ParameterDef, value: Any) -> str:
        """Format a value according to parameter type."""
        if value is None:
            return str(p.default)
        if p.type == "bool":
            # Accept int (0/1) or bool
            if isinstance(value, str):
                return value.lower()
            return "true" if value else "false"
        if p.type in ("int", "enum"):
            return str(int(round(float(value))))
        if p.type == "float":
            step = p.step or 0.1
            decimals = 0
            if "." in str(step):
                decimals = len(str(step).rstrip("0").split(".")[-1])
            return f"{float(value):.{decimals}f}"
        # fixed or unknown
        return str(value)

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> str:
        opt = self.optimizable()
        return (
            f"ParameterSchema({self.ea_name}): "
            f"{len(self.parameters)} params total, "
            f"{len(opt)} optimizable"
        )
