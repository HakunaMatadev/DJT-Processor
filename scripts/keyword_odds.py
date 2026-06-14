"""
keyword_odds.py
────────────────
Keyword frequency & probability tool — answers "how often does Trump say X,
and how likely is he to say it again in the next N days?" This is the kind
of question "Will Trump say X this week/month?" Polymarket markets bet on.

These markets are deliberately excluded from market_screener.py's main
opportunity feed (see NOISE_PATTERNS), so this module is a standalone
complement: search any word or phrase, get historical frequency stats and
Poisson-based probabilities, and (if available) compare against live
"Will Trump say ..." markets to see if there's an edge.

Usage:
    python keyword_odds.py "tariff"
    python keyword_odds.py "cat"
    python keyword_odds.py "supreme court"
"""

import argparse
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "trump_tracker.duckdb"

# Standard horizons used for the probability table / API response.
PROBABILITY_WINDOWS = [1, 3, 7, 14, 30, 60, 90]

QUOTED_TERM_RE = re.compile(r'"([^"]+)"')


def build_pattern(term: str) -> str:
    """Whole-word/phrase, case-insensitive regex for DuckDB regexp_matches."""
    words = term.strip().lower().split()
    if not words:
        raise ValueError("Search term cannot be empty")
    return r"\b" + r"\s+".join(re.escape(w) for w in words) + r"\b"


def keyword_matches(con, term: str, since=None, limit=None) -> pd.DataFrame:
    """Posts whose content_clean contains `term` as a whole word/phrase."""
    pattern = build_pattern(term)
    sql = """
        SELECT id, created_at, source, content_clean
        FROM posts
        WHERE regexp_matches(content_clean, ?, 'i')
    """
    params = [pattern]
    if since is not None:
        sql += " AND created_at >= ?"
        params.append(since)
    sql += " ORDER BY created_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return con.execute(sql, params).df()


def keyword_weekly_series(con, term: str, weeks: int = 26) -> pd.DataFrame:
    """Weekly mention counts for charting (last `weeks` weeks)."""
    pattern = build_pattern(term)
    df = con.execute("""
        SELECT DATE_TRUNC('week', created_at)::DATE AS week, COUNT(*) AS mentions
        FROM posts
        WHERE regexp_matches(content_clean, ?, 'i')
        GROUP BY 1
        ORDER BY 1
    """, [pattern]).df()
    if df.empty:
        return df
    df["week"] = df["week"].astype(str)
    df["mentions"] = df["mentions"].astype(int)
    return df.tail(weeks).reset_index(drop=True)


def probability_for_days(rate_per_day: float, days: float) -> float:
    """P(>=1 mention in `days`), modeling mentions as a Poisson process."""
    if rate_per_day <= 0 or days <= 0:
        return 0.0
    return 1.0 - math.exp(-rate_per_day * days)


def keyword_stats(con, term: str, now=None) -> dict:
    """
    Frequency stats for `term`: total mentions, recency, mention rates over
    7d/30d/90d/all-time, a "primary" rate (recency-weighted with fallback),
    and Poisson probabilities for PROBABILITY_WINDOWS.
    """
    now = now or datetime.now(timezone.utc)
    pattern = build_pattern(term)

    total_mentions, first_seen, last_seen = con.execute("""
        SELECT COUNT(*), MIN(created_at), MAX(created_at)
        FROM posts WHERE regexp_matches(content_clean, ?, 'i')
    """, [pattern]).fetchone()

    windows = {}
    for label, days in (("7d", 7), ("30d", 30), ("90d", 90)):
        cutoff = now - timedelta(days=days)
        cnt = con.execute("""
            SELECT COUNT(*) FROM posts
            WHERE regexp_matches(content_clean, ?, 'i') AND created_at >= ?
        """, [pattern, cutoff]).fetchone()[0]
        windows[label] = {"mentions": cnt, "rate_per_day": cnt / days}

    if total_mentions and first_seen is not None:
        span_days = max((now - first_seen).total_seconds() / 86400, 1.0)
        all_time_rate = total_mentions / span_days
    else:
        all_time_rate = 0.0
    windows["all_time"] = {"mentions": total_mentions, "rate_per_day": all_time_rate}

    # Recency-weighted rate: prefer 30d, fall back to 90d, then all-time.
    if windows["30d"]["rate_per_day"] > 0:
        primary_rate, primary_source = windows["30d"]["rate_per_day"], "30d"
    elif windows["90d"]["rate_per_day"] > 0:
        primary_rate, primary_source = windows["90d"]["rate_per_day"], "90d"
    else:
        primary_rate, primary_source = all_time_rate, "all_time"

    days_since_last = None
    if last_seen is not None:
        days_since_last = (now - last_seen).total_seconds() / 86400

    probabilities = {
        days: probability_for_days(primary_rate, days) for days in PROBABILITY_WINDOWS
    }

    return {
        "term": term,
        "pattern": pattern,
        "total_mentions": int(total_mentions),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "days_since_last": days_since_last,
        "windows": windows,
        "primary_rate": primary_rate,
        "primary_source": primary_source,
        "probabilities": probabilities,
    }


