"""
03_analyze.py
─────────────
Runs keyword velocity analysis, spike detection, and correlates
Trump post signals with Polymarket odds movements.

Outputs:
  - data/keyword_velocity.parquet   (weekly keyword counts per category)
  - data/spike_alerts.csv           (current elevated keyword categories)
  - data/signal_vs_odds.parquet     (category signal vs. market YES price over time)

Usage:
    python 03_analyze.py
    python 03_analyze.py --output-dir ./reports
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from market_screener import print_opportunities, score_markets

DB_PATH = Path(__file__).parent.parent / "data" / "trump_tracker.duckdb"
OUTPUT_DIR = Path(__file__).parent.parent / "data"


# ── Keyword velocity ─────────────────────────────────────────────────────────

def compute_keyword_velocity(con: duckdb.DuckDBPyConnection, freq: str = "W") -> pd.DataFrame:
    """
    Returns a DataFrame: index = week, columns = category,
    values = number of unique posts mentioning that category.
    """
    df = con.execute("""
        SELECT
            DATE_TRUNC('week', created_at)  AS week,
            category,
            COUNT(DISTINCT post_id)         AS posts_mentioning
        FROM keyword_hits
        GROUP BY 1, 2
        ORDER BY 1, 2
    """).df()

    if df.empty:
        return df

    pivot = df.pivot_table(
        index="week", columns="category",
        values="posts_mentioning", aggfunc="sum"
    ).fillna(0)

    pivot.index = pd.to_datetime(pivot.index)
    return pivot


def spike_ratio(velocity: pd.DataFrame, lookback_weeks: int = 2,
                baseline_weeks: int = 8) -> pd.Series:
    """
    For each category, compute: recent_rate / baseline_rate.
    Values > 2.0 signal elevated activity.
    """
    if velocity.empty or len(velocity) < lookback_weeks + 1:
        return pd.Series(dtype=float)

    recent = velocity.iloc[-lookback_weeks:].mean()
    baseline = velocity.iloc[-(lookback_weeks + baseline_weeks):-lookback_weeks].mean()
    ratio = recent / (baseline + 1e-6)
    return ratio.sort_values(ascending=False)


# ── Post-level features ───────────────────────────────────────────────────────

def daily_stats(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    Daily aggregate stats: post count, avg word count, avg caps ratio,
    avg exclamation count, posts per source.
    """
    return con.execute("""
        SELECT
            DATE_TRUNC('day', created_at)::DATE  AS date,
            source,
            COUNT(*)                              AS post_count,
            AVG(word_count)                       AS avg_word_count,
            AVG(caps_ratio)                       AS avg_caps_ratio,
            AVG(exclamation_count)                AS avg_exclamations,
            SUM(CASE WHEN is_repost THEN 1 ELSE 0 END) AS repost_count
        FROM posts
        GROUP BY 1, 2
        ORDER BY 1
    """).df()


