"""
main.py
MT5 EA Strategy Optimizer — CLI Entry Point
LEGSTECH_EA_V2 | XAUUSD | H1

Usage:
    python main.py                 # interactive mode
    python main.py --baseline      # run baseline only and show analysis
    python main.py --auto          # fully automated loop (no human prompts)
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

import yaml
import pandas as pd
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich import print as rprint

# ── Project imports ──────────────────────────────────────────────────────────
from data.models import Run, RunMetrics, Candidate
from data.store import DataStore
from mt5.ini_builder import IniBuilder
from mt5.runner import MT5Runner
from mt5.report_parser import ReportParser
from mt5.log_reader import TradeLogReader
from analysis.reversal import ReversalAnalyzer
from analysis.time_performance import TimePerformanceAnalyzer
from analysis.entry_exit_quality import EntryExitQualityAnalyzer
from analysis.equity_curve import EquityCurveAnalyzer
from scoring.composite import CompositeScorer
from mutation.engine import MutationEngine
from validation.gate import ValidationGate

console = Console()

CONFIG_PATH   = Path("config.yaml")
MANIFEST_PATH = Path("mutation/param_manifest.yaml")
KB_PATH       = Path("mutation/knowledge_base.yaml")
DB_PATH       = Path("optimizer.db")
RUNS_DIR      = Path("runs")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Component factory ─────────────────────────────────────────────────────────

def build_components(cfg: dict):
    store    = DataStore(DB_PATH, RUNS_DIR)
    builder  = IniBuilder(CONFIG_PATH, MANIFEST_PATH)
    runner   = MT5Runner(CONFIG_PATH)
    parser   = ReportParser()
    log_rdr  = TradeLogReader(
        broker_tz_offset_hours=cfg["broker"]["timezone_offset_hours"],
        pip_size=0.1,   # XAUUSD: 0.1 per pip
    )
    analyzers = [
        ReversalAnalyzer(
            mfe_threshold_pips=cfg["analysis"]["reversal"]["mfe_threshold_pips"],
            min_reversal_rate=cfg["analysis"]["reversal"]["min_reversal_rate"],
            permutation_n=cfg["analysis"]["reversal"]["permutation_n"],
        ),
        TimePerformanceAnalyzer(
            z_score_threshold=cfg["analysis"]["time_performance"]["z_score_threshold"],
            min_bucket_trades=cfg["analysis"]["time_performance"]["min_trades_per_bucket"],
            permutation_n=cfg["analysis"]["time_performance"]["permutation_n"],
        ),
        EntryExitQualityAnalyzer(
            poor_exit_threshold=cfg["analysis"]["entry_exit"]["poor_exit_quality"],
            poor_entry_threshold=cfg["analysis"]["entry_exit"]["poor_entry_quality"],
        ),
        EquityCurveAnalyzer(
            max_flatness=cfg["analysis"]["equity_curve"]["max_flatness_score"],
            min_r_squared=cfg["analysis"]["equity_curve"]["min_r_squared"],
        ),
    ]
    scorer  = CompositeScorer(str(CONFIG_PATH))
    mutator = MutationEngine(KB_PATH, MANIFEST_PATH,
                             dedup_lookback=cfg["mutation"]["dedup_lookback_runs"])
    gate    = ValidationGate(CONFIG_PATH)
    return store, builder, runner, parser, log_rdr, analyzers, scorer, mutator, gate


# ── Single run pipeline ───────────────────────────────────────────────────────

def execute_run(
    run_id:        str,
    params:        dict[str, Any],
    period_start:  str,
    period_end:    str,
    phase:         str,
    hypothesis_id: Optional[str],
    cfg:           dict,
    store:         DataStore,
    builder:       IniBuilder,
    runner:        MT5Runner,
    parser:        ReportParser,
    log_rdr:       TradeLogReader,
    analyzers:     list,
    scorer:        CompositeScorer,
) -> tuple[Optional[RunMetrics], pd.DataFrame]:
    """
    Execute one complete backtest run:
    1. Build INI → Launch MT5 → Wait → Parse report → Merge logger CSV
    2. Compute derived fields, enrich trades
    3. Compute composite score
    4. Save everything to store
    Returns (metrics, trades_df) or (None, empty_df) on failure.
    """
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build INI
    ini_path = builder.build(
        run_id=run_id,
        params=params,
        period_start=period_start,
        period_end=period_end,
        output_dir=run_dir,
        phase=phase,
    )

    # 2. Save run record
    run = Run(
        run_id=run_id,
        ea_name=cfg["ea"]["name"],
        symbol=cfg["ea"]["symbol"],
        timeframe=cfg["ea"]["timeframe"],
        period_start=period_start,
        period_end=period_end,
        params=params,
        phase=phase,
        hypothesis_id=hypothesis_id,
        tester_model=cfg["mt5"]["tester_model"],
        ini_snapshot=ini_path.read_text(),
    )
    store.save_run(run)

    # 3. Launch MT5
    report_dir = run_dir / "report"
    # MT5 Files folder: may need adjusting to actual terminal data path
    mt5_files_dir = None  # TODO: set to actual MQL5/Files path once terminal path confirmed

    console.print(f"[dim]Launching MT5... (timeout {cfg['mt5']['tester_timeout_seconds']}s)[/dim]")
    result = runner.run(run_id, ini_path, report_dir, log_csv_search_dir=mt5_files_dir)

    if not result.success:
        logger.error(f"Run {run_id} failed: {result.error_message}")
        console.print(f"[red]✗ MT5 run failed: {result.error_message}[/red]")
        return None, pd.DataFrame()

    # 4. Parse report
    metrics, trades = parser.parse(result.report_xml, result.report_html)
    if metrics is None:
        console.print(f"[red]✗ Could not parse report for {run_id}[/red]")
        return None, pd.DataFrame()
    metrics.run_id = run_id

    # 5. Merge TradeLogger CSV → enrich with MAE/MFE + derived fields
    if trades:
        trades = log_rdr.merge(
            trades, result.trade_log_csv,
            reversal_mfe_threshold_pips=cfg["analysis"]["reversal"]["mfe_threshold_pips"],
        )
        trades_df = pd.DataFrame([t.model_dump() for t in trades])
    else:
        trades_df = pd.DataFrame()

    # 6. Compute reversal rate and MFE capture for metrics
    if not trades_df.empty:
        if "result_class" in trades_df.columns:
            losers        = trades_df[trades_df["net_money"] < 0]
            reversals     = trades_df[trades_df["result_class"] == "reversal"]
            metrics.reversal_rate = (
                len(reversals) / max(1, len(losers))
            )
        if "mfe_capture_ratio" in trades_df.columns:
            metrics.avg_mfe_capture = float(
                trades_df["mfe_capture_ratio"].dropna().mean() or 0
            )
        if "mfe_pips" in trades_df.columns:
            metrics.avg_mfe_pips = float(trades_df["mfe_pips"].dropna().mean() or 0)
            metrics.avg_mae_pips = float(trades_df.get("mae_pips", pd.Series()).dropna().mean() or 0)

    # 7. Compute composite score (session stats can be added here in v2)
    metrics.composite_score = scorer.score(metrics)

    # 8. Persist
    store.save_metrics(metrics)
    if not trades_df.empty:
        store.save_trades(run_id, trades)
    run.report_path  = result.report_xml
    run.log_csv_path = result.trade_log_csv
    store.save_run(run)

    return metrics, trades_df


# ── Analysis pipeline ─────────────────────────────────────────────────────────

def run_analysis(
    run_id:    str,
    trades_df: pd.DataFrame,
    metrics:   RunMetrics,
    analyzers: list,
    store:     DataStore,
) -> list:
    """Run all analyzers and persist findings."""
    all_findings = []
    for analyzer in analyzers:
        findings = analyzer.run(trades_df, metrics, run_id)
        all_findings.extend(findings)
        console.print(
            f"  [green]✓[/green] {analyzer.name:<25} → {len(findings)} finding(s)"
        )

    all_findings.sort(key=lambda f: f.confidence, reverse=True)
    store.save_findings(all_findings)
    return all_findings


# ── Display helpers ───────────────────────────────────────────────────────────

def display_metrics(metrics: RunMetrics, label: str = "Backtest Results") -> None:
    table = Table(title=label, show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="dim")
    table.add_column("Value", justify="right")

    table.add_row("Net Profit",      f"${metrics.net_profit:,.2f}")
    table.add_row("Profit Factor",   f"{metrics.profit_factor:.3f}")
    table.add_row("Calmar Ratio",    f"{metrics.calmar_ratio:.3f}")
    table.add_row("Max Drawdown",    f"{metrics.max_drawdown_pct*100:.1f}%  (${metrics.max_drawdown_abs:,.0f})")
    table.add_row("Total Trades",    str(metrics.total_trades))
    table.add_row("Win Rate",        f"{metrics.win_rate*100:.1f}%")
    table.add_row("Sharpe",          f"{metrics.sharpe_ratio:.3f}")
    table.add_row("Recovery Factor", f"{metrics.recovery_factor:.3f}")
    if metrics.avg_mfe_capture is not None:
        table.add_row("MFE Capture",  f"{metrics.avg_mfe_capture*100:.1f}%")
    if metrics.reversal_rate is not None:
        table.add_row("Reversal Rate", f"{metrics.reversal_rate*100:.1f}%")
    table.add_row("Composite Score", f"[bold]{metrics.composite_score:.4f}[/bold]")

    console.print(table)


def display_findings(findings: list) -> None:
    table = Table(title="Analysis Findings", show_header=True, header_style="bold yellow")
    table.add_column("#", width=3)
    table.add_column("Finding", max_width=60)
    table.add_column("Severity", width=8)
    table.add_column("Confidence", width=10, justify="right")
    table.add_column("Est. Impact", width=12, justify="right")

    sev_colors = {"high": "red", "medium": "yellow", "low": "dim"}

    for i, f in enumerate(findings, 1):
        color = sev_colors.get(f.severity, "white")
        table.add_row(
            str(i),
            f.description[:60] + ("…" if len(f.description) > 60 else ""),
            f"[{color}]{f.severity.upper()}[/{color}]",
            f"{f.confidence:.2f}",
            f"${f.impact_estimate_pnl:,.0f}",
        )
    console.print(table)


def display_hypotheses(hypotheses: list, current_params: dict) -> None:
    table = Table(title="Proposed Hypotheses", show_header=True, header_style="bold magenta")
    table.add_column("#", width=3)
    table.add_column("Description", max_width=50)
    table.add_column("Parameter Changes", max_width=40)

    for i, h in enumerate(hypotheses, 1):
        changes = []
        for param, val in h.param_delta.items():
            old = current_params.get(param, "?")
            changes.append(f"{param}: {old} → {val}")
        table.add_row(
            str(i),
            h.description[:50],
            "\n".join(changes),
        )
    console.print(table)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main(auto_mode: bool = False, baseline_only: bool = False) -> None:
    cfg = load_config()
    store, builder, runner, parser, log_rdr, analyzers, scorer, mutator, gate = (
        build_components(cfg)
    )

    ea   = cfg["ea"]
    per  = cfg["periods"]

    console.print(Panel(
        f"[bold cyan]MT5 EA Strategy Optimizer[/bold cyan]\n"
        f"EA: {ea['name']}  |  Symbol: {ea['symbol']}  |  TF: {ea['timeframe']}\n"
        f"Train: {per['train_start']} → {per['train_end']}  |  "
        f"OOS: {per['oos_start']} → {per['oos_end']} [bold red](LOCKED)[/bold red]",
        title="[bold]Session Start[/bold]",
    ))

    # ── PHASE 0: Baseline ─────────────────────────────────────────────────────
    console.rule("[bold]Phase 0: Baseline Run[/bold]")
    default_params = builder.default_params()
    baseline_id    = f"baseline_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    baseline_metrics, baseline_trades = execute_run(
        run_id=baseline_id,
        params=default_params,
        period_start=per["train_start"],
        period_end=per["train_end"],
        phase="baseline",
        hypothesis_id=None,
        cfg=cfg, store=store, builder=builder, runner=runner,
        parser=parser, log_rdr=log_rdr, analyzers=analyzers, scorer=scorer,
    )

    if baseline_metrics is None:
        console.print("[red]Baseline run failed. Check MT5 config and terminal path.[/red]")
        sys.exit(1)

    display_metrics(baseline_metrics, label="Baseline Results")

    if baseline_only:
        # ── Analysis only ────────────────────────────────────────────────────
        console.rule("[bold]Analysis[/bold]")
        findings = run_analysis(baseline_id, baseline_trades, baseline_metrics,
                                analyzers, store)
        display_findings(findings)
        return

    current_params  = default_params.copy()
    current_metrics = baseline_metrics
    best_score      = baseline_metrics.composite_score
    iteration       = 0
    no_improvement_count = 0

    max_iter = cfg["optimization"]["max_iterations"]
    conv_win = cfg["optimization"]["convergence_window"]
    conv_thr = cfg["optimization"]["convergence_threshold"]

    # ── Iteration loop ────────────────────────────────────────────────────────
    while iteration < max_iter:
        iteration += 1
        console.rule(f"[bold]Iteration {iteration}[/bold]")

        # Analysis
        console.print("[bold]Running analysis modules...[/bold]")
        parent_run_id = baseline_id if iteration == 1 else f"iter_{iteration-1}"
        trades_df = store.load_trades(
            baseline_id if iteration == 1 else f"iter_{iteration-1}_best"
        )
        if trades_df.empty:
            trades_df = baseline_trades

        findings = run_analysis(
            baseline_id, trades_df, current_metrics, analyzers, store
        )
        display_findings(findings[:8])  # top 8

        if not findings:
            console.print("[yellow]No actionable findings. Stopping.[/yellow]")
            break

        # Mutation
        recent_deltas = store.get_recent_param_deltas(cfg["mutation"]["dedup_lookback_runs"])
        hypotheses = mutator.propose(
            findings=findings,
            current_params=current_params,
            recent_deltas=recent_deltas,
            max_proposals=cfg["mutation"]["max_hypotheses_per_cycle"],
        )

        if not hypotheses:
            console.print("[yellow]No new hypotheses available. Stopping.[/yellow]")
            break

        display_hypotheses(hypotheses, current_params)

        # Human approval (skipped in auto mode)
        if auto_mode:
            selected_indices = list(range(len(hypotheses)))
        else:
            choice = Prompt.ask(
                "Apply which hypotheses?",
                default="1",
            )
            if choice.lower() in ("skip", "s", ""):
                console.print("[dim]Skipping...[/dim]")
                continue
            if choice.lower() == "all":
                selected_indices = list(range(len(hypotheses)))
            else:
                selected_indices = [int(x.strip()) - 1 for x in choice.split(",")]

        # Test selected hypotheses
        iteration_best: Optional[RunMetrics] = None
        iteration_best_params: Optional[dict] = None
        iteration_best_hyp = None

        for idx in selected_indices:
            if idx < 0 or idx >= len(hypotheses):
                continue
            hyp = hypotheses[idx]
            test_params = {**current_params, **hyp.param_delta}
            run_id      = f"iter_{iteration}_h{idx+1}"

            console.print(f"\n[bold]Testing hypothesis {idx+1}: {hyp.description}[/bold]")
            store.save_hypothesis(hyp)

            test_metrics, test_trades = execute_run(
                run_id=run_id,
                params=test_params,
                period_start=per["train_start"],
                period_end=per["train_end"],
                phase="explore",
                hypothesis_id=hyp.hypothesis_id,
                cfg=cfg, store=store, builder=builder, runner=runner,
                parser=parser, log_rdr=log_rdr, analyzers=analyzers, scorer=scorer,
            )

            if test_metrics is None:
                continue

            display_metrics(test_metrics, label=f"H{idx+1} Results")
            delta_score = test_metrics.composite_score - current_metrics.composite_score
            color = "green" if delta_score > 0 else "red"
            console.print(
                f"Score delta: [{color}]{delta_score:+.4f}[/{color}] "
                f"({current_metrics.composite_score:.4f} → {test_metrics.composite_score:.4f})"
            )

            store.update_hypothesis_status(hyp.hypothesis_id, "tested", run_id)

            if iteration_best is None or test_metrics.composite_score > iteration_best.composite_score:
                iteration_best        = test_metrics
                iteration_best_params = test_params
                iteration_best_hyp    = hyp

        if iteration_best is None:
            console.print("[red]All hypotheses failed to run.[/red]")
            continue

        # Validation gate
        gate_result = gate.run_is_check(iteration_best)
        if not gate_result.passed:
            console.print(f"[red]IS gate failed: {gate_result.details}[/red]")
            store.update_hypothesis_status(iteration_best_hyp.hypothesis_id, "rejected")
            no_improvement_count += 1
        else:
            # Walk-forward validation
            if auto_mode or Confirm.ask("Run walk-forward validation?", default=True):
                wfv = gate.run_walk_forward(iteration_best_params, cfg, store, builder,
                                            runner, parser, log_rdr, analyzers, scorer)
                console.print(f"WFV: OOS/IS ratio = {wfv.oos_is_ratio:.2f} "
                               f"(threshold {cfg['thresholds']['min_wfv_ratio']:.2f})")
                if wfv.passed:
                    console.print(f"[green]Walk-forward PASSED[/green]")
                else:
                    console.print(f"[yellow]Walk-forward FAILED — not promoting.[/yellow]")
                    store.update_hypothesis_status(iteration_best_hyp.hypothesis_id, "rejected")
                    no_improvement_count += 1
                    continue

            # OOS test
            run_oos = auto_mode or Confirm.ask("Run OOS validation?", default=False)
            oos_score = None
            if run_oos:
                oos_metrics, _ = execute_run(
                    run_id=f"oos_{iteration}",
                    params=iteration_best_params,
                    period_start=per["oos_start"],
                    period_end=per["oos_end"],
                    phase="oos",
                    hypothesis_id=iteration_best_hyp.hypothesis_id,
                    cfg=cfg, store=store, builder=builder, runner=runner,
                    parser=parser, log_rdr=log_rdr, analyzers=analyzers, scorer=scorer,
                )
                if oos_metrics:
                    oos_score = oos_metrics.composite_score
                    oos_deg = (iteration_best.composite_score - oos_score) / max(0.001, iteration_best.composite_score)
                    if oos_deg > cfg["thresholds"]["max_oos_degradation"]:
                        console.print(f"[red]OOS degradation {oos_deg:.1%} > threshold. Rejected.[/red]")
                        store.update_hypothesis_status(iteration_best_hyp.hypothesis_id, "rejected")
                        no_improvement_count += 1
                        continue
                    display_metrics(oos_metrics, label="OOS Results")

            # Promote candidate
            candidate = Candidate(
                run_id=iteration_best.run_id,
                composite_score=iteration_best.composite_score,
                oos_score=oos_score,
                params=iteration_best_params,
            )
            store.save_candidate(candidate)
            store.update_hypothesis_status(iteration_best_hyp.hypothesis_id, "validated")
            console.print(f"[bold green]✅ Candidate C{candidate.candidate_id} promoted![/bold green]")

            # Update baseline
            improvement = iteration_best.composite_score - best_score
            if improvement >= conv_thr:
                current_params  = iteration_best_params
                current_metrics = iteration_best
                best_score      = iteration_best.composite_score
                no_improvement_count = 0
            else:
                no_improvement_count += 1

        # Convergence check
        if no_improvement_count >= conv_win:
            console.print(
                f"[yellow]Convergence: no improvement in {no_improvement_count} iterations. Stopping.[/yellow]"
            )
            break

        if not (auto_mode or Confirm.ask("Continue to next iteration?", default=True)):
            break

    # ── Summary ───────────────────────────────────────────────────────────────
    console.rule("[bold]Optimization Complete[/bold]")
    candidates = store.list_candidates()
    if candidates:
        console.print(f"[bold green]{len(candidates)} candidate(s) promoted.[/bold green]")
        console.print(f"Best composite score: {max(c['composite_score'] for c in candidates):.4f}")
    else:
        console.print("[yellow]No candidates were promoted in this session.[/yellow]")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser_cli = argparse.ArgumentParser(description="MT5 EA Strategy Optimizer")
    parser_cli.add_argument("--auto",      action="store_true", help="Run fully automated (no prompts)")
    parser_cli.add_argument("--baseline",  action="store_true", help="Run baseline + analysis only")
    parser_cli.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser_cli.parse_args()

    logger.remove()
    logger.add(sys.stderr, level=args.log_level)
    logger.add("optimizer.log", level="DEBUG", rotation="10 MB")

    main(auto_mode=args.auto, baseline_only=args.baseline)
