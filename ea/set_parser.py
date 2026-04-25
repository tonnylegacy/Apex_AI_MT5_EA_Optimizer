"""
ea/set_parser.py
Parse any MT5 .set file into a ParameterSchema.

Handles both .set formats:
  value|min|max|step          (single pipe  — most common)
  value||min||max||step||Y/N  (double pipe  — some MT5 builds)

Fixed detection:
  min == max                  → type="fixed" (e.g. InpMagicNumber=202402|202402|202402|1)
  min == 0 AND max == 0       → type="fixed" (zeroed range = "don't optimize")
  No range at all             → type="fixed"

Type detection (non-fixed only):
  min==0, max==1, step==1     → bool
  "." in step string          → float
  max - min <= 8, step==1     → enum  (small discrete integer set)
  otherwise                   → int
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from ea.schema import ParameterDef, ParameterSchema


# ── Tester section keys to skip (not EA inputs) ───────────────────────────────

_TESTER_KEYS = {
    "expert", "symbol", "period", "optimization", "model",
    "fromdate", "todate", "forwardmode", "report", "replacereport",
    "shutdownterminal", "deposit", "currency", "leverage",
    "optimizationmode", "forwarddate", "optimizationiterations",
}

# EA params that should always be fixed even if they have a range
# Includes magic numbers, debug/UI flags, and MT5 timeframe enum constants
# (HTF/MTF/LTF values like 16388 are PERIOD_H1/etc — not optimizable scalars).
_FORCE_FIXED_PATTERNS = [
    "testermode", "testeri", "testerinit", "showpanel",
    "magicnumber", "magic",
    "htf", "mtf", "ltf",        # higher/medium/lower timeframe enums
    "_tf", "timeframe",
    "comment", "label", "prefix",
    "verbose", "debug", "log",
]


class SetParser:
    """
    Parses a MT5 .set file into a ParameterSchema.

    Usage:
        parser = SetParser()
        schema = parser.parse(
            path=Path("C:/MT5 Set files/LEGSTECH_EA_V2.set"),
            ea_name="LEGSTECH_EA_V2",
            default_optimize=False,  # user chooses via UI
        )
    """

    def parse(
        self,
        path: Path,
        ea_name: str,
        default_optimize: bool = False,
        force_optimize: Optional[set[str]] = None,
        force_fixed:    Optional[set[str]] = None,
        auto_infer_ranges: bool = True,
    ) -> ParameterSchema:
        """
        Parse a .set file and return a ParameterSchema.

        Args:
            path:             Path to the .set file.
            ea_name:          Display name for the EA.
            default_optimize: Whether to mark all optimizable params as optimize=True by default.
                              If False (default), the user selects via UI.
            force_optimize:   Set of param names that are always optimize=True regardless.
            force_fixed:      Set of param names that are always type="fixed".
        """
        force_optimize = force_optimize or set()
        force_fixed    = force_fixed    or set()

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f".set file not found: {path}")

        try:
            text = path.read_text(encoding="utf-16")
        except UnicodeError:
            text = path.read_text(encoding="utf-8", errors="replace")

        parameters: dict[str, ParameterDef] = {}
        current_section = ""

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(";"):
                continue

            # Section header
            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].lower()
                continue

            # Skip lines without "="
            if "=" not in line:
                continue

            name, _, rest = line.partition("=")
            name = name.strip()
            rest = rest.strip()

            # Skip tester-section metadata keys
            if name.lower() in _TESTER_KEYS:
                continue

            # Parse the value + optional range
            param = self._parse_param(name, rest)
            if param is None:
                logger.debug(f"SetParser: skipped unrecognised line: {line!r}")
                continue

            # If the file had no optimization metadata (just `Name=value`),
            # heuristically infer a reasonable range so the user can still
            # optimize. Triggered for raw MT5 "saved settings" .set files.
            if auto_infer_ranges and param.type == "fixed" and "|" not in rest:
                inferred = self._infer_range_from_value(name, rest)
                if inferred is not None:
                    param = inferred

            # Apply force-fixed overrides
            if name in force_fixed or self._is_force_fixed(name):
                param.type     = "fixed"
                param.optimize = False
            elif name in force_optimize:
                param.optimize = True
            elif default_optimize and param.type != "fixed":
                param.optimize = True

            parameters[name] = param

        if not parameters:
            raise ValueError(f"No EA input parameters found in .set file: {path}")

        schema = ParameterSchema(ea_name=ea_name, source_set=path, parameters=parameters)
        logger.info(f"SetParser: parsed {schema.summary()} from {path.name}")
        return schema

    # ── Internal ─────────────────────────────────────────────────────────────

    def _parse_param(self, name: str, rest: str) -> Optional[ParameterDef]:
        """
        Parse a single parameter line.
        rest is everything after the first "=" on the line.
        """
        # Normalise: double-pipe "||" → single "|"
        rest = re.sub(r"\|\|", "|", rest)

        # Strip trailing Y/N optimize flag if present
        yn_match = re.search(r"\|([YN])$", rest, re.IGNORECASE)
        if yn_match:
            rest = rest[:yn_match.start()]

        parts = [p.strip() for p in rest.split("|")]

        if len(parts) == 1:
            # No range info → fixed
            value = self._cast_value(parts[0])
            return ParameterDef(
                name=name, default=value, type="fixed",
                min=None, max=None, step=None, optimize=False,
            )

        if len(parts) < 4:
            # Incomplete range — treat as fixed  
            value = self._cast_value(parts[0])
            return ParameterDef(
                name=name, default=value, type="fixed",
                min=None, max=None, step=None, optimize=False,
            )

        raw_val, raw_min, raw_max, raw_step = parts[0], parts[1], parts[2], parts[3]

        try:
            default_f = float(raw_val)
            min_f     = float(raw_min)
            max_f     = float(raw_max)
            step_f    = float(raw_step)
        except ValueError:
            value = self._cast_value(raw_val)
            return ParameterDef(
                name=name, default=value, type="fixed",
                min=None, max=None, step=None, optimize=False,
            )

        # Fixed detection
        is_fixed = (
            abs(min_f - max_f) < 1e-9          # min == max
            or (abs(min_f) < 1e-9 and abs(max_f) < 1e-9)  # both zero (zeroed range)
        )
        if is_fixed:
            return ParameterDef(
                name=name,
                default=self._typed_default(raw_val, raw_step),
                type="fixed",
                min=min_f, max=max_f, step=step_f,
                optimize=False,
            )

        # Type detection
        ptype = self._detect_type(min_f, max_f, step_f, raw_step, raw_val)
        default = self._typed_cast(ptype, default_f, raw_val)

        return ParameterDef(
            name=name,
            default=default,
            type=ptype,
            min=min_f,
            max=max_f,
            step=step_f,
            optimize=False,  # user sets this via UI; can be overridden by caller
        )

    @staticmethod
    def _detect_type(min_f: float, max_f: float, step_f: float,
                     raw_step: str, raw_val: str) -> str:
        """Infer parameter type from its range."""
        # Bool: exactly 0–1 with step 1
        if abs(min_f) < 1e-9 and abs(max_f - 1.0) < 1e-9 and abs(step_f - 1.0) < 1e-9:
            return "bool"

        # Float: step has decimal component
        if "." in raw_step and not raw_step.endswith(".0") and float(raw_step) % 1 != 0:
            return "float"
        # Also float if default value has meaningful decimal
        if "." in raw_val and float(raw_val) % 1 != 0:
            return "float"

        # Enum: small integer set (≤ 8 distinct values, step 1)
        n_values = int(round((max_f - min_f) / step_f)) + 1 if step_f > 0 else 1
        if abs(step_f - 1.0) < 1e-9 and n_values <= 8:
            return "enum"

        return "int"

    @staticmethod
    def _typed_cast(ptype: str, value_f: float, raw: str) -> Any:
        if ptype == "bool":
            return value_f != 0 or raw.lower() in ("true", "1")
        if ptype == "int":
            return int(round(value_f))
        if ptype == "enum":
            return int(round(value_f))
        return value_f  # float

    @staticmethod
    def _typed_default(raw: str, raw_step: str) -> Any:
        """Cast a fixed-param value without range context."""
        lower = raw.lower()
        if lower in ("true", "false"):
            return lower == "true"
        try:
            f = float(raw)
            # Return int if it's a whole number and step is integer-like
            if "." not in raw_step or raw_step.endswith(".0"):
                if f == int(f):
                    return int(f)
            return f
        except ValueError:
            return raw

    @staticmethod
    def _cast_value(raw: str) -> Any:
        lower = raw.lower()
        if lower in ("true", "false"):
            return lower == "true"
        try:
            f = float(raw)
            return int(f) if f == int(f) and "." not in raw else f
        except ValueError:
            return raw

    @staticmethod
    def _is_force_fixed(name: str) -> bool:
        """Return True for params that are always fixed regardless of their range."""
        lower = name.lower()
        return any(pat in lower for pat in _FORCE_FIXED_PATTERNS)

    # ── Range inference for "saved settings" .set files ──────────────────────

    def _infer_range_from_value(self, name: str, raw: str) -> Optional[ParameterDef]:
        """
        Heuristic range inference for .set files that contain bare `Name=value`
        lines (no `|min|max|step` metadata). Common with files saved out of MT5
        directly rather than exported as an optimization preset.

        Returns None to leave the param as fixed (e.g. magic numbers, strings).
        """
        lower = name.lower()

        # Force-fixed by name pattern → keep fixed
        if self._is_force_fixed(name):
            return None

        # Bool: explicit true/false, or value 0/1 + name implies a toggle
        if raw.lower() in ("true", "false"):
            default_b = raw.lower() == "true"
            return ParameterDef(
                name=name, type="bool", default=default_b,
                min=0.0, max=1.0, step=1.0, optimize=False,
            )

        try:
            v = float(raw)
        except ValueError:
            return None  # non-numeric (e.g. enum string) → leave fixed

        is_int_value = ("." not in raw) and v == int(v)
        toggle_names = ("use", "enable", "allow", "show", "is_", "include", "with")
        looks_toggle = any(lower.startswith(p) for p in toggle_names) and v in (0, 1)
        if looks_toggle:
            return ParameterDef(
                name=name, type="bool", default=bool(int(v)),
                min=0.0, max=1.0, step=1.0, optimize=False,
            )

        if v == 0:
            # Can't ±% a zero meaningfully — give a small fixed nudge
            if is_int_value:
                return ParameterDef(name=name, type="int", default=0,
                                    min=0.0, max=10.0, step=1.0, optimize=False)
            return ParameterDef(name=name, type="float", default=0.0,
                                min=0.0, max=1.0, step=0.05, optimize=False)

        # Pattern-based ranges by name suffix / keyword
        # (lower-bound, upper-bound multipliers, step, force_int)
        patterns = [
            ("pips",        0.5, 2.0,  None,  True),
            ("period",      0.5, 2.0,  1.0,   True),
            ("lookback",    0.5, 2.0,  1.0,   True),
            ("multiplier",  0.5, 2.0,  None,  False),
            ("ratio",       0.5, 2.0,  None,  False),
            ("rrratio",     0.5, 2.5,  0.25,  False),
            ("percent",     0.5, 2.0,  None,  False),
            ("pct",         0.5, 2.0,  None,  False),
            ("risk",        0.5, 2.0,  None,  False),
            ("buffer",      0.5, 2.0,  None,  False),
            ("threshold",   0.5, 2.0,  None,  False),
            ("score",       0.5, 1.5,  0.05,  False),
            ("size",        0.5, 2.0,  None,  False),
            ("lot",         0.5, 2.0,  0.01,  False),
            ("spread",      0.5, 2.0,  1.0,   True),
            ("trades",      0.5, 2.5,  1.0,   True),
            ("hour",        0.0, 23.0, 1.0,   True),
            ("session",     0.0, 23.0, 1.0,   True),
        ]
        match = None
        for kw, *_ in patterns:
            if kw in lower:
                match = next(p for p in patterns if p[0] == kw)
                break

        if match:
            _, lo_mul, hi_mul, step_override, force_int = match
            lo = abs(v) * lo_mul
            hi = abs(v) * hi_mul
            if v < 0:
                lo, hi = -hi, -lo
            # Special-case hour ranges (absolute, not %-of-value)
            if match[0] in ("hour", "session"):
                lo, hi = 0.0, 23.0
        else:
            # Generic fallback: ±50% of value
            lo = abs(v) * 0.5
            hi = abs(v) * 2.0
            if v < 0:
                lo, hi = -hi, -lo
            step_override = None
            force_int = is_int_value

        # Choose step
        if step_override is not None:
            step = step_override
        elif force_int:
            span = hi - lo
            step = max(1.0, round(span / 10.0))
        else:
            span = hi - lo
            step = round(span / 10.0, 4)
            if step <= 0:
                step = 0.01

        ptype = "int" if force_int else "float"
        default = int(round(v)) if force_int else v

        return ParameterDef(
            name=name, type=ptype, default=default,
            min=round(lo, 4), max=round(hi, 4), step=step,
            optimize=False,
        )
