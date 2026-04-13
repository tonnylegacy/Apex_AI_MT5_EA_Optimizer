"""
optimizer/lhs_sampler.py
Latin Hypercube Sampling — generates diverse parameter sets that
span the FULL optimization range with minimum samples.

Why LHS instead of random:
  With 20 samples and 8 parameters:
  - Pure random: clusters near the center, misses extremes
  - LHS: divides each parameter into 20 equal bands, samples exactly
    one value per band — GUARANTEED coverage of the full space.

  Result: 20 LHS samples cover the space better than 200 random samples.
"""
from __future__ import annotations

import random
import math
from typing import Any

from loguru import logger

from ea.schema import ParameterSchema, ParameterDef


class LatinHypercubeSampler:
    """
    Generates n_samples diverse parameter dicts from a ParameterSchema.

    Usage:
        sampler = LatinHypercubeSampler(seed=42)
        samples = sampler.sample(schema, n_samples=20)
        # → list of 20 dicts, each covering different regions

    Each sample is a complete param dict (all params, optimizable + fixed).
    Fixed params always take their automation-safe default value.
    """

    def __init__(self, seed: int = None):
        self.rng = random.Random(seed)

    def sample(self, schema: ParameterSchema, n_samples: int) -> list[dict[str, Any]]:
        """
        Generate n_samples diverse parameter sets.
        Returns list of complete param dicts (ready for IniBuilder).
        """
        opts = schema.optimizable()
        if not opts:
            logger.warning("LHSampler: no optimizable params — returning n_samples copies of defaults")
            return [schema.defaults() for _ in range(n_samples)]

        logger.info(
            f"LHSampler: generating {n_samples} samples over "
            f"{len(opts)} optimizable params: {[p.name for p in opts]}"
        )

        # Build LHS columns: one per optimizable param
        # Each column is a shuffled list of n_samples values — each from a different band
        columns: dict[str, list[Any]] = {}
        for param in opts:
            columns[param.name] = self._lhs_column(param, n_samples)

        # Assemble rows: combine column values
        base = schema.defaults()
        samples = []
        for i in range(n_samples):
            row = dict(base)  # start with all defaults (includes fixed params)
            for param in opts:
                row[param.name] = columns[param.name][i]
            samples.append(row)

        return samples

    # ── Internal ─────────────────────────────────────────────────────────────

    def _lhs_column(self, param: ParameterDef, n: int) -> list[Any]:
        """
        Generate n values for one parameter using Latin Hypercube spacing.
        Each value comes from a different equally-sized band of the range.
        The list is shuffled so rows don't correlate across parameters.
        """
        if param.type == "bool":
            # Alternate True/False, shuffled
            vals = [True if i < n // 2 else False for i in range(n)]
            # Ensure roughly 50/50
            if n % 2 == 1:
                vals.append(self.rng.choice([True, False]))
            vals = vals[:n]
            self.rng.shuffle(vals)
            return vals

        if param.type == "enum":
            # Cycle through enum values with shuffled repetition
            enum_vals = param.enum_values
            vals = []
            while len(vals) < n:
                cycle = list(enum_vals)
                self.rng.shuffle(cycle)
                vals.extend(cycle)
            self.rng.shuffle(vals)
            return vals[:n]

        if param.type in ("int", "float"):
            return self._lhs_continuous(param, n)

        # fixed: return default repeated
        return [param.default] * n

    def _lhs_continuous(self, param: ParameterDef, n: int) -> list[Any]:
        """LHS for continuous (float) or discrete (int) parameters."""
        lo   = float(param.min)
        hi   = float(param.max)
        step = float(param.step) if param.step else (hi - lo) / n

        band_size = (hi - lo) / n

        values = []
        for i in range(n):
            band_lo  = lo + i * band_size
            band_hi  = band_lo + band_size

            # Random point within the band
            raw = self.rng.uniform(band_lo, band_hi)

            # Snap to valid step grid
            if step > 0:
                steps_from_lo = round((raw - lo) / step)
                snapped = lo + steps_from_lo * step
                # Clamp to [lo, hi]
                snapped = max(lo, min(hi, snapped))
            else:
                snapped = raw

            values.append(param.clamp(snapped))

        # Shuffle so the ordering isn't correlated with other params
        self.rng.shuffle(values)
        return values

    def sample_neighbors(
        self,
        base_params: dict[str, Any],
        schema: ParameterSchema,
        n_neighbors: int,
        step_pct: float = 0.20,
    ) -> list[dict[str, Any]]:
        """
        Generate n_neighbors variants of base_params by nudging
        the most impactful optimizable params by ±step_pct of their range.

        Used in Phase 2 refinement.

        step_pct=0.20 means ±20% of (max - min). Much larger
        than the old ±0.5 step — actually explores the space.
        """
        opts = schema.optimizable()
        if not opts:
            return [dict(base_params)]

        neighbors = []
        for _ in range(n_neighbors):
            candidate = dict(base_params)

            # Perturb 2–4 random optimizable params
            n_perturb = min(len(opts), self.rng.randint(2, 4))
            to_perturb = self.rng.sample(opts, n_perturb)

            for param in to_perturb:
                current = candidate[param.name]
                if param.type == "bool":
                    # 50% chance to flip
                    if self.rng.random() < 0.5:
                        candidate[param.name] = not current
                    continue
                if param.type == "enum":
                    candidate[param.name] = self.rng.choice(param.enum_values)
                    continue

                # float / int: nudge by ±step_pct of range
                span  = float(param.max) - float(param.min)
                delta = span * step_pct * self.rng.choice([-1, 1])
                raw   = float(current) + delta
                candidate[param.name] = param.clamp(raw)

            neighbors.append(candidate)

        return neighbors
