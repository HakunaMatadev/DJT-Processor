# Trump Signal Tracker — Agent Brief

You are working on a Polymarket prediction market edge-finding tool.
It correlates Trump's Truth Social post patterns (keyword spikes, agitation metrics)
with Polymarket prediction market odds to surface mispriced bets.

The core insight: when Trump posts about a topic 2–3× more than his baseline rate,
a related Polymarket market often hasn't repriced yet. That gap is the edge.

---

## Project state (as of handoff)

All files are in `scripts/`. The pipeline runs in order:

```
01_ingest.py          → pulls Truth Social + Twitter archive → DuckDB
02_polymarket.py      → fetches live Polymarket odds → DuckDB
03_analyze.py         → spike detection + market screener → market_opportunities.csv
04_scheduler.py       → runs the above on a schedule
05_backfill_outcomes.py → populates resolved market history for backtesting
categories.py         → shared keyword groups (imported by other scripts)
market_screener.py    → ranks markets by signal/edge/liquidity composite score
polymarket_client.py  → Polymarket Gamma API client (no auth required)
trump_signal_tracker.jsx → React dashboard (browser-side only currently)
```

DB lives at `data/trump_tracker.duckdb`. Tables: `posts`, `keyword_hits`,
`polymarket_snapshots`, `market_outcomes`.

---

## Fix these three bugs first — they will prevent the pipeline from running

### Bug 1 — Missing sys.path guard in polymarket_client.py

`polymarket_client.py` does `from categories import ...` but has no `sys.path` guard.
This works when called from `02_polymarket.py` (which adds the path) but breaks on
direct import or IDE runs.

Fix: add this at the very top of `polymarket_client.py`, before any local imports:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
```

### Bug 2 — requirements.txt is incomplete

Current `requirements.txt` is missing packages that are actively imported.

Replace the entire file with:

```
duckdb>=0.10.0
pandas>=2.0.0
requests>=2.31.0
pyarrow>=14.0.0
scikit-learn>=1.4.0
pytz>=2024.1
flask>=3.0.0
python-dotenv>=1.0.0
truststore>=0.9.1; sys_platform != "linux"
```

### Bug 3 — NOISE_PATTERNS date regex is too aggressive in market_screener.py

The pattern `r" win on \d{4}-\d{2}-\d{2}"` was intended to filter out daily
bet markets, but it also filters legitimate policy markets that happen to have
a date in the question (e.g. "Will Trump announce tariffs before 2025-12-31?").

Fix: make it more specific so it only matches the daily-bet format:

```python
# Change this line in NOISE_PATTERNS:
r" win on \d{4}-\d{2}-\d{2}",
# To:
r"^will .{1,30} win on \d{4}-\d{2}-\d{2}\?$",
```

Also add a debug mode to `is_actionable_market()` so we can see what's being filtered:

```python
def is_actionable_market(question: str, debug: bool = False) -> bool:
    q = question.lower()
    if _matches_any(q, NOISE_PATTERNS):
        if debug:
            print(f"  [filtered] {question[:60]}")
        return False
    return _matches_any(q, ACTION_PATTERNS) or (
        "trump" in q and not _matches_any(q, NOISE_PATTERNS)
    )
```

---

## Build these four features in order

### Feature 1 — Add `polymarket_url` and `slug` to every snapshot (30 min)

The Gamma API returns a `slug` field on every market. We need it stored so the
dashboard can link directly to the bet page. Without this, users can see an
opportunity but can't click through to place the bet.

**In `polymarket_client.py`, update `parse_market()`:**

```python
def parse_market(market: dict, *, snapshotted_at: datetime | None = None) -> dict | None:
    # ... existing code ...
    slug = market.get("slug") or ""
    polymarket_url = f"https://polymarket.com/event/{slug}" if slug else ""

    return {
        "id": mid,
        "question": question,
        "category": infer_category(question, description),
        "yes_price": parse_yes_price(market),
        "volume_24h": float(market.get("volume24hr") or market.get("volume24h") or 0),
        "total_volume": float(market.get("volume") or market.get("usdcVolume") or 0),
        "end_date": end_date,
        "active": bool(market.get("active", True)),
        "slug": slug,
        "polymarket_url": polymarket_url,
        "snapshotted_at": snapshotted_at or datetime.now(timezone.utc),
    }
