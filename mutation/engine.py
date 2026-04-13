"""
mutation/engine.py
Translates analysis findings into concrete parameter hypotheses.
Uses knowledge_base.yaml rules as a structured ruleset.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np
import yaml
from loguru import logger

from data.models import Finding, Hypothesis


class MutationEngine:
    """
    Finding → Hypothesis translator.

    Workflow:
    1. Load knowledge_base.yaml rules
    2. For each finding, find matching rules
    3. Filter rules already tested recently (dedup)
    4. Resolve dynamic mutation values (percentile-based, derived)
    5. Build Hypothesis objects
    6. Return sorted by estimated PnL impact
    """

    def __init__(
        self,
        kb_path:         str | Path = "mutation/knowledge_base.yaml",
        manifest_path:   str | Path = "mutation/param_manifest.yaml",
        dedup_lookback:  int        = 10,
    ):
        with open(kb_path) as f:
            self.kb = yaml.safe_load(f)["rules"]
        with open(manifest_path) as f:
            self.manifest = yaml.safe_load(f)["parameters"]
        self.dedup_lookback = dedup_lookback

    # ── Public ────────────────────────────────────────────────────────────────

    def propose(
        self,
        findings:       list[Finding],
        current_params: dict[str, Any],
        recent_deltas:  list[dict],     # from store.get_recent_param_deltas()
        max_proposals:  int = 3,
    ) -> list[Hypothesis]:
        """
        Generate hypotheses from findings, de-duplicate, and return top-N.
        """
        hypotheses: list[Hypothesis] = []

        for finding in findings:
            for rule in self.kb:
                if not self._rule_matches(rule, finding, current_params):
                    continue

                param_delta = self._build_delta(rule, finding, current_params)
                if not param_delta:
                    continue

                # Skip if identical delta was recently tested
                if self._already_tested(param_delta, recent_deltas):
                    logger.debug(f"Skipping rule {rule['id']} — already tested.")
                    continue

                h = Hypothesis(
                    parent_run_id=finding.run_id,
                    finding_ids=[finding.finding_id],
                    description=f"[{rule['id']}] {rule['action_label']}",
                    param_delta=param_delta,
                    strategy=rule.get("strategy", "targeted"),
                    kb_rule_id=rule["id"],
                )
                hypotheses.append((h, finding.impact_estimate_pnl))

        # Sort by impact descending, deduplicate by KB rule
        seen_rules = set()
        ranked: list[Hypothesis] = []
        for h, impact in sorted(hypotheses, key=lambda x: x[1], reverse=True):
            if h.kb_rule_id not in seen_rules:
                seen_rules.add(h.kb_rule_id)
                ranked.append(h)
            if len(ranked) >= max_proposals:
                break

        logger.info(f"Proposed {len(ranked)} hypotheses from {len(findings)} findings.")
        return ranked

    # ── Rule matching ─────────────────────────────────────────────────────────

    def _rule_matches(
        self, rule: dict, finding: Finding, current_params: dict
    ) -> bool:
        """Check if a KB rule's trigger matches this finding and current params."""
        trigger = rule.get("trigger", {})

        # Analyzer match
        if trigger.get("analyzer") and trigger["analyzer"] != finding.analyzer:
            return False

        # Evaluate condition expression against finding evidence + current params
        condition = trigger.get("condition", "")
        if condition:
            env = {**finding.evidence, **current_params}
            # Simple boolean parsing for conditions like "reversal_rate > 0.15"
            try:
                if not self._eval_condition(condition, env):
                    return False
            except Exception as e:
                logger.debug(f"Rule {rule['id']} condition eval error: {e}")
                return False

        return True

    def _eval_condition(self, condition: str, env: dict) -> bool:
        """
        Evaluate a simple condition string.
        Supports: >, <, >=, <=, ==, AND, OR
        Variables are looked up in env dict.
        """
        # Replace variable names with their values
        tokens = condition.split()
        resolved_tokens = []
        for token in tokens:
            if token in ("AND", "OR", "and", "or", ">", "<", ">=", "<=", "==", "!="):
                resolved_tokens.append(token.lower())
            elif token in env:
                val = env[token]
                resolved_tokens.append(str(val) if not isinstance(val, str) else f'"{val}"')
            else:
                resolved_tokens.append(token)

        expr = " ".join(resolved_tokens)
        return bool(eval(expr, {"__builtins__": {}}))  # restricted eval

    # ── Delta building ────────────────────────────────────────────────────────

    def _build_delta(
        self,
        rule: dict,
        finding: Finding,
        current_params: dict,
    ) -> dict[str, Any]:
        """
        Translate a KB mutation spec into a concrete {param_name: new_value} dict.
        Handles: set, multiply, derive_from, set_to_percentile.
        """
        delta: dict[str, Any] = {}
        mutations = rule.get("mutations", {})

        for param_name, mutation_spec in mutations.items():
            current = current_params.get(param_name)
            spec    = self.manifest.get(param_name, {})
            ptype   = spec.get("type", "float")
            p_min   = spec.get("min")
            p_max   = spec.get("max")

            new_val = self._resolve_mutation(
                mutation_spec, current, ptype, p_min, p_max, finding
            )
            if new_val is not None:
                delta[param_name] = new_val

                # Auto-cascade: if enabling a bool, set defaults for depends_on params
                if ptype == "bool" and new_val is True:
                    delta.update(self._cascade_dependencies(param_name, current_params))

        return delta

    def _resolve_mutation(
        self,
        spec:     dict | Any,
        current:  Any,
        ptype:    str,
        p_min:    Optional[float],
        p_max:    Optional[float],
        finding:  Finding,
    ) -> Optional[Any]:
        """Resolve a single mutation spec into a concrete value."""
        if not isinstance(spec, dict):
            return spec   # bare value

        # set: directly set to a value
        if "set" in spec:
            return spec["set"]

        # multiply: multiply current value by factor
        if "multiply" in spec and current is not None:
            result = float(current) * spec["multiply"]
            if "clamp_min" in spec:
                result = max(spec["clamp_min"], result)
            if p_min is not None:
                result = max(p_min, result)
            if p_max is not None:
                result = min(p_max, result)
            return round(result, 2) if ptype == "float" else int(result)

        # set_to_percentile: use Nth percentile of a finding evidence list
        if "set_to_percentile" in spec:
            pct  = spec["set_to_percentile"] / 100.0
            data = finding.evidence.get("mfe_pips_distribution", [])
            if data:
                val = float(np.percentile(data, pct * 100))
                if "scale" in spec:
                    val *= spec["scale"]
                if p_min is not None:
                    val = max(p_min, val)
                if p_max is not None:
                    val = min(p_max, val)
                return round(val, 1)

        # derive_from: use evidence field
        if "derive_from" in spec:
            key = spec["derive_from"]
            val = finding.evidence.get(key)
            if val is not None:
                return val

        return None

    def _cascade_dependencies(
        self, bool_param: str, current_params: dict
    ) -> dict[str, Any]:
        """When a bool param is enabled, fill in sensible defaults for its dependents."""
        cascade = {}
        for name, spec in self.manifest.items():
            if spec.get("depends_on") != bool_param:
                continue
            # Only set if not already in current params or at a sub-optimal default
            if name not in current_params:
                cascade[name] = spec.get("default", 0)
        return cascade

    # ── Deduplication ─────────────────────────────────────────────────────────

    def _already_tested(self, delta: dict, recent_deltas: list[dict]) -> bool:
        """Check if an identical param delta was tested recently."""
        delta_str = json.dumps(delta, sort_keys=True)
        for past in recent_deltas:
            if json.dumps(past, sort_keys=True) == delta_str:
                return True
        return False
