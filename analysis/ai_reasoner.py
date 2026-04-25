"""
analysis/ai_reasoner.py
AI Reasoning Layer — uses Claude API to interpret backtest results,
identify patterns across runs, and suggest intelligent parameter changes.

Two modes:
  1. analyze()              → AIInsight  (per-run diagnostic, display-only)
  2. suggest_next_params()  → AIParamSuggestion  (autonomous loop — drives next test)

Usage:
    reasoner = AIReasoner(api_key="sk-ant-...")
    insight  = reasoner.analyze(findings, metrics, run_history)
    suggest  = reasoner.suggest_next_params(current_params, schema_info, history, targets)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import requests
from loguru import logger

from data.models import Finding, RunMetrics


# ── Output models ─────────────────────────────────────────────────────────────

@dataclass
class AIInsight:
    """Structured AI reasoning output for one optimization run."""
    headline:     str                   # One-sentence diagnosis
    diagnosis:    str                   # 2-3 sentence explanation of WHY
    patterns:     list[str]             # Key patterns in plain English
    suggestions:  list[dict]            # [{param, from, to, reason}]
    confidence:   str                   # "high" | "medium" | "low"
    risk_flags:   list[str]             # Warnings (overfitting risk, data issues etc)
    run_id:       str = ""
    error:        Optional[str] = None  # Set if API call failed

    def to_dict(self) -> dict:
        return {
            "headline":    self.headline,
            "diagnosis":   self.diagnosis,
            "patterns":    self.patterns,
            "suggestions": self.suggestions,
            "confidence":  self.confidence,
            "risk_flags":  self.risk_flags,
            "run_id":      self.run_id,
            "error":       self.error,
        }


@dataclass
class AIParamSuggestion:
    """
    Output of suggest_next_params() — drives the autonomous optimization loop.
    Contains concrete parameter values for the next backtest.
    """
    analysis:    str          # What the AI observed and why it's making these changes
    changes:     list[dict]   # [{param, value, reason}] — specific values, not deltas
    confidence:  float        # 0.0–1.0
    goal_status: dict         # {profit_factor_met, drawdown_ok, calmar_met}
    error:       Optional[str] = None


# ── Reasoner ──────────────────────────────────────────────────────────────────

class AIReasoner:
    """
    Calls Claude API to reason about backtest results.
    Falls back gracefully if API key is missing or call fails.
    """

    MODEL   = "claude-opus-4-7"   # default — overridden by config.ai.model when present
    API_URL = "https://api.anthropic.com/v1/messages"
    TIMEOUT = 30  # seconds

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        if model:
            self.MODEL = model
        # If the caller passes a placeholder like "${ANTHROPIC_API_KEY}" or an
        # empty string, treat it as missing and fall back to the env var.
        candidate = (api_key or "").strip()
        if not candidate or candidate.startswith("${") or candidate in ("YOUR_API_KEY", "sk-ant-..."):
            candidate = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self.api_key = candidate
        self.enabled = bool(self.api_key)
        if not self.enabled:
            logger.warning(
                "AIReasoner: No API key found. Set ANTHROPIC_API_KEY in your environment "
                "or in config.yaml under ai.anthropic_api_key. AI insights will be skipped."
            )

    # ── Public ────────────────────────────────────────────────────────────────

    def analyze(
        self,
        findings:    list[Finding],
        metrics:     RunMetrics,
        run_history: list[dict],   # list of {run_id, score, calmar, pf, params, phase}
        current_params: dict = {},
    ) -> AIInsight:
        """
        Main entry point. Returns an AIInsight.
        Never raises — always returns something useful.
        """
        if not self.enabled:
            return self._fallback_insight(findings, metrics)

        prompt = self._build_prompt(findings, metrics, run_history, current_params)

        try:
            raw = self._call_claude(prompt)
            insight = self._parse_response(raw, metrics.run_id)
            insight.run_id = metrics.run_id
            logger.info(f"AIReasoner: insight generated for {metrics.run_id} — {insight.confidence} confidence")
            return insight
        except Exception as e:
            logger.error(f"AIReasoner API error: {e}")
            fallback = self._fallback_insight(findings, metrics)
            fallback.error = str(e)
            return fallback

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        findings:    list[Finding],
        metrics:     RunMetrics,
        run_history: list[dict],
        current_params: dict,
    ) -> str:

        # Serialize findings
        findings_text = "\n".join([
            f"- [{f.severity.upper()}] {f.analyzer}: {f.description} "
            f"(confidence={f.confidence:.2f}, est. impact=${f.impact_estimate_pnl:.0f})"
            for f in findings[:8]  # cap at 8 to stay within context
        ]) or "No findings generated."

        # Serialize run history (last 5 runs)
        history_text = ""
        if run_history:
            history_text = "\n".join([
                f"- {r.get('run_id','?')} | score={r.get('score',0):.4f} | "
                f"calmar={r.get('calmar',0):.3f} | pf={r.get('pf',0):.3f} | phase={r.get('phase','?')}"
                for r in run_history[-5:]
            ])
        else:
            history_text = "This is the first run."

        # Serialize key current params
        key_params = {k: v for k, v in current_params.items()
                      if any(kw in k.lower() for kw in
                             ["risk", "trail", "sl", "tp", "rr", "session", "atr", "spread", "be"])}
        params_text = json.dumps(key_params, indent=2) if key_params else "{}"

        return f"""You are an expert algorithmic trading system analyst specializing in MetaTrader 5 Expert Advisors and systematic trading strategy optimization.

