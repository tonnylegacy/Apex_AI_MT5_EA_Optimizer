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
_FORCE_FIXED_PATTERNS = [
    "testermode", "testeri", "testerinit", "showpanel",
    "magicnumber", "magic",
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
