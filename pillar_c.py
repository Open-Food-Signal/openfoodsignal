#!/usr/bin/env python3
"""
OpenFoodSignal — Pillar C "lite" (harvest outlook) for olive oil.

First implementation stage of METHODOLOGY.md Pillar C, using weather data
only (no satellite vegetation indices yet). Two agronomically grounded
indicators per growing region, computed per outlook crop year Y
(= harvest starting October of calendar year Y):

  water:      precipitation sum from 1 Oct (Y-1) to 15 Jun (Y)
              — winter recharge plus spring rain; higher is better.
  bloom_heat: days with Tmax >= 34 °C between 1 May and 15 Jun (Y)
              — heat during flowering aborts fruit set; lower is better.

Data: Open-Meteo Historical Weather API (ERA5/ERA5-Land reanalysis,
gap-free since 1940, no authentication). Data licence CC BY 4.0 —
attribution is included in the JSON output. Note: ERA5 updates with a
5-7 day delay, so the most recent days are always missing.

Partial windows: for the current year the windows may be incomplete
(bloom runs until 15 Jun; ERA5 lags ~1 week). To compare like with like,
ALL years are evaluated over the same truncated day-of-year span as the
current year, and the output reports the window coverage.

Scoring: z-scores against the trailing 10 reference years (weather is
noisier than balance-sheet data, so the window is longer than the 5 years
used in Pillars A/B — a deliberate, documented deviation), sigma floor as
in Pillar A, mapped to 0-100. Region scores are production-weighted
(ES 0.62 / IT 0.20 / EL 0.18, as in Pillar B).

IMPORTANT: score_C is NOT yet part of the headline index. Including it
changes the index definition and therefore requires a methodology version
bump (v0.2) after backtesting. Until then it is published as a separate,
clearly labelled outlook signal.

Outputs:
  data/pillar_c_series.csv   per-outlook-year metrics and scores
  data/pillar_c_latest.json  latest outlook score

Usage:
  pip install requests pandas
  python pillar_c.py

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
# Configuration (starting values — calibrate via backtest before v0.2)
# ---------------------------------------------------------------------------

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

# One representative coordinate per main growing region (simplification of
# v-lite; multiple grid points per region are a later refinement).
REGIONS = {
    "andalusia": {"lat": 37.8, "lon": -3.8, "weight": 0.62},   # Jaén province
    "apulia":    {"lat": 41.0, "lon": 16.5, "weight": 0.20},   # Bari hinterland
    "crete":     {"lat": 35.4, "lon": 24.0, "weight": 0.18},   # Chania region
}

FIRST_OUTLOOK_YEAR = 2005      # earliest harvest year to evaluate
REFERENCE_YEARS = 10           # trailing reference window for z-scores
SIGMA_FLOOR_FACTOR = 0.25      # as in Pillar A (§1.5)
HEAT_THRESHOLD_C = 34.0        # Tmax threshold during bloom

# Window definitions, relative to outlook year Y:
WATER_START = (10, 1)          # 1 Oct of Y-1
WATER_END = (6, 15)            # 15 Jun of Y
BLOOM_START = (5, 1)           # 1 May of Y
BLOOM_END = (6, 15)            # 15 Jun of Y

MIN_COVERAGE = 0.5             # below this fraction, the year is skipped

OUT_DIR = Path("data")


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def fetch_region(name: str, cfg: dict) -> pd.DataFrame:
    """Daily precipitation and Tmax for one region since FIRST year."""
    params = {
        "latitude": cfg["lat"],
        "longitude": cfg["lon"],
        "start_date": f"{FIRST_OUTLOOK_YEAR - 1}-10-01",
        "end_date": dt.date.today().isoformat(),
        "daily": "precipitation_sum,temperature_2m_max",
        "timezone": "UTC",
    }
    resp = requests.get(BASE_URL, params=params, timeout=120)
    resp.raise_for_status()
    payload = resp.json()
    daily = payload.get("daily") or {}
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(daily.get("time", [])),
            "precip": daily.get("precipitation_sum", []),
            "tmax": daily.get("temperature_2m_max", []),
        }
    ).dropna(subset=["date"]).set_index("date")
    # ERA5 lags ~5-7 days: trailing rows can be null — drop them.
    df = df.dropna(how="all")
    if df.empty:
        sys.exit(f"ERROR: no weather data returned for region '{name}'.")
    return df


# ---------------------------------------------------------------------------
# Metrics per outlook year (with like-for-like truncated windows)
# ---------------------------------------------------------------------------

def window(df: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
    return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


def compute_region_metrics(df: pd.DataFrame, last_data: dt.date) -> pd.DataFrame:
    """Per outlook year: water sum, bloom heat days, window coverage.

    The effective window ends are truncated to the day-of-year reach of the
    current (latest) outlook year, so every year is measured over exactly
    the same calendar span.
    """
    today_year = last_data.year
    current_outlook = today_year if last_data >= dt.date(today_year, 1, 1) else today_year

    def eff_end(y: int, month_day: tuple) -> dt.date:
        nominal = dt.date(y, *month_day)
        # truncate to the same day-of-year as data availability in the
        # current outlook year
        current_nominal = dt.date(current_outlook, *month_day)
        cutoff = min(current_nominal, last_data)
        return min(nominal, dt.date(y, cutoff.month, cutoff.day))

    rows = []
    for y in range(FIRST_OUTLOOK_YEAR, current_outlook + 1):
        water_start = dt.date(y - 1, *WATER_START)
        water_end = eff_end(y, WATER_END)
        bloom_start = dt.date(y, *BLOOM_START)
        bloom_end = eff_end(y, BLOOM_END)

        w = window(df, water_start, water_end)
        b = window(df, bloom_start, bloom_end)

        water_days_nominal = (dt.date(y, *WATER_END) - water_start).days + 1
        bloom_days_nominal = (dt.date(y, *BLOOM_END) - bloom_start).days + 1
        coverage = min(
            len(w) / water_days_nominal if water_days_nominal else 0,
            len(b) / bloom_days_nominal if bloom_days_nominal else 0,
        )
        if coverage < MIN_COVERAGE:
            continue
        rows.append(
            {
                "outlook_year": y,
                "water_mm": float(w["precip"].sum()),
                "bloom_heat_days": int((b["tmax"] >= HEAT_THRESHOLD_C).sum()),
                "coverage": round(coverage, 3),
            }
        )
    return pd.DataFrame(rows).set_index("outlook_year")


# ---------------------------------------------------------------------------
# Scoring (z vs trailing reference years, sigma floor, 0-100 mapping)
# ---------------------------------------------------------------------------

def zscore(series: pd.Series, min_std: float = 0.0) -> pd.Series:
    """z vs trailing reference years, with three guards against a
    degenerate (zero/NaN) standard deviation:
      1. relative sigma floor as in Pillar A (§1.5),
      2. an absolute minimum std (for count metrics that can sit at zero
         for an entire reference decade, e.g. bloom heat days),
      3. fallback to the full-history std.
    If the metric is constant everywhere, the numerator is zero and z = 0.
    """
    mean = series.shift(1).rolling(REFERENCE_YEARS).mean()
    std = series.shift(1).rolling(REFERENCE_YEARS).std()
    full = series.dropna()
    cv_full = full.std() / full.mean() if len(full) > 2 and full.mean() else 0.0
    floor = SIGMA_FLOOR_FACTOR * abs(cv_full) * mean.abs()
    std = pd.concat([std, floor], axis=1).max(axis=1)
    global_std = float(full.std()) if len(full) > 2 else 0.0
    std = std.where(std > 0, other=global_std)
    std = std.clip(lower=max(min_std, 1e-9))
    return (series - mean) / std


def score_region(metrics: pd.DataFrame) -> pd.DataFrame:
    metrics = metrics.copy()
    metrics["z_water"] = zscore(metrics["water_mm"])
    # heat days are counts and can be 0 for a whole decade: min_std=0.5
    metrics["z_heat"] = zscore(metrics["bloom_heat_days"].astype(float),
                               min_std=0.5)
    metrics["s_water"] = (50 + 25 * metrics["z_water"]).clip(0, 100)
    metrics["s_heat"] = (50 - 25 * metrics["z_heat"]).clip(0, 100)
    metrics["score"] = ((metrics["s_water"] + metrics["s_heat"]) / 2).round(0)
    return metrics


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    region_scores = {}
    coverage_min = 1.0
    last_data_overall = None

    for name, cfg in REGIONS.items():
        print(f"Fetching ERA5 daily data for {name} ...")
        df = fetch_region(name, cfg)
        last_data = df.index.max().date()
        last_data_overall = (
            min(last_data_overall, last_data) if last_data_overall else last_data
        )
        metrics = compute_region_metrics(df, last_data)
        region_scores[name] = score_region(metrics)

    print("Combining regions ...")
    combined = pd.DataFrame(
        {name: m["score"] for name, m in region_scores.items()}
    ).dropna()
    weights = pd.Series({n: REGIONS[n]["weight"] for n in combined.columns})
    combined["score_c"] = (
        (combined * (weights / weights.sum())).sum(axis=1).round(0)
    )
    for name, m in region_scores.items():
        combined[f"{name}_water_mm"] = m["water_mm"].round(1)
        combined[f"{name}_heat_days"] = m["bloom_heat_days"]
        combined[f"{name}_coverage"] = m["coverage"]
        coverage_min = min(coverage_min, float(m["coverage"].iloc[-1]))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "pillar_c_series.csv"
    combined.to_csv(csv_path, index_label="outlook_year", float_format="%.4f")

    last_year = int(combined.index[-1])
    last = combined.iloc[-1]
    preliminary = coverage_min < 0.98
    latest = {
        "pillar": "harvest_outlook",
        "stage": "lite (weather only, no vegetation indices yet)",
        "outlook_crop_year": f"{last_year}/{str(last_year + 1)[-2:]}",
        "score": int(last["score_c"]),
        "preliminary": preliminary,
        "window_coverage_min": round(coverage_min, 3),
        "regions": {
            name: {
                "score": int(last[name]),
                "water_mm": float(last[f"{name}_water_mm"]),
                "bloom_heat_days": int(last[f"{name}_heat_days"]),
                "weight": REGIONS[name]["weight"],
            }
            for name in REGIONS
        },
        "data_as_of": str(last_data_overall),
        "note": "Not part of the headline index yet — pending backtest and "
                "methodology v0.2.",
        "attribution": "Weather data by Open-Meteo.com (ERA5/ERA5-Land, "
                       "Copernicus), CC BY 4.0",
    }
    json_path = OUT_DIR / "pillar_c_latest.json"
    json_path.write_text(json.dumps(latest, indent=2, ensure_ascii=False))

    flag = " (preliminary — windows not yet complete)" if preliminary else ""
    print(f"\nDone. Harvest outlook {latest['outlook_crop_year']}: "
          f"score {latest['score']}{flag}")
    for name in REGIONS:
        r = latest["regions"][name]
        print(f"  {name:10s} score {r['score']:3d} | water {r['water_mm']:7.1f} mm "
              f"| bloom heat days {r['bloom_heat_days']}")
    print(f"  -> {csv_path}\n  -> {json_path}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as err:
        sys.exit(
            f"HTTP error from data source: {err}\n"
            "Check BASE_URL against the API documentation:\n"
            "https://open-meteo.com/en/docs/historical-weather-api"
        )
