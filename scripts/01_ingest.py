"""
01_ingest.py
────────────
Pulls Trump's Truth Social posts (live CNN feed) and optionally imports
a Twitter/X archive export into a local DuckDB database.

Usage:
    python 01_ingest.py                     # Truth Social only
    python 01_ingest.py --twitter tweets.js # + Twitter archive
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytz

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import duckdb
import pandas as pd
import requests

DB_PATH = Path(__file__).parent.parent / "data" / "trump_tracker.duckdb"
TRUTH_SOCIAL_URL = "https://ix.cnn.io/data/truth-social/truth_archive.json"

# ── Schema ─────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS posts (
    id              TEXT PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL,
    content         TEXT,
    content_clean   TEXT,          -- HTML stripped
    source          TEXT NOT NULL, -- 'truth_social' | 'twitter'
    url             TEXT,
    replies_count   INTEGER DEFAULT 0,
    reblogs_count   INTEGER DEFAULT 0,
    favourites_count INTEGER DEFAULT 0,
    hour_of_day     INTEGER,       -- 0-23 EST
    day_of_week     INTEGER,       -- 0=Mon 6=Sun
    is_repost       BOOLEAN DEFAULT FALSE,
    word_count      INTEGER,
    caps_ratio      FLOAT,         -- fraction of alpha chars that are UPPERCASE
    exclamation_count INTEGER DEFAULT 0,
    ingested_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS keyword_hits (
    id          INTEGER PRIMARY KEY,  -- auto-increment via sequence
    post_id     TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    category    TEXT NOT NULL,
    context     TEXT,               -- 40-char window around match
    created_at  TIMESTAMPTZ NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS keyword_hits_seq;

CREATE TABLE IF NOT EXISTS polymarket_snapshots (
    id              TEXT NOT NULL,   -- Polymarket market id
    question        TEXT,
    category        TEXT,
    yes_price       FLOAT,           -- 0.0 – 1.0 probability
    volume_24h      FLOAT,
    total_volume    FLOAT,
    end_date        DATE,
    active          BOOLEAN,
    snapshotted_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_outcomes (
    market_id       TEXT PRIMARY KEY,
    question        TEXT,
    category        TEXT,
    resolve_date    DATE,
    resolved_yes    BOOLEAN,
    notes           TEXT
);
"""

# ── Keyword groups ──────────────────────────────────────────────────────────

KEYWORD_GROUPS = {
    "tariffs_trade": [
        "tariff", "tariffs", "trade war", "trade deal", "import tax",
        "china deal", "trade deficit", "most favored nation", "section 232",
        "section 301", "customs", "duty", "duties", "trade agreement",
    ],
    "personnel_firing": [
        "fired", "resign", "resigns", "resigned", "appointment", "appoint",
        "nominated", "nominee", "secretary of", "attorney general",
        "you're fired", "termination", "removed from",
    ],
    "iran_middle_east": [
        "iran", "tehran", "nuclear deal", "sanctions", "ayatollah",
        "persian gulf", "strait of hormuz", "israel", "hamas", "hezbollah",
        "gaza", "west bank", "middle east", "saudi arabia",
    ],
    "ukraine_nato": [
        "ukraine", "zelensky", "zelenskyy", "nato", "putin", "russia",
        "kyiv", "moscow", "peace deal", "ceasefire", "war in ukraine",
    ],
    "economy_markets": [
        "stock market", "dow jones", "s&p", "nasdaq", "inflation",
        "interest rate", "federal reserve", "fed chair", "powell",
        "recession", "gdp", "unemployment", "jobs report",
    ],
    "legal_investigation": [
        "witch hunt", "hoax", "rigged", "unfair", "indicted", "indictment",
        "trial", "verdict", "acquitted", "criminal", "corrupt",
        "weaponized", "two-tier", "political persecution",
    ],
    "immigration_border": [
        "border", "illegal", "deportation", "deport", "ice", "cbp",
        "migrant", "migrants", "asylum", "invasion", "caravan",
        "remain in mexico", "title 42",
    ],
    "midterms_elections": [
        "midterm", "midterms", "2026", "house seats", "senate seats",
        "republican majority", "democrat", "maga", "america first",
        "vote", "election integrity", "ballot",
    ],
    "executive_actions": [
        "executive order", "e.o.", "proclamation", "veto", "signed",
        "declared", "emergency", "national emergency", "pardon", "pardoned",
    ],
    "health_fitness": [
        "great shape", "perfect health", "doctor", "physical", "cognitive",
        "walter reed", "medical", "strong and healthy",
    ],
    "media_attacks": [
        "fake news", "lamestream", "enemy of the people", "cnn",
        "new york times", "washington post", "msnbc", "mainstream media",
        "corrupt media",
    ],
    "china": [
        "china", "chinese", "xi jinping", "beijing", "ccp",
        "fentanyl", "tiktok", "taiwan", "south china sea",
    ],
}

