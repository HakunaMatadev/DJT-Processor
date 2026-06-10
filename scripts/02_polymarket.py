"""
02_polymarket.py
────────────────
Fetches live Trump-related prediction market odds from Polymarket's
public Gamma API (no authentication required) and stores snapshots
in DuckDB for correlation analysis.

Usage:
    python 02_polymarket.py            # fetch + store
    python 02_polymarket.py --list     # print markets found, no DB write
"""

import argparse
import sys
from pathlib import Path

import duckdb

# Allow imports from scripts/ when run as a file
sys.path.insert(0, str(Path(__file__).parent))

from polymarket_client import fetch_trump_markets, parse_market, store_snapshots

DB_PATH = Path(__file__).parent.parent / "data" / "trump_tracker.duckdb"


def _safe_text(text: str, limit: int = 50) -> str:
    return text[:limit].encode("ascii", errors="replace").decode("ascii")


def print_markets(markets: list[dict], limit: int | None = 25):
    parsed = [parse_market(m) for m in markets]
    parsed = [r for r in parsed if r is not None]
    parsed.sort(key=lambda r: r["volume_24h"], reverse=True)
    shown = parsed if limit is None else parsed[:limit]
    print(f"\n{'-'*80}")
    print(f"{'QUESTION':<52}  {'YES%':>5}  {'24h VOL':>10}  CATEGORY")
    print(f"{'-'*80}")
    for r in shown:
        yes_pct = f"{r['yes_price']*100:.0f}%" if r["yes_price"] is not None else "  -  "
        vol = f"${r['volume_24h']:,.0f}"
        q = _safe_text(r["question"], 50)
        print(f"{q:<52}  {yes_pct:>5}  {vol:>10}  {r['category']}")
    print(f"{'-'*80}")
    if limit is not None and len(parsed) > limit:
        print(f"Showing top {limit} of {len(parsed)} markets (use --list for all)\n")
    else:
        print(f"Total: {len(parsed)} markets\n")


def main():
    parser = argparse.ArgumentParser(description="Fetch Polymarket Trump odds")
    parser.add_argument("--list", action="store_true", help="Print markets, skip DB write")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    print("Fetching Trump-related markets from Polymarket Gamma API...")
    markets = fetch_trump_markets()
    print(f"  -> {len(markets)} markets retrieved")

    print_markets(markets, limit=None if args.list else 25)

    if args.list or not markets:
        return

    con = duckdb.connect(str(args.db))
    df = store_snapshots(markets, con)

    if df is not None and not df.empty:
        print(f"  OK {len(df):,} market snapshots stored")
        print("\nBy category (avg YES probability):")
        summary = df.groupby("category")["yes_price"].mean().sort_values(ascending=False)
        for cat, pct in summary.items():
            print(f"  {cat:<30}  {pct*100:.0f}%")

    con.close()


if __name__ == "__main__":
    main()
