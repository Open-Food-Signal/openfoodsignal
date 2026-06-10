# OpenFoodSignal — Methodology v0.1

**Commodity:** Olive oil (EU scope, world context)
**Status:** Draft for public review · Pillars A and B operational, Pillars C and D specified but not yet implemented
**License:** Methodology and index data: ODbL 1.0 · Code: AGPL 3.0

---

## 0. Overview

The OpenFoodSignal availability index condenses public market, trade and satellite data into a single 0–100 score per commodity ("100" = excellent availability), published weekly with a traffic-light representation and a plain-language explanation.

The full model consists of four pillars:

| Pillar | Meaning | Target weight | Status in v0.1 |
|---|---|---|---|
| A — Stock coverage | How long would current stocks last? | 35 % | **operational** |
| B — Price signal | How far are real prices from their norm? | 25 % | **operational** |
| C — Harvest outlook | What do weather and vegetation data say about the next crop? | 30 % | specified, v0.2 |
| D — Trade stress | Export restrictions, abnormal trade flows | 10 % | specified, v0.2 |

In v0.1 only A and B are computed. Their weights are renormalized to **A = 58 %, B = 42 %** (preserving the 35:25 ratio). Consequence to be stated openly wherever the index is shown: *v0.1 describes the present state of the market; it is not yet forward-looking. The early-warning capability arrives with Pillar C.*

All thresholds and weights in this document are **starting values**. They will be calibrated in the historical backtest (2015–2026) and any change will be released as a new methodology version. The index is only as good as its reproducibility: every published score must be recomputable from the referenced raw data and the code in this repository.

---

## 1. Pillar A — Stock coverage

### 1.1 Concept

The stocks-to-use ratio (SUR) is the classic fundamental indicator of commodity scarcity: ending stocks divided by annual use. A market can tolerate a poor harvest if warehouses are full; it cannot if they are empty. Pillar A measures how the current SUR compares with its own recent history.

### 1.2 Data sources

| Source | Series | Frequency | Notes |
|---|---|---|---|
| International Olive Council (IOC), Statistics Dashboard | World and per-country balance sheets: production, consumption, ending stocks per crop year (from 1990/91) | updated ~monthly | primary source for world scope |
| EU Agri-Food Data Portal (agridata.ec.europa.eu) | EU production and end-of-campaign stocks per member state | monthly/annual | primary source for EU scope |
| AICA (Spain) | Spanish monthly production and stock figures | monthly | highest-frequency signal; Spain ≈ half of world production |

The olive oil **crop year runs 1 October – 30 September**. All balance-sheet quantities are assigned to crop years, not calendar years.

### 1.3 Computation

1. **SUR for the current crop year *t***:

   `SUR_t = ending_stocks_t / consumption_t`

   Within a running crop year, `ending_stocks_t` is the latest published stock estimate and `consumption_t` the latest consumption forecast (IOC/EU short-term outlook). Both inputs and their publication dates are stored with each index run.

2. **Reference statistics**: mean `μ` and standard deviation `σ` of SUR over the **five preceding crop years** (t−1 … t−5).

3. **Standardization**: `z_A = (SUR_t − μ) / σ`

4. **Mapping to score** (linear, clamped):

   `score_A = clamp(50 + 25 · z_A, 0, 100)`

   i.e. a SUR two standard deviations below its five-year norm scores 0; two above scores 100.

### 1.4 Update cadence and carry-forward

Balance-sheet data arrive monthly at best. Between releases the last computed `score_A` is carried forward; the JSON output records the age of the underlying data (`stocks_data_age_days`). If the newest usable stock figure is older than 90 days, the data-quality flag (§3) degrades.

### 1.5 Known limitations and edge cases

- **Revisions**: IOC and EU figures are revised. The pipeline recomputes the full history on every run; revisions therefore change past scores. Published snapshots are archived so that "the index as shown on date X" remains reconstructable.
- **Scope mismatch**: world SUR and EU SUR can diverge. v0.1 publishes the **EU scope** as the headline (matching the consumer audience) and the world SUR as a context field.
- **Small σ**: if the five-year σ is unrealistically small (calm reference period), z explodes. σ is floored at 25 % of μ's five-year coefficient of variation across the full history.

---

## 2. Pillar B — Price signal

### 2.1 Concept

Producer prices are the fastest public proxy for scarcity: they react within weeks to harvest news and stock draw-downs. Pillar B measures how far current **real** (inflation-adjusted) producer prices deviate from their own five-year norm. Price is deliberately *one* pillar, not the index itself — otherwise the index would merely restate the price ticker and add nothing for the consumer.

