# Trump Signal Tracker

A research tool that correlates Donald Trump's Truth Social / Twitter posting
patterns with Polymarket prediction market odds, looking for mispriced bets.

The core insight: when Trump posts about a topic 2-3x more than his baseline
rate, a related Polymarket market often hasn't repriced yet. That gap is the
edge. The project also includes a standalone **keyword odds tool** for the
"will Trump say X this week/month?" style markets, which answers: how often
does he say a given word or phrase, and how likely is he to say it again
within N days?

This is a research/educational project, not financial advice.

---

## How it works (data flow)

```
                 ┌─────────────────────┐
truth_archive ──▶│ 01_ingest.py         │──▶ posts, keyword_hits
twitter archive ▶│ (clean + tag posts)  │     (DuckDB)
                 └─────────────────────┘
                                                 │
                 ┌─────────────────────┐        │
Polymarket API ─▶│ 02_polymarket.py     │──▶ polymarket_snapshots
(Gamma API)      │ (live odds snapshot) │        │
                 └─────────────────────┘        │
                                                  ▼
                 ┌─────────────────────┐   ┌───────────────────────┐
                 │ 03_analyze.py        │──▶│ data/*.parquet, *.csv │
                 │ - keyword velocity   │   │ market_opportunities  │
                 │ - spike detection    │   │   .csv ("Best Bets")  │
                 │ - market_screener    │   └───────────────────────┘
                 └─────────────────────┘
                                                  │
                 ┌─────────────────────┐         │
                 │ 05_backfill_outcomes │──▶ market_outcomes        │
                 │ (resolved markets)   │   (for backtesting)       │
                 └─────────────────────┘                            │
                                                                      │
                 ┌─────────────────────┐                            │
                 │ 08_score_predictions │──▶ predictions             │
                 │ (log + resolve +    │   (calibration log)         │
                 │  calibration report)│                            │
                 └─────────────────────┘                            │
                                                                      ▼
                 ┌─────────────────────┐   ┌─────────────────────────┐
                 │ 07_api.py (Flask)    │◀──│ DuckDB: trump_tracker   │
                 │ :5001                │   │   .duckdb               │
                 └──────────┬──────────┘   └─────────────────────────┘
                             │ JSON
                             ▼
                 ┌─────────────────────┐
                 │ dashboard/ (Vite +   │
                 │ React, :5173)        │
                 └─────────────────────┘

04_scheduler.py runs 01/02/03/05/08 on a recurring schedule (or --once / --step).
06_bet_log.py records bets you actually place and computes P&L vs market_outcomes.
keyword_odds.py is a standalone module used by 07_api.py and the CLI for
keyword frequency/probability lookups (the "Keyword Odds" tab).
08_score_predictions.py logs Best Bets/Keyword Odds predictions, later checks
them against ground truth, and reports calibration (the "Track Record" tab).
```

---

## Database

Everything lives in a single DuckDB file: `data/trump_tracker.duckdb`.

| Table | Purpose |
|---|---|
| `posts` | Every ingested Truth Social / Twitter post, cleaned text + derived features (`hour_of_day`, `caps_ratio`, `exclamation_count`, `word_count`, `is_repost`, etc.) |
| `keyword_hits` | One row per (post, keyword, category) match, with a 40-char context snippet — used for category-level velocity/spike charts |
| `polymarket_snapshots` | Append-only log of Polymarket odds over time (question, category, `yes_price`, volume, `end_date`, `slug`, `polymarket_url`). Append-only by design so odds history is preserved |
| `market_outcomes` | Resolved markets (question, category, `resolve_date`, `resolved_yes`) used for backtesting/calibration |
| `bet_log` | Bets you actually placed (side, entry price, stake, signal at time of bet) plus realized P&L once resolved |
| `predictions` | Logged model predictions (Best Bets picks + tracked Keyword Odds queries) with `predicted_prob`, `market_price`, a `check_after` date, and (once resolved) `actual_outcome` — used for calibration scoring |

`posts` currently spans ~2009 to today across both Truth Social and Twitter
(~89k rows in the reference dataset).

---

## Keyword & category system

