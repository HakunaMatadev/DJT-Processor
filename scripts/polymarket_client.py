"""
Shared Polymarket Gamma API client: fetch, filter, parse, and store snapshots.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import json
import time
from datetime import datetime, timezone

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import duckdb
import pandas as pd
import requests

from categories import TRUMP_MARKET_TERMS, infer_category

GAMMA_BASE = "https://gamma-api.polymarket.com"
USER_AGENT = "trump-tracker-research/1.0"


def fetch_markets(
    *,
    active: bool | None = None,
    closed: bool | None = None,
    limit: int = 100,
    max_pages: int = 60,
    order: str = "volume24hr",
) -> list[dict]:
    """Paginate through Polymarket markets."""
    all_markets: list[dict] = []
    offset = 0

    for _ in range(max_pages):
        params: dict = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "ascending": "false",
        }
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()

        resp = requests.get(
            f"{GAMMA_BASE}/markets",
            params=params,
            timeout=20,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break

        all_markets.extend(page)
        offset += limit
        if len(page) < limit:
            break
        time.sleep(0.15)

    return all_markets


def is_trump_related(market: dict) -> bool:
    """Return True if a market is about Trump or his administration."""
    text = " ".join([
        market.get("question") or "",
        market.get("description") or "",
        market.get("slug") or "",
    ]).lower()
    return any(term in text for term in TRUMP_MARKET_TERMS)


def _parse_outcome_prices(market: dict) -> list:
    raw = market.get("outcomePrices") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return raw if isinstance(raw, list) else []


def parse_yes_price(market: dict) -> float | None:
    outcome_prices = _parse_outcome_prices(market)
    if outcome_prices:
        try:
            return float(outcome_prices[0])
        except (ValueError, IndexError, TypeError):
            pass
    try:
        return float(market.get("lastTradePrice"))
    except (ValueError, TypeError):
        return None


def parse_resolve_date(market: dict):
    for field in ("closedTime", "endDate", "endDateIso", "umaEndDate"):
        raw = market.get(field)
        if not raw:
            continue
        try:
            return pd.to_datetime(raw, utc=True).date()
        except Exception:
            continue
    return None


def parse_resolved_yes(market: dict) -> bool | None:
    """Infer YES resolution from outcome prices on closed markets."""
    if not market.get("closed"):
        return None

    prices = _parse_outcome_prices(market)
    if len(prices) < 2:
        return None

    try:
        yes_price = float(prices[0])
        no_price = float(prices[1])
    except (ValueError, TypeError):
        return None

    if yes_price >= 0.99 and no_price <= 0.01:
        return True
    if no_price >= 0.99 and yes_price <= 0.01:
        return False
    return None


def parse_market(market: dict, *, snapshotted_at: datetime | None = None) -> dict | None:
    mid = str(market.get("id") or market.get("conditionId") or "")
    question = market.get("question") or market.get("title") or ""
    description = market.get("description") or ""
    if not mid or not question:
        return None

    end_date = None
    end_raw = market.get("endDate") or market.get("end_date_iso")
    if end_raw:
        try:
            end_date = pd.to_datetime(end_raw, utc=True).date()
        except Exception:
            pass

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


def parse_outcome(market: dict) -> dict | None:
    """Parse a closed market into a market_outcomes row."""
    mid = str(market.get("id") or market.get("conditionId") or "")
    question = market.get("question") or market.get("title") or ""
    description = market.get("description") or ""
    resolved_yes = parse_resolved_yes(market)
    resolve_date = parse_resolve_date(market)

    if not mid or not question or resolved_yes is None or resolve_date is None:
        return None

    status = market.get("umaResolutionStatus") or ""
    notes = f"auto-backfill ({status})" if status else "auto-backfill"

    return {
        "market_id": mid,
        "question": question,
        "category": infer_category(question, description),
        "resolve_date": resolve_date,
        "resolved_yes": resolved_yes,
        "notes": notes,
    }


def fetch_trump_markets(*, include_closed: bool = False) -> list[dict]:
    """Fetch active Trump markets, optionally including recently closed ones."""
    seen: set[str] = set()
    results: list[dict] = []

    active = fetch_markets(active=True, closed=False, order="volume24hr")
    for market in active:
        mid = str(market.get("id", ""))
        if mid and mid not in seen and is_trump_related(market):
            seen.add(mid)
            results.append(market)

    if include_closed:
        closed = fetch_markets(closed=True, order="volume")
        for market in closed:
            mid = str(market.get("id", ""))
            if mid and mid not in seen and is_trump_related(market):
                seen.add(mid)
                results.append(market)

    return results


def store_snapshots(markets: list[dict], con: duckdb.DuckDBPyConnection) -> pd.DataFrame | None:
    rows = [parse_market(m) for m in markets]
    rows = [r for r in rows if r is not None]
    if not rows:
        return None

    df = pd.DataFrame(rows)
    cols = list(df.columns)
    col_list = ", ".join(cols)
    con.register("pm_snapshots", df)
    con.execute(
        f"INSERT INTO polymarket_snapshots ({col_list}) SELECT {col_list} FROM pm_snapshots"
    )
    con.unregister("pm_snapshots")
    return df


def upsert_outcomes(outcomes: list[dict], con: duckdb.DuckDBPyConnection) -> int:
    if not outcomes:
        return 0

    df = pd.DataFrame(outcomes)
    con.register("pm_outcomes", df)
    con.execute("""
        INSERT INTO market_outcomes
        SELECT * FROM pm_outcomes
        ON CONFLICT (market_id) DO UPDATE SET
            question = excluded.question,
            category = excluded.category,
            resolve_date = excluded.resolve_date,
            resolved_yes = excluded.resolved_yes,
            notes = excluded.notes
    """)
    con.unregister("pm_outcomes")
    return len(df)
