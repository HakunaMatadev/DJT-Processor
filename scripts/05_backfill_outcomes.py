"""
05_backfill_outcomes.py
───────────────────────
Fetches resolved Trump-related Polymarket markets and auto-populates
the market_outcomes table for backtesting.

Usage:
    python 05_backfill_outcomes.py
    python 05_backfill_outcomes.py --list
"""

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent))

from polymarket_client import fetch_markets, is_trump_related, parse_outcome, upsert_outcomes

DB_PATH = Path(__file__).parent.parent / "data" / "trump_tracker.duckdb"


def fetch_resolved_trump_markets() -> list[dict]:
    closed = fetch_markets(closed=True, order="volume")
    return [m for m in closed if is_trump_related(m) and m.get("closed")]


def _safe_text(text: str, limit: int = 48) -> str:
    return text[:limit].encode("ascii", errors="replace").decode("ascii")


def main():
    parser = argparse.ArgumentParser(description="Backfill resolved Polymarket outcomes")
    parser.add_argument("--list", action="store_true", help="Print resolved markets only")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    print("Fetching resolved Trump-related markets from Polymarket...")
    markets = fetch_resolved_trump_markets()
    print(f"  -> {len(markets)} closed Trump markets found")

    outcomes = [parse_outcome(m) for m in markets]
    outcomes = [o for o in outcomes if o is not None]
    print(f"  -> {len(outcomes)} markets with parseable resolutions")

    if args.list or not outcomes:
        print(f"\n{'-'*80}")
        print(f"{'QUESTION':<50}  {'YES':>4}  {'DATE':>12}  CATEGORY")
        print(f"{'-'*80}")
        for o in sorted(outcomes, key=lambda x: x["resolve_date"], reverse=True):
            q = _safe_text(o["question"], 48)
            yes = "Y" if o["resolved_yes"] else "N"
            print(f"{q:<50}  {yes:>4}  {str(o['resolve_date']):>12}  {o['category']}")
        print(f"{'-'*80}")
        if args.list:
            return

    if not outcomes:
        print("No outcomes to store.")
        return

    con = duckdb.connect(str(args.db))
    stored = upsert_outcomes(outcomes, con)
    total = con.execute("SELECT COUNT(*) FROM market_outcomes").fetchone()[0]
    print(f"  OK {stored:,} outcomes upserted ({total:,} total in DB)")
    con.close()


if __name__ == "__main__":
    main()
