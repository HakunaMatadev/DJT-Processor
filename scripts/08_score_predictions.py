"""
08_score_predictions.py
────────────────────────
Closes the loop on the Keyword Odds and Best Bets models: log a prediction
with its modeled probability and a "check after" date, then later check it
against ground truth (did the term get mentioned? did the market resolve
YES?) and report calibration (Brier score + probability-bucket table).

Usage:
    python scripts/08_score_predictions.py track "tariff" --days 30
    python scripts/08_score_predictions.py log-bestbets
    python scripts/08_score_predictions.py resolve
    python scripts/08_score_predictions.py report [--type keyword_odds|best_bet]
    python scripts/08_score_predictions.py list [--pending] [--type keyword_odds|best_bet]
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from keyword_odds import keyword_stats, probability_for_days, matching_markets, build_pattern

DB_PATH = Path(__file__).parent.parent / "data" / "trump_tracker.duckdb"
OPPS_PATH = Path(__file__).parent.parent / "data" / "market_opportunities.csv"

PREDICTIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY,
    pred_type       TEXT NOT NULL,     -- 'keyword_odds' | 'best_bet'
    subject         TEXT NOT NULL,     -- keyword term, or Polymarket market_id
    question        TEXT,              -- human-readable question text
    category        TEXT,
    predicted_prob  FLOAT NOT NULL,    -- our model's probability estimate (0-1)
    market_price    FLOAT,             -- market yes_price at time of prediction, if any
    polymarket_url  TEXT,
    logged_at       TIMESTAMPTZ DEFAULT NOW(),
    window_start    TIMESTAMPTZ,       -- start of the period being predicted (keyword_odds)
    check_after     TIMESTAMPTZ NOT NULL,  -- earliest time the outcome can be checked
    resolved_at     TIMESTAMPTZ,
    actual_outcome  BOOLEAN,           -- TRUE/FALSE once resolved, NULL = pending
    notes           TEXT
);
CREATE SEQUENCE IF NOT EXISTS predictions_seq;
"""


def _safe_text(text, limit: int = 50) -> str:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        text = ""
    return str(text)[:limit].encode("ascii", errors="replace").decode("ascii")


def ensure_predictions_table(con: duckdb.DuckDBPyConnection):
    """Idempotent — safe to call before every read/write."""
    con.execute(PREDICTIONS_SCHEMA_SQL)


# ── Logging ──────────────────────────────────────────────────────────────────

def log_keyword_prediction(con: duckdb.DuckDBPyConnection, term: str, window_days: int = 30, now=None) -> dict:
    """
    Snapshot the current Keyword Odds estimate for `term` over `window_days`
    as a checkable prediction: "Will Trump say <term> within N days?"
    """
    now = now or datetime.now(timezone.utc)
    ensure_predictions_table(con)

    stats = keyword_stats(con, term, now=now)
    predicted_prob = probability_for_days(stats["primary_rate"], window_days)

    # If a live "Will Trump say ..." market exists for this term, record its
    # current price as a reference point (best-edge match, may not share the
    # exact same horizon).
    markets = matching_markets(con, term, stats=stats, now=now)
    market_price = None
    polymarket_url = None
    for m in markets:
        if m["yes_price"] is not None:
            market_price = m["yes_price"]
            polymarket_url = m["polymarket_url"]
            break

    check_after = now + timedelta(days=window_days)
    question = f'Will Trump say "{term}" within {window_days} days?'

    pred_id = con.execute("SELECT nextval('predictions_seq')").fetchone()[0]
    con.execute("""
        INSERT INTO predictions (
            id, pred_type, subject, question, category, predicted_prob,
            market_price, polymarket_url, logged_at, window_start, check_after
        ) VALUES (?, 'keyword_odds', ?, ?, NULL, ?, ?, ?, ?, ?, ?)
    """, [pred_id, term, question, predicted_prob, market_price, polymarket_url, now, now, check_after])

    return {
        "id": pred_id,
        "pred_type": "keyword_odds",
        "subject": term,
        "question": question,
        "predicted_prob": predicted_prob,
        "market_price": market_price,
        "polymarket_url": polymarket_url,
        "check_after": check_after,
    }