### 2.2 Data sources

| Source | Series | Frequency |
|---|---|---|
| EU Agri-Food Data Portal | Weekly producer prices, extra virgin olive oil, per market (Jaén/ES, Bari/IT, Chania/EL and others; series from 2010) | weekly |
| IOC price monitor | Producer prices Jaén, Bari, Chania | monthly (cross-check) |
| Eurostat | HICP all-items index, euro area | monthly |

### 2.3 Computation

1. **Composite nominal price**: production-weighted average of the extra-virgin producer prices of the three reference markets, weights = each country's share of EU production over the last five crop years (recomputed annually):

   `P_t = Σ w_i · p_{i,t}`  with current starting weights ≈ ES 0.62, IT 0.20, EL 0.18

2. **Deflation**: `P_real_t = P_t / HICP_t · HICP_base` (base = January 2020). The latest available HICP month is applied to newer weeks.

3. **Smoothing**: 4-week rolling median of `P_real`, to suppress single-week noise without hiding genuine moves.

4. **Reference statistics**: mean `μ` and standard deviation `σ` of the smoothed real price over the **trailing 260 weeks** (5 years).

5. **Standardization and inversion** (high price = low availability):

   `z_B = (P_real_smoothed_t − μ) / σ`
   `score_B = clamp(50 − 25 · z_B, 0, 100)`

### 2.4 Update cadence and edge cases

- **Missing weeks** (holidays, reporting gaps): a market's last price is carried forward for at most 2 weeks; beyond that the market is dropped from the composite for that week and weights are renormalized. The output flags this (`price_markets_used`).
- **Structural breaks** (e.g. a reference market stops reporting): handled by methodology version bump, never silently.
- **Why z-score and not percentile?** z is explainable in one sentence and behaves sensibly at the extremes of a short history. The backtest will test a percentile variant; if it clearly wins, v0.2 switches — documented, not silent.

---

## 3. Combination, traffic light, data quality

**Index (v0.1):** `index = 0.58 · score_A + 0.42 · score_B`, rounded to integer.

**Traffic light:** green ≥ 67 · yellow 34–66 · red ≤ 33.
**Trend:** difference between today's index and its value 30 days ago, bucketed into improving / stable (±3) / worsening.

**Data-quality flag**, published with every run:

| Flag | Condition |
|---|---|
| `ok` | all inputs fresh (stocks ≤ 90 d, prices ≤ 2 w) |
| `degraded` | one pillar on carry-forward beyond its threshold |
| `stale` | both pillars beyond thresholds — index shown greyed out, no traffic light |

**Plain-language explanation:** generated from a fixed template that names the dominant pillar and its direction, e.g. *"Yellow: EU stocks are 18 % below their five-year norm (Pillar A), while real producer prices are close to normal (Pillar B)."* No free-form generation — every sentence must be derivable from the published numbers.

---

## 4. Output schema (excerpt, `food-availability-signal v0.1`)

```json
{
  "signal_version": "0.1",
  "methodology_version": "0.1.0",
  "commodity": "olive-oil",
  "scope": "EU",
  "timestamp": "2026-06-08",
  "availability_index": 61,
  "traffic_light": "yellow",
  "trend_30d": "improving",
  "data_quality": "ok",
  "pillars": {
    "stocks":  { "score": 48, "sur": 0.31, "sur_5y_mean": 0.38, "data_age_days": 21 },
    "price":   { "score": 79, "p_real_eur_per_100kg": 412, "z": -1.16,
                 "markets_used": ["jaen", "bari", "chania"] }
  },
  "sources": ["ioc-dashboard", "eu-agrifood-portal", "eurostat-hicp"],
  "methodology_url": "https://github.com/openfoodsignal/olive-oil-index/blob/main/METHODOLOGY.md",
  "license": "ODbL-1.0"
}
```

---

## 5. Governance of this document

- Changes to formulas, weights or thresholds require a pull request with a written rationale and take effect only with a new `methodology_version` (semver: parameter recalibration = minor, structural change = major).
- The backtest report (2015–2026) will be published alongside v0.2 and is the designated instrument for calibrating all starting values above.
- Open questions tracked as issues: percentile vs z-score mapping (§2.4), EU vs world headline scope (§1.5), weight calibration (§3).

*Comments and challenges to this methodology are explicitly welcome — credibility through reproducibility is the project's only capital.*
