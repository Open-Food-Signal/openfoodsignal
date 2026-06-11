#!/usr/bin/env python3
"""
OpenFoodSignal — Pillar B (price signal) pipeline for olive oil.

Implements METHODOLOGY.md §2 (v0.1):
  1. Fetch weekly extra-virgin producer prices for the reference markets
     Jaén (ES), Bari (IT), Chania (EL) from the EU Agri-food Data Portal API.
  2. Build a production-weighted nominal composite price (€/100 kg).
  3. Deflate with the euro-area HICP (base: January 2020).
  4. Smooth with a 4-week rolling median.
  5. Standardize against the trailing 5-year window and map to score_B
     (0–100, high price = low availability).

Outputs:
  data/pillar_b_series.csv   full weekly series (for the backtest)
  data/pillar_b_latest.json  latest score in food-availability-signal style

Usage:
  pip install requests pandas
  python pillar_b.py                # full history since 2010
  python pillar_b.py --since 2019-01-01

API documentation:
  https://agridata.ec.europa.eu/extensions/API_Documentation/oliveoil.html

Compatibility: Python 3.7+ (the __future__ import below keeps modern type
annotations from being evaluated on older interpreters).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuration (starting values per METHODOLOGY.md — calibrate via backtest)
# ---------------------------------------------------------------------------

# Base URL of the machine-to-machine interface. If requests fail with 404,
# check the API documentation page above for the current host/path.
BASE_URL = "https://www.ec.europa.eu/agrifood/api"

# Reference markets: match patterns checked against the official market
# list (accent-insensitive). The NUTS region codes (ES616, ITF42, GR434)
# are script-independent — the Greek markets appear in Greek script in the
# API list ('Χανιά (GR434)' = Chania). Weights ≈ 5-year share of EU
# production (METHODOLOGY.md §2.3); they are renormalized whenever a
# market has no usable price in a given week.
REFERENCE_MARKETS = {
    "jaen":   {"weight": 0.62, "member_state": "ES", "patterns": ["es616", "jaen"]},
    "bari":   {"weight": 0.20, "member_state": "IT", "patterns": ["itf42", "bari"]},
    "chania": {"weight": 0.18, "member_state": "EL", "patterns": ["gr434", "χανιά", "chania"]},
}

PRODUCT_PREFIX = "Extra virgin"   # resolved against /oliveOil/products
CARRY_FORWARD_WEEKS = 2           # §2.4: max gap filled per market
SMOOTH_WINDOW = 4                 # §2.3: rolling median window (weeks)
Z_WINDOW = 260                    # §2.3: trailing 5 years of weeks
Z_MIN_PERIODS = 156               # require >= 3 years before scoring
HICP_BASE = "2020-01"             # §2.3: deflation base period

EUROSTAT_HICP_URL = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/"
    "data/prc_hicp_midx?format=JSON&lang=EN&unit=I15&coicop=CP00&geo=EA"
)

OUT_DIR = Path("data")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Lowercase and strip accents, so 'Jaén (ES616)' matches 'jaen'."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def get_json(url: str, params: dict | None = None):
    resp = requests.get(
        url, params=params, headers={"Accept": "application/json"}, timeout=60
    )
    resp.raise_for_status()
    return resp.json()


def parse_price(raw: str) -> float:
    """API returns strings like '€275.00' — strip symbols defensively."""
    cleaned = raw.replace("\u20ac", "").replace("EUR", "").replace(",", "").strip()
    return float(cleaned)


# ---------------------------------------------------------------------------
# Step 1 — resolve official names, fetch prices
# ---------------------------------------------------------------------------

def resolve_market_names() -> dict[str, str]:
    """Map our market keys to the exact names the API expects."""
    official = get_json(f"{BASE_URL}/oliveOil/markets")
    resolved = {}
    for key, cfg in REFERENCE_MARKETS.items():
        patterns = [normalize(p) for p in cfg["patterns"]]
        matches = [
            m for m in official
            if any(pat in normalize(m) for pat in patterns)
        ]
        if not matches:
            sys.exit(
                f"ERROR: no market matching any of {cfg['patterns']} "
                f"in API market list.\nAvailable markets: {official}"
            )
        resolved[key] = matches[0]
    return resolved


def resolve_product_name() -> str:
    products = get_json(f"{BASE_URL}/oliveOil/products")
    matches = [p for p in products if normalize(p).startswith(normalize(PRODUCT_PREFIX))]
    if not matches:
        sys.exit(f"ERROR: no product starting with '{PRODUCT_PREFIX}'. Got: {products}")
    return matches[0]


def fetch_market_prices(market_name: str, product: str, since: dt.date) -> pd.Series:
    """Weekly price series for one market, indexed by week-end date."""
    params = {
        "markets": market_name,
        "products": product,
        "beginDate": since.strftime("%d/%m/%Y"),
        "endDate": dt.date.today().strftime("%d/%m/%Y"),
    }
    rows = get_json(f"{BASE_URL}/oliveOil/prices", params=params)
    if not rows:
        print(f"WARNING: no rows for market '{market_name}'")
        return pd.Series(dtype=float)
    records = {}
    for row in rows:
        week_end = dt.datetime.strptime(row["endDate"], "%d/%m/%Y").date()
        records[week_end] = parse_price(row["price"])
    series = pd.Series(records).sort_index()
    series.index = pd.to_datetime(series.index)
    return series


# ---------------------------------------------------------------------------
# Step 2/3 — composite and deflation
# ---------------------------------------------------------------------------

def build_composite(series_by_key: dict[str, pd.Series]) -> pd.DataFrame:
    """Weighted nominal composite on a common weekly grid (W-SUN)."""
    frame = pd.DataFrame(
        {k: s.resample("W-SUN").mean() for k, s in series_by_key.items() if not s.empty}
    )
    # §2.4 carry-forward: fill at most CARRY_FORWARD_WEEKS missing weeks
    frame = frame.ffill(limit=CARRY_FORWARD_WEEKS)

    weights = pd.Series({k: REFERENCE_MARKETS[k]["weight"] for k in frame.columns})

    def weighted_row(row: pd.Series):
        valid = row.dropna()
        if valid.empty:
            return pd.Series({"composite_nominal": float("nan"), "markets_used": ""})
        w = weights[valid.index]
        w = w / w.sum()  # renormalize over markets present this week
        return pd.Series(
            {
                "composite_nominal": float((valid * w).sum()),
                "markets_used": ",".join(valid.index),
            }
        )

    return frame.apply(weighted_row, axis=1).join(frame.add_prefix("price_"))


def fetch_hicp() -> pd.Series:
    """Euro-area HICP all-items, monthly, rebased to HICP_BASE = 100."""
    data = get_json(EUROSTAT_HICP_URL)
    time_cat = data["dimension"]["time"]["category"]["index"]  # {'2010-01': 0, ...}
    values = data["value"]                                     # {'0': 99.1, ...}
    series = {
        pd.Period(period, freq="M"): values[str(idx)]
        for period, idx in time_cat.items()
        if str(idx) in values
    }
    hicp = pd.Series(series).sort_index()
    base = hicp[pd.Period(HICP_BASE, freq="M")]
    return hicp / base * 100.0


def deflate(df: pd.DataFrame, hicp: pd.Series) -> pd.DataFrame:
    months = df.index.to_period("M")
    # Use each week's month; for weeks newer than the last HICP release,
    # fall back to the latest available month (§2.3).
    hicp_aligned = pd.Series(
        [hicp.get(m, hicp.iloc[-1]) for m in months], index=df.index
    )
    df["hicp"] = hicp_aligned
    df["composite_real"] = df["composite_nominal"] / df["hicp"] * 100.0
    return df


# ---------------------------------------------------------------------------
# Step 4/5 — smoothing, z-score, score
# ---------------------------------------------------------------------------

def score(df: pd.DataFrame) -> pd.DataFrame:
    df["real_smoothed"] = df["composite_real"].rolling(SMOOTH_WINDOW).median()
    mean = df["real_smoothed"].rolling(Z_WINDOW, min_periods=Z_MIN_PERIODS).mean()
    std = df["real_smoothed"].rolling(Z_WINDOW, min_periods=Z_MIN_PERIODS).std()
    df["z"] = (df["real_smoothed"] - mean) / std
    df["score_b"] = (50.0 - 25.0 * df["z"]).clip(0.0, 100.0).round(0)
    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2010-06-01",
                        help="start date YYYY-MM-DD (default: full history)")
    args = parser.parse_args()
    since = dt.date.fromisoformat(args.since)

    print("Resolving official market and product names ...")
    markets = resolve_market_names()
    product = resolve_product_name()
    print(f"  product: {product}")
    for key, name in markets.items():
        print(f"  market '{key}' -> '{name}'")

    print("Fetching weekly prices ...")
    series_by_key = {
        key: fetch_market_prices(name, product, since)
        for key, name in markets.items()
    }

    print("Building composite, deflating, scoring ...")
    df = build_composite(series_by_key)
    df = deflate(df, fetch_hicp())
    df = score(df)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "pillar_b_series.csv"
    df.to_csv(csv_path, index_label="week_end")

    last = df.dropna(subset=["score_b"]).iloc[-1]
    latest = {
        "signal_version": "0.1",
        "methodology_version": "0.1.0",
        "commodity": "olive-oil",
        "scope": "EU",
        "pillar": "price",
        "week_end": str(last.name.date()),
        "score": int(last["score_b"]),
        "z": round(float(last["z"]), 2),
        "p_real_eur_per_100kg": round(float(last["real_smoothed"]), 2),
        "p_nominal_eur_per_100kg": round(float(last["composite_nominal"]), 2),
        "markets_used": last["markets_used"].split(","),
        "sources": ["eu-agrifood-portal", "eurostat-hicp"],
        "license": "ODbL-1.0",
    }
    json_path = OUT_DIR / "pillar_b_latest.json"
    json_path.write_text(json.dumps(latest, indent=2, ensure_ascii=False))

    print(f"\nDone. Latest Pillar B score: {latest['score']} "
          f"(week ending {latest['week_end']}, z={latest['z']})")
    print(f"  -> {csv_path}\n  -> {json_path}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as err:
        sys.exit(
            f"HTTP error from data source: {err}\n"
            "Check BASE_URL against the API documentation:\n"
            "https://agridata.ec.europa.eu/extensions/API_Documentation/oliveoil.html"
        )
