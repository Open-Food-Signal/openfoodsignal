#!/usr/bin/env python3
"""
OpenFoodSignal — Pillar A (stock coverage) pipeline for olive oil.

Implements METHODOLOGY.md §1 (v0.1) with one documented deviation:
the EU production endpoint provides ending stocks and production, but no
consumption series. v0.1 therefore uses a stock-coverage proxy

    CR_t = ending_stocks_t / mean(production of the 5 preceding crop years)

instead of the true stocks-to-use ratio. The denominator is a stable
scale for "size of the market"; switching to real consumption (IOC
balance sheets) is tracked as an open issue and will be a minor
methodology version bump.

Steps:
  1. Fetch annual production and ending stocks for the main producer
     states (ES, IT, EL, PT — together >95 % of EU production) from the
     EU Agri-food Data Portal API.
  2. Aggregate per crop year (Oct–Sep), compute the coverage ratio CR.
  3. Standardize CR against the 5 preceding crop years (z-score with
     sigma floor, §1.5) and map to score_A = clamp(50 + 25·z, 0, 100).
  4. If data/pillar_b_latest.json exists, combine both pillars into the
     v0.1 headline index (§3: 0.58·A + 0.42·B) -> data/index_latest.json.

Outputs:
  data/pillar_a_series.csv   per-crop-year series (for the backtest)
  data/pillar_a_latest.json  latest Pillar A score
  data/index_latest.json     combined v0.1 index (if Pillar B data found)

Usage:
  pip install requests pandas
  python pillar_a.py

API documentation:
  https://agridata.ec.europa.eu/extensions/API_Documentation/oliveoil.html

Compatibility: Python 3.7+.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuration (starting values per METHODOLOGY.md — calibrate via backtest)
# ---------------------------------------------------------------------------

BASE_URL = "https://www.ec.europa.eu/agrifood/api"

# Main producer member states (>95 % of EU production).
MEMBER_STATES = ["ES", "IT", "EL", "PT"]

FIRST_PRODUCTION_YEAR = 2005   # earliest crop year to request
Z_WINDOW_YEARS = 5             # §1.3: reference = 5 preceding crop years
SIGMA_FLOOR_FACTOR = 0.25      # §1.5: floor on sigma, see sigma_floor()
STALE_AFTER_DAYS = 90          # §1.4: data-quality threshold

WEIGHT_A = 0.58                # §3 (v0.1 renormalized weights)
WEIGHT_B = 0.42

OUT_DIR = Path("data")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_json(url: str, params: dict | None = None):
    resp = requests.get(
        url, params=params, headers={"Accept": "application/json"}, timeout=60
    )
    resp.raise_for_status()
    return resp.json()


def crop_year_label(production_year: int) -> str:
    """2023 -> '2023/24' (olive crop year runs 1 Oct – 30 Sep, §1.2)."""
    return f"{production_year}/{str(production_year + 1)[-2:]}"


# ---------------------------------------------------------------------------
# Step 1 — fetch annual balance data per member state
# ---------------------------------------------------------------------------

def fetch_member_state(ms: str) -> list[dict]:
    """Annual rows for one member state; fall back to per-year requests."""
    rows = get_json(
        f"{BASE_URL}/oliveOil/production",
        params={"memberStateCodes": ms, "granularity": "annual"},
    )
    if rows:
        return rows
    # Fallback: some APIs require explicit years — request them one by one.
    print(f"  note: empty bulk response for {ms}, retrying per year ...")
    rows = []
    for year in range(FIRST_PRODUCTION_YEAR, dt.date.today().year + 1):
        rows.extend(
            get_json(
                f"{BASE_URL}/oliveOil/production",
                params={
                    "memberStateCodes": ms,
                    "granularity": "annual",
                    "productionYears": year,
                },
            )
        )
    return rows


def build_balance() -> pd.DataFrame:
    """Aggregate production and ending stocks per crop year across states."""
    records = []
    for ms in MEMBER_STATES:
        print(f"  fetching {ms} ...")
        for row in fetch_member_state(ms):
            year = row.get("productionYear")
            if year is None or int(year) < FIRST_PRODUCTION_YEAR:
                continue
            records.append(
                {
                    "production_year": int(year),
                    "member_state": row.get("memberStateCode", ms),
                    "production": float(row.get("yearProductionQuantity") or 0.0),
                    "ending_stocks": float(row.get("endingStockQuantity") or 0.0),
                    "is_estimated": str(row.get("isEstimated", "N")).upper() == "Y",
                }
            )
    if not records:
        sys.exit("ERROR: production endpoint returned no usable rows.")

    df = pd.DataFrame(records)
    agg = (
        df.groupby("production_year")
        .agg(
            production=("production", "sum"),
            ending_stocks=("ending_stocks", "sum"),
            states_reporting=("member_state", "nunique"),
            any_estimated=("is_estimated", "any"),
        )
        .sort_index()
    )
    agg["crop_year"] = [crop_year_label(y) for y in agg.index]
    return agg


# ---------------------------------------------------------------------------
# Step 2/3 — coverage ratio, z-score with sigma floor, score
# ---------------------------------------------------------------------------

def score(agg: pd.DataFrame) -> pd.DataFrame:
    # Coverage ratio: stocks vs mean production of the 5 preceding years.
    prod_ref = agg["production"].shift(1).rolling(Z_WINDOW_YEARS).mean()
    agg["coverage_ratio"] = agg["ending_stocks"] / prod_ref

    mean = agg["coverage_ratio"].shift(1).rolling(Z_WINDOW_YEARS).mean()
    std = agg["coverage_ratio"].shift(1).rolling(Z_WINDOW_YEARS).std()

    # §1.5 sigma floor: protect against unrealistically calm reference
    # periods. Floor = factor · full-history coefficient of variation · mean.
    cr = agg["coverage_ratio"].dropna()
    cv_full = cr.std() / cr.mean() if len(cr) > 2 and cr.mean() else 0.0
    floor = SIGMA_FLOOR_FACTOR * cv_full * mean
    std = pd.concat([std, floor], axis=1).max(axis=1)

    agg["z"] = (agg["coverage_ratio"] - mean) / std
    agg["score_a"] = (50.0 + 25.0 * agg["z"]).clip(0.0, 100.0).round(0)
    return agg


# ---------------------------------------------------------------------------
# Step 4 — combine with Pillar B if available
# ---------------------------------------------------------------------------

def traffic_light(index: int) -> str:
    if index >= 67:
        return "green"
    if index >= 34:
        return "yellow"
    return "red"


def combine(latest_a: dict) -> None:
    b_path = OUT_DIR / "pillar_b_latest.json"
    if not b_path.exists():
        print("  (no Pillar B data found — run pillar_b.py to get the "
              "combined index)")
        return
    latest_b = json.loads(b_path.read_text())
    index = round(WEIGHT_A * latest_a["score"] + WEIGHT_B * latest_b["score"])
    combined = {
        "signal_version": "0.1",
        "methodology_version": "0.1.0",
        "commodity": "olive-oil",
        "scope": "EU",
        "timestamp": str(dt.date.today()),
        "availability_index": index,
        "traffic_light": traffic_light(index),
        "data_quality": latest_a["data_quality"],
        "pillars": {
            "stocks": latest_a,
            "price": latest_b,
        },
        "sources": ["eu-agrifood-portal", "eurostat-hicp"],
        "license": "ODbL-1.0",
    }
    out = OUT_DIR / "index_latest.json"
    out.write_text(json.dumps(combined, indent=2, ensure_ascii=False))
    print(f"\nCombined v0.1 index: {index} ({combined['traffic_light']})"
          f"\n  -> {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Fetching annual production and ending stocks ...")
    agg = build_balance()

    print("Computing coverage ratio and score ...")
    agg = score(agg)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "pillar_a_series.csv"
    # Limit floats to 4 decimals so the CSV stays readable in Excel & Co.
    agg.to_csv(csv_path, index_label="production_year", float_format="%.4f")

    scored = agg.dropna(subset=["score_a"])
    if scored.empty:
        sys.exit("ERROR: not enough history to compute a score.")
    last = scored.iloc[-1]

    # Data age: ending stocks refer to 30 Sep of crop year end (§1.2/§1.4).
    stocks_date = dt.date(int(last.name) + 1, 9, 30)
    age_days = max(0, (dt.date.today() - stocks_date).days)
    data_quality = "ok" if (last["any_estimated"] or age_days <= STALE_AFTER_DAYS) \
        else "degraded"

    latest = {
        "pillar": "stocks",
        "crop_year": last["crop_year"],
        "score": int(last["score_a"]),
        "z": round(float(last["z"]), 2),
        "coverage_ratio": round(float(last["coverage_ratio"]), 3),
        "ending_stocks_1000t": round(float(last["ending_stocks"]), 1),
        "states_reporting": int(last["states_reporting"]),
        "is_estimated": bool(last["any_estimated"]),
        "data_age_days": age_days,
        "data_quality": data_quality,
        "note": "v0.1 coverage proxy: stocks / 5y mean production "
                "(true stocks-to-use pending IOC consumption data)",
    }
    json_path = OUT_DIR / "pillar_a_latest.json"
    json_path.write_text(json.dumps(latest, indent=2, ensure_ascii=False))

    print(f"\nDone. Latest Pillar A score: {latest['score']} "
          f"(crop year {latest['crop_year']}, z={latest['z']}, "
          f"quality={data_quality})")
    print(f"  -> {csv_path}\n  -> {json_path}")

    combine(latest)


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as err:
        sys.exit(
            f"HTTP error from data source: {err}\n"
            "Check BASE_URL against the API documentation:\n"
            "https://agridata.ec.europa.eu/extensions/API_Documentation/oliveoil.html"
        )