```

**In `01_ingest.py`, add the columns to the schema:**

```sql
ALTER TABLE polymarket_snapshots ADD COLUMN IF NOT EXISTS slug TEXT DEFAULT '';
ALTER TABLE polymarket_snapshots ADD COLUMN IF NOT EXISTS polymarket_url TEXT DEFAULT '';
```

Add this inside `SCHEMA_SQL` as part of the `polymarket_snapshots` CREATE TABLE,
and also run the ALTER statements in `main()` after `con.execute(SCHEMA_SQL)` to
migrate existing databases:

```python
con.execute("ALTER TABLE polymarket_snapshots ADD COLUMN IF NOT EXISTS slug TEXT DEFAULT ''")
con.execute("ALTER TABLE polymarket_snapshots ADD COLUMN IF NOT EXISTS polymarket_url TEXT DEFAULT ''")
```

**In `market_screener.py`, pass `polymarket_url` through `score_markets()`:**

Add `polymarket_url` to the `rows.append({...})` dict in `score_markets()`:
```python
"polymarket_url": str(m.get("polymarket_url") or ""),
```

And add it to the output CSV so the dashboard can use it.


### Feature 2 — Build `scripts/07_api.py` — Flask API bridge (1–2 hours)

The React dashboard currently does all analysis in the browser. The Python scripts
do the real work (with full DB history, backtesting, calibrated scoring) but the
dashboard can't see it. This API bridges them.

Create `scripts/07_api.py`:

```python
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

DB_PATH = Path(__file__).parent.parent / "data" / "trump_tracker.duckdb"
OPPS_PATH = Path(__file__).parent.parent / "data" / "market_opportunities.csv"

app = Flask(__name__)
CORS(app)  # allow requests from React dev server


def get_con():
    return duckdb.connect(str(DB_PATH), read_only=True)


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
    print("Endpoints: /api/health /api/opportunities /api/spikes /api/velocity /api/heatmap /api/agitation /api/polymarket /api/stats")
    app.run(host="0.0.0.0", port=5001, debug=True)
