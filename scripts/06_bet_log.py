"""
06_bet_log.py
─────────────
Track actual bets placed and compute P&L against resolved outcomes.

Usage:
    python scripts/06_bet_log.py log --market-id "abc123" --side YES --price 0.42 --stake 50
    python scripts/06_bet_log.py pnl
    python scripts/06_bet_log.py list
"""

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from market_screener import category_signals

DB_PATH = Path(__file__).parent.parent / "data" / "trump_tracker.duckdb"


def _safe_text(text, limit: int = 50) -> str:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        text = ""
    return str(text)[:limit].encode("ascii", errors="replace").decode("ascii")


def _display_question(row) -> str:
    question = row["question"]
    if question is None or (isinstance(question, float) and pd.isna(question)):
        return str(row["market_id"])
    return str(question)


def _lookup_market(con: duckdb.DuckDBPyConnection, market_id: str) -> dict:
    row = con.execute("""
        SELECT question, category
        FROM polymarket_snapshots
        WHERE id = ?
        ORDER BY snapshotted_at DESC
        LIMIT 1
    """, [market_id]).fetchone()
    if row:
        return {"question": row[0], "category": row[1]}
    return {"question": None, "category": None}


def cmd_log(args, con: duckdb.DuckDBPyConnection):
    side = args.side.upper()
    if side not in ("YES", "NO"):
        print("ERROR --side must be YES or NO", file=sys.stderr)
        sys.exit(1)
    if not (0.0 < args.price < 1.0):
        print("ERROR --price must be between 0 and 1", file=sys.stderr)
        sys.exit(1)

    market = _lookup_market(con, args.market_id)
    category = market["category"]

    signal_spike = None
    if category:
        signals = category_signals(con)
        if not signals.empty:
            match = signals[signals["category"] == category]
            if not match.empty:
                signal_spike = float(match.iloc[0]["spike_ratio"])

    bet_id = con.execute("SELECT nextval('bet_log_seq')").fetchone()[0]
    con.execute("""
        INSERT INTO bet_log (
            id, market_id, question, category, side, entry_price,
            stake_usd, signal_spike, signal_category, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        bet_id, args.market_id, market["question"], category,
        side, args.price, args.stake, signal_spike, category, args.notes,
    ])

    print(
        f"OK Logged bet #{bet_id}: {side} @ {args.price*100:.0f}% "
        f"for ${args.stake:,.2f} on {_safe_text(market['question'] or args.market_id, 60)}"
    )


def cmd_list(args, con: duckdb.DuckDBPyConnection):
    df = con.execute("""
        SELECT id, logged_at, market_id, question, category, side,
               entry_price, stake_usd, resolved_yes, pnl_usd
        FROM bet_log
        ORDER BY logged_at DESC
    """).df()

    if df.empty:
        print("No bets logged yet.")
        return

    print(f"\n{'-'*100}")
    print(f"{'ID':>4}  {'LOGGED':<19}  {'SIDE':<4}  {'PRICE':>6}  {'STAKE':>10}  {'RESULT':<8}  {'PNL':>10}  QUESTION")
    print(f"{'-'*100}")
    for _, row in df.iterrows():
        result = "pending"
        if row["resolved_yes"] is not None and not pd.isna(row["resolved_yes"]):
            result = "YES" if row["resolved_yes"] else "NO"
        pnl = "-"
        if row["pnl_usd"] is not None and not pd.isna(row["pnl_usd"]):
            pnl = f"${row['pnl_usd']:,.2f}"
        q = _safe_text(_display_question(row), 40)
        print(
            f"{row['id']:>4}  {str(row['logged_at'])[:19]:<19}  {row['side']:<4}  "
            f"{row['entry_price']*100:>5.0f}%  ${row['stake_usd']:>9,.2f}  {result:<8}  {pnl:>10}  {q}"
        )
    print(f"{'-'*100}\n")


def cmd_pnl(args, con: duckdb.DuckDBPyConnection):
    df = con.execute("""
        SELECT b.id, b.market_id, b.question, b.side, b.entry_price, b.stake_usd,
               o.resolved_yes
        FROM bet_log b
        LEFT JOIN market_outcomes o ON o.market_id = b.market_id
        ORDER BY b.logged_at
    """).df()

    if df.empty:
        print("No bets logged yet.")
        return

    print(f"\n{'-'*100}")
    print(f"{'ID':>4}  {'SIDE':<4}  {'PRICE':>6}  {'STAKE':>10}  {'RESULT':<8}  {'PNL':>10}  {'ROI':>7}  QUESTION")
    print(f"{'-'*100}")

    resolved_count = 0
    total_staked = 0.0
    total_pnl = 0.0

    for _, row in df.iterrows():
        resolved_yes = row["resolved_yes"]
        side = row["side"]
        price = float(row["entry_price"])
        stake = float(row["stake_usd"])
        q = _safe_text(_display_question(row), 40)

        if resolved_yes is None or pd.isna(resolved_yes):
            print(f"{row['id']:>4}  {side:<4}  {price*100:>5.0f}%  ${stake:>9,.2f}  {'pending':<8}  {'-':>10}  {'-':>7}  {q}")
            continue

        resolved_yes = bool(resolved_yes)
        if side == "YES":
            won = resolved_yes
            pnl = stake * (1 / price - 1) if won else -stake
        else:
            won = not resolved_yes
            pnl = stake * (1 / (1 - price) - 1) if won else -stake

        roi = pnl / stake * 100
        result = "YES" if resolved_yes else "NO"
        resolved_count += 1
        total_staked += stake
        total_pnl += pnl

        con.execute("UPDATE bet_log SET resolved_yes = ?, pnl_usd = ? WHERE id = ?",
                     [resolved_yes, pnl, row["id"]])

        print(f"{row['id']:>4}  {side:<4}  {price*100:>5.0f}%  ${stake:>9,.2f}  {result:<8}  ${pnl:>9,.2f}  {roi:>6.1f}%  {q}")

    print(f"{'-'*100}")
    if resolved_count:
        cum_roi = total_pnl / total_staked * 100 if total_staked else 0
        print(
            f"Resolved bets: {resolved_count}  |  Total staked: ${total_staked:,.2f}  |  "
            f"Total P&L: ${total_pnl:,.2f}  |  Cumulative ROI: {cum_roi:+.1f}%"
        )
    else:
        print("No resolved bets yet.")
    print(f"{'-'*100}\n")


def main():
    parser = argparse.ArgumentParser(description="Track bets placed and compute P&L")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    log_p = sub.add_parser("log", help="Log a new bet")
    log_p.add_argument("--market-id", required=True)
    log_p.add_argument("--side", required=True, help="YES or NO")
    log_p.add_argument("--price", required=True, type=float, help="Entry price (0-1)")
    log_p.add_argument("--stake", required=True, type=float, help="Stake in USD")
    log_p.add_argument("--notes", default=None)

    sub.add_parser("pnl", help="Compute P&L against resolved outcomes")
    sub.add_parser("list", help="List all logged bets")

    args = parser.parse_args()
    con = duckdb.connect(str(args.db))

    if args.command == "log":
        cmd_log(args, con)
    elif args.command == "pnl":
        cmd_pnl(args, con)
    elif args.command == "list":
        cmd_list(args, con)

    con.close()


if __name__ == "__main__":
    main()
