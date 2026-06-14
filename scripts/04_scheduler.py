"""
04_scheduler.py
───────────────
Runs the full pipeline on a schedule:
  • Truth Social ingest: every 30 minutes
  • Polymarket odds:     every 60 minutes
  • Analysis:           every 6 hours

Run once manually or set up as a system cron / GitHub Actions job.

Usage:
    python 04_scheduler.py              # run loop forever
    python 04_scheduler.py --once       # run all steps once and exit
    python 04_scheduler.py --step ingest
    python 04_scheduler.py --step polymarket
    python 04_scheduler.py --step analyze
    python 04_scheduler.py --step outcomes
    python 04_scheduler.py --step predictions
"""

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
LOG_DIR = SCRIPTS_DIR.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "scheduler.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

INGEST_INTERVAL_SECS      = 30 * 60   # 30 min
POLYMARKET_INTERVAL_SECS  = 60 * 60   # 60 min
ANALYZE_INTERVAL_SECS     = 6 * 3600  # 6 hours
OUTCOMES_INTERVAL_SECS    = 24 * 3600  # 24 hours
PREDICTIONS_INTERVAL_SECS = 24 * 3600  # 24 hours

last_run: dict[str, float] = {
    "ingest":      0,
    "polymarket":  0,
    "analyze":     0,
    "outcomes":    0,
    "predictions": 0,
}


def run_step(name: str, script: str, extra_args: list[str] = []):
    log.info(f">> Running step: {name}")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script)] + extra_args,
        capture_output=False,
        text=True,
    )
    if result.returncode == 0:
        log.info(f"OK {name} completed")
    else:
        log.error(f"FAIL {name} failed (returncode={result.returncode})")
    return result.returncode == 0


def should_run(step: str, interval: int) -> bool:
    return (time.time() - last_run[step]) >= interval


def tick():
    now = time.time()

    if should_run("ingest", INGEST_INTERVAL_SECS):
        ok = run_step("Truth Social ingest", "01_ingest.py")
        if ok:
            last_run["ingest"] = now

    if should_run("polymarket", POLYMARKET_INTERVAL_SECS):
        ok = run_step("Polymarket odds", "02_polymarket.py")
        if ok:
            last_run["polymarket"] = now

    if should_run("analyze", ANALYZE_INTERVAL_SECS):
        ok = run_step("Analysis", "03_analyze.py")
        if ok:
            last_run["analyze"] = now

    if should_run("outcomes", OUTCOMES_INTERVAL_SECS):
        ok = run_step("Outcome backfill", "05_backfill_outcomes.py")
        if ok:
            last_run["outcomes"] = now

    if should_run("predictions", PREDICTIONS_INTERVAL_SECS):
        ok1 = run_step("Best Bets prediction snapshot", "08_score_predictions.py", ["log-bestbets"])
        ok2 = run_step("Resolve predictions", "08_score_predictions.py", ["resolve"])
        if ok1 and ok2:
            last_run["predictions"] = now


def main():
    parser = argparse.ArgumentParser(description="Trump tracker scheduler")
    parser.add_argument("--once", action="store_true", help="Run all steps once and exit")
    parser.add_argument("--step", choices=["ingest", "polymarket", "analyze", "outcomes", "predictions"],
                        help="Run a single step and exit")
    args = parser.parse_args()

    if args.step:
        if args.step == "predictions":
            run_step("Best Bets prediction snapshot", "08_score_predictions.py", ["log-bestbets"])
            run_step("Resolve predictions", "08_score_predictions.py", ["resolve"])
            return
        script_map = {
            "ingest":     "01_ingest.py",
            "polymarket": "02_polymarket.py",
            "analyze":    "03_analyze.py",
            "outcomes":   "05_backfill_outcomes.py",
        }
        run_step(args.step, script_map[args.step])
        return

    if args.once:
        log.info("Running full pipeline once…")
        run_step("Truth Social ingest", "01_ingest.py")
        run_step("Polymarket odds", "02_polymarket.py")
        run_step("Analysis", "03_analyze.py")
        run_step("Outcome backfill", "05_backfill_outcomes.py")
        run_step("Best Bets prediction snapshot", "08_score_predictions.py", ["log-bestbets"])
        run_step("Resolve predictions", "08_score_predictions.py", ["resolve"])
        log.info("Done.")
        return

    log.info("Starting Trump Tracker scheduler (Ctrl+C to stop)…")
    log.info(f"  Ingest:     every {INGEST_INTERVAL_SECS//60} min")
    log.info(f"  Polymarket: every {POLYMARKET_INTERVAL_SECS//60} min")
    log.info(f"  Analysis:   every {ANALYZE_INTERVAL_SECS//3600} hours")
    log.info(f"  Outcomes:   every {OUTCOMES_INTERVAL_SECS//3600} hours")
    log.info(f"  Predictions: every {PREDICTIONS_INTERVAL_SECS//3600} hours")

    while True:
        try:
            tick()
        except KeyboardInterrupt:
            log.info("Scheduler stopped by user.")
            break
        except Exception as e:
            log.exception(f"Unexpected error in tick: {e}")
        time.sleep(60)  # check every minute


if __name__ == "__main__":
    main()
