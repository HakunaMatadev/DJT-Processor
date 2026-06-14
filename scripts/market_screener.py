"""
Rank Polymarket questions by signal strength, liquidity, and historical edge.

Filters out low-value markets (speech bets, Nobel prizes, sports, post counts)
and scores policy/outcome markets where Trump post signals may lead odds.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import duckdb
import pandas as pd

# Markets where post keywords rarely predict resolution
NOISE_PATTERNS = [
    r"will trump say",
    r'will .+ say "',
    r"nobel peace prize",
    r"truth social posts?",
    r"white house post",
    r"approval rating",
    r"publicly insult",
    r"spread:",
    r"o/u \d",
    r"^will .{1,30} win on \d{4}-\d{2}-\d{2}\?$",
    r"win the 2028",
    r"presidential election",
    r"republican presidential",
    r"bell ceremony",
    r"during ufc",
    r"during fox interview",
    r"during monday news",
    r"this week\?",
    r"during the next prime min",
    r"during event",
    r"during debate",
    r"flip the bird",
    r"dance during",
    r"praise allah",
    r"praise lionel messi",
    r'post "',
    r'say "ufc"',
    r"press conference",
    r"seen together before",
    r"meet next in",
    r"not meet\?",
    r"gold cards?",
    r"before gta vi",
    r"attend the g7",
    r"attend.*summit",
    r"attend.*world cup",
    r"attend usa opening",
    r"attend nato summit",
]

# Markets where Trump post activity is plausibly predictive
ACTION_PATTERNS = [
    r"ceasefire", r"tariff", r"pardon", r"executive order",
    r"strike on", r"agreement", r"sanction", r"impeach",
    r"out by", r"resign", r"declassif", r"recognize",
    r"withdraw troops", r"blockade", r"acquire greenland",
    r"indicted", r"announces", r"unfreeze", r"relief by",
    r"opponent federally charged", r"national emergency",
    r"security guarantee", r"in effect by",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def is_actionable_market(question: str, debug: bool = False) -> bool:
    q = question.lower()
    if _matches_any(q, NOISE_PATTERNS):
        if debug:
            print(f"  [filtered] {question[:60]}")
        return False
    return _matches_any(q, ACTION_PATTERNS) or (
        "trump" in q and not _matches_any(q, NOISE_PATTERNS)
    )


def category_signals(con: duckdb.DuckDBPyConnection, window_days: int = 14) -> pd.DataFrame:
    """Recent keyword activity vs baseline per category."""
    return con.execute(f"""
        WITH recent AS (
            SELECT
                category,
                COUNT(DISTINCT post_id) AS posts_recent,
                AVG(p.caps_ratio) AS avg_caps_recent
            FROM keyword_hits kh
            JOIN posts p ON p.id = kh.post_id
            WHERE kh.created_at >= CURRENT_TIMESTAMP - INTERVAL '{window_days}' DAY
            GROUP BY 1
        ),
        baseline AS (
            SELECT
                category,
                COUNT(DISTINCT post_id) / 8.0 AS posts_per_week
            FROM keyword_hits
            WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '70' DAY
              AND created_at < CURRENT_TIMESTAMP - INTERVAL '{window_days}' DAY
            GROUP BY 1
        ),
        streaks AS (
            SELECT
                category,
                MAX(streak_len) AS current_streak_days
            FROM (
                SELECT
                    category,
                    grp,
                    COUNT(*) AS streak_len
                FROM (
                    SELECT
                        category,
                        DATE_TRUNC('day', created_at)::DATE AS day,
                        day - ROW_NUMBER() OVER (
                            PARTITION BY category ORDER BY DATE_TRUNC('day', created_at)::DATE
                        )::INT AS grp
                    FROM keyword_hits
                    WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
                    GROUP BY 1, 2
                ) grouped
                GROUP BY 1, 2
            ) streak_groups
            GROUP BY 1
        )
        SELECT
            COALESCE(r.category, b.category) AS category,
            COALESCE(r.posts_recent, 0) AS posts_recent,
            COALESCE(b.posts_per_week, 0) AS baseline_weekly,
            CASE
                WHEN COALESCE(b.posts_per_week, 0) = 0 THEN 1.0
                ELSE COALESCE(r.posts_recent, 0) / (b.posts_per_week * 2.0)
            END AS spike_ratio,
            COALESCE(r.avg_caps_recent, 0) AS avg_caps_recent,
            COALESCE(s.current_streak_days, 0) AS streak_days
        FROM recent r
        FULL OUTER JOIN baseline b ON r.category = b.category
        LEFT JOIN streaks s ON COALESCE(r.category, b.category) = s.category
    """).df()


def category_calibration(con: duckdb.DuckDBPyConnection, window_days: int = 14) -> pd.DataFrame:
    """
    Historical YES rate by category for resolved markets,
    split by whether keyword spike was elevated pre-resolution.
    """
    outcomes = con.execute("SELECT * FROM market_outcomes").df()
    if outcomes.empty:
        return pd.DataFrame(columns=["category", "elevated_yes_rate", "normal_yes_rate", "n_resolved"])

    rows = []
    for _, market in outcomes.iterrows():
        resolve_date = pd.to_datetime(market["resolve_date"])
        window_start = resolve_date - pd.Timedelta(days=window_days)
        baseline_start = resolve_date - pd.Timedelta(days=window_days + 30)
        cat = market["category"]

        window_count = con.execute("""
            SELECT COUNT(DISTINCT post_id) FROM keyword_hits
            WHERE category = ? AND created_at BETWEEN ? AND ?
        """, [cat, window_start, resolve_date]).fetchone()[0]

        baseline_count = con.execute("""
            SELECT COUNT(DISTINCT post_id) FROM keyword_hits
            WHERE category = ? AND created_at BETWEEN ? AND ?
        """, [cat, baseline_start, window_start]).fetchone()[0]

        spike = window_count / max(baseline_count / 30 * window_days, 1)
        rows.append({
            "category": cat,
            "resolved_yes": bool(market["resolved_yes"]),
            "elevated_spike": spike >= 1.3,
        })

    hist = pd.DataFrame(rows)
    summary = []
    for cat, grp in hist.groupby("category"):
        elevated = grp[grp["elevated_spike"]]
        normal = grp[~grp["elevated_spike"]]
        summary.append({
            "category": cat,
            "elevated_yes_rate": elevated["resolved_yes"].mean() if len(elevated) else None,
            "normal_yes_rate": normal["resolved_yes"].mean() if len(normal) else None,
            "overall_yes_rate": grp["resolved_yes"].mean(),
            "n_resolved": len(grp),
        })
    return pd.DataFrame(summary)


def _question_key(question: str) -> str:
    q = question.lower()
    q = re.sub(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b",
        "date",
        q,
    )
    q = re.sub(r"\b20\d{2}\b", "year", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q[:70]


def score_markets(
    con: duckdb.DuckDBPyConnection,
    odds_moves: pd.DataFrame | None = None,
    min_volume: float = 5000,
    top_n: int = 25,
) -> pd.DataFrame:
    """Return ranked actionable markets with suggested side and reasoning."""
    if odds_moves is None or odds_moves.empty:
        odds_moves = con.execute("""
            SELECT
                id AS market_id,
                question,
                category,
                yes_price AS current_yes_price,
                volume_24h AS current_volume_24h,
                end_date,
                polymarket_url,
                snapshotted_at AS latest_snapshot
            FROM polymarket_snapshots
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY snapshotted_at DESC) = 1
        """).df()

    signals = category_signals(con)
    calibration = category_calibration(con)
    signal_map = signals.set_index("category").to_dict("index") if not signals.empty else {}
    cal_map = calibration.set_index("category").to_dict("index") if not calibration.empty else {}

    now = datetime.now(timezone.utc)
    rows = []
    for _, m in odds_moves.iterrows():
        question = m["question"]
        if not is_actionable_market(question):
            continue

        volume = float(m.get("current_volume_24h") or 0)
        if volume < min_volume:
            continue

        yes_price = float(m["current_yes_price"])
        if yes_price <= 0.02 or yes_price >= 0.98:
            continue  # little upside left

        cat = m["category"]
        sig = signal_map.get(cat, {})
        cal = cal_map.get(cat, {})

        spike = float(sig.get("spike_ratio") or 1.0)
        caps = float(sig.get("avg_caps_recent") or 0)
        streak = int(sig.get("streak_days") or 0)

        elevated_rate = cal.get("elevated_yes_rate")
        normal_rate = cal.get("normal_yes_rate")
        overall_rate = cal.get("overall_yes_rate")

        if spike >= 1.3 and elevated_rate is not None:
            implied_yes = elevated_rate
        elif overall_rate is not None:
            implied_yes = overall_rate
        else:
            implied_yes = 0.5

        # Boost implied probability when signal is hot
        if spike >= 1.5:
            implied_yes = min(0.92, implied_yes * min(spike / 1.3, 1.4))
        elif spike <= 0.7:
            implied_yes = max(0.08, implied_yes * spike)

        edge = implied_yes - yes_price
        suggested_side = "YES" if edge > 0.05 else ("NO" if edge < -0.05 else "PASS")

        days_to_end = None
        end_date = m.get("end_date")
        if end_date is not None and not pd.isna(end_date):
            days_to_end = (pd.Timestamp(end_date).tz_localize("UTC") - pd.Timestamp(now)).days

        liquidity_score = min(1.0, volume / 100_000)
        signal_score = min(1.0, max(0, (spike - 0.8) / 1.2))
        edge_score = min(1.0, abs(edge) / 0.25)
        urgency_score = 1.0 if days_to_end is not None and 0 < days_to_end <= 30 else 0.5
        streak_score = min(1.0, streak / 5)

        composite = (
            0.30 * edge_score +
            0.25 * signal_score +
            0.20 * liquidity_score +
            0.15 * urgency_score +
            0.10 * streak_score
        )

        if suggested_side == "PASS":
            composite *= 0.5

        reasons = []
        if spike >= 1.3:
            reasons.append(f"{cat} keyword spike {spike:.1f}x")
        if streak >= 3:
            reasons.append(f"{streak}d mention streak")
        if elevated_rate is not None and spike >= 1.3:
            reasons.append(f"hist YES {elevated_rate*100:.0f}% when spiking")
        if edge > 0.05:
            reasons.append(f"edge +{edge*100:.0f}pp vs market")
        elif edge < -0.05:
            reasons.append(f"edge {edge*100:.0f}pp vs market (NO lean)")

        rows.append({
            "market_id": m["market_id"],
            "question": question,
            "category": cat,
            "yes_price": yes_price,
            "volume_24h": volume,
            "spike_ratio": round(spike, 2),
            "streak_days": streak,
            "implied_yes": round(implied_yes, 3),
            "edge_pp": round(edge * 100, 1),
            "suggested_side": suggested_side,
            "composite_score": round(composite, 3),
            "days_to_end": days_to_end,
            "reason": "; ".join(reasons) if reasons else "policy market, moderate signal",
            "polymarket_url": str(m.get("polymarket_url") or ""),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["_key"] = df["question"].map(_question_key)
    df = df.sort_values("volume_24h", ascending=False).drop_duplicates(subset=["_key"], keep="first")
    df = df.drop(columns=["_key"])
    df = df[df["suggested_side"] != "PASS"].sort_values(
        "composite_score", ascending=False
    ).head(top_n)
    return df.reset_index(drop=True)


def print_opportunities(opportunities: pd.DataFrame, limit: int = 15):
    if opportunities.empty:
        print("  No actionable opportunities above threshold.")
        print("  Tip: run 02_polymarket.py daily to build odds history for edge detection.")
        return

    print(f"\n{'='*90}")
    print("  TOP BETTING OPPORTUNITIES (signal-ranked)")
    print(f"{'='*90}")
    print(f"{'SIDE':<4} {'YES%':>5} {'EDGE':>6} {'SCORE':>5}  {'CATEGORY':<20}  QUESTION")
    print(f"{'-'*90}")
    for _, row in opportunities.head(limit).iterrows():
        q = row["question"][:42].encode("ascii", errors="replace").decode("ascii")
        print(
            f"{row['suggested_side']:<4} {row['yes_price']*100:>4.0f}% "
            f"{row['edge_pp']:>+5.0f}pp {row['composite_score']:>5.2f}  "
            f"{row['category']:<20}  {q}"
        )
        if row["reason"]:
            print(f"      -> {row['reason']}")
    print(f"{'='*90}\n")