You are analyzing the results of an automated EA optimization run. Your job is to:
1. Diagnose WHY the EA is performing the way it is
2. Identify the most important patterns
3. Suggest specific, actionable parameter changes with clear reasoning

## Current Run Metrics
- Run ID: {metrics.run_id}
- Net Profit: ${metrics.net_profit:.2f}
- Profit Factor: {metrics.profit_factor:.3f}
- Calmar Ratio: {metrics.calmar_ratio:.3f}
- Max Drawdown: {metrics.max_drawdown_pct*100:.1f}%
- Total Trades: {metrics.total_trades}
- Win Rate: {metrics.win_rate*100:.1f}%
- Sharpe Ratio: {metrics.sharpe_ratio:.3f}
- Reversal Rate: {getattr(metrics, 'reversal_rate', None) and f"{metrics.reversal_rate*100:.1f}%" or "N/A"}
- Avg MFE Capture: {getattr(metrics, 'avg_mfe_capture', None) and f"{metrics.avg_mfe_capture*100:.1f}%" or "N/A"}
- Composite Score: {metrics.composite_score:.4f}

## Analysis Findings
{findings_text}

## Run History (recent runs)
{history_text}

## Current Key Parameters
{params_text}

## Instructions
Respond ONLY with a valid JSON object. No preamble, no markdown, no backticks.
The JSON must have exactly these keys:

{{
  "headline": "One sentence diagnosis (max 15 words)",
  "diagnosis": "2-3 sentences explaining WHY the EA is performing this way. Be specific about the root cause.",
  "patterns": ["pattern 1 in plain English", "pattern 2", "pattern 3"],
  "suggestions": [
    {{"param": "InpTrailStartPips", "from": 20, "to": 15, "reason": "Why this change helps"}},
    {{"param": "InpRRRatio", "from": 1.5, "to": 2.0, "reason": "Why this change helps"}}
  ],
  "confidence": "high|medium|low",
  "risk_flags": ["any overfitting concerns, data issues, or warnings"]
}}