def matching_markets(con, term: str, stats: dict | None = None, now=None) -> list[dict]:
    """
    Cross-reference live 'Will Trump say "X" ...' Polymarket markets whose
    quoted term matches `term`, and compute our model's edge vs. the market
    YES price.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    if stats is None:
        stats = keyword_stats(con, term, now=now)

    df = con.execute("""
        SELECT question, end_date, yes_price, volume_24h, total_volume,
               slug, polymarket_url, active
        FROM polymarket_snapshots
        WHERE regexp_matches(question, 'say\\s+"', 'i')
        QUALIFY ROW_NUMBER() OVER (PARTITION BY question ORDER BY snapshotted_at DESC) = 1
    """).df()

    term_norm = term.strip().lower()
    word_re = re.compile(r"\b" + re.escape(term_norm) + r"\b")

    results = []
    for _, row in df.iterrows():
        m = QUOTED_TERM_RE.search(row["question"])
        if not m:
            continue
        quoted = m.group(1).strip().lower()
        if quoted != term_norm and not word_re.search(quoted):
            continue

        end_date = row["end_date"]
        days_to_end = None
        our_prob = None
        edge = None
        if pd.notna(end_date):
            days_to_end = (end_date.date() - today).days
            if days_to_end > 0:
                our_prob = probability_for_days(stats["primary_rate"], days_to_end)
                if pd.notna(row["yes_price"]):
                    edge = our_prob - float(row["yes_price"])

        results.append({
            "question": row["question"],
            "end_date": str(end_date) if pd.notna(end_date) else None,
            "days_to_end": days_to_end,
            "yes_price": float(row["yes_price"]) if pd.notna(row["yes_price"]) else None,
            "volume_24h": float(row["volume_24h"]) if pd.notna(row["volume_24h"]) else None,
            "our_probability": our_prob,
            "edge": edge,
            "active": bool(row["active"]) if pd.notna(row["active"]) else None,
            "polymarket_url": row["polymarket_url"],
        })

    results.sort(key=lambda r: (r["edge"] is None, -(r["edge"] or 0)))
    return results


# ── CLI report ────────────────────────────────────────────────────────────────

def _fmt_pct(x):
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def print_report(term: str, stats: dict, markets: list[dict]):
    print("=" * 60)
    print(f"  KEYWORD ODDS: \"{term}\"")
    print("=" * 60)

    if stats["total_mentions"] == 0:
        print("\nNo mentions found in the archive.")
        print("=" * 60)
        return

    print(f"\nTotal mentions (all-time): {stats['total_mentions']:,}")
    print(f"First seen: {stats['first_seen']}")
    print(f"Last seen:  {stats['last_seen']}  ({stats['days_since_last']:.1f} days ago)")

    print("\nRecent activity:")
    for label in ("7d", "30d", "90d", "all_time"):
        w = stats["windows"][label]
        print(f"  {label:<8}  {w['mentions']:>6,} mentions   ({w['rate_per_day']:.3f} / day)")

    print(f"\nPrimary rate used for probabilities: {stats['primary_rate']:.3f} mentions/day"
          f"  (source: {stats['primary_source']})")

    print("\nProbability of >=1 mention within:")
    for days in PROBABILITY_WINDOWS:
        p = stats["probabilities"][days]
        print(f"  {days:>3} days  ->  {p * 100:5.1f}%")

    if markets:
        print("\nMatching live 'Will Trump say ...' markets:")
        for m in markets:
            print(f"\n  - {m['question']}")
            if m["days_to_end"] is not None:
                print(f"    ends {m['end_date']}  ({m['days_to_end']} days from now)")
            else:
                print(f"    ends {m['end_date']}")
            edge_str = f"{m['edge'] * 100:+.1f}pp" if m["edge"] is not None else "n/a"
            print(f"    market YES price: {_fmt_pct(m['yes_price'])}"
                  f"   our estimate: {_fmt_pct(m['our_probability'])}"
                  f"   edge: {edge_str}")
            if m["polymarket_url"]:
                print(f"    {m['polymarket_url']}")
    else:
        print("\nNo matching 'Will Trump say ...' markets currently tracked.")

    print("\n" + "=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Keyword frequency & probability analyzer")
    parser.add_argument("term", help='Word or phrase to search for, e.g. "tariff" or "cat"')
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)
    try:
        stats = keyword_stats(con, args.term)
        markets = matching_markets(con, args.term, stats=stats)
        print_report(args.term, stats, markets)
    finally:
        con.close()


if __name__ == "__main__":
    main()
