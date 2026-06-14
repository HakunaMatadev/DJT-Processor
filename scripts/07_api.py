"""
07_api.py
─────────
Local Flask API that exposes DuckDB query results to the React dashboard.
Run with: python scripts/07_api.py
Then set REACT_APP_API_URL=http://localhost:5001 in .env
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, jsonify, request
from flask_cors import CORS
import duckdb
import pandas as pd

from market_screener import score_markets, category_signals
from polymarket_client import fetch_trump_markets, parse_market, store_snapshots
from keyword_odds import keyword_stats, keyword_weekly_series, matching_markets
import importlib
score_predictions = importlib.import_module("08_score_predictions")

DB_PATH = Path(__file__).parent.parent / "data" / "trump_tracker.duckdb"
OPPS_PATH = Path(__file__).parent.parent / "data" / "market_opportunities.csv"

app = Flask(__name__)
CORS(app)  # allow requests from React dev server


def get_con():
    return duckdb.connect(str(DB_PATH), read_only=True)


def get_writable_con():
    return duckdb.connect(str(DB_PATH))


@app.route("/api/health")
def health():
    con = get_con()
    posts = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    snapshots = con.execute("SELECT COUNT(*) FROM polymarket_snapshots").fetchone()[0]
    con.close()
    return jsonify({"status": "ok", "posts": posts, "pm_snapshots": snapshots,
                    "timestamp": datetime.now(timezone.utc).isoformat()})


@app.route("/api/opportunities")
def opportunities():
    """Ranked betting opportunities from last analysis run."""
    if OPPS_PATH.exists():
        df = pd.read_csv(OPPS_PATH)
        # Ensure polymarket_url column exists
        if "polymarket_url" not in df.columns:
            df["polymarket_url"] = ""
        return jsonify(df.fillna("").to_dict("records"))
    # Fall back to live computation if CSV doesn't exist
    con = duckdb.connect(str(DB_PATH), read_only=True)
    opps = score_markets(con)
    con.close()
    return jsonify(opps.fillna("").to_dict("records") if not opps.empty else [])


@app.route("/api/spikes")
def spikes():
    """Current keyword spike ratios."""
    con = get_con()
    signals = category_signals(con)
    con.close()
    return jsonify(signals.fillna(0).to_dict("records"))


@app.route("/api/velocity")
def velocity():
    """Weekly keyword velocity per category (last 26 weeks)."""
    con = get_con()
    df = con.execute("""
        SELECT
            DATE_TRUNC('week', created_at)::DATE AS week,
            category,
            COUNT(DISTINCT post_id) AS posts_mentioning
        FROM keyword_hits
        GROUP BY 1, 2
        ORDER BY 1, 2
    """).df()
    con.close()
    if df.empty:
        return jsonify([])
    pivot = df.pivot_table(index="week", columns="category",
                           values="posts_mentioning", aggfunc="sum").fillna(0)
    pivot.index = pivot.index.astype(str)
    records = pivot.reset_index().tail(26).to_dict("records")
    return jsonify(records)


@app.route("/api/heatmap")
def heatmap():
    """Posting frequency by hour and day of week."""
    con = get_con()
    df = con.execute("""
        SELECT hour_of_day, day_of_week, COUNT(*) AS post_count
        FROM posts
        WHERE hour_of_day IS NOT NULL AND day_of_week IS NOT NULL
        GROUP BY 1, 2
    """).df()
    con.close()
    return jsonify(df.to_dict("records"))


@app.route("/api/agitation")
def agitation():
    """Weekly caps ratio and exclamation counts."""
    con = get_con()
    df = con.execute("""
        SELECT
            DATE_TRUNC('week', created_at)::DATE AS week,
            AVG(caps_ratio) * 100 AS avg_caps_pct,
            AVG(exclamation_count) AS avg_excl,
            COUNT(*) AS post_count
        FROM posts
        GROUP BY 1
        ORDER BY 1
    """).df()
    con.close()
    df["week"] = df["week"].astype(str)
    return jsonify(df.tail(26).to_dict("records"))


@app.route("/api/polymarket")
def polymarket_live():
    """Live Polymarket snapshot — fetches fresh data on each call."""
    try:
        markets = fetch_trump_markets()
        parsed = [parse_market(m) for m in markets]
        parsed = [p for p in parsed if p is not None]
        # Convert dates to strings
        for p in parsed:
            if p.get("end_date"):
                p["end_date"] = str(p["end_date"])
            if p.get("snapshotted_at"):
                p["snapshotted_at"] = p["snapshotted_at"].isoformat()
        return jsonify(parsed)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/keyword")
def keyword_odds():
    """Frequency stats, weekly history, and live 'Will Trump say ...' market odds for a search term."""
    term = (request.args.get("q") or "").strip()
    if not term:
        return jsonify({"error": "missing query parameter 'q'"}), 400

    con = get_con()
    try:
        stats = keyword_stats(con, term)
        weekly = keyword_weekly_series(con, term)
        markets = matching_markets(con, term, stats=stats)
    finally:
        con.close()

    return jsonify({
        "term": stats["term"],
        "total_mentions": stats["total_mentions"],
        "first_seen": stats["first_seen"].isoformat() if stats["first_seen"] else None,
        "last_seen": stats["last_seen"].isoformat() if stats["last_seen"] else None,
        "days_since_last": stats["days_since_last"],
        "windows": stats["windows"],
        "primary_rate": stats["primary_rate"],
        "primary_source": stats["primary_source"],
        "probabilities": stats["probabilities"],
        "weekly_series": weekly.to_dict("records") if not weekly.empty else [],
        "markets": markets,
    })


@app.route("/api/keyword/track", methods=["POST"])
def keyword_track():
    """Log a Keyword Odds prediction ('Will Trump say X within N days?') to score later."""
    body = request.get_json(silent=True) or {}
    term = (body.get("term") or "").strip()
    days = body.get("days", 30)
    if not term:
        return jsonify({"error": "missing 'term'"}), 400
    try:
        days = int(days)
    except (TypeError, ValueError):
        return jsonify({"error": "'days' must be an integer"}), 400

    con = get_writable_con()
    try:
        result = score_predictions.log_keyword_prediction(con, term, window_days=days)
    finally:
        con.close()

    return jsonify({
        "id": result["id"],
        "pred_type": result["pred_type"],
        "subject": result["subject"],
        "question": result["question"],
        "predicted_prob": result["predicted_prob"],
        "market_price": result["market_price"],
        "polymarket_url": result["polymarket_url"],
        "check_after": result["check_after"].isoformat(),
    })


@app.route("/api/predictions")
def predictions_list():
    """List logged predictions, optionally filtered by ?type= and ?pending=true."""
    pred_type = request.args.get("type")
    pending = request.args.get("pending", "").lower() in ("1", "true", "yes")

    sql = """
        SELECT id, pred_type, subject, question, category, predicted_prob,
               market_price, polymarket_url, logged_at, window_start,
               check_after, resolved_at, actual_outcome
        FROM predictions
    """
    conditions = []
    params = []
    if pending:
        conditions.append("actual_outcome IS NULL")
    if pred_type:
        conditions.append("pred_type = ?")
        params.append(pred_type)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY logged_at DESC"

    con = get_con()
    try:
        df = con.execute(sql, params).df()
    finally:
        con.close()

    if df.empty:
        return jsonify([])

    for col in ("logged_at", "window_start", "check_after", "resolved_at"):
        df[col] = df[col].astype(str).replace("NaT", None)

    df = df.astype(object).where(pd.notna(df), None)
    return jsonify(df.to_dict("records"))


@app.route("/api/predictions/calibration")
def predictions_calibration():
    """Brier score + probability-bucket calibration table, optionally filtered by ?type=."""
    pred_type = request.args.get("type")

    con = get_con()
    try:
        report = score_predictions.calibration_report(con, pred_type=pred_type)
    finally:
        con.close()

    return jsonify(report)


@app.route("/api/stats")
def stats():
    """Summary stats for the header cards."""
    con = get_con()
    posts_total = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    ts_count = con.execute("SELECT COUNT(*) FROM posts WHERE source='truth_social'").fetchone()[0]
    tw_count = con.execute("SELECT COUNT(*) FROM posts WHERE source='twitter'").fetchone()[0]
    pm_count = con.execute("SELECT COUNT(*) FROM polymarket_snapshots").fetchone()[0]
    outcomes_count = con.execute("SELECT COUNT(*) FROM market_outcomes").fetchone()[0]
    con.close()
    return jsonify({
        "posts_total": posts_total,
        "truth_social": ts_count,
        "twitter": tw_count,
        "pm_snapshots": pm_count,
        "resolved_outcomes": outcomes_count,
    })


if __name__ == "__main__":
    print("Trump Tracker API running on http://localhost:5001")
    print("Endpoints: /api/health /api/opportunities /api/spikes /api/velocity /api/heatmap /api/agitation /api/polymarket /api/keyword /api/keyword/track /api/predictions /api/predictions/calibration /api/stats")
    app.run(host="0.0.0.0", port=5001, debug=True)
