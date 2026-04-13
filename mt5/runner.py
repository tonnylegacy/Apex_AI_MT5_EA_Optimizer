"""
mt5/runner.py
Robust MT5 Strategy Tester runner with:
 1. MT5 process control (kill existing, launch fresh)
 2. Pre-run environment validation
 3. Historical data readiness wait
 4. Actionable error messages
 5. Auto-retry on failure (1 retry)
 6. Report detection in MT5 native reports folder
"""
from __future__ import annotations

import shutil
import subprocess
import time
import psutil
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from data.models import RunResult


# ── Custom Exceptions ─────────────────────────────────────────────────────────

class MT5TimeoutError(RuntimeError):
    pass

class MT5ValidationError(RuntimeError):
    pass


# ── MT5Runner ─────────────────────────────────────────────────────────────────

class MT5Runner:

    POLL_INTERVAL_S     = 5      # seconds between report checks
    PROCESS_SETTLE_S    = 2      # seconds to wait after process exit
    MT5_INIT_WAIT_S     = 8      # seconds after launch before testing begins
    KILL_WAIT_S         = 3      # seconds after killing MT5 before launching fresh
    MAX_RETRIES         = 1      # retry once on failure

    MT5_EXE_NAME        = "terminal64.exe"

    def __init__(self, config_path: str | Path = "config.yaml"):
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        self.cfg             = cfg
        self.terminal_exe    = Path(cfg["mt5"]["terminal_exe"])
        self.timeout_s       = cfg["mt5"]["tester_timeout_seconds"]
        self.appdata_path    = Path(cfg["mt5"]["appdata_path"])
        # MT5 writes reports to the ROOT of the terminal data folder
        # (not a 'reports' subfolder) — confirmed by log inspection
        self.mt5_reports_dir = self.appdata_path
        self.mql5_files_dir  = Path(cfg["mt5"].get(
            "mql5_files_path",
            str(self.appdata_path / "MQL5" / "Files")
        ))
        self.data_wait_s     = cfg["mt5"].get("data_readiness_wait_seconds", 10)

    # ── Public entry point ────────────────────────────────────────────────────

    def run(
        self,
        run_id: str,
        ini_path: Path,
        report_dir: Path,
        log_csv_search_dir: Optional[Path] = None,
    ) -> RunResult:
        """
        Full execution pipeline with retry:
        1. Kill existing MT5
        2. Validate environment
        3. Launch fresh MT5
        4. Wait for data readiness
        5. Poll for report
        6. Auto-retry once on failure
        """
        report_dir.mkdir(parents=True, exist_ok=True)
        report_stem = f"Optimizer_{run_id}"

        for attempt in range(1, self.MAX_RETRIES + 2):
            is_retry = attempt > 1
            if is_retry:
                logger.warning(f"[{run_id}] Retry attempt {attempt}...")

            try:
                # ── Step 1: Kill any running MT5 ─────────────────────────
                self._kill_mt5(run_id)

                # ── Step 1b: Clear stale reports from previous runs ──────
                self._clear_stale_reports(run_id)

                # ── Step 2: Pre-run validation ───────────────────────────
                self._validate(run_id)

                # ── Step 3: Launch fresh MT5 ─────────────────────────────
                proc = self._launch_mt5(run_id, ini_path)

                # ── Step 4: Wait for MT5 to initialize + data ────────────
                logger.info(f"[{run_id}] Waiting {self.MT5_INIT_WAIT_S}s for MT5 to initialize...")
                time.sleep(self.MT5_INIT_WAIT_S)

                # ── Step 5: Wait for report + TradeLog ───────────────────
                result = self._wait_for_report(run_id, proc, report_dir, report_stem)

                # ── Step 6: Find TradeLogger CSV ─────────────────────────
                result.trade_log_csv = self._find_trade_log(run_id, log_csv_search_dir)

                logger.success(f"[{run_id}] Run complete. Report: {result.report_xml}")
                return result

            except MT5ValidationError as e:
                logger.error(f"[{run_id}] Validation error: {e}")
                return RunResult(run_id=run_id, success=False, error_message=str(e))

            except Exception as e:
                logger.warning(f"[{run_id}] Attempt {attempt} failed: {e}")
                if attempt > self.MAX_RETRIES:
                    msg = self._diagnose_failure(str(e))
                    logger.error(f"[{run_id}] All retries exhausted. {msg}")
                    return RunResult(run_id=run_id, success=False, error_message=msg)
                logger.info(f"[{run_id}] Will retry after {self.KILL_WAIT_S}s...")
                time.sleep(self.KILL_WAIT_S)

    # ── Step 1: Kill existing MT5 ─────────────────────────────────────────────

    def _kill_mt5(self, run_id: str) -> None:
        """Find and terminate any running MT5 processes."""
        killed = 0
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                if proc.info["name"] and self.MT5_EXE_NAME.lower() in proc.info["name"].lower():
                    logger.info(f"[{run_id}] Closing existing MT5 (PID {proc.pid})...")
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if killed > 0:
            logger.info(f"[{run_id}] Closed {killed} MT5 instance(s). Waiting {self.KILL_WAIT_S}s...")
            time.sleep(self.KILL_WAIT_S)
        else:
            logger.debug(f"[{run_id}] No existing MT5 process found.")

    # ── Step 2: Pre-run validation ────────────────────────────────────────────

    def _validate(self, run_id: str) -> None:
        """Validate all required files and directories exist before launch."""
        errors = []

        # Check terminal executable
        if not self.terminal_exe.exists():
            errors.append(f"MT5 terminal not found: {self.terminal_exe}")

        # Check EA compiled file
        ea_name = self.cfg["ea"]["file"]
        ea_candidates = [
            self.appdata_path / "MQL5" / "Experts" / f"{ea_name}.ex5",
            self.appdata_path / "MQL5" / "Experts" / f"{ea_name}",
        ]
        ea_found = any(p.exists() for p in ea_candidates)
        if not ea_found:
            errors.append(
                f"EA file not found: {ea_name}.ex5 — "
                f"ensure you compiled the EA in MetaEditor before running."
            )

        # Check appdata path
        if not self.appdata_path.exists():
            errors.append(f"MT5 appdata folder not found: {self.appdata_path}")

        # Symbol check (basic — just ensure it's set)
        symbol = self.cfg["ea"].get("symbol", "")
        if not symbol:
            errors.append("Symbol not configured in config.yaml ea.symbol")

        if errors:
            for e in errors:
                logger.error(f"[{run_id}] Validation: {e}")
            raise MT5ValidationError(
                "Pre-run validation failed:\n" + "\n".join(f"  • {e}" for e in errors)
            )

        logger.debug(f"[{run_id}] Pre-run validation passed.")

    # ── Step 3: Launch MT5 ───────────────────────────────────────────────────

    def _launch_mt5(self, run_id: str, ini_path: Path) -> subprocess.Popen:
        """Launch a fresh MT5 instance with the given INI config."""
        cmd = [str(self.terminal_exe), f"/config:{ini_path}"]
        logger.info(f"[{run_id}] Launching MT5: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.debug(f"[{run_id}] MT5 PID: {proc.pid}")
        return proc

    # ── Step 5: Wait for report ───────────────────────────────────────────────

    def _wait_for_report(
        self,
        run_id: str,
        proc: subprocess.Popen,
        report_dir: Path,
        report_stem: str,
    ) -> RunResult:
        """
        Poll both our local dir and MT5's native reports folder.
        MT5 writes the report to <appdata>\\reports\\ using the name from INI Report= field.
        """
        elapsed = 0
        search_dirs = [report_dir, self.mt5_reports_dir]

        while elapsed < self.timeout_s:
            # Check for report in all locations
            for search in search_dirs:
                if not search.exists():
                    continue
                result = self._find_report_files(search, report_stem)
                if result:
                    xml_path, html_path = result
                    time.sleep(self.PROCESS_SETTLE_S)
                    # Archive to our local report dir
                    if xml_path:
                        report_dir.mkdir(parents=True, exist_ok=True)
                        dest = report_dir / Path(xml_path).name
                        if not dest.exists():
                            shutil.copy2(xml_path, dest)
                    logger.info(f"[{run_id}] Report found in {search} after {elapsed}s")
                    return RunResult(
                        run_id=run_id,
                        report_xml=xml_path,
                        report_html=html_path,
                        success=True,
                    )

            # Check if process already exited
            ret = proc.poll()
            if ret is not None:
                time.sleep(self.PROCESS_SETTLE_S)
                # Final check after process exit
                for search in search_dirs:
                    if not search.exists():
                        continue
                    result = self._find_report_files(search, report_stem)
                    if result:
                        xml_path, html_path = result
                        return RunResult(
                            run_id=run_id,
                            report_xml=xml_path,
                            report_html=html_path,
                            success=True,
                        )

                # Process exited but no report — diagnose
                if ret != 0:
                    raise RuntimeError(
                        f"MT5 exited with error code {ret}. "
                        f"Possible causes: invalid INI parameters, EA not compiled, "
                        f"or missing historical data."
                    )
                else:
                    raise RuntimeError(
                        "MT5 exited without generating a report. "
                        "Possible causes: Symbol data not downloaded, "
                        "invalid date range, or EA failed to initialize."
                    )

            time.sleep(self.POLL_INTERVAL_S)
            elapsed += self.POLL_INTERVAL_S
            if elapsed % 30 == 0:
                logger.info(f"[{run_id}] Still waiting for report... {elapsed}/{self.timeout_s}s")

        raise MT5TimeoutError(
            f"No report after {self.timeout_s}s. "
            f"MT5 may be stuck or the test is taking too long. "
            f"Consider reducing the test date range or using OHLC M1 model."
        )

    def _clear_stale_reports(self, run_id: str) -> None:
        """Remove stale Optimizer_* reports from MT5 appdata root to avoid false-positive detection."""
        try:
            import glob
            # Only delete files NOT matching the current run_id
            for pattern in ["*.htm", "*.xml", "*.html"]:
                for f in self.mt5_reports_dir.glob(f"Optimizer_*{pattern[-3:]}"):
                    if run_id not in f.name:
                        try:
                            f.unlink()
                            logger.debug(f"[{run_id}] Cleared stale report: {f.name}")
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"[{run_id}] Could not clear stale reports: {e}")

    def _find_report_files(
        self, search_dir: Path, report_stem: str
    ) -> Optional[tuple[Optional[str], Optional[str]]]:
        """Search for report XML/HTML files by stem prefix."""
        xml_list = sorted(
            list(search_dir.glob(f"{report_stem}*.xml")) +
            list(search_dir.glob(f"{report_stem}*.XML")),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        htm_list = sorted(
            list(search_dir.glob(f"{report_stem}*.htm")) +
            list(search_dir.glob(f"{report_stem}*.html")) +
            list(search_dir.glob(f"{report_stem}*.HTM")),
            key=lambda p: p.stat().st_mtime, reverse=True
        )

        if xml_list or htm_list:
            return (
                str(xml_list[0]) if xml_list else None,
                str(htm_list[0]) if htm_list else None,
            )
        return None

    # ── TradeLogger CSV lookup ────────────────────────────────────────────────

    def _find_trade_log(
        self, run_id: str, search_dir: Optional[Path]
    ) -> Optional[Path]:
        """Find the TradeLogger CSV written by the EA during the backtest."""
        ea_name = self.cfg["ea"]["file"]
        symbol  = self.cfg["ea"]["symbol"]

        candidates = [
            self.mql5_files_dir / f"{ea_name}_{symbol}_TradeLog.csv",
        ]
        if search_dir:
            candidates.append(search_dir / f"{ea_name}_{symbol}_TradeLog.csv")

        for path in candidates:
            if path.exists():
                logger.debug(f"[{run_id}] TradeLog found: {path}")
                return path

        logger.debug(f"[{run_id}] TradeLog CSV not found (fallback to report-only mode).")
        return None

    # ── Error diagnosis ───────────────────────────────────────────────────────

    def _diagnose_failure(self, error_msg: str) -> str:
        """Convert technical errors to actionable user-facing messages."""
        msg = error_msg.lower()
        if "exit" in msg and "cleanly" in msg:
            return (
                "MT5 started but did not produce a report.\n"
                "✦ Check that XAUUSD historical data is downloaded in MT5\n"
                "✦ Ensure the date range (2022-2023) has data available\n"
                "✦ Verify the EA compiled successfully in MetaEditor"
            )
        if "timeout" in msg:
            return (
                f"MT5 tester timed out after {self.timeout_s}s.\n"
                "✦ Try a shorter date range in config.yaml\n"
                "✦ Switch tester_model to 4 (OHLC M1) for faster runs"
            )
        if "validation" in msg or "not found" in msg:
            return error_msg
        return (
            f"MT5 run failed: {error_msg}\n"
            "✦ Ensure MT5 is fully closed before starting the optimizer\n"
            "✦ Check config.yaml paths are correct"
        )