```

Also add `flask` and `flask-cors` to `requirements.txt`.


### Feature 3 — Add a "Best Bets" tab to the React dashboard (1–2 hours)

Update `trump_signal_tracker.jsx` to:

1. Add an `API_BASE` constant at the top:
```javascript
const API_BASE = process.env.REACT_APP_API_URL || null;
```

2. Add a `useLiveAPI` hook that tries to fetch from the Flask API, falls back to
   browser-side analysis if the API isn't running:
```javascript
async function fetchFromAPI(endpoint, fallback) {
  if (!API_BASE) return fallback;
  try {
    const res = await fetch(`${API_BASE}${endpoint}`);
    if (!res.ok) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}
```

3. Add a new "Best Bets" tab (make it the first/default tab) that displays the
   `market_opportunities.csv` data from `/api/opportunities`. Each row should show:
   - `suggested_side` badge (green YES / red NO)
   - Market question (truncated to 60 chars)
   - `yes_price` as a percentage
   - `edge_pp` with +/- coloring
   - `spike_ratio` with category label
   - `composite_score` as a small progress bar
   - A "Bet →" button linking to `polymarket_url` (opens in new tab)
   - The `reason` field as small muted text below

4. Add an API status indicator in the header — a small green/red dot showing
   whether the Flask API is reachable. When it's green, data comes from the full
   Python pipeline. When it's red, it falls back to browser-side analysis with
   a note explaining the limitation.

5. Replace the static stat cards with live data from `/api/stats` when the API
   is available, so the header shows real DB counts instead of just the
   browser-fetched post count.


### Feature 4 — Add `scripts/06_bet_log.py` — bet tracking (30 min)

Create a simple bet journal that records actual bets placed and computes P&L.
This is essential for validating the model over time.

```python
"""
06_bet_log.py
─────────────
Track actual bets placed and compute P&L against resolved outcomes.

Usage:
    python scripts/06_bet_log.py log --market-id "abc123" --side YES --price 0.42 --stake 50
    python scripts/06_bet_log.py pnl
    python scripts/06_bet_log.py list
"""
```

Add a `bet_log` table to the DB schema in `01_ingest.py`:

```sql
CREATE TABLE IF NOT EXISTS bet_log (
    id              INTEGER PRIMARY KEY,
    logged_at       TIMESTAMPTZ DEFAULT NOW(),
    market_id       TEXT NOT NULL,
    question        TEXT,
    category        TEXT,
    side            TEXT NOT NULL,     -- 'YES' or 'NO'
    entry_price     FLOAT NOT NULL,    -- 0.0 to 1.0
    stake_usd       FLOAT NOT NULL,
    signal_spike    FLOAT,             -- spike_ratio at time of bet
    signal_category TEXT,
    resolved_yes    BOOLEAN,           -- filled in when market resolves
    pnl_usd         FLOAT,             -- filled in when resolved
    notes           TEXT
);
CREATE SEQUENCE IF NOT EXISTS bet_log_seq;
```

The `pnl` command should join `bet_log` with `market_outcomes` on `market_id`
and compute: for YES bets, `pnl = stake * (1/entry_price - 1)` if resolved YES,
else `-stake`. For NO bets, `pnl = stake * (1/(1-entry_price) - 1)` if resolved NO,
else `-stake`. Print a summary table and cumulative ROI.

---

## Environment setup (run this first if starting fresh)

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

mkdir -p data data/logs

# Download Twitter archive from https://www.thetrumparchive.com
# Save as: data/trump_twitter_archive.json

python scripts/01_ingest.py --twitter data/trump_twitter_archive.json
python scripts/02_polymarket.py
python scripts/05_backfill_outcomes.py
python scripts/03_analyze.py

# Check your picks:
python -c "import pandas as pd; print(pd.read_csv('data/market_opportunities.csv').to_string())"

# Start the API (separate terminal):
python scripts/07_api.py
```

---

## Key data paths

```
data/trump_tracker.duckdb          main database
data/trump_twitter_archive.json    Twitter archive (download separately)
data/market_opportunities.csv      ranked betting picks — updated by 03_analyze.py
data/keyword_velocity.parquet      weekly keyword trends
data/signal_vs_odds.parquet        correlation data
data/logs/scheduler.log            pipeline run log
```

---

## What NOT to change

- `categories.py` keyword lists are calibrated — don't add high-frequency words
  like "deal", "win", "great" as they'll generate false spikes on everything
- The `NOISE_PATTERNS` in `market_screener.py` were tuned to remove junk markets
  (speech bets, event-attendance bets, daily over/under posts) — don't trim them
- The `polymarket_snapshots` table uses append-only inserts (not upserts) by design
  so we preserve odds history over time — don't change this to upsert
- `01_ingest.py` skips posts already in the DB by checking existing IDs — this
  is intentional; don't switch to full re-ingestion on every run

---

## How to interpret market_opportunities.csv output

| Column | What it means |
|--------|--------------|
| `suggested_side` | YES = buy YES shares, NO = buy NO shares, PASS = skip |
| `yes_price` | Current market price (0–1). YES at 0.38 means market gives 38% chance |
| `edge_pp` | Model's estimated edge in percentage points. +18 = model thinks YES should be 56% not 38% |
| `composite_score` | 0–1 ranking score. Above 0.5 = strong pick. Above 0.7 = high conviction |
| `spike_ratio` | Keyword spike multiplier. 2.4 = Trump mentions this topic 2.4× his baseline |
| `streak_days` | Consecutive days Trump has mentioned this category |
| `reason` | Human-readable explanation of why this market was flagged |
| `polymarket_url` | Direct link to place the bet |

**Edge is only meaningful when `spike_ratio >= 1.3` AND `volume_24h >= $5000`.**
Low-volume markets can have large apparent edges just because they're illiquid.

---

## Composite score weights (in market_screener.py)

Current weights:
- `edge_score` 30% — gap between model's implied probability and market price
- `signal_score` 25% — keyword spike strength
- `liquidity_score` 20% — 24h volume (normalized to $100K)
- `urgency_score` 15% — resolves within 30 days = 1.0, otherwise 0.5
- `streak_score` 10% — consecutive-day mention streak

Once you have 20+ resolved outcomes in `market_outcomes`, re-evaluate these
weights using the prediction features in `data/prediction_features.parquet`.
If `signal_score` shows high predictive power, raise it to 35% and lower
`liquidity_score` to 15%.