def keyword_monthly(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        SELECT
            DATE_TRUNC('month', created_at)::DATE AS month,
            category,
            COUNT(DISTINCT post_id)               AS posts_mentioning,
            COUNT(*)                              AS total_hits
        FROM keyword_hits
        GROUP BY 1, 2
        ORDER BY 1, 2
    """).df()


def top_posts_for_category(con: duckdb.DuckDBPyConnection,
                           category: str, n: int = 10) -> pd.DataFrame:
    return con.execute("""
        SELECT
            p.created_at,
            p.source,
            p.content_clean,
            p.reblogs_count,
            p.favourites_count,
            kh.keyword,
            kh.context
        FROM keyword_hits kh
        JOIN posts p ON p.id = kh.post_id
        WHERE kh.category = ?
        ORDER BY p.favourites_count DESC
        LIMIT ?
    """, [category, n]).df()


# ── Polymarket odds movement ──────────────────────────────────────────────────

def compute_odds_movements(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    For each market, compute current YES price and 7-day / 30-day changes
    using historical snapshots stored in polymarket_snapshots.
    """
    return con.execute("""
        WITH latest AS (
            SELECT
                id,
                question,
                category,
                yes_price AS current_yes_price,
                volume_24h AS current_volume_24h,
                end_date,
                polymarket_url,
                snapshotted_at AS latest_snapshot
            FROM polymarket_snapshots
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY id ORDER BY snapshotted_at DESC
            ) = 1
        ),
        price_7d AS (
            SELECT
                id,
                yes_price AS yes_price_7d_ago
            FROM polymarket_snapshots
            WHERE snapshotted_at <= CURRENT_TIMESTAMP - INTERVAL 7 DAY
              AND yes_price IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY id ORDER BY snapshotted_at DESC
            ) = 1
        ),
        price_30d AS (
            SELECT
                id,
                yes_price AS yes_price_30d_ago
            FROM polymarket_snapshots
            WHERE snapshotted_at <= CURRENT_TIMESTAMP - INTERVAL 30 DAY
              AND yes_price IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY id ORDER BY snapshotted_at DESC
            ) = 1
        )
        SELECT
            l.id AS market_id,
            l.question,
            l.category,
            l.current_yes_price,
            p7.yes_price_7d_ago,
            p30.yes_price_30d_ago,
            l.current_yes_price - p7.yes_price_7d_ago AS change_7d,
            l.current_yes_price - p30.yes_price_30d_ago AS change_30d,
            l.current_volume_24h,
            l.end_date,
            l.polymarket_url,
            l.latest_snapshot
        FROM latest l
        LEFT JOIN price_7d p7 ON l.id = p7.id
        LEFT JOIN price_30d p30 ON l.id = p30.id
        WHERE l.current_yes_price IS NOT NULL
        ORDER BY ABS(l.current_yes_price - COALESCE(p7.yes_price_7d_ago, l.current_yes_price)) DESC
    """).df()


# ── Signal vs. Polymarket correlation ────────────────────────────────────────

def signal_vs_odds(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    For each (week, category): keyword velocity + avg YES price from
    Polymarket snapshots taken that week.
    """
    # Keyword velocity per week/category
    kv = con.execute("""
        SELECT
            DATE_TRUNC('week', created_at)::DATE AS week,
            category,
            COUNT(DISTINCT post_id)              AS keyword_posts
        FROM keyword_hits
        GROUP BY 1, 2
    """).df()

    # Polymarket snapshot avg per week/category
    pm = con.execute("""
        SELECT
            DATE_TRUNC('week', snapshotted_at)::DATE AS week,
            category,
            AVG(yes_price)                           AS avg_yes_price,
            AVG(volume_24h)                          AS avg_volume_24h
        FROM polymarket_snapshots
        WHERE yes_price IS NOT NULL
        GROUP BY 1, 2
    """).df()

    if pm.empty:
        return kv  # return just keyword data if no Polymarket data yet

    merged = pd.merge(kv, pm, on=["week", "category"], how="outer")
    merged = merged.sort_values(["category", "week"])
    return merged


# ── Spike alerts ──────────────────────────────────────────────────────────────

def generate_spike_alerts(spike: pd.Series, threshold: float = 1.8) -> pd.DataFrame:
    """Return categories with spike ratio above threshold + context."""
    elevated = spike[spike >= threshold].reset_index()
    elevated.columns = ["category", "spike_ratio"]
    elevated["alert_level"] = elevated["spike_ratio"].apply(
        lambda r: "HIGH" if r >= 3.0 else ("ELEVATED" if r >= 2.0 else "WATCH")
    )
    elevated["generated_at"] = datetime.now(timezone.utc)
    return elevated.sort_values("spike_ratio", ascending=False)


# ── Posting time heatmap data ─────────────────────────────────────────────────

def posting_heatmap(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Returns (hour_of_day, day_of_week, count) for heatmap rendering."""
    return con.execute("""
        SELECT
            hour_of_day,
            day_of_week,
            COUNT(*) AS post_count
        FROM posts
        WHERE hour_of_day IS NOT NULL AND day_of_week IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 2, 1
    """).df()


# ── Prediction feature matrix ─────────────────────────────────────────────────

def build_prediction_features(con: duckdb.DuckDBPyConnection,
                               window_days: int = 14) -> pd.DataFrame:
    """
    For each resolved Polymarket market (if any in market_outcomes table),
    compute pre-resolution features for model training.
    """
    outcomes = con.execute("SELECT * FROM market_outcomes").df()
    if outcomes.empty:
        print("  No resolved market outcomes stored yet. Add them via the UI.")
        return pd.DataFrame()

    feature_rows = []
    for _, market in outcomes.iterrows():
        resolve_date = pd.to_datetime(market["resolve_date"])
        window_start = resolve_date - timedelta(days=window_days)
        baseline_start = resolve_date - timedelta(days=window_days + 30)
        cat = market["category"]

        # Keyword velocity in window vs baseline
        window_count = con.execute("""
            SELECT COUNT(DISTINCT post_id) FROM keyword_hits
            WHERE category = ?
            AND created_at BETWEEN ? AND ?
        """, [cat, window_start, resolve_date]).fetchone()[0]

        baseline_count = con.execute("""
            SELECT COUNT(DISTINCT post_id) FROM keyword_hits
            WHERE category = ?
            AND created_at BETWEEN ? AND ?
        """, [cat, baseline_start, window_start]).fetchone()[0]

        spike = window_count / max(baseline_count / 30 * window_days, 1)

        # Avg caps ratio in window posts mentioning this category
        caps = con.execute("""
            SELECT AVG(p.caps_ratio)
            FROM posts p
            JOIN keyword_hits kh ON kh.post_id = p.id
            WHERE kh.category = ?
            AND p.created_at BETWEEN ? AND ?
        """, [cat, window_start, resolve_date]).fetchone()[0] or 0

        # Post frequency change
        window_posts = con.execute("""
            SELECT COUNT(*) FROM posts
            WHERE created_at BETWEEN ? AND ?
        """, [window_start, resolve_date]).fetchone()[0]

        baseline_posts = con.execute("""
            SELECT COUNT(*) FROM posts
            WHERE created_at BETWEEN ? AND ?
        """, [baseline_start, window_start]).fetchone()[0]

        freq_ratio = window_posts / max(baseline_posts / 30 * window_days, 1)

        # Latest Polymarket YES price before resolve
        pm_price = con.execute("""
            SELECT yes_price FROM polymarket_snapshots
            WHERE category = ?
            AND snapshotted_at <= ?
            ORDER BY snapshotted_at DESC
            LIMIT 1
        """, [cat, resolve_date]).fetchone()
        pm_price = pm_price[0] if pm_price else None

        feature_rows.append({
            "market_id":             market["market_id"],
            "question":              market["question"],
            "category":              cat,
            "resolve_date":          resolve_date,
            "resolved_yes":          market["resolved_yes"],
            f"keyword_spike_{window_days}d": spike,
            "avg_caps_ratio":        caps,
            "post_freq_ratio":       freq_ratio,
            "last_pm_yes_price":     pm_price,
            "days_to_resolve":       window_days,
        })

    return pd.DataFrame(feature_rows)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run Trump signal analysis")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(args.db))

    print("=" * 60)
    print("  TRUMP SIGNAL ANALYZER")
    print("=" * 60)

    # 1. Keyword velocity
    print("\n[1/7] Computing keyword velocity...")
    velocity = compute_keyword_velocity(con)
    if not velocity.empty:
        velocity.to_parquet(args.output_dir / "keyword_velocity.parquet")
        print(f"  -> Saved keyword_velocity.parquet ({len(velocity)} weeks)")

    # 2. Spike detection
    print("\n[2/7] Detecting spikes...")
    if not velocity.empty:
        spikes = spike_ratio(velocity)
        alerts = generate_spike_alerts(spikes)
        if not alerts.empty:
            alerts.to_csv(args.output_dir / "spike_alerts.csv", index=False)
            print(f"\n  Current spike alerts:")
            for _, row in alerts.iterrows():
                print(f"    {row['alert_level']}  {row['category']:<30}  {row['spike_ratio']:.1f}x")
        else:
            print("  No spikes above threshold currently.")

    # 3. Signal vs. Polymarket odds
    print("\n[3/7] Computing Polymarket odds movements...")
    odds_moves = compute_odds_movements(con)
    if not odds_moves.empty:
        odds_moves.to_parquet(args.output_dir / "polymarket_odds_movement.parquet")
        print(f"  -> Saved polymarket_odds_movement.parquet ({len(odds_moves)} markets)")
        moving = odds_moves.dropna(subset=["change_7d"]).head(5)
        if not moving.empty:
            print("  Top 7-day movers:")
            for _, row in moving.iterrows():
                chg = row["change_7d"]
                sign = "+" if chg >= 0 else ""
                print(f"    {row['category']:<22}  {sign}{chg*100:.1f}pp  {row['question'][:45]}")
        else:
            print("  (Need more snapshot history for 7-day change calculations)")

    print("\n[4/7] Correlating signals with Polymarket odds...")
    sig = signal_vs_odds(con)
    if not sig.empty:
        sig.to_parquet(args.output_dir / "signal_vs_odds.parquet")
        pm_count = con.execute("SELECT COUNT(*) FROM polymarket_snapshots").fetchone()[0]
        print(f"  -> {pm_count:,} Polymarket snapshots in DB")
        print(f"  -> Saved signal_vs_odds.parquet")

    # 5. Posting heatmap
    print("\n[5/7] Computing posting heatmap...")
    heatmap = posting_heatmap(con)
    if not heatmap.empty:
        heatmap.to_csv(args.output_dir / "posting_heatmap.csv", index=False)
        print(f"  -> Saved posting_heatmap.csv")

    # 5. Prediction features (if resolved outcomes exist)
    print("\n[6/7] Building prediction feature matrix...")
    features = build_prediction_features(con)
    if not features.empty:
        features.to_parquet(args.output_dir / "prediction_features.parquet")
        print(f"  -> Saved prediction_features.parquet ({len(features)} resolved markets)")

    # 7. Market opportunity screener
    print("\n[7/7] Ranking actionable betting opportunities...")
    opportunities = score_markets(con, odds_moves=odds_moves if not odds_moves.empty else None)
    if not opportunities.empty:
        opportunities.to_csv(args.output_dir / "market_opportunities.csv", index=False)
        print(f"  -> Saved market_opportunities.csv ({len(opportunities)} picks)")
    print_opportunities(opportunities)

    # Quick DB summary
    print("\n" + "=" * 60)
    posts_total = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    ts_count = con.execute("SELECT COUNT(*) FROM posts WHERE source='truth_social'").fetchone()[0]
    tw_count = con.execute("SELECT COUNT(*) FROM posts WHERE source='twitter'").fetchone()[0]
    pm_count = con.execute("SELECT COUNT(*) FROM polymarket_snapshots").fetchone()[0]
    hits_count = con.execute("SELECT COUNT(*) FROM keyword_hits").fetchone()[0]
    print(f"  Posts: {posts_total:,}  (TruthSocial: {ts_count:,}  Twitter: {tw_count:,})")
    print(f"  Keyword hits: {hits_count:,}")
    print(f"  Polymarket snapshots: {pm_count:,}")
    print("=" * 60)

    con.close()


if __name__ == "__main__":
    main()