def log_best_bet_predictions(con: duckdb.DuckDBPyConnection, opportunities_df: pd.DataFrame | None = None, now=None) -> list[dict]:
    """
    Snapshot current Best Bets opportunities (data/market_opportunities.csv)
    as predictions, one per market_id, skipping any market_id already logged.
    """
    now = now or datetime.now(timezone.utc)
    ensure_predictions_table(con)

    if opportunities_df is None:
        if not OPPS_PATH.exists():
            return []
        opportunities_df = pd.read_csv(OPPS_PATH)

    if opportunities_df.empty:
        return []

    existing = {
        row[0] for row in con.execute(
            "SELECT subject FROM predictions WHERE pred_type = 'best_bet'"
        ).fetchall()
    }

    logged = []
    for _, row in opportunities_df.iterrows():
        market_id = str(row["market_id"])
        if market_id in existing:
            continue
        if pd.isna(row.get("implied_yes")) or pd.isna(row.get("days_to_end")):
            continue

        days_to_end = float(row["days_to_end"])
        if days_to_end <= 0:
            continue

        check_after = now + timedelta(days=days_to_end)
        market_price = float(row["yes_price"]) if pd.notna(row.get("yes_price")) else None

        pred_id = con.execute("SELECT nextval('predictions_seq')").fetchone()[0]
        con.execute("""
            INSERT INTO predictions (
                id, pred_type, subject, question, category, predicted_prob,
                market_price, polymarket_url, logged_at, check_after
            ) VALUES (?, 'best_bet', ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            pred_id, market_id, row.get("question"), row.get("category"),
            float(row["implied_yes"]), market_price, row.get("polymarket_url"),
            now, check_after,
        ])
        logged.append({"id": pred_id, "subject": market_id, "question": row.get("question")})
        existing.add(market_id)

    return logged


# ── Resolution ───────────────────────────────────────────────────────────────

def resolve_keyword_predictions(con: duckdb.DuckDBPyConnection, now=None) -> list[dict]:
    """
    For pending keyword_odds predictions whose check_after has passed, check
    whether the term was actually mentioned between window_start and
    check_after.
    """
    now = now or datetime.now(timezone.utc)
    ensure_predictions_table(con)

    pending = con.execute("""
        SELECT id, subject, window_start, check_after
        FROM predictions
        WHERE pred_type = 'keyword_odds' AND actual_outcome IS NULL AND check_after <= ?
    """, [now]).df()

    resolved = []
    for _, row in pending.iterrows():
        term = row["subject"]
        pattern = build_pattern(term)
        cnt = con.execute("""
            SELECT COUNT(*) FROM posts
            WHERE regexp_matches(content_clean, ?, 'i')
              AND created_at >= ? AND created_at <= ?
        """, [pattern, row["window_start"], row["check_after"]]).fetchone()[0]

        outcome = cnt > 0
        con.execute("""
            UPDATE predictions SET resolved_at = ?, actual_outcome = ?
            WHERE id = ?
        """, [now, outcome, row["id"]])
        resolved.append({"id": int(row["id"]), "subject": term, "actual_outcome": outcome})

    return resolved


def resolve_best_bet_predictions(con: duckdb.DuckDBPyConnection, now=None) -> list[dict]:
    """
    For pending best_bet predictions whose check_after has passed, look up
    the resolved outcome in market_outcomes (populated by
    05_backfill_outcomes.py). Left pending if no outcome is recorded yet.
    """
    now = now or datetime.now(timezone.utc)
    ensure_predictions_table(con)

    pending = con.execute("""
        SELECT id, subject
        FROM predictions
        WHERE pred_type = 'best_bet' AND actual_outcome IS NULL AND check_after <= ?
    """, [now]).df()

    resolved = []
    for _, row in pending.iterrows():
        outcome_row = con.execute(
            "SELECT resolved_yes FROM market_outcomes WHERE market_id = ?", [row["subject"]]
        ).fetchone()

        if outcome_row is None or outcome_row[0] is None:
            continue  # market hasn't resolved (or hasn't been backfilled) yet

        outcome = bool(outcome_row[0])
        con.execute("""
            UPDATE predictions SET resolved_at = ?, actual_outcome = ?
            WHERE id = ?
        """, [now, outcome, row["id"]])
        resolved.append({"id": int(row["id"]), "subject": row["subject"], "actual_outcome": outcome})

    return resolved


# ── Calibration report ────────────────────────────────────────────────────────

BUCKET_EDGES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]


def calibration_report(con: duckdb.DuckDBPyConnection, pred_type: str | None = None) -> dict:
    """
    Brier score + probability-bucket calibration table over all resolved
    predictions (optionally filtered by pred_type). Read-only — assumes the
    predictions table already exists.
    """
    sql = "SELECT predicted_prob, actual_outcome FROM predictions WHERE actual_outcome IS NOT NULL"
    params = []
    if pred_type:
        sql += " AND pred_type = ?"
        params.append(pred_type)

    df = con.execute(sql, params).df()

    if df.empty:
        return {"pred_type": pred_type, "n_resolved": 0, "brier_score": None, "buckets": []}

    df["actual_int"] = df["actual_outcome"].astype(int)
    brier = float(((df["predicted_prob"] - df["actual_int"]) ** 2).mean())

    buckets = []
    for i in range(len(BUCKET_EDGES) - 1):
        lo, hi = BUCKET_EDGES[i], BUCKET_EDGES[i + 1]
        sub = df[(df["predicted_prob"] >= lo) & (df["predicted_prob"] < hi)]
        if sub.empty:
            continue
        buckets.append({
            "range": f"{lo * 100:.0f}-{min(hi, 1.0) * 100:.0f}%",
            "n": int(len(sub)),
            "avg_predicted": float(sub["predicted_prob"].mean()),
            "actual_rate": float(sub["actual_int"].mean()),
        })

    return {"pred_type": pred_type, "n_resolved": int(len(df)), "brier_score": brier, "buckets": buckets}


# ── CLI ──────────────────────────────────────────────────────────────────────

def cmd_track(args, con):
    result = log_keyword_prediction(con, args.term, window_days=args.days)
    market_str = ""
    if result["market_price"] is not None:
        market_str = f"  market price: {result['market_price'] * 100:.0f}%"
    print(
        f"OK Logged prediction #{result['id']}: {_safe_text(result['question'], 70)} "
        f"-> our estimate {result['predicted_prob'] * 100:.1f}%{market_str}"
    )
    print(f"   Will check after {result['check_after'].strftime('%Y-%m-%d')}")


def cmd_log_bestbets(args, con):
    logged = log_best_bet_predictions(con)
    if not logged:
        print("No new Best Bet predictions to log (no opportunities file, or all already logged).")
        return
    print(f"OK Logged {len(logged)} new Best Bet prediction(s):")
    for item in logged:
        print(f"  #{item['id']}  {_safe_text(item['question'] or item['subject'], 70)}")


def cmd_resolve(args, con):
    kw_resolved = resolve_keyword_predictions(con)
    bb_resolved = resolve_best_bet_predictions(con)
    total = len(kw_resolved) + len(bb_resolved)
    if total == 0:
        print("No predictions ready to resolve.")
        return
    print(f"OK Resolved {total} prediction(s):")
    for item in kw_resolved + bb_resolved:
        outcome = "YES" if item["actual_outcome"] else "NO"
        print(f"  #{item['id']}  {_safe_text(item['subject'], 60)}  -> {outcome}")


def cmd_report(args, con):
    report = calibration_report(con, pred_type=args.type)
    label = args.type or "all types"

    print(f"\n{'=' * 60}")
    print(f"  CALIBRATION REPORT ({label})")
    print(f"{'=' * 60}")

    if report["n_resolved"] == 0:
        print("\nNo resolved predictions yet. Run `resolve` after some")
        print("predictions' check-after dates have passed.")
        print(f"{'=' * 60}\n")
        return

    print(f"\nResolved predictions: {report['n_resolved']}")
    print(f"Brier score: {report['brier_score']:.4f}  (0 = perfect, 0.25 = always guessing 50%)")

    print(f"\n{'Predicted':>12}  {'Actual':>8}  {'N':>5}")
    print(f"{'-' * 30}")
    for b in report["buckets"]:
        print(f"{b['avg_predicted'] * 100:>11.1f}%  {b['actual_rate'] * 100:>7.1f}%  {b['n']:>5}")
    print(f"\n{'=' * 60}\n")


def cmd_list(args, con):
    sql = """
        SELECT id, pred_type, subject, question, predicted_prob, market_price,
               logged_at, check_after, actual_outcome
        FROM predictions
    """
    conditions = []
    params = []
    if args.pending:
        conditions.append("actual_outcome IS NULL")
    if args.type:
        conditions.append("pred_type = ?")
        params.append(args.type)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY logged_at DESC"

    df = con.execute(sql, params).df()
    if df.empty:
        print("No predictions logged yet.")
        return

    print(f"\n{'-' * 100}")
    print(f"{'ID':>4}  {'TYPE':<12}  {'PRED':>6}  {'MKT':>6}  {'STATUS':<8}  {'CHECK AFTER':<12}  SUBJECT/QUESTION")
    print(f"{'-' * 100}")
    for _, row in df.iterrows():
        status = "pending"
        if row["actual_outcome"] is not None and not pd.isna(row["actual_outcome"]):
            status = "YES" if row["actual_outcome"] else "NO"
        mkt = f"{row['market_price'] * 100:.0f}%" if pd.notna(row["market_price"]) else "-"
        label = row["question"] if pd.notna(row["question"]) and row["question"] else row["subject"]
        print(
            f"{row['id']:>4}  {row['pred_type']:<12}  {row['predicted_prob'] * 100:>5.0f}%  {mkt:>6}  "
            f"{status:<8}  {str(row['check_after'])[:10]:<12}  {_safe_text(label, 50)}"
        )
    print(f"{'-' * 100}\n")


def main():
    parser = argparse.ArgumentParser(description="Log and score predictions for calibration tracking")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    track_p = sub.add_parser("track", help="Log a Keyword Odds prediction to check later")
    track_p.add_argument("term", help='Word or phrase, e.g. "tariff"')
    track_p.add_argument("--days", type=int, default=30, help="Horizon in days (default 30)")

    sub.add_parser("log-bestbets", help="Snapshot current Best Bets opportunities as predictions")
    sub.add_parser("resolve", help="Check pending predictions past their check-after date against ground truth")

    report_p = sub.add_parser("report", help="Print a calibration report (Brier score + buckets)")
    report_p.add_argument("--type", choices=["keyword_odds", "best_bet"], default=None)

    list_p = sub.add_parser("list", help="List logged predictions")
    list_p.add_argument("--pending", action="store_true", help="Only show unresolved predictions")
    list_p.add_argument("--type", choices=["keyword_odds", "best_bet"], default=None)

    args = parser.parse_args()
    con = duckdb.connect(str(args.db))
    ensure_predictions_table(con)

    try:
        if args.command == "track":
            cmd_track(args, con)
        elif args.command == "log-bestbets":
            cmd_log_bestbets(args, con)
        elif args.command == "resolve":
            cmd_resolve(args, con)
        elif args.command == "report":
            cmd_report(args, con)
        elif args.command == "list":
            cmd_list(args, con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
