#!/usr/bin/env python3
"""
OpenFoodSignal — Backtest for the olive oil index (v0.1).

Reconstructs the combined weekly index from the historical pillar series
and answers the project's core validation question:

    When would the traffic light have turned red, and how much lead time
    would that have given before the peak of the 2022-2024 crisis?

Inputs (produced by pillar_b.py and pillar_a.py):
  data/pillar_b_series.csv   weekly price-pillar series since 2010
  data/pillar_a_series.csv   annual stock-pillar series per crop year

Outputs:
  data/backtest_series.csv   weekly combined index (the backtest dataset)
  docs/backtest_chart.png    combined index over time with thresholds
  docs/BACKTEST.md           auto-generated report with the key findings

Method notes (also stated in the report):
  * Both pillar series are computed with trailing windows only, so each
    week's score uses no future information.
  * The underlying balance data are "as revised today", not "as known
    then" — EU/IOC figures get revised. A true point-in-time backtest
    would need archived data vintages (out of scope for v0.1).
  * Pillar A scores are assigned to all weeks of their crop year
    (Oct-Sep), assuming in-season estimates were available.
  * v0.1 has no Pillar C (harvest outlook); the measured lead time is
    therefore a lower bound for the full model.

Usage:
  pip install requests pandas matplotlib
  python backtest.py

Compatibility: Python 3.7+.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

WEIGHT_A = 0.58
WEIGHT_B = 0.42
RED_MAX = 33
GREEN_MIN = 67
START = "2015-01-01"          # backtest window start (needs 5y warm-up before)
MIN_EPISODE_WEEKS = 4          # ignore red blips shorter than this

DATA_DIR = Path("data")
DOCS_DIR = Path("docs")


def crop_year_of(ts: pd.Timestamp) -> int:
    """Crop year (Oct-Sep): Nov 2022 -> 2022, Mar 2023 -> 2022."""
    return ts.year if ts.month >= 10 else ts.year - 1


def load_series() -> pd.DataFrame:
    b_path = DATA_DIR / "pillar_b_series.csv"
    a_path = DATA_DIR / "pillar_a_series.csv"
    for p in (b_path, a_path):
        if not p.exists():
            sys.exit(f"ERROR: {p} not found — run pillar_b.py and "
                     f"pillar_a.py first.")

    b = pd.read_csv(b_path, parse_dates=["week_end"])
    a = pd.read_csv(a_path)

    b = b.dropna(subset=["score_b"]).copy()
    b["crop_year"] = b["week_end"].apply(crop_year_of)

    a_scores = a.dropna(subset=["score_a"]).set_index("production_year")

    df = b.merge(
        a_scores[["score_a", "coverage_ratio"]],
        left_on="crop_year", right_index=True, how="inner",
    )
    df["index"] = (WEIGHT_A * df["score_a"] + WEIGHT_B * df["score_b"]).round(0)
    df["light"] = pd.cut(
        df["index"], bins=[-1, RED_MAX, GREEN_MIN - 1, 100],
        labels=["red", "yellow", "green"],
    )
    df = df[df["week_end"] >= pd.Timestamp(START)].reset_index(drop=True)
    if df.empty:
        sys.exit("ERROR: no overlapping weeks between the pillar series.")
    return df


def find_episodes(df: pd.DataFrame) -> list[dict]:
    """Consecutive red phases of at least MIN_EPISODE_WEEKS."""
    episodes, run = [], []
    for _, row in df.iterrows():
        if row["light"] == "red":
            run.append(row)
        else:
            if len(run) >= MIN_EPISODE_WEEKS:
                episodes.append(run)
            run = []
    if len(run) >= MIN_EPISODE_WEEKS:
        episodes.append(run)

    out = []
    for run in episodes:
        seg = pd.DataFrame(run)
        out.append(
            {
                "first_red": seg["week_end"].iloc[0].date(),
                "last_red": seg["week_end"].iloc[-1].date(),
                "weeks": len(seg),
                "min_index": int(seg["index"].min()),
            }
        )
    return out


def analyze(df: pd.DataFrame) -> dict:
    episodes = find_episodes(df)

    # Reference point for severity: week of the highest real producer price.
    peak_row = df.loc[df["real_smoothed"].idxmax()]
    peak_date = peak_row["week_end"].date()

    # The crisis episode = the red episode containing (or nearest before)
    # the price peak.
    crisis = None
    for ep in episodes:
        if ep["first_red"] <= peak_date:
            crisis = ep
    lead_days = (peak_date - crisis["first_red"]).days if crisis else None

    # First yellow before the crisis episode (earliest warning of any kind):
    first_yellow = None
    if crisis:
        before = df[df["week_end"] < pd.Timestamp(crisis["first_red"])]
        # walk backwards while the light is not green
        non_green = before[before["light"] != "green"]
        if not non_green.empty:
            # find the start of the contiguous non-green run ending at crisis
            idx = list(before.index)
            run_start = None
            for i in reversed(idx):
                if before.loc[i, "light"] == "green":
                    break
                run_start = i
            if run_start is not None:
                first_yellow = before.loc[run_start, "week_end"].date()

    return {
        "episodes": episodes,
        "peak_date": peak_date,
        "peak_real_price": round(float(peak_row["real_smoothed"]), 1),
        "crisis": crisis,
        "lead_days": lead_days,
        "first_yellow": first_yellow,
        "span": (df["week_end"].iloc[0].date(), df["week_end"].iloc[-1].date()),
        "n_weeks": len(df),
    }


def make_chart(df: pd.DataFrame, res: dict) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("note: matplotlib not installed — skipping chart "
              "(pip install matplotlib)")
        return None

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=150)
    ax.plot(df["week_end"], df["index"], color="#A87D08", linewidth=1.6,
            label="combined index (v0.1)")
    ax.axhline(GREEN_MIN, color="#2E8C43", linestyle="--", linewidth=0.9,
               label=f"green ≥ {GREEN_MIN}")
    ax.axhline(RED_MAX, color="#C8402B", linestyle="--", linewidth=0.9,
               label=f"red ≤ {RED_MAX}")
    for ep in res["episodes"]:
        ax.axvspan(ep["first_red"], ep["last_red"], color="#C8402B",
                   alpha=0.08)
    ax.axvline(res["peak_date"], color="#20261A", linewidth=0.9,
               linestyle=":", label="real price peak")
    ax.set_ylim(0, 100)
    ax.set_ylabel("availability index")
    ax.set_title("OpenFoodSignal backtest — olive oil, combined index v0.1")
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    out = DOCS_DIR / "backtest_chart.png"
    fig.savefig(out)
    plt.close(fig)
    return out


def write_report(res: dict, chart: Path | None) -> Path:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    c = res["crisis"]
    lead_weeks = res["lead_days"] // 7 if res["lead_days"] is not None else None

    lines = []
    lines.append("# Backtest report — olive oil index v0.1")
    lines.append("")
    lines.append(f"*Auto-generated by `backtest.py` on {dt.date.today()}. "
                 f"Window: {res['span'][0]} to {res['span'][1]} "
                 f"({res['n_weeks']} weeks).*")
    lines.append("")
    lines.append("## Core question")
    lines.append("")
    lines.append("Would the index have flagged the 2022–2024 olive oil "
                 "crisis — and how early?")
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    if c and res["lead_days"] is not None:
        lines.append(f"- The traffic light turned **red on "
                     f"{c['first_red']}** and stayed red for "
                     f"{c['weeks']} weeks (minimum index: "
                     f"{c['min_index']}).")
        if res["first_yellow"]:
            lines.append(f"- The first warning (leaving green) came even "
                         f"earlier, on **{res['first_yellow']}**.")
        lines.append(f"- The real producer price peaked on "
                     f"**{res['peak_date']}** "
                     f"({res['peak_real_price']} €/100 kg, deflated).")
        lines.append(f"- **Lead time red → price peak: {res['lead_days']} "
                     f"days (~{lead_weeks} weeks).** Retail shelf prices "
                     f"lag producer prices further, so consumer-facing "
                     f"lead time is larger still.")
    else:
        lines.append("- No qualifying red episode found before the price "
                     "peak — inspect `data/backtest_series.csv`.")
    lines.append("")
    if res["episodes"]:
        lines.append("All red episodes detected "
                     f"(≥ {MIN_EPISODE_WEEKS} consecutive weeks):")
        lines.append("")
        lines.append("| first red | last red | weeks | min index |")
        lines.append("|---|---|---|---|")
        for ep in res["episodes"]:
            lines.append(f"| {ep['first_red']} | {ep['last_red']} | "
                         f"{ep['weeks']} | {ep['min_index']} |")
        lines.append("")
    if chart:
        lines.append("![Backtest chart](backtest_chart.png)")
        lines.append("")
    lines.append("## Limitations (v0.1)")
    lines.append("")
    lines.append("- Balance data are *as revised today*, not as known at "
                 "the time; a point-in-time backtest would require "
                 "archived data vintages.")
    lines.append("- Pillar A uses the coverage proxy (stocks / 5y mean "
                 "production) and is assigned to all weeks of its crop "
                 "year, assuming in-season estimates.")
    lines.append("- Pillar C (harvest outlook) is not implemented yet — "
                 "the measured lead time is a **lower bound** for the "
                 "full model.")
    lines.append("- Score saturation (hard clamp at 0/100) is a known "
                 "open issue; unclamped z-scores are preserved in the "
                 "pillar series.")
    lines.append("")

    out = DOCS_DIR / "BACKTEST.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> None:
    print("Loading pillar series and combining weekly index ...")
    df = load_series()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    series_path = DATA_DIR / "backtest_series.csv"
    df.to_csv(series_path, index=False, float_format="%.4f")

    print("Analyzing episodes and lead time ...")
    res = analyze(df)
    chart = make_chart(df, res)
    report = write_report(res, chart)

    print(f"\nBacktest window: {res['span'][0]} → {res['span'][1]}")
    if res["crisis"]:
        print(f"First red:      {res['crisis']['first_red']}")
        print(f"Price peak:     {res['peak_date']}")
        print(f"Lead time:      {res['lead_days']} days")
    print(f"\n  -> {series_path}")
    if chart:
        print(f"  -> {chart}")
    print(f"  -> {report}")


if __name__ == "__main__":
    main()