Trump's posts are tagged into ~12 topic categories (`tariffs_trade`,
`personnel_firing`, `iran_middle_east`, `ukraine_nato`, `economy_markets`,
`legal_investigation`, `immigration_border`, `midterms_elections`,
`executive_actions`, `health_fitness`, `media_attacks`, `china`), each backed
by a curated list of keywords/phrases.

- **`scripts/categories.py`** is the canonical keyword list, used by
  `polymarket_client.py` (to categorize Polymarket questions) and
  `market_screener.py` (to compute category signal strength). It does
  whole-word matching (`\bkeyword\b`).
- **`scripts/01_ingest.py`** has its own near-duplicate `KEYWORD_GROUPS`
  used to populate `keyword_hits` during ingestion (substring matching,
  not whole-word). The two lists are *intentionally* not refactored into one
  shared definition right now — they were tuned somewhat independently and
  are calibrated to avoid false-positive spikes (e.g. avoid generic words
  like "deal", "win", "great"). **Don't add high-frequency generic words to
  either list** without checking the spike charts for false positives.
- **`dashboard/src/TrumpSignalTracker.jsx`** also has its own copy of
  `KEYWORD_GROUPS` for the browser-only fallback mode (when the Flask API
  isn't running).

---

## "Best Bets" — the composite opportunity score

`scripts/market_screener.py` ranks live Polymarket markets that look
actionable. It deliberately **filters out** noise markets via
`NOISE_PATTERNS` — including generic "Will Trump say X this week?" markets,
daily over/under bets, and event-attendance bets — because those don't fit
the keyword-spike-vs-market-price model below.

For each remaining market, `score_markets()` computes a `composite_score`
(0-1) as a weighted blend:

| Component | Weight | What it measures |
|---|---|---|
| `edge_score` | 30% | Gap between our model's implied probability and the market's `yes_price` |
| `signal_score` | 25% | Keyword spike strength for the market's category (recent 14d posting rate vs. 70d baseline) |
| `liquidity_score` | 20% | 24h trading volume, normalized to $100K |
| `urgency_score` | 15% | Markets resolving within 30 days score 1.0, others 0.5 |
| `streak_score` | 10% | Consecutive days Trump has posted about that category |

Output is written to `data/market_opportunities.csv` and served at
`/api/opportunities`. Each row includes a suggested side (`YES`/`NO`/`PASS`),
`edge_pp`, `spike_ratio`, `composite_score`, a human-readable `reason`, and a
`polymarket_url` to place the bet.

Edge is only meaningful when `spike_ratio >= 1.3` AND `volume_24h >= $5,000`
— low-volume markets can show large apparent edges just because they're
illiquid.

---

## Keyword Odds — "will Trump say X?" tool

`scripts/keyword_odds.py` is a standalone complement to the screener above,
built specifically for "Will Trump say X this week/month?" style markets
(the ones `NOISE_PATTERNS` excludes from Best Bets).

Given a search term or phrase, it:

1. **Searches `posts.content_clean`** for the term as a whole word/phrase
   (case-insensitive regex, e.g. `\btariff\b`).
2. **Computes mention rates** over the last 7, 30, and 90 days, plus
   all-time.
3. Picks a **"primary" rate** with recency-weighted fallback: use the 30-day
   rate if it's non-zero, else the 90-day rate, else the all-time rate. This
   avoids a single recent burst (or total silence) from dominating the
   estimate.
4. **Models future mentions as a Poisson process**: the probability of at
   least one mention in the next `N` days is

   ```
   P(>=1 mention in N days) = 1 - exp(-rate_per_day * N)
   ```

   reported for N = 1, 3, 7, 14, 30, 60, 90 days.
5. **Cross-references live Polymarket markets** matching
   `Will Trump say "<term>" ...`, computes days remaining until the market's
   `end_date`, and reports an **edge** = our modeled probability − the
   market's current `yes_price`.

### CLI usage

```bash
python scripts/keyword_odds.py "tariff"
python scripts/keyword_odds.py "cat"
python scripts/keyword_odds.py "supreme court"
```

### API

`GET /api/keyword?q=<term>` returns total mentions, first/last seen,
7d/30d/90d/all-time windows, the primary rate + probabilities, a weekly
mention time series (for charting), and any matching live markets with edge.

### Dashboard

The **Keyword Odds** tab provides a search box (with suggested terms),
a probability breakdown for each horizon, a weekly mentions chart, and a
list of matching live markets highlighting the edge vs. our model.

Note: this is a simple frequency model. It doesn't account for things like
scheduled events (e.g. a UFC fight that week) that might make a topic far
more or less likely than its historical base rate suggests — treat the
"edge" numbers as a starting point for research, not a signal to bet on
directly.

---

## Track Record — does the model "train" on past results?

There's no neural net here, but the project does close the loop between
*predictions* and *outcomes* so you can see whether the model's probability
estimates are actually calibrated.

`scripts/08_score_predictions.py` logs every prediction the model makes into
the `predictions` table, then later checks what really happened:

1. **Logging.**
   - Every Best Bets opportunity (`data/market_opportunities.csv`) is
     snapshotted once per `market_id`, recording its `implied_yes` (our
     model's probability), the market's `yes_price` at the time, and a
     `check_after` date equal to the market's `end_date`.
   - Any Keyword Odds query can be **tracked** (via the dashboard's "Track"
     button or `python scripts/08_score_predictions.py track "<term>" --days
     N`), recording the modeled probability that the term comes up again
     within `N` days, and a `check_after` date `N` days out.

2. **Resolving.** Once `check_after` has passed:
   - Keyword Odds predictions are graded against `posts` directly — did the
     term actually get mentioned again before `check_after`?
   - Best Bets predictions are graded against `market_outcomes` (populated
     by `05_backfill_outcomes.py`) — did the market resolve YES? If the
     market hasn't resolved/been backfilled yet, the prediction stays
     pending.

3. **Calibration report.** Once predictions have resolved outcomes, a
   **Brier score** (mean squared error between predicted probability and
   0/1 outcome — 0 is perfect, 0.25 is "always guess 50%") and a
   probability-bucket table are computed: for predictions made at ~70%, did
   they resolve YES about 70% of the time? Systematic over/under-confidence
   shows up here.

This *is* the "training" loop in a soft sense: `category_calibration()` (in
`market_screener.py`) already recomputes each category's `implied_yes` rates
from `market_outcomes` on every `03_analyze.py` run, so as
`05_backfill_outcomes.py` accumulates more resolved markets, the Best Bets
model's probabilities adjust automatically. The `predictions` table is what
makes that adjustment *visible* — it's the historical scoreboard.

### CLI usage

```bash
# Track a Keyword Odds prediction ("will Trump say X within N days?")
python scripts/08_score_predictions.py track "tariff" --days 30

# Snapshot current Best Bets opportunities as predictions (dedup'd by market_id)
python scripts/08_score_predictions.py log-bestbets

# Check pending predictions whose check_after date has passed
python scripts/08_score_predictions.py resolve

# Print the calibration report (Brier score + probability buckets)
python scripts/08_score_predictions.py report
python scripts/08_score_predictions.py report --type keyword_odds

# List logged predictions
python scripts/08_score_predictions.py list
python scripts/08_score_predictions.py list --pending --type best_bet
```

### API

- `POST /api/keyword/track` — body `{"term": "tariff", "days": 30}`, logs a
  Keyword Odds prediction.
- `GET /api/predictions` — list logged predictions, filterable by
  `?type=keyword_odds|best_bet` and `?pending=true`.
- `GET /api/predictions/calibration` — Brier score + bucket table, filterable
  by `?type=`.

### Dashboard

The **Track Record** tab shows total/pending/resolved counts, the Brier
score, a predicted-vs-actual bar chart by probability bucket, and the full
list of logged predictions. The **Keyword Odds** tab has a "Track this
prediction" control next to the probability bars to log a new prediction.

---

## Setup

### 1. Python pipeline

```bash
python -m venv venv
venv\Scripts\activate          # Windows (PowerShell: venv\Scripts\Activate.ps1)
# source venv/bin/activate      # macOS/Linux

pip install -r requirements.txt

mkdir data, data\logs           # PowerShell; or: mkdir -p data data/logs

# Download the Twitter archive from https://www.thetrumparchive.com
# and save it as data/trump_twitter_archive.json

python scripts/01_ingest.py --twitter data/trump_twitter_archive.json
python scripts/02_polymarket.py
python scripts/05_backfill_outcomes.py
python scripts/03_analyze.py
```

`03_analyze.py` writes `data/market_opportunities.csv` and prints a summary
of the top picks.

### 2. Flask API

```bash
python scripts/07_api.py
```

Runs on `http://localhost:5001`. Endpoints: `/api/health`,
`/api/opportunities`, `/api/spikes`, `/api/velocity`, `/api/heatmap`,
`/api/agitation`, `/api/polymarket`, `/api/keyword`, `/api/keyword/track`,
`/api/predictions`, `/api/predictions/calibration`, `/api/stats`.

### 3. Dashboard

```bash
cd dashboard
npm install
npm run dev
```

Runs on `http://localhost:5173`. It reads the API URL from
`dashboard/.env` (`VITE_API_URL=http://localhost:5001`) and falls back to
browser-only analysis (fetching the public Truth Social archive and
Polymarket API directly) if the Flask API isn't reachable.

> **Windows + AVG Antivirus note:** if `npm install` hangs with
> `UNABLE_TO_VERIFY_LEAF_SIGNATURE`, AVG's "Web/Mail Shield" is intercepting
> TLS. Export its root cert and set `NODE_EXTRA_CA_CERTS` before running npm
> commands (see `dashboard/.certs/` if already set up on this machine).

---

## Running the pipeline on a schedule

```bash
python scripts/04_scheduler.py              # run forever
python scripts/04_scheduler.py --once       # run ingest+polymarket+analyze+outcomes+predictions once
python scripts/04_scheduler.py --step ingest
python scripts/04_scheduler.py --step polymarket
python scripts/04_scheduler.py --step analyze
python scripts/04_scheduler.py --step outcomes
python scripts/04_scheduler.py --step predictions
```

- Truth Social ingest: every 30 minutes
- Polymarket odds snapshot: every 60 minutes
- Analysis (spikes + screener): every 6 hours
- Outcome backfill: every 24 hours
- Predictions (snapshot Best Bets + resolve pending predictions): every 24 hours

Logs go to `data/logs/scheduler.log`.

---

## Tracking your bets

```bash
python scripts/06_bet_log.py log --market-id "abc123" --side YES --price 0.42 --stake 50
python scripts/06_bet_log.py list
python scripts/06_bet_log.py pnl
```

`pnl` joins `bet_log` with `market_outcomes` and computes realized P&L plus
cumulative ROI, for YES bets: `stake * (1/entry_price - 1)` if resolved YES,
else `-stake` (mirrored for NO bets).

---

## Project layout

```
scripts/
  01_ingest.py          Truth Social + Twitter -> DuckDB (posts, keyword_hits)
  02_polymarket.py       Live Polymarket odds -> polymarket_snapshots
  03_analyze.py          Velocity, spikes, screener -> market_opportunities.csv
  04_scheduler.py         Runs the pipeline on a schedule
  05_backfill_outcomes.py Resolved markets -> market_outcomes
  06_bet_log.py           Bet journal + P&L
  07_api.py               Flask API for the dashboard
  08_score_predictions.py Log/resolve predictions -> calibration report
  categories.py           Shared keyword groups + category inference
  market_screener.py      NOISE_PATTERNS, ACTION_PATTERNS, composite scoring
  polymarket_client.py     Polymarket Gamma API client
  keyword_odds.py          Keyword frequency & probability tool

data/
  trump_tracker.duckdb     Main database
  trump_twitter_archive.json   Twitter archive (download separately)
  market_opportunities.csv     Latest "Best Bets" output
  *.parquet                    Velocity/correlation/feature data
  logs/                        Scheduler logs

dashboard/
  src/TrumpSignalTracker.jsx   React dashboard (Vite)
  .env                         VITE_API_URL=http://localhost:5001

trump_signal_tracker.jsx   Portable copy of the dashboard component
AGENT.md                   Original project brief / implementation notes
```