# ── Helpers ─────────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    """Remove HTML tags and decode common entities."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def caps_ratio(text: str) -> float:
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return 0.0
    return sum(1 for c in alpha if c.isupper()) / len(alpha)


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    if df["created_at"].dt.tz is None:
        raise ValueError("created_at timestamps must be timezone-aware before DB write")

    # Convert to US/Eastern for hour/day features
    eastern = df["created_at"].dt.tz_convert("America/New_York")
    df["hour_of_day"] = eastern.dt.hour
    df["day_of_week"] = eastern.dt.dayofweek

    df["content_clean"] = df["content"].apply(strip_html)
    df["word_count"] = df["content_clean"].str.split().str.len().fillna(0).astype(int)
    df["caps_ratio"] = df["content_clean"].apply(caps_ratio)
    df["exclamation_count"] = df["content_clean"].str.count("!").fillna(0).astype(int)

    # Repost detection: preserve archive flag if set, else infer from content
    inferred_repost = (
        df["content_clean"].str.strip().eq("") |
        df["content_clean"].str.startswith("RT ")
    )
    if "is_repost" in df.columns:
        df["is_repost"] = df["is_repost"].fillna(False) | inferred_repost
    else:
        df["is_repost"] = inferred_repost
    return df


def extract_keyword_hits(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, post in df.iterrows():
        text = (post.get("content_clean") or "").lower()
        for category, keywords in KEYWORD_GROUPS.items():
            for kw in keywords:
                idx = text.find(kw.lower())
                if idx >= 0:
                    start = max(0, idx - 40)
                    end = min(len(text), idx + len(kw) + 40)
                    context = "…" + text[start:end] + "…"
                    rows.append({
                        "post_id": post["id"],
                        "keyword": kw,
                        "category": category,
                        "context": context,
                        "created_at": post["created_at"],
                    })
    return pd.DataFrame(rows)


# ── Data loaders ─────────────────────────────────────────────────────────────

def fetch_truth_social() -> pd.DataFrame:
    print(f"  Fetching Truth Social archive from CNN feed…")
    resp = requests.get(TRUTH_SOCIAL_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    df = pd.DataFrame(data)

    # Normalise columns
    rename = {
        "id": "id",
        "created_at": "created_at",
        "content": "content",
        "url": "url",
        "replies_count": "replies_count",
        "reblogs_count": "reblogs_count",
        "favourites_count": "favourites_count",
    }
    df = df.rename(columns=rename)[[c for c in rename.values() if c in df.columns]]
    df["source"] = "truth_social"
    print(f"  -> {len(df):,} Truth Social posts loaded")
    return df


def _parse_is_repost(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).lower() in {"t", "true", "1", "yes"}


def _localize_eastern(naive_dt: datetime) -> datetime | None:
    """Convert naive Eastern archive timestamps to UTC, handling DST edge cases."""
    eastern = pytz.timezone("America/New_York")
    try:
        return eastern.localize(naive_dt, is_dst=None).astimezone(timezone.utc)
    except pytz.exceptions.AmbiguousTimeError:
        return eastern.localize(naive_dt, is_dst=True).astimezone(timezone.utc)
    except pytz.exceptions.NonExistentTimeError:
        return eastern.localize(naive_dt + timedelta(hours=1), is_dst=True).astimezone(timezone.utc)


def _is_trump_archive_format(sample: dict) -> bool:
    """Trump Twitter Archive uses 'date'; personal exports use 'created_at'."""
    tweet = sample.get("tweet", sample)
    return "date" in tweet and "created_at" not in tweet


def load_twitter_archive(path: Path) -> pd.DataFrame:
    """
    Loads Trump's Twitter archive from the Trump Twitter Archive JSON
    (https://www.thetrumparchive.com) or a personal Twitter export tweets.js.
    Handles both formats.
    """
    print(f"  Loading Twitter archive from {path}...")
    text = path.read_text(encoding="utf-8")

    # Personal export format: window.YTD.tweets.part0 = [...]
    if text.strip().startswith("window."):
        text = re.sub(r"^window\.[^=]+=\s*", "", text.strip())
    raw = json.loads(text)

    archive_format = bool(raw) and _is_trump_archive_format(raw[0])
    format_name = "Trump Twitter Archive" if archive_format else "personal Twitter export"
    print(f"  Detected format: {format_name}")

    rows = []
    for item in raw:
        tweet = item.get("tweet", item)
        if tweet.get("isDeleted", "f") in ("t", True):
            continue

        tweet_id = str(tweet.get("id_str") or tweet.get("id") or "")
        if archive_format:
            rows.append({
                "id":               tweet_id,
                "created_at":       tweet.get("date", ""),
                "content":          tweet.get("text", ""),
                "url":              f"https://twitter.com/i/web/status/{tweet_id}",
                "replies_count":    0,
                "reblogs_count":    int(tweet.get("retweets", 0) or 0),
                "favourites_count": int(tweet.get("favorites", 0) or 0),
                "is_repost":        _parse_is_repost(tweet.get("isRetweet")),
                "source":           "twitter",
            })
        else:
            rows.append({
                "id":               tweet_id,
                "created_at":       tweet.get("created_at", ""),
                "content":          tweet.get("full_text") or tweet.get("text", ""),
                "url":              f"https://twitter.com/i/web/status/{tweet_id}",
                "replies_count":    int(tweet.get("reply_count", 0) or 0),
                "reblogs_count":    int(tweet.get("retweet_count", 0) or 0),
                "favourites_count": int(tweet.get("favorite_count", 0) or 0),
                "is_repost":        _parse_is_repost(tweet.get("is_retweet")),
                "source":           "twitter",
            })

    df = pd.DataFrame(rows)
    df = df[df["id"].str.len() > 0].drop_duplicates(subset=["id"], keep="first")
    if not df.empty and archive_format:
        # Archive dates are US/Eastern local time strings without tz info
        df["created_at"] = [
            _localize_eastern(ts.to_pydatetime())
            for ts in pd.to_datetime(df["created_at"])
        ]
        df = df[pd.notna(df["created_at"])]
    print(f"  -> {len(df):,} Twitter posts loaded")
    return df


# ── Database writer ──────────────────────────────────────────────────────────

def write_to_db(df: pd.DataFrame, con: duckdb.DuckDBPyConnection) -> int:
    """Upsert-style insert: skip any post IDs already in the DB."""
    existing_ids = set(
        row[0] for row in con.execute("SELECT id FROM posts").fetchall()
    )
    new = df[~df["id"].isin(existing_ids)].copy()
    if new.empty:
        return 0

    cols = [
        "id", "created_at", "content", "content_clean", "source", "url",
        "replies_count", "reblogs_count", "favourites_count",
        "hour_of_day", "day_of_week", "is_repost",
        "word_count", "caps_ratio", "exclamation_count",
    ]
    for c in cols:
        if c not in new.columns:
            new[c] = None

    col_list = ", ".join(cols)
    con.register("new_posts", new[cols])
    con.execute(f"INSERT INTO posts ({col_list}) SELECT {col_list} FROM new_posts")
    con.unregister("new_posts")
    return len(new)


def write_keyword_hits(hits: pd.DataFrame, con: duckdb.DuckDBPyConnection):
    """Insert keyword hits that aren't already stored."""
    if hits.empty:
        return
    existing = set(
        row[0] for row in
        con.execute("SELECT post_id || '|' || keyword FROM keyword_hits").fetchall()
    )
    hits["key"] = hits["post_id"] + "|" + hits["keyword"]
    new_hits = hits[~hits["key"].isin(existing)].drop(columns=["key"])
    if new_hits.empty:
        return
    new_hits["id"] = [
        con.execute("SELECT nextval('keyword_hits_seq')").fetchone()[0]
        for _ in range(len(new_hits))
    ]
    cols = ["id", "post_id", "keyword", "category", "context", "created_at"]
    col_list = ", ".join(cols)
    con.register("new_hits", new_hits[cols])
    con.execute(f"INSERT INTO keyword_hits ({col_list}) SELECT {col_list} FROM new_hits")
    con.unregister("new_hits")
    print(f"  -> {len(new_hits):,} new keyword hits stored")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ingest Trump post data")
    parser.add_argument("--twitter", type=Path, help="Path to tweets.js or trump_twitter_archive.json")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="DuckDB file path")
    args = parser.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(args.db))
    con.execute(SCHEMA_SQL)

    frames = []

    # Truth Social
    try:
        ts_df = fetch_truth_social()
        frames.append(ts_df)
    except Exception as e:
        print(f"  WARN Truth Social fetch failed: {e}", file=sys.stderr)

    # Twitter archive
    if args.twitter and args.twitter.exists():
        try:
            tw_df = load_twitter_archive(args.twitter)
            frames.append(tw_df)
        except Exception as e:
            print(f"  WARN Twitter archive load failed: {e}", file=sys.stderr)
    elif args.twitter:
        print(f"  WARN Twitter file not found: {args.twitter}", file=sys.stderr)

    if not frames:
        print("No data loaded. Exiting.", file=sys.stderr)
        sys.exit(1)

    combined = pd.concat(frames, ignore_index=True)
    combined = enrich(combined)

    new_posts = write_to_db(combined, con)
    print(f"\nOK {new_posts:,} new posts written to DB")

    # Extract keyword hits for all posts (new only is handled inside)
    hits = extract_keyword_hits(combined)
    write_keyword_hits(hits, con)

    # Summary
    total = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    total_hits = con.execute("SELECT COUNT(*) FROM keyword_hits").fetchone()[0]
    print(f"\nDatabase summary:")
    print(f"  Posts total:        {total:,}")
    print(f"  Keyword hits total: {total_hits:,}")
    print(f"  DB path: {args.db}")

    con.close()


if __name__ == "__main__":
    main()