Be direct and technical. The user is an experienced forex trader. Max 2-3 suggestions. Focus on what will actually move the needle."""

    # ── API call ──────────────────────────────────────────────────────────────

    def _call_claude(self, prompt: str) -> str:
        """
        Call Claude. If a token-stream callback was registered via
        `set_stream_callback`, use the SSE streaming endpoint and forward each
        text delta via the callback so the dashboard can render the AI's
        reasoning as it types.
        """
        headers = {
            "Content-Type":    "application/json",
            "x-api-key":       self.api_key,
            "anthropic-version": "2023-06-01",
        }
        stream_cb = getattr(self, "_stream_cb", None)

        if stream_cb is None:
            # ── Non-streaming path (used when no UI is attached) ──
            body = {
                "model":      self.MODEL,
                "max_tokens": 1024,
                "messages":   [{"role": "user", "content": prompt}],
            }
            resp = requests.post(self.API_URL, headers=headers, json=body, timeout=self.TIMEOUT)
            if resp.status_code != 200:
                raise RuntimeError(f"Claude API returned {resp.status_code}: {resp.text[:300]}")
            return resp.json()["content"][0]["text"]

        # ── Streaming path: parses SSE events, accumulates text, fires callback per delta ──
        body = {
            "model":      self.MODEL,
            "max_tokens": 1024,
            "stream":     True,
            "messages":   [{"role": "user", "content": prompt}],
        }
        try:
            stream_cb({"event": "start"})
            with requests.post(self.API_URL, headers=headers, json=body, timeout=self.TIMEOUT, stream=True) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"Claude API returned {resp.status_code}: {resp.text[:300]}")
                full_text = []
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw or not raw.startswith("data:"):
                        continue
                    payload = raw[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        evt = json.loads(payload)
                    except Exception:
                        continue
                    if evt.get("type") == "content_block_delta":
                        delta = (evt.get("delta") or {}).get("text") or ""
                        if delta:
                            full_text.append(delta)
                            try:
                                stream_cb({"event": "delta", "text": delta})
                            except Exception:
                                pass
                    elif evt.get("type") == "message_stop":
                        break
                stream_cb({"event": "end"})
                return "".join(full_text)
        except Exception as e:
            try: stream_cb({"event": "error", "error": str(e)})
            except Exception: pass
            raise

    def set_stream_callback(self, cb) -> None:
        """Register a callback `cb(event_dict)` that receives token deltas
        during streaming Claude calls. Pass None to disable streaming."""
        self._stream_cb = cb

    # ── Response parser ───────────────────────────────────────────────────────

    def _parse_response(self, raw: str, run_id: str) -> AIInsight:
        """Parse Claude's JSON response into an AIInsight."""
        # Strip any accidental markdown fences
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        data = json.loads(text)

        return AIInsight(
            headline=data.get("headline", "Analysis complete."),
            diagnosis=data.get("diagnosis", ""),
            patterns=data.get("patterns", []),
            suggestions=data.get("suggestions", []),
            confidence=data.get("confidence", "medium"),
            risk_flags=data.get("risk_flags", []),
            run_id=run_id,
        )

    # ── Autonomous loop: parameter suggestion ─────────────────────────────────

    def suggest_next_params(
        self,
        current_best_params: dict,
        schema_info: list[dict],       # [{name, type, min, max, step, current}]
        iteration_history: list[dict], # [{iteration, run_id, score, pf, calmar, dd, params_changed}]
        targets: dict,                 # {min_profit_factor, max_drawdown_pct, min_calmar}
    ) -> AIParamSuggestion:
        """
        Ask the AI what parameter set to test next in the autonomous loop.
        Returns concrete values for each parameter to change.
        Never raises — returns an error suggestion on failure.
        """
        if not self.enabled:
            return AIParamSuggestion(
                analysis="AI not available — no API key configured.",
                changes=[],
                confidence=0.0,
                goal_status={},
                error="no_api_key",
            )

        prompt = self._build_evolution_prompt(
            current_best_params, schema_info, iteration_history, targets
        )

        try:
            raw = self._call_claude(prompt)
            return self._parse_suggestion(raw)
        except Exception as e:
            logger.error(f"AIReasoner.suggest_next_params failed: {e}")
            return AIParamSuggestion(
                analysis=f"AI call failed: {e}",
                changes=[],
                confidence=0.0,
                goal_status={},
                error=str(e),
            )

    def _build_evolution_prompt(
        self,
        current_best_params: dict,
        schema_info: list[dict],
        iteration_history: list[dict],
        targets: dict,
    ) -> str:
        """Build the evolution prompt for the autonomous loop."""

        # Format parameter schema table
        schema_rows = []
        for p in schema_info:
            current_val = current_best_params.get(p["name"], p.get("default", "?"))
            schema_rows.append(
                f"  {p['name']:<30} | {p['type']:<6} | {p.get('min','?'):>8} – {p.get('max','?'):<8} "
                f"| step={p.get('step','?'):<6} | CURRENT={current_val}"
            )
        schema_table = "\n".join(schema_rows) or "  (no optimizable parameters)"

        # Format iteration history
        if iteration_history:
            hist_rows = []
            for h in iteration_history[-15:]:  # last 15 to stay in context
                changes_str = ", ".join(
                    f"{c['param']}={c['value']}" for c in h.get("changes", [])
                ) or "baseline"
                hist_rows.append(
                    f"  iter={h.get('iteration','?'):>3} | score={h.get('score',0):.4f} | "
                    f"pf={h.get('pf',0):.2f} | calmar={h.get('calmar',0):.2f} | "
                    f"dd={h.get('dd',0):.1f}% | trades={h.get('trades',0)} | "
                    f"changes=[{changes_str}]"
                )
            history_table = "\n".join(hist_rows)
        else:
            history_table = "  (no iterations yet — this is the first AI suggestion)"

        # Format targets
        target_pf      = targets.get("min_profit_factor", 1.5)
        target_dd      = targets.get("max_drawdown_pct", 20.0)
        target_calmar  = targets.get("min_calmar", 0.5)

        return f"""You are an expert MetaTrader 5 EA optimization engine running in autonomous mode.

Your job is to decide EXACTLY which parameters to change and to what SPECIFIC VALUES for the next backtest.
You must reason from the history of tried configurations and move intelligently toward the targets.

## Optimization Targets (ALL must be met to stop)
- Profit Factor   ≥ {target_pf}
- Max Drawdown    ≤ {target_dd}%
- Calmar Ratio    ≥ {target_calmar}

## Optimizable Parameter Schema
{schema_table}

## Iteration History (most recent 15, newest last)
{history_table}

## Your Task
1. Identify which metrics are furthest from their targets
2. Identify which parameter changes correlate with improvements in those metrics
3. Identify unexplored regions of the parameter space
4. Choose 1–3 parameters to change and provide SPECIFIC VALUES within valid range

Rules:
- Values MUST be within [min, max] and snap to valid step increments
- Do NOT suggest a combination already tested in the history above
- Make changes that are logically motivated — explain the reasoning
- If previous attempts moved in one direction and improved scores, continue that direction
- If previous attempts got stuck, try a different parameter or a larger change

Respond ONLY with valid JSON (no markdown, no extra text):
{{
  "analysis": "2-3 sentences: what pattern you see in the history and WHY you are making these specific changes",
  "changes": [
    {{"param": "ExactParamName", "value": 1.5, "reason": "one-line reason"}},
    {{"param": "AnotherParam",  "value": 12,  "reason": "one-line reason"}}
  ],
  "confidence": 0.75,
  "goal_status": {{
    "profit_factor_met": false,
    "drawdown_ok": true,
    "calmar_met": false
  }}
}}"""

    def _parse_suggestion(self, raw: str) -> AIParamSuggestion:
        """Parse AI suggestion response into AIParamSuggestion."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        data = json.loads(text)

        # Normalize confidence to float
        conf = data.get("confidence", 0.5)
        if isinstance(conf, str):
            conf = {"high": 0.85, "medium": 0.6, "low": 0.3}.get(conf.lower(), 0.5)
        conf = float(max(0.0, min(1.0, conf)))

        return AIParamSuggestion(
            analysis=data.get("analysis", ""),
            changes=data.get("changes", []),
            confidence=conf,
            goal_status=data.get("goal_status", {}),
        )

    # ── Fallback (no API key or error) ────────────────────────────────────────

    def _fallback_insight(self, findings: list[Finding], metrics: RunMetrics) -> AIInsight:
        """
        Rule-based fallback when Claude API is unavailable.
        Still useful — surfaces the top finding in plain language.
        """
        if not findings:
            return AIInsight(
                headline="No significant patterns detected in this run.",
                diagnosis=(
                    f"The EA completed {metrics.total_trades} trades with a profit factor of "
                    f"{metrics.profit_factor:.2f} and {metrics.win_rate*100:.0f}% win rate. "
                    "No statistically significant failure patterns were identified."
                ),
                patterns=[],
                suggestions=[],
                confidence="low",
                risk_flags=["AI reasoning unavailable — set ANTHROPIC_API_KEY for full analysis"],
            )

        top = findings[0]
        second = findings[1] if len(findings) > 1 else None

        patterns = [f.description[:120] for f in findings[:3]]
        suggestions = []
        for f in findings[:2]:
            for param, val in (f.suggested_params or {}).items():
                suggestions.append({
                    "param":  param,
                    "from":   "current",
                    "to":     val,
                    "reason": f"Suggested by {f.analyzer} analyzer (confidence {f.confidence:.2f})"
                })

        risk_flags = ["AI reasoning running in fallback mode — set ANTHROPIC_API_KEY for full Opus analysis"]
        if metrics.total_trades < 100:
            risk_flags.append(f"Low trade count ({metrics.total_trades}) — statistical confidence is limited")
        if metrics.max_drawdown_pct > 0.25:
            risk_flags.append(f"High drawdown ({metrics.max_drawdown_pct*100:.0f}%) — risk parameters need review")

        headline = f"{top.severity.upper()} issue: {top.description[:60]}..."
        diagnosis = (
            f"Primary issue ({top.analyzer}): {top.description} "
            f"Estimated impact: ${top.impact_estimate_pnl:.0f}. "
        )
        if second:
            diagnosis += f"Secondary issue ({second.analyzer}): {second.description[:100]}."

        return AIInsight(
            headline=headline,
            diagnosis=diagnosis,
            patterns=patterns,
            suggestions=suggestions[:3],
            confidence="medium" if findings and findings[0].confidence > 0.7 else "low",
            risk_flags=risk_flags,
        )
